# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
Random EPrime model generator.

Entry point: generate_model(rng, cfg) → (Model, param_values)
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Optional

from .ast_nodes import (
    Type, Domain,
    IntRangeDomain, BoolDomain, MatrixDomain, CompositeIntDomain,
    IndexOfDomain, DomainBinOp,
    IntLit, BoolLit, Var, BinOp, UnaryOp, AbsVal,
    Index, Slice, ArrayLit, Comprehension, MultiVarComprehension,
    Quantifier, MultiVarQuantifier, FuncCall,
    RowComprehension, TableConstraint, Slice3D,
    InDomain,
    GivenDecl, DomainLettingDecl, LettingDecl, FindDecl, Objective, Model, Expr,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GenConfig:
    max_depth: int = 4          # max expression nesting depth
    max_find_vars: int = 4      # max decision variables
    max_given_params: int = 3   # max given parameters
    max_constraints: int = 5    # max top-level constraints
    max_array_len: int = 6      # max 1-D array size in domains
    max_matrix_rows: int = 4    # max rows in 2-D matrix domains
    max_matrix_cols: int = 4    # max cols in 2-D matrix domains
    int_range: tuple[int,int] = (-5, 20)  # range for generated int literals

    # Feature weights (relative probability).  Set to 0 to disable.
    feat_allDiff: float = 1.0
    feat_quantifier: float = 1.0
    feat_sum_agg: float = 1.0
    feat_matrix_slice: float = 0.6
    feat_abs_val: float = 0.5
    feat_implication: float = 0.8
    feat_objective: float = 0.5
    feat_bool_vars: float = 0.5
    feat_2d_matrix: float = 0.6
    feat_comprehension: float = 0.5
    feat_toInt: float = 0.4
    feat_count: float = 0.4
    feat_table: float = 0.8    # weight for table constraints in _gen_bool
    max_table_rows: int = 8    # max rows in a generated table
    max_table_arity: int = 4   # max number of variables in a table constraint
    feat_div_mod: float = 0.5  # / and % operators (trigger undefinedness when divisor=0)
    feat_power: float = 0.4    # ** operator (undefined for 0**0 or negative exponent)
    feat_factorial: float = 0.3  # factorial(x) (undefined for x<0)
    feat_product: float = 0.4  # product(arr) — array product aggregate
    feat_lex: float = 0.5      # <=lex / <lex / >=lex / >lex on arrays
    feat_atleast_atmost: float = 0.6   # atleast/atmost global constraints
    feat_alldiff_except: float = 0.4   # alldifferent_except global constraint
    feat_arr_logic: float = 0.5        # and(arr) / or(arr) on bool arrays
    feat_circuit: float = 0.6          # circuit(X) — Hamiltonian circuit on 1D array
    feat_inverse: float = 0.5          # inverse(X, Y) — functional inverse of two 1D arrays
    feat_gcc: float = 0.5              # gcc(X, Vals, C) — global cardinality constraint
    feat_min_max_scalar: float = 0.5   # min(x,y) / max(x,y) — binary scalar forms
    feat_popcount: float = 0.3         # popcount(x) — bit population count (non-decision arg only)
    feat_multi_quantifier: float = 0.4 # forAll i,j : d . body — multi-variable quantifiers
    feat_composite_domain: float = 0.25 # int(a..b, c, d..e) — non-contiguous integer domains
    feat_cumulative: float = 0.4       # cumulative(S, D, R, Cap) — resource scheduling
    feat_disjunctive: float = 0.4      # disjunctive(S, D) — non-overlapping tasks
    feat_diffn: float = 0.3            # diffn(X, Y, W, H) — 2D rectangle non-overlap
    feat_where: float = 0.4            # where clause constraints on given parameters
    feat_multi_comprehension: float = 0.35  # [expr | i : d1, j : d2, ...] multi-variable comprehensions
    feat_in_domain: float = 0.6   # expr in int(lo..hi) / int(a..b, c, d..e) — domain membership
    feat_flatten: float = 0.5     # flatten(M) — flatten a 2D int matrix to a 1D array
    feat_domain_letting: float = 0.3  # letting D be domain ... with find x : D
    feat_list: float = 0.4            # list(e1, e2, ...) — construct 1D int array from scalar exprs
    feat_toset: float = 0.5           # x in toSet(arr) — set membership over 1D int array values
    feat_cat_1d: float = 0.4          # cat(arr1, arr2) — concatenate two 1D int arrays in any array context
    feat_unary_minus: float = 0.4     # -x — integer negation (unary minus)
    feat_sum_arr: float = 0.5         # sum(X) — sum of elements of a 1D int array (distinct from sum quantifier)
    feat_indexOf: float = 0.35        # indexOf(arr) as domain in forAll/exists/sum quantifiers
    feat_domain_binop: float = 0.35   # D1 union/intersect/- D2 — domain expressions in 'in' and lettings
    feat_branching_on: float = 0.25   # branching on [...] — solver search variable ordering
    force_simple_objective: bool = False  # always produce a sum-of-scalars objective (for diff testing)


# ---------------------------------------------------------------------------
# Scope: tracks variables available in the current expression context
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    vars: dict[str, Type] = field(default_factory=dict)
    domains: dict[str, "Domain"] = field(default_factory=dict)
    # table_lettings: name → arity (number of columns)
    table_lettings: dict[str, int] = field(default_factory=dict)
    # table3d_givens: name → arity (= size of 3rd index domain = number of columns)
    table3d_givens: dict[str, int] = field(default_factory=dict)
    # table2d_givens: name → arity (= size of 2nd index domain = number of columns)
    table2d_givens: dict[str, int] = field(default_factory=dict)

    def with_var(self, name: str, t: Type, domain: "Domain | None" = None) -> "Scope":
        new = Scope(dict(self.vars), dict(self.domains),
                    dict(self.table_lettings), dict(self.table3d_givens),
                    dict(self.table2d_givens))
        new.vars[name] = t
        if domain is not None:
            new.domains[name] = domain
        return new

    def of_type(self, t: Type) -> list[str]:
        return [n for n, vt in self.vars.items() if vt == t]

    def scalar_ints(self) -> list[str]:
        return self.of_type(Type.INT)

    def scalar_bools(self) -> list[str]:
        return self.of_type(Type.BOOL)

    def arrays_of(self, t: Type) -> list[str]:
        return self.of_type(t)


# ---------------------------------------------------------------------------
# Generator class
# ---------------------------------------------------------------------------

class Generator:
    def __init__(self, rng: random.Random, cfg: GenConfig):
        self.rng = rng
        self.cfg = cfg
        self._name_counter = 0

    # --- helpers ------------------------------------------------------------

    def _fresh(self, prefix: str = "v") -> str:
        self._name_counter += 1
        return f"{prefix}{self._name_counter}"

    def _coin(self, p: float = 0.5) -> bool:
        return self.rng.random() < p

    def _choice(self, seq):
        return self.rng.choice(seq)

    def _randint(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo, hi)

    def _weighted(self, items: list, weights: list[float]):
        return self.rng.choices(items, weights=weights, k=1)[0]

    # --- domain generation --------------------------------------------------

    def gen_int_domain(self, min_size: int = 2) -> IntRangeDomain:
        cfg = self.cfg
        lo = self._randint(*cfg.int_range)
        size = self._randint(min_size, max(min_size, cfg.max_array_len * 3))
        return IntRangeDomain(lo, lo + size)

    def gen_element_domain(self) -> IntRangeDomain | BoolDomain:
        if self._coin(self.cfg.feat_bool_vars * 0.3):
            return BoolDomain()
        return self.gen_int_domain(2)

    def gen_1d_domain(self) -> IntRangeDomain:
        size = self._randint(2, self.cfg.max_array_len)
        return IntRangeDomain(1, size)   # always 1-based index range

    def gen_matrix_domain(self) -> MatrixDomain:
        if self._coin(self.cfg.feat_2d_matrix):
            rows = self._randint(2, self.cfg.max_matrix_rows)
            cols = self._randint(2, self.cfg.max_matrix_cols)
            idx = [IntRangeDomain(1, rows), IntRangeDomain(1, cols)]
        else:
            n = self._randint(2, self.cfg.max_array_len)
            idx = [IntRangeDomain(1, n)]
        elem = self.gen_element_domain()
        return MatrixDomain(idx, elem)

    def gen_find_domain(self) -> Domain:
        r = self.rng.random()
        if r < 0.30:
            return self.gen_int_domain(2)
        elif r < 0.50 and self.cfg.feat_bool_vars > 0:
            return BoolDomain()
        elif r < 0.56 and self.cfg.feat_composite_domain > 0 and self._coin(self.cfg.feat_composite_domain):
            return self.gen_composite_int_domain()
        else:
            return self.gen_matrix_domain()

    def gen_3d_matrix_domain(self, arity: int) -> MatrixDomain:
        """3-D matrix domain for use as a table source.
        dim1 = outer slicing dimension (small), dim2 = rows, dim3 = arity (columns)."""
        d1 = self._randint(2, max(2, self.cfg.max_array_len // 2))
        d2 = self._randint(2, self.cfg.max_table_rows)
        elem = self.gen_int_domain(2)
        return MatrixDomain(
            [IntRangeDomain(1, d1), IntRangeDomain(1, d2), IntRangeDomain(1, arity)],
            elem,
        )

    def gen_domain_binop(self) -> DomainBinOp:
        """Generate a binary domain expression: D1 union/intersect/- D2."""
        op = self._choice(["union", "intersect", "-"])
        left  = (self.gen_composite_int_domain() if self._coin(0.3)
                 else self.gen_int_domain(1))
        right = (self.gen_composite_int_domain() if self._coin(0.3)
                 else self.gen_int_domain(1))
        return DomainBinOp(op, left, right)

    def _quant_domain(self, scope: "Scope") -> "IntRangeDomain | IndexOfDomain":
        """Return a domain for a quantifier variable.

        With probability feat_indexOf (when 1-D int arrays are available),
        uses indexOf(arr) so the quantifier iterates over the array's actual
        index range.  Otherwise falls back to a fresh literal IntRangeDomain.
        """
        if self.cfg.feat_indexOf > 0 and self._coin(self.cfg.feat_indexOf):
            arr_1d = scope.arrays_of(Type.ARRAY_INT)
            if arr_1d:
                name = self._choice(arr_1d)
                return IndexOfDomain(Var(name, Type.ARRAY_INT))
        return self.gen_int_domain(2)

    def gen_composite_int_domain(self) -> CompositeIntDomain:
        """Generate a non-contiguous int domain like int(1..3, 7, 10..12)."""
        n_parts = self._randint(2, 4)
        parts: list[IntRangeDomain] = []
        lo = self._randint(*self.cfg.int_range)
        for _ in range(n_parts):
            size = self._randint(0, 4)   # 0 = single value
            parts.append(IntRangeDomain(lo, lo + size))
            lo += size + self._randint(1, 5)  # gap between parts
        return CompositeIntDomain(parts)

    def gen_given_domain(self) -> Domain:
        r = self.rng.random()
        if r < 0.5:
            return self.gen_int_domain(1)
        elif r < 0.65:
            return BoolDomain()
        else:
            return self.gen_matrix_domain()

    # --- expression generation ----------------------------------------------

    def gen_expr(self, want: Type, depth: int, scope: Scope) -> Expr:
        """Generate an expression of type `want` with remaining depth `depth`."""
        if want == Type.INT:
            return self._gen_int(depth, scope)
        if want == Type.BOOL:
            return self._gen_bool(depth, scope)
        if want in (Type.ARRAY_INT, Type.ARRAY_BOOL):
            return self._gen_array(want, depth, scope)
        raise ValueError(f"Cannot generate expr of type {want}")

    # -- int -----------------------------------------------------------------

    def _gen_int(self, depth: int, scope: Scope) -> Expr:
        int_vars = scope.scalar_ints()
        if depth <= 0 or (int_vars and self._coin(0.4)):
            if int_vars and self._coin(0.6):
                return Var(self._choice(int_vars), Type.INT)
            lo, hi = self.cfg.int_range
            return IntLit(self._randint(lo, hi))

        choices = ["lit", "binop_arith", "binop_cmp_int"]
        weights = [1.0, 2.0, 0.5]

        if int_vars:
            choices += ["var"]
            weights += [3.0]

        # sum quantifier
        if self.cfg.feat_sum_agg > 0 and depth >= 2:
            choices += ["sum_quant"]
            weights += [self.cfg.feat_sum_agg]

        # index into array (1D and 2D both OK)
        arr_int_vars = scope.arrays_of(Type.ARRAY_INT) + scope.arrays_of(Type.MATRIX_INT)
        if arr_int_vars:
            choices += ["index"]
            weights += [1.5]

        # toInt(bool)
        bool_vars = scope.scalar_bools()
        if bool_vars and self.cfg.feat_toInt > 0:
            choices += ["toInt"]
            weights += [self.cfg.feat_toInt]

        # abs value
        if self.cfg.feat_abs_val > 0:
            choices += ["abs"]
            weights += [self.cfg.feat_abs_val * 0.5]

        # unary minus: -x (integer negation)
        if self.cfg.feat_unary_minus > 0:
            choices += ["unary_minus"]
            weights += [self.cfg.feat_unary_minus]

        # count/min/max/product/sum require 1D arrays only
        arr_1d_int_vars = scope.arrays_of(Type.ARRAY_INT)
        if arr_1d_int_vars and self.cfg.feat_count > 0:
            choices += ["count"]
            weights += [self.cfg.feat_count]
        if arr_1d_int_vars:
            choices += ["min_arr", "max_arr"]
            weights += [0.4, 0.4]
        if arr_1d_int_vars and self.cfg.feat_product > 0:
            choices += ["product"]
            weights += [self.cfg.feat_product]
        if arr_1d_int_vars and self.cfg.feat_sum_arr > 0:
            choices += ["sum_arr"]
            weights += [self.cfg.feat_sum_arr]

        # division / modulo — create undefinedness when right side is 0
        if self.cfg.feat_div_mod > 0:
            choices += ["div_mod"]
            weights += [self.cfg.feat_div_mod]

        # power — undefined for 0**0 or negative exponent
        if self.cfg.feat_power > 0:
            choices += ["power"]
            weights += [self.cfg.feat_power]

        # factorial — undefined for negative argument
        if self.cfg.feat_factorial > 0:
            choices += ["factorial"]
            weights += [self.cfg.feat_factorial]

# min(x,y) / max(x,y) — binary scalar forms
        if self.cfg.feat_min_max_scalar > 0 and int_vars:
            choices += ["min_scalar", "max_scalar"]
            weights += [self.cfg.feat_min_max_scalar * 0.5, self.cfg.feat_min_max_scalar * 0.5]

        # popcount(x) — bit pop count, non-decision arg only (use literal)
        if self.cfg.feat_popcount > 0:
            choices += ["popcount"]
            weights += [self.cfg.feat_popcount]

        pick = self._weighted(choices, weights)

        if pick == "lit":
            lo, hi = self.cfg.int_range
            return IntLit(self._randint(lo, hi))
        if pick == "var":
            return Var(self._choice(int_vars), Type.INT)
        if pick == "binop_arith":
            op = self._choice(["+", "-", "*"])
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            return BinOp(op, l, r, Type.INT)
        if pick == "binop_cmp_int":
            # toInt of a comparison — useful in sum contexts
            op = self._choice(["=", "!=", "<", "<=", ">", ">="])
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            inner = BinOp(op, l, r, Type.BOOL)
            return FuncCall("toInt", [inner], Type.INT)
        if pick == "sum_quant":
            d = self._quant_domain(scope)
            qv = self._fresh("q")
            body = self._gen_int(depth - 1, scope.with_var(qv, Type.INT))
            return Quantifier("sum", qv, d, body, Type.INT)
        if pick == "index":
            name = self._choice(arr_int_vars)
            vtype = scope.vars[name]
            return self._make_index(name, vtype, scope, depth)
        if pick == "toInt":
            b = self._gen_bool(depth - 1, scope)
            return FuncCall("toInt", [b], Type.INT)
        if pick == "abs":
            inner = self._gen_int(depth - 1, scope)
            return AbsVal(inner)
        if pick == "unary_minus":
            inner = self._gen_int(depth - 1, scope)
            return UnaryOp("-", inner, Type.INT)
        if pick == "count":
            name = self._choice(arr_1d_int_vars)
            arr_expr = Var(name, scope.vars[name])
            val = self._gen_int(0, scope)
            return FuncCall("count", [arr_expr, val], Type.INT)
        if pick in ("min_arr", "max_arr"):
            fname = "min" if pick == "min_arr" else "max"
            name = self._choice(arr_1d_int_vars)
            arr_expr = Var(name, scope.vars[name])
            return FuncCall(fname, [arr_expr], Type.INT)
        if pick == "product":
            name = self._choice(arr_1d_int_vars)
            arr_expr = Var(name, scope.vars[name])
            return FuncCall("product", [arr_expr], Type.INT)
        if pick == "sum_arr":
            name = self._choice(arr_1d_int_vars)
            arr_expr = Var(name, scope.vars[name])
            return FuncCall("sum", [arr_expr], Type.INT)
        if pick == "div_mod":
            op = self._choice(["/", "%"])
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            return BinOp(op, l, r, Type.INT)
        if pick == "power":
            l = self._gen_int(depth - 1, scope)
            # Cap exponent to a small literal to avoid astronomical values
            r = IntLit(self._randint(-1, 4))
            return BinOp("**", l, r, Type.INT)
        if pick == "factorial":
            inner = self._gen_int(depth - 1, scope)
            return FuncCall("factorial", [inner], Type.INT)
        if pick == "min_scalar":
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            return FuncCall("min", [l, r], Type.INT)
        if pick == "max_scalar":
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            return FuncCall("max", [l, r], Type.INT)
        if pick == "popcount":
            # popcount must be non-decision: use a non-negative literal
            val = IntLit(self._randint(0, 1000))
            return FuncCall("popcount", [val], Type.INT)

        lo, hi = self.cfg.int_range
        return IntLit(self._randint(lo, hi))

    # -- bool ----------------------------------------------------------------

    def _gen_bool(self, depth: int, scope: Scope) -> Expr:
        bool_vars = scope.scalar_bools()
        if depth <= 0:
            if bool_vars and self._coin(0.5):
                return Var(self._choice(bool_vars), Type.BOOL)
            return BoolLit(self._coin())

        # Pre-compute array sets once (no RNG calls).
        arr_int     = scope.arrays_of(Type.ARRAY_INT)   # 1D int arrays
        arr_bool_1d = scope.arrays_of(Type.ARRAY_BOOL)  # 1D bool arrays

        choices = ["lit", "cmp_int", "logical"]
        weights = [0.5, 2.5, 1.5]

        if bool_vars:
            choices += ["var"]
            weights += [2.0]

        # in_domain: any INT expression (including scalars, array elements, …)
        # is always available since we can always produce an int expression
        if self.cfg.feat_in_domain > 0:
            choices += ["in_domain"]
            weights += [self.cfg.feat_in_domain]

        # in_toset: scalar in toSet(arr) — checks whether a value appears in the
        # multiset of values stored in a 1D int array
        if self.cfg.feat_toset > 0 and arr_int:
            choices += ["in_toset"]
            weights += [self.cfg.feat_toset]

        if self.cfg.feat_allDiff > 0 and (arr_int or depth >= 2):
            choices += ["allDiff"]
            weights += [self.cfg.feat_allDiff]

        if self.cfg.feat_quantifier > 0 and depth >= 2:
            choices += ["forAll", "exists"]
            weights += [self.cfg.feat_quantifier, self.cfg.feat_quantifier * 0.6]

        if self.cfg.feat_implication > 0 and depth >= 2:
            choices += ["impl", "iff"]
            weights += [self.cfg.feat_implication, self.cfg.feat_implication * 0.5]

        if bool_vars or depth >= 1:
            choices += ["not"]
            weights += [0.4]

        if self.cfg.feat_table > 0 and len(scope.scalar_ints()) >= 2:
            choices += ["table"]
            weights += [self.cfg.feat_table]

        # Lex ordering requires two 1D int arrays (not 2D matrices)
        if self.cfg.feat_lex > 0 and len(arr_int) >= 2:
            choices += ["lex"]
            weights += [self.cfg.feat_lex]

        if self.cfg.feat_atleast_atmost > 0 and arr_int:
            choices += ["atleast", "atmost"]
            weights += [self.cfg.feat_atleast_atmost * 0.5, self.cfg.feat_atleast_atmost * 0.5]

        if self.cfg.feat_alldiff_except > 0 and arr_int:
            choices += ["alldiff_except"]
            weights += [self.cfg.feat_alldiff_except]

        if self.cfg.feat_arr_logic > 0 and arr_bool_1d:
            choices += ["and_arr", "or_arr"]
            weights += [self.cfg.feat_arr_logic * 0.5, self.cfg.feat_arr_logic * 0.5]

        if self.cfg.feat_circuit > 0 and arr_int:
            choices += ["circuit"]
            weights += [self.cfg.feat_circuit]

        if self.cfg.feat_inverse > 0 and len(arr_int) >= 2:
            choices += ["inverse"]
            weights += [self.cfg.feat_inverse]

        if self.cfg.feat_gcc > 0 and arr_int:
            choices += ["gcc"]
            weights += [self.cfg.feat_gcc]

        if self.cfg.feat_multi_quantifier > 0 and depth >= 2:
            choices += ["forAll_multi", "exists_multi"]
            weights += [self.cfg.feat_multi_quantifier, self.cfg.feat_multi_quantifier * 0.5]

        if self.cfg.feat_cumulative > 0 and arr_int:
            choices += ["cumulative"]
            weights += [self.cfg.feat_cumulative]
        if self.cfg.feat_disjunctive > 0 and arr_int:
            choices += ["disjunctive"]
            weights += [self.cfg.feat_disjunctive]
        if self.cfg.feat_diffn > 0 and len(arr_int) >= 2:
            choices += ["diffn"]
            weights += [self.cfg.feat_diffn]

        pick = self._weighted(choices, weights)

        if pick == "lit":
            return BoolLit(self._coin())
        if pick == "var":
            return Var(self._choice(bool_vars), Type.BOOL)
        if pick == "cmp_int":
            op = self._choice(["=", "!=", "<", "<=", ">", ">="])
            l = self._gen_int(depth - 1, scope)
            r = self._gen_int(depth - 1, scope)
            return BinOp(op, l, r, Type.BOOL)
        if pick == "in_domain":
            inner = self._gen_int(depth - 1, scope)
            if self.cfg.feat_domain_binop > 0 and self._coin(self.cfg.feat_domain_binop):
                d = self.gen_domain_binop()
            elif self._coin(0.4) and self.cfg.feat_composite_domain > 0:
                d = self.gen_composite_int_domain()
            else:
                d = self.gen_int_domain(1)
            return InDomain(inner, d)
        if pick == "in_toset":
            name = self._choice(arr_int)
            scalar = self._gen_int(depth - 1, scope)
            toset = FuncCall("toSet", [Var(name, scope.vars[name])], Type.ARRAY_INT)
            return BinOp("in", scalar, toset, Type.BOOL)
        if pick == "logical":
            op = self._choice(["/\\", "\\/"])
            l = self._gen_bool(depth - 1, scope)
            r = self._gen_bool(depth - 1, scope)
            return BinOp(op, l, r, Type.BOOL)
        if pick == "allDiff":
            if arr_int and self._coin(0.6):
                name = self._choice(arr_int)
                arr_expr: Expr = Var(name, scope.vars[name])
            else:
                arr_expr = self._gen_array(Type.ARRAY_INT, depth - 1, scope)
            return FuncCall("allDiff", [arr_expr], Type.BOOL)
        if pick in ("forAll", "exists"):
            d = self._quant_domain(scope)
            qv = self._fresh("i")
            body = self._gen_bool(depth - 1, scope.with_var(qv, Type.INT))
            return Quantifier(pick, qv, d, body, Type.BOOL)
        if pick in ("impl", "iff"):
            op = "=>" if pick == "impl" else "<->"
            l = self._gen_bool(depth - 1, scope)
            r = self._gen_bool(depth - 1, scope)
            return BinOp(op, l, r, Type.BOOL)
        if pick == "not":
            inner = self._gen_bool(depth - 1, scope)
            return UnaryOp("!", inner, Type.BOOL)
        if pick == "table":
            t = self.gen_table_constraint(scope)
            if t is not None:
                return t
        if pick == "lex":
            op = self._choice(["<=lex", "<lex", ">=lex", ">lex"])
            a1, a2 = self.rng.sample(arr_int, 2)
            return BinOp(op, Var(a1, scope.vars[a1]), Var(a2, scope.vars[a2]), Type.BOOL)
        if pick in ("atleast", "atmost"):
            # atleast/atmost(X, [C], [Vals]) — array, count-array, values-array
            name = self._choice(arr_int)
            arr_expr = Var(name, scope.vars[name])
            lo, hi = self.cfg.int_range
            val   = IntLit(self._randint(lo, hi))
            count = IntLit(self._randint(0, 3))
            return FuncCall(pick, [arr_expr,
                                   ArrayLit([count], Type.ARRAY_INT),
                                   ArrayLit([val],   Type.ARRAY_INT)], Type.BOOL)
        if pick == "alldiff_except":
            name = self._choice(arr_int)
            arr_expr = Var(name, scope.vars[name])
            lo, hi = self.cfg.int_range
            val = IntLit(self._randint(lo, hi))
            return FuncCall("alldifferent_except", [arr_expr, val], Type.BOOL)
        if pick in ("and_arr", "or_arr"):
            fname = "and" if pick == "and_arr" else "or"
            name = self._choice(arr_bool_1d)
            return FuncCall(fname, [Var(name, scope.vars[name])], Type.BOOL)
        if pick == "circuit":
            name = self._choice(arr_int)
            return FuncCall("circuit", [Var(name, scope.vars[name])], Type.BOOL)
        if pick == "inverse":
            a, b = self.rng.sample(arr_int, 2)
            return FuncCall("inverse", [Var(a, scope.vars[a]), Var(b, scope.vars[b])], Type.BOOL)
        if pick == "gcc":
            # gcc(X, Vals, C): X is decision array; Vals and C are literal arrays.
            # We intentionally allow duplicate values in Vals to expose two SR bugs:
            #   (a) SR does not validate Vals uniqueness before handing off to Minion.
            #   (b) When Minion aborts with error code 9 ("GCC: Repeated values are
            #       not allowed"), SR misinterprets the missing solution file as UNSAT
            #       instead of surfacing an error — producing a silent wrong answer
            #       that the SAT backend does not share.
            name = self._choice(arr_int)
            arr_expr = Var(name, scope.vars[name])
            d = scope.domains.get(name)
            lo, hi = (d.element_domain.lo, d.element_domain.hi) if (
                isinstance(d, MatrixDomain) and isinstance(d.element_domain, IntRangeDomain)
            ) else self.cfg.int_range
            k      = self._randint(1, 3)
            vals   = [IntLit(self._randint(lo, hi)) for _ in range(k)]
            counts = [IntLit(self._randint(0, 3))   for _ in range(k)]
            return FuncCall("gcc", [arr_expr,
                                    ArrayLit(vals,   Type.ARRAY_INT),
                                    ArrayLit(counts, Type.ARRAY_INT)], Type.BOOL)
        if pick in ("forAll_multi", "exists_multi"):
            kind = "forAll" if pick == "forAll_multi" else "exists"
            d = self._quant_domain(scope)
            v1, v2 = self._fresh("i"), self._fresh("j")
            ext = scope.with_var(v1, Type.INT).with_var(v2, Type.INT)
            body = self._gen_bool(depth - 1, ext)
            return MultiVarQuantifier(kind, [v1, v2], d, body, Type.BOOL)
        if pick == "cumulative":
            name   = self._choice(arr_int)
            n      = self._array_len(name, scope)
            starts = Var(name, scope.vars[name])
            durs   = ArrayLit([IntLit(self._randint(1, 5)) for _ in range(n)], Type.ARRAY_INT)
            res    = ArrayLit([IntLit(self._randint(1, 3)) for _ in range(n)], Type.ARRAY_INT)
            cap    = IntLit(self._randint(2, 8))
            return FuncCall("cumulative", [starts, durs, res, cap], Type.BOOL)
        if pick == "disjunctive":
            name   = self._choice(arr_int)
            n      = self._array_len(name, scope)
            starts = Var(name, scope.vars[name])
            durs   = ArrayLit([IntLit(self._randint(1, 5)) for _ in range(n)], Type.ARRAY_INT)
            return FuncCall("disjunctive", [starts, durs], Type.BOOL)
        if pick == "diffn":
            a1, a2 = self.rng.sample(arr_int, 2)
            n  = self._array_len(a1, scope)
            xs = Var(a1, scope.vars[a1])
            ys = Var(a2, scope.vars[a2])
            ws = ArrayLit([IntLit(self._randint(1, 4)) for _ in range(n)], Type.ARRAY_INT)
            hs = ArrayLit([IntLit(self._randint(1, 4)) for _ in range(n)], Type.ARRAY_INT)
            return FuncCall("diffn", [xs, ys, ws, hs], Type.BOOL)

        return BoolLit(self._coin())

    # -- arrays --------------------------------------------------------------

    def _gen_array(self, want: Type, depth: int, scope: Scope) -> Expr:
        elem_type = Type.INT if want == Type.ARRAY_INT else Type.BOOL
        n = self._randint(2, self.cfg.max_array_len)

        choices = ["literal"]
        weights = [1.0]

        arr_vars = scope.arrays_of(want)
        if arr_vars:
            choices += ["var"]
            weights += [2.0]

        if depth >= 1 and self.cfg.feat_comprehension > 0 and elem_type == Type.INT:
            choices += ["comprehension"]
            weights += [self.cfg.feat_comprehension]

        if depth >= 1 and self.cfg.feat_multi_comprehension > 0 and elem_type == Type.INT:
            choices += ["multi_comprehension"]
            weights += [self.cfg.feat_multi_comprehension]

        if depth >= 1 and self.cfg.feat_matrix_slice > 0:
            mat_vars = scope.arrays_of(Type.MATRIX_INT if elem_type == Type.INT else Type.MATRIX_BOOL)
            if mat_vars:
                choices += ["slice"]
                weights += [self.cfg.feat_matrix_slice]
        else:
            mat_vars = []

        # flatten(M): 2-D int matrix → 1-D int array (no new AST node needed — uses FuncCall)
        if want == Type.ARRAY_INT and self.cfg.feat_flatten > 0:
            mat_int_vars = scope.arrays_of(Type.MATRIX_INT)
            if mat_int_vars:
                choices += ["flatten"]
                weights += [self.cfg.feat_flatten]
        else:
            mat_int_vars = []

        # list(e1, e2, ...): construct a 1-indexed 1D int array from 2–5 scalar int expressions.
        # Exercises SR's list() function separately from bare array-literal syntax [e1, e2, ...].
        if want == Type.ARRAY_INT and self.cfg.feat_list > 0 and depth >= 1:
            choices += ["list"]
            weights += [self.cfg.feat_list]

        # cat(arr1, arr2): concatenate two 1D int arrays in any array context.
        # Currently cat() is only generated inside table constraints (for 2D matrices);
        # this extends it to general 1D array positions (allDiff, gcc, circuit, etc.).
        if want == Type.ARRAY_INT and self.cfg.feat_cat_1d > 0 and depth >= 1:
            choices += ["cat_1d"]
            weights += [self.cfg.feat_cat_1d]

        pick = self._weighted(choices, weights)

        if pick == "var":
            return Var(self._choice(arr_vars), want)
        if pick == "comprehension":
            d = IntRangeDomain(1, n)
            qv = self._fresh("k")
            body = self._gen_int(depth - 1, scope.with_var(qv, Type.INT))
            cond = self._gen_bool(0, scope.with_var(qv, Type.INT)) if self._coin(0.3) else None
            return Comprehension(body, qv, d, cond, want)
        if pick == "multi_comprehension":
            return self._gen_multi_var_comprehension(depth, scope, want)
        if pick == "flatten":
            name = self._choice(mat_int_vars)
            return FuncCall("flatten", [Var(name, Type.MATRIX_INT)], Type.ARRAY_INT)
        if pick == "list":
            k = self._randint(2, 5)
            elems = [self._gen_int(depth - 1, scope) for _ in range(k)]
            return FuncCall("list", elems, Type.ARRAY_INT)
        if pick == "cat_1d":
            a1 = self._gen_array(Type.ARRAY_INT, depth - 1, scope)
            a2 = self._gen_array(Type.ARRAY_INT, depth - 1, scope)
            return FuncCall("cat", [a1, a2], Type.ARRAY_INT)
        if pick == "slice":
            name = self._choice(mat_vars)
            mat_type = scope.vars[name]
            # pick a random fixed row or column, in-bounds
            if self._coin():
                row_expr = self._inbounds_int(name, 0, scope)
                return Slice(Var(name, mat_type), row_expr, None, want)
            else:
                col_expr = self._inbounds_int(name, 1, scope)
                return Slice(Var(name, mat_type), None, col_expr, want)

        # literal
        elems = [self.gen_expr(elem_type, depth - 1, scope) for _ in range(n)]
        return ArrayLit(elems, want)

    def _gen_multi_var_comprehension(self, depth: int, scope: Scope,
                                     want: Type) -> MultiVarComprehension:
        """Generate [expr | v1 : d1, v2 : d2, ...] with 2–3 iteration variables.

        Each variable gets its own small domain so the Cartesian product does not
        explode.  Variables are added to the scope so the body expression can use
        any of them.
        """
        n_vars = self._randint(2, 3)
        iter_vars = []
        ext_scope = scope
        for _ in range(n_vars):
            v = self._fresh("k")
            # Use small domains to keep the output array a reasonable size.
            lo = self._randint(1, 3)
            hi = lo + self._randint(1, 3)
            d = IntRangeDomain(lo, hi)
            iter_vars.append((v, d))
            ext_scope = ext_scope.with_var(v, Type.INT)

        body = self._gen_int(depth - 1, ext_scope)

        cond: Optional[Expr] = None
        if self._coin(0.3) and n_vars >= 2:
            v0 = Var(iter_vars[0][0], Type.INT)
            v1 = Var(iter_vars[1][0], Type.INT)
            op = self._choice(["<", "<=", "!="])
            cond = BinOp(op, v0, v1, Type.BOOL)

        return MultiVarComprehension(body, iter_vars, cond, want)

    # -- index helper --------------------------------------------------------

    def _inbounds_int(self, name: str, dim: int, scope: Scope) -> "Expr":
        """Return an IntLit within the index domain for variable `name`, dimension `dim`."""
        d = scope.domains.get(name)
        if isinstance(d, MatrixDomain) and dim < len(d.index_domains):
            idx_d = d.index_domains[dim]
            return IntLit(self._randint(idx_d.lo, idx_d.hi))
        # fallback: use a small positive literal to avoid obvious out-of-bounds
        return IntLit(1)

    def _make_index(self, name: str, vtype: Type, scope: Scope, depth: int) -> Expr:
        arr = Var(name, vtype)
        if vtype in (Type.ARRAY_INT, Type.ARRAY_BOOL):
            idx = self._inbounds_int(name, 0, scope)
            elem_type = Type.INT if vtype == Type.ARRAY_INT else Type.BOOL
            return Index(arr, [idx], elem_type)
        else:  # 2-D matrix
            r = self._inbounds_int(name, 0, scope)
            c = self._inbounds_int(name, 1, scope)
            elem_type = Type.INT if vtype == Type.MATRIX_INT else Type.BOOL
            return Index(arr, [r, c], elem_type)

    def _array_len(self, name: str, scope: Scope) -> int:
        """Return the element count of a 1D array variable."""
        d = scope.domains.get(name)
        if isinstance(d, MatrixDomain) and len(d.index_domains) == 1:
            idx = d.index_domains[0]
            return idx.hi - idx.lo + 1
        return self.cfg.max_array_len

    # --- table constraints --------------------------------------------------

    def _col_range(self, name: str, scope: Scope) -> tuple[int, int]:
        """Return (lo, hi) for the domain of scalar int variable `name`."""
        d = scope.domains.get(name)
        if isinstance(d, IntRangeDomain):
            return d.lo, d.hi
        return self.cfg.int_range

    def _gen_table_rows_inline(self, col_ranges: list[tuple[int,int]],
                                n_rows: int) -> ArrayLit:
        """[[v1,v2,...], ...] — 2D matrix literal."""
        rows = []
        for _ in range(n_rows):
            cells = [IntLit(self._randint(lo, hi)) for lo, hi in col_ranges]
            rows.append(ArrayLit(cells, Type.ARRAY_INT))
        return ArrayLit(rows, Type.MATRIX_INT)

    def _gen_table_rows_comprehension(self, col_ranges: list[tuple[int,int]]) -> RowComprehension:
        """Comprehension form for a table matrix.
        Two strategies:
          - Single iterator: [[i, f(i), ...] | i : dom, cond?]
          - Multi-variable:  [[v1, v2, ...] | v1 : dom1, v2 : dom2, ..., cond?]
        """
        arity = len(col_ranges)

        if self._coin(0.5):
            # Multi-variable form: one comprehension variable per column.
            # Cap each domain to at most 4 values to avoid huge tables.
            iter_vars = []
            var_exprs: list[Expr] = []
            for lo, hi in col_ranges:
                capped_hi = min(lo + 3, hi)
                v = self._fresh("t")
                iter_vars.append((v, IntRangeDomain(lo, capped_hi)))
                var_exprs.append(Var(v, Type.INT))

            cond: Optional[Expr] = None
            if self._coin(0.5) and arity >= 2:
                op = self._choice(["<", "<=", "!="])
                cond = BinOp(op, var_exprs[0], var_exprs[1], Type.BOOL)

            return RowComprehension(var_exprs, iter_vars, cond)
        else:
            # Single-iterator form: first column iterates, rest are derived.
            lo, hi = col_ranges[0]
            iter_var = self._fresh("t")
            iter_v = Var(iter_var, Type.INT)

            col_exprs: list[Expr] = [iter_v]
            for clo, chi in col_ranges[1:]:
                offset = self._randint(-(chi - clo) // 2, (chi - clo) // 2)
                if offset == 0:
                    col_exprs.append(iter_v)
                else:
                    col_exprs.append(BinOp("+", iter_v, IntLit(offset), Type.INT))

            cond = None
            if self._coin(0.4):
                mid = (lo + hi) // 2
                op = self._choice(["<=", ">=", "!="])
                cond = BinOp(op, iter_v, IntLit(mid), Type.BOOL)

            return RowComprehension(col_exprs, [(iter_var, IntRangeDomain(lo, hi))], cond)

    def _table_eligible_arrays(self, scope: Scope) -> list[str]:
        """1D int array find vars suitable as the first arg of a table constraint."""
        result = []
        for name in scope.arrays_of(Type.ARRAY_INT):
            d = scope.domains.get(name)
            if (isinstance(d, MatrixDomain)
                    and len(d.index_domains) == 1
                    and isinstance(d.element_domain, IntRangeDomain)):
                result.append(name)
        return result

    def _table_eligible_matrices(self, scope: Scope) -> list[str]:
        """2D int matrix find vars whose rows can be used as first arg of a table constraint."""
        result = []
        for name in scope.arrays_of(Type.MATRIX_INT):
            d = scope.domains.get(name)
            if (isinstance(d, MatrixDomain)
                    and len(d.index_domains) == 2
                    and isinstance(d.element_domain, IntRangeDomain)):
                result.append(name)
        return result

    def gen_table_constraint(self, scope: Scope) -> Optional[TableConstraint]:
        """Generate a table constraint, or None if not possible.

        First arg forms:
          scalars     — [v1, v2, ...] explicit list of scalar int find vars (default)
          array_var   — arr_var  (1D int array find var used directly)
          slice       — M[i, ..] (row-slice of 2D int matrix find var)
        """
        int_vars = scope.scalar_ints()
        arr_eligible = self._table_eligible_arrays(scope)
        mat_eligible = self._table_eligible_matrices(scope)

        # Build weighted choice for the first-argument form
        first_forms: list[str] = []
        first_weights: list[float] = []
        if len(int_vars) >= 2:
            first_forms.append("scalars")
            first_weights.append(3.0)
        if arr_eligible:
            first_forms.append("array_var")
            first_weights.append(1.5)
        if mat_eligible:
            first_forms.append("slice")
            first_weights.append(1.0)

        if not first_forms:
            return None

        first_pick = self._weighted(first_forms, first_weights)

        if first_pick == "array_var":
            name = self._choice(arr_eligible)
            d = scope.domains[name]
            arity = d.index_domains[0].hi - d.index_domains[0].lo + 1
            elem_d = d.element_domain
            col_ranges = [(elem_d.lo, elem_d.hi)] * arity
            vars_expr: Expr = Var(name, Type.ARRAY_INT)
        elif first_pick == "slice":
            name = self._choice(mat_eligible)
            d = scope.domains[name]
            row_idx = self._inbounds_int(name, 0, scope)
            arity = d.index_domains[1].hi - d.index_domains[1].lo + 1
            elem_d = d.element_domain
            col_ranges = [(elem_d.lo, elem_d.hi)] * arity
            vars_expr = Slice(Var(name, Type.MATRIX_INT), row_idx, None, Type.ARRAY_INT)
        else:  # scalars
            # If a 2D given is available, sometimes bias arity to match its column count
            # so the param_2d table-expr form can fire.
            param2d_candidates = [(n, a) for n, a in scope.table2d_givens.items()
                                   if 2 <= a <= len(int_vars)]
            if param2d_candidates and self._coin(0.4):
                _, arity = self._choice(param2d_candidates)
            else:
                arity = min(self._randint(2, self.cfg.max_table_arity), len(int_vars))
            arity = min(arity, len(int_vars))
            chosen = self.rng.sample(int_vars, arity)
            col_ranges = [self._col_range(n, scope) for n in chosen]
            vars_expr = ArrayLit([Var(n, Type.INT) for n in chosen], Type.ARRAY_INT)

        n_rows = self._randint(1, self.cfg.max_table_rows)

        # Pick table-expression form — weights start at zero for forms that require
        # a matching given/letting and are enabled only when one exists.
        table_weights: dict[str, float] = {
            "inline":        2.0,
            "comprehension": 1.0,
            "reference":     0.0,
            "3d_slice":      0.0,
            "param_2d":      0.0,
            "cat":           1.0,
            "flatten":       0.0,
        }

        matching_lets = [n for n, a in scope.table_lettings.items() if a == arity]
        if matching_lets:
            table_weights["reference"] = 1.5

        matching_3d = [n for n, a in scope.table3d_givens.items() if a == arity]
        if matching_3d:
            table_weights["3d_slice"] = 1.5
            table_weights["flatten"]  = 1.0

        matching_2d = [n for n, a in scope.table2d_givens.items() if a == arity]
        if matching_2d:
            table_weights["param_2d"] = 1.5

        form = self._weighted(list(table_weights), list(table_weights.values()))

        if form == "comprehension":
            table_expr = self._gen_table_rows_comprehension(col_ranges)
        elif form == "reference" and matching_lets:
            table_expr = Var(self._choice(matching_lets), Type.MATRIX_INT)
        elif form == "3d_slice" and matching_3d:
            name = self._choice(matching_3d)
            d = scope.domains[name]
            # Only fix dim 0 or 1 — fixing dim 2 (the arity/column dim) gives wrong column count
            fixed_dim = self._choice([0, 1])
            idx_d = d.index_domains[fixed_dim]
            fixed_idx = IntLit(self._randint(idx_d.lo, idx_d.hi))
            table_expr = Slice3D(Var(name, Type.MATRIX3D_INT), fixed_dim, fixed_idx)
        elif form == "param_2d" and matching_2d:
            table_expr = Var(self._choice(matching_2d), Type.MATRIX_INT)
        elif form == "flatten" and matching_3d:
            name = self._choice(matching_3d)
            # flatten(1, m3d): [d1,d2,d3] → [d1*d2, d3] (2D, arity cols)
            table_expr = FuncCall("flatten", [IntLit(1), Var(name, Type.MATRIX3D_INT)], Type.MATRIX_INT)
        elif form == "cat":
            m1 = self._gen_table_rows_inline(col_ranges, max(1, n_rows // 2))
            m2 = self._gen_table_rows_inline(col_ranges, max(1, n_rows - n_rows // 2))
            table_expr = FuncCall("cat", [m1, m2], Type.MATRIX_INT)
        else:
            table_expr = self._gen_table_rows_inline(col_ranges, n_rows)

        return TableConstraint(vars_expr, table_expr, short=False)

    def _gen_table_letting(self, arity: int, col_ranges: list[tuple[int,int]],
                            name: str,
                            scope: Optional["Scope"] = None) -> LettingDecl:
        """Generate a letting that holds a table matrix.

        Forms chosen randomly:
          inline       — [[v1,v2,...], ...] literal
          comprehension — [[t, f(t)] | t : domain, cond?]  or multi-var variant
          slice3d      — T3D[k, .., ..]  (only when scope has matching 3D givens)
        """
        letting_forms = ["inline", "comprehension"]
        letting_weights = [2.0, 1.0]

        # Add slice3d form if a matching 3D given is in scope
        matching_3d: list[str] = []
        if scope is not None:
            matching_3d = [n for n, a in scope.table3d_givens.items() if a == arity]
            if matching_3d:
                letting_forms.append("slice3d")
                letting_weights.append(1.0)

        lform = self._weighted(letting_forms, letting_weights)

        if lform == "comprehension":
            return LettingDecl(name, self._gen_table_rows_comprehension(col_ranges))

        if lform == "slice3d" and matching_3d:
            src = self._choice(matching_3d)
            d = scope.domains[src]  # type: ignore[union-attr]
            fixed_dim = self._choice([0, 1])
            idx_d = d.index_domains[fixed_dim]
            fixed_idx = IntLit(self._randint(idx_d.lo, idx_d.hi))
            return LettingDecl(name, Slice3D(Var(src, Type.MATRIX3D_INT), fixed_dim, fixed_idx))

        # Default: inline matrix literal
        n_rows = self._randint(2, self.cfg.max_table_rows)
        return LettingDecl(name, self._gen_table_rows_inline(col_ranges, n_rows))

    # --- constraint generation (top-level booleans) ------------------------

    def gen_constraint(self, scope: Scope) -> Expr:
        depth = self.cfg.max_depth
        return self._gen_bool(depth, scope)

    # --- param value generation --------------------------------------------

    def gen_param_value(self, d: Domain) -> Expr:
        """Generate a concrete value (no variables) for a given domain."""
        if isinstance(d, IntRangeDomain):
            return IntLit(self._randint(d.lo, d.hi))
        if isinstance(d, CompositeIntDomain):
            part = self._choice(d.parts)
            return IntLit(self._randint(part.lo, part.hi))
        if isinstance(d, BoolDomain):
            return BoolLit(self._coin())
        if isinstance(d, MatrixDomain):
            # Recursively generate: peel the outermost index dimension each time.
            outer_d = d.index_domains[0]
            n = outer_d.hi - outer_d.lo + 1
            if len(d.index_domains) == 1:
                # Base: 1-D array of element values
                elems = [self.gen_param_value(d.element_domain) for _ in range(n)]
                elem_type = Type.ARRAY_INT if isinstance(d.element_domain, IntRangeDomain) else Type.ARRAY_BOOL
                return ArrayLit(elems, elem_type)
            else:
                # Peel outer dimension → array of sub-matrices
                inner_domain = MatrixDomain(d.index_domains[1:], d.element_domain)
                outer_elems = [self.gen_param_value(inner_domain) for _ in range(n)]
                return ArrayLit(outer_elems, Type.MATRIX_INT)
        raise TypeError(f"Unknown domain: {d!r}")

    # --- top-level model generation ----------------------------------------

    def generate(self) -> tuple[Model, dict[str, Expr]]:
        cfg = self.cfg
        self._name_counter = 0

        # 1. Given parameters
        n_given = self._randint(0, cfg.max_given_params)
        givens: list[GivenDecl] = []
        for _ in range(n_given):
            name = self._fresh("p")
            d = self.gen_given_domain()
            givens.append(GivenDecl(name, d))

        # 2. Param values
        param_vals: dict[str, Expr] = {g.name: self.gen_param_value(g.domain) for g in givens}

        # 3. Find variables
        n_find = self._randint(1, cfg.max_find_vars)
        finds: list[FindDecl] = []
        for _ in range(n_find):
            name = self._fresh("x")
            d = self.gen_find_domain()
            finds.append(FindDecl(name, d))

        # 3b. Optionally create domain lettings for find variables with scalar domains.
        # Multiple find vars that happen to share the same domain reuse the same letting,
        # which tests SR's domain-alias resolution with shared references.
        # Additionally, sometimes create a standalone letting with a DomainBinOp domain.
        domain_lettings: list[DomainLettingDecl] = []
        if cfg.feat_domain_letting > 0 and self._coin(cfg.feat_domain_letting):
            domain_repr_to_name: dict[str, str] = {}
            for f in finds:
                if not isinstance(f.domain, (IntRangeDomain, BoolDomain, CompositeIntDomain)):
                    continue
                if not self._coin(0.6):
                    continue
                key = repr(f.domain)
                if key not in domain_repr_to_name:
                    dl_name = self._fresh("D")
                    domain_repr_to_name[key] = dl_name
                    domain_lettings.append(DomainLettingDecl(dl_name, f.domain))
                f.letting_name = domain_repr_to_name[key]
        # Standalone domain-binop letting (not tied to any find variable — used only
        # in in-domain expressions generated later, or just to exercise SR parsing).
        if cfg.feat_domain_binop > 0 and self._coin(cfg.feat_domain_binop * 0.5):
            dl_name = self._fresh("D")
            domain_lettings.append(DomainLettingDecl(dl_name, self.gen_domain_binop()))

        # 4. Build scope from find variables (givens are ground in param file)
        scope = Scope()
        for f in finds:
            t = _domain_type(f.domain)
            scope.vars[f.name] = t
            scope.domains[f.name] = f.domain

        # Also add given scalars to scope (they have concrete values but model
        # can still reference them)
        for g in givens:
            t = _domain_type(g.domain)
            scope.vars[g.name] = t
            scope.domains[g.name] = g.domain
            # Track 2D int matrix givens for direct use as table expressions
            if (isinstance(g.domain, MatrixDomain)
                    and len(g.domain.index_domains) == 2
                    and isinstance(g.domain.element_domain, IntRangeDomain)):
                arity = g.domain.index_domains[1].hi - g.domain.index_domains[1].lo + 1
                scope.table2d_givens[g.name] = arity

        int_find_vars = [f.name for f in finds if _domain_type(f.domain) == Type.INT]

        # 4b. Optionally add 0–2 3D-matrix givens (for 3d_slice, flatten, and slice3d-letting forms).
        # These are added before lettings so lettings can reference them.
        extra_givens: list[GivenDecl] = []
        if len(int_find_vars) >= 2 and self._coin(0.5):
            for _ in range(self._randint(1, 2)):
                arity = min(self._randint(2, cfg.max_table_arity), len(int_find_vars))
                d3 = self.gen_3d_matrix_domain(arity)
                gname = self._fresh("T3D")
                extra_givens.append(GivenDecl(gname, d3))
                param_vals[gname] = self.gen_param_value(d3)
                scope.vars[gname] = Type.MATRIX3D_INT
                scope.domains[gname] = d3
                scope.table3d_givens[gname] = arity
        givens = givens + extra_givens

        # 4c. Pre-generate table lettings (inline, comprehension, or slice of a 3D given).
        lettings: list[LettingDecl] = []
        if len(int_find_vars) >= 2 and self._coin(0.5):
            for _ in range(self._randint(1, 2)):
                arity = min(self._randint(2, cfg.max_table_arity), len(int_find_vars))
                chosen = self.rng.sample(int_find_vars, arity)
                col_ranges = []
                for vname in chosen:
                    d = scope.domains.get(vname)
                    col_ranges.append((d.lo, d.hi) if isinstance(d, IntRangeDomain) else cfg.int_range)
                tname = self._fresh("TBL")
                lettings.append(self._gen_table_letting(arity, col_ranges, tname, scope=scope))
                scope.table_lettings[tname] = arity

        # 4d. where clauses on given scalar int parameters
        where_clauses: list[Expr] = []
        if cfg.feat_where > 0 and self._coin(cfg.feat_where):
            given_int_names = [g.name for g in givens
                               if isinstance(g.domain, (IntRangeDomain, CompositeIntDomain))]
            if given_int_names:
                # Simple constraints: p > constant, or p1 <= p2
                n_where = self._randint(1, 2)
                for _ in range(n_where):
                    if len(given_int_names) >= 2 and self._coin(0.4):
                        a, b = self.rng.sample(given_int_names, 2)
                        op = self._choice(["<=", "<", "!=", "="])
                        where_clauses.append(BinOp(op, Var(a, Type.INT), Var(b, Type.INT), Type.BOOL))
                    else:
                        name = self._choice(given_int_names)
                        d = scope.domains[name]
                        if isinstance(d, IntRangeDomain):
                            lo_bound = d.lo - 1
                        elif isinstance(d, CompositeIntDomain):
                            lo_bound = d.parts[0].lo - 1
                        else:
                            lo_bound = -100
                        op = self._choice([">=", ">", "<=", "<"])
                        where_clauses.append(BinOp(op, Var(name, Type.INT), IntLit(lo_bound), Type.BOOL))

        # 5. Constraints
        n_constraints = self._randint(1, cfg.max_constraints)
        constraints: list[Expr] = []
        for _ in range(n_constraints):
            constraints.append(self.gen_constraint(scope))

        # 6. Objective (optional)
        objective: Optional[Objective] = None
        if self._coin(cfg.feat_objective):
            direction = self._choice(["minimising", "maximising"])
            if cfg.force_simple_objective:
                # Sum of scalar int find-vars only — always well-defined and
                # evaluable in Python from the solution file (used for diff testing).
                scalar_ints = [f.name for f in finds
                               if isinstance(f.domain, (IntRangeDomain, CompositeIntDomain))]
                if scalar_ints:
                    names = self.rng.sample(scalar_ints, min(3, len(scalar_ints)))
                    obj_expr: Expr = Var(names[0], Type.INT)
                    for n in names[1:]:
                        obj_expr = BinOp("+", obj_expr, Var(n, Type.INT), Type.INT)
                    objective = Objective(direction, obj_expr)
                # else: no scalar finds → no objective (skip)
            else:
                obj_expr = self._gen_int(cfg.max_depth - 1, scope)
                objective = Objective(direction, obj_expr)

        # 7. branching on [...] — list scalar/array find-vars for solver search order
        branching_on: list[Expr] = []
        if cfg.feat_branching_on > 0 and self._coin(cfg.feat_branching_on):
            # Pick 1–3 find variables (scalar ints and/or arrays) for the branching list
            branch_candidates = [
                Var(f.name, _domain_type(f.domain)) for f in finds
                if not isinstance(f.domain, BoolDomain)
            ]
            if branch_candidates:
                k = self._randint(1, min(3, len(branch_candidates)))
                branching_on = self.rng.sample(branch_candidates, k)

        m = Model(
            givens=givens,
            domain_lettings=domain_lettings,
            lettings=lettings,
            finds=finds,
            where=where_clauses,
            constraints=constraints,
            objective=objective,
            branching_on=branching_on,
        )
        return m, param_vals


def _domain_type(d: Domain) -> Type:
    if isinstance(d, IntRangeDomain):
        return Type.INT
    if isinstance(d, CompositeIntDomain):
        return Type.INT
    if isinstance(d, BoolDomain):
        return Type.BOOL
    if isinstance(d, MatrixDomain):
        return d.vtype()
    raise TypeError(f"Unknown domain: {d!r}")


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def generate_model(
    seed: Optional[int] = None,
    cfg: Optional[GenConfig] = None,
) -> tuple[Model, dict]:
    """Generate a random (model, param_values) pair."""
    rng = random.Random(seed)
    cfg = cfg or GenConfig()
    gen = Generator(rng, cfg)
    return gen.generate()
