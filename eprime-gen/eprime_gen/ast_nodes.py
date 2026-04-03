# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
AST node definitions for Essence Prime models.
Every node has a `type` attribute indicating its value type (INT, BOOL, ARRAY).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Type(Enum):
    INT = auto()
    BOOL = auto()
    ARRAY_INT = auto()    # 1-D array of int
    ARRAY_BOOL = auto()   # 1-D array of bool
    MATRIX_INT = auto()   # 2-D matrix of int
    MATRIX_BOOL = auto()  # 2-D matrix of bool
    MATRIX3D_INT = auto() # 3-D matrix of int (used as table sources)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

@dataclass
class IntRangeDomain:
    lo: int
    hi: int

    def vtype(self) -> Type:
        return Type.INT

    def __repr__(self) -> str:
        return f"int({self.lo}..{self.hi})"


@dataclass
class BoolDomain:
    def vtype(self) -> Type:
        return Type.BOOL

    def __repr__(self) -> str:
        return "bool"


@dataclass
class MatrixDomain:
    """matrix indexed by [index_domains] of element_domain"""
    index_domains: list  # list of IntRangeDomain
    element_domain: IntRangeDomain | BoolDomain

    def vtype(self) -> Type:
        if len(self.index_domains) == 1:
            return Type.ARRAY_INT if isinstance(self.element_domain, IntRangeDomain) else Type.ARRAY_BOOL
        if len(self.index_domains) == 2:
            return Type.MATRIX_INT if isinstance(self.element_domain, IntRangeDomain) else Type.MATRIX_BOOL
        return Type.MATRIX3D_INT  # 3-D (int only; used for table sources)

    def __repr__(self) -> str:
        idx = ", ".join(repr(d) for d in self.index_domains)
        return f"matrix indexed by [{idx}] of {self.element_domain!r}"


@dataclass
class CompositeIntDomain:
    """int(lo1..hi1, v, lo2..hi2, ...) — non-contiguous integer domain"""
    parts: list[IntRangeDomain]   # lo==hi means single value

    def vtype(self) -> Type:
        return Type.INT

    def __repr__(self) -> str:
        bits = []
        for p in self.parts:
            bits.append(str(p.lo) if p.lo == p.hi else f"{p.lo}..{p.hi}")
        return f"int({', '.join(bits)})"


@dataclass
class IndexOfDomain:
    """indexOf(X) — the index domain of the outer dimension of 1-D array X.
    Valid as the domain of a quantifier variable.
    e.g.  forAll j : indexOf(arr) . body"""
    array: "Expr"   # ARRAY_INT or ARRAY_BOOL


@dataclass
class DomainBinOp:
    """D1 union D2 / D1 intersect D2 / D1 - D2 — binary domain expression.
    Used in  'letting D be domain D1 union D2'  and  'x in (D1 union D2)'."""
    op: str   # "union", "intersect", "-"
    left: "IntRangeDomain | CompositeIntDomain | DomainBinOp"
    right: "IntRangeDomain | CompositeIntDomain | DomainBinOp"


Domain = IntRangeDomain | BoolDomain | MatrixDomain | CompositeIntDomain


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass
class IntLit:
    value: int
    type: Type = field(init=False, default=Type.INT)


@dataclass
class BoolLit:
    value: bool
    type: Type = field(init=False, default=Type.BOOL)


@dataclass
class Var:
    name: str
    type: Type


@dataclass
class BinOp:
    op: str   # +, -, *, /, %, /\, \/, =>, <=>, =, !=, <, >, <=, >=
    left: "Expr"
    right: "Expr"
    type: Type


@dataclass
class UnaryOp:
    op: str   # -, !
    operand: "Expr"
    type: Type


@dataclass
class AbsVal:
    """| expr |  — absolute value, produces INT"""
    expr: "Expr"
    type: Type = field(init=False, default=Type.INT)


@dataclass
class Index:
    """matrix[i] or matrix[i,j] — produces scalar element"""
    array: "Expr"
    indices: list["Expr"]
    type: Type   # element type of the array


@dataclass
class Slice:
    """matrix[i,..] or matrix[..,j] — produces a 1-D array"""
    matrix: "Expr"
    row: "Expr | None"      # None → wildcard '..'
    col: "Expr | None"      # None → wildcard '..'
    type: Type   # ARRAY_INT or ARRAY_BOOL


@dataclass
class ArrayLit:
    """[ e1, e2, ... ] — literal array"""
    elements: list["Expr"]
    type: Type   # ARRAY_INT or ARRAY_BOOL


@dataclass
class Comprehension:
    """[ expr | var : domain, (cond)? ] — array comprehension"""
    expr: "Expr"
    var: str
    domain: IntRangeDomain
    condition: Optional["Expr"]   # must be BOOL if present
    type: Type   # ARRAY_INT or ARRAY_BOOL


@dataclass
class Quantifier:
    """forAll / exists / sum  var : domain . body"""
    kind: str          # "forAll", "exists", "sum"
    var: str
    domain: "IntRangeDomain | IndexOfDomain"
    body: "Expr"
    type: Type         # BOOL for forAll/exists, INT for sum


@dataclass
class FuncCall:
    """Built-in function calls: allDiff, min, max, count, toInt, ..."""
    name: str
    args: list["Expr"]
    type: Type


@dataclass
class IfExpr:
    """if(cond, then_expr, else_expr)"""
    cond: "Expr"
    then_expr: "Expr"
    else_expr: "Expr"
    type: Type


@dataclass
class Slice3D:
    """matrix3d[i,..,..] / matrix3d[..,i,..] / matrix3d[..,..,i]
    Fix one dimension of a 3-D matrix to produce a 2-D slice."""
    matrix: "Expr"
    fixed_dim: int    # 0, 1, or 2
    fixed_idx: "Expr"
    type: Type = field(init=False, default=Type.MATRIX_INT)


@dataclass
class OldTuple:
    """<v1, v2, ...> — angle-bracket tuple syntax used inside table constraints"""
    values: list[int]
    type: Type = field(init=False, default=Type.ARRAY_INT)


@dataclass
class MultiVarQuantifier:
    """forAll i, j : domain . body  — two or more variables over the same domain"""
    kind: str          # "forAll", "exists"
    vars: list[str]
    domain: "IntRangeDomain | IndexOfDomain"
    body: "Expr"
    type: Type         # BOOL for forAll/exists


@dataclass
class RowComprehension:
    """[[col_expr, ...] | var : domain, ... (cond)?] — comprehension yielding rows for a table.
    iter_vars is a list of (name, domain) pairs (one or more comprehension variables)."""
    col_exprs: list["Expr"]              # expressions for each column
    iter_vars: list                      # list of (str, Domain) tuples
    condition: Optional["Expr"]
    type: Type = field(init=False, default=Type.MATRIX_INT)


@dataclass
class TableConstraint:
    """table(vars_expr, table_expr)"""
    vars_expr: "Expr"         # 1D matrix expression: ArrayLit of scalars, array var, slice, etc.
    table_expr: "Expr"        # 2D matrix expression (inline, reference, comprehension, etc.)
    short: bool = False       # True → emit 'tableshort' instead of 'table'
    type: Type = field(init=False, default=Type.BOOL)


@dataclass
class MultiVarComprehension:
    """[expr | v1 : d1, v2 : d2, ..., (cond)?]
    Multi-variable array comprehension: iterates over the Cartesian product of the
    given domains, producing one element per combination."""
    expr: "Expr"
    iter_vars: list            # list of (str, IntRangeDomain) pairs
    condition: Optional["Expr"]
    type: Type                 # ARRAY_INT or ARRAY_BOOL


@dataclass
class InDomain:
    """expr in domain — membership test, produces BOOL.
    Covers both contiguous ranges (int(lo..hi)) and non-contiguous domains
    (int(a..b, c, d..e)).  Bool membership (b in bool) is also valid EPrime
    but is not generated here."""
    expr: "Expr"               # must be Type.INT
    domain: "IntRangeDomain | CompositeIntDomain | DomainBinOp"
    type: Type = field(init=False, default=Type.BOOL)


Expr = (IntLit | BoolLit | Var | BinOp | UnaryOp | AbsVal
        | Index | Slice | Slice3D | ArrayLit | Comprehension
        | Quantifier | MultiVarQuantifier | FuncCall | IfExpr
        | OldTuple | RowComprehension | TableConstraint
        | MultiVarComprehension | InDomain)


# ---------------------------------------------------------------------------
# Top-level declarations
# ---------------------------------------------------------------------------

@dataclass
class GivenDecl:
    name: str
    domain: Domain


@dataclass
class DomainLettingDecl:
    """letting name be domain D  — assigns an alias to a domain so find
    declarations can reference it by name instead of spelling out the full domain."""
    name: str
    domain: "IntRangeDomain | BoolDomain | CompositeIntDomain | DomainBinOp"


@dataclass
class LettingDecl:
    """letting name be expr  — value letting (scalar expressions only)"""
    name: str
    expr: Expr


@dataclass
class FindDecl:
    name: str
    domain: Domain
    letting_name: Optional[str] = None  # if set, printer uses this alias instead of the full domain


@dataclass
class Objective:
    direction: str   # "minimising" | "maximising"
    expr: Expr


@dataclass
class Model:
    givens: list[GivenDecl] = field(default_factory=list)
    domain_lettings: list[DomainLettingDecl] = field(default_factory=list)
    lettings: list[LettingDecl] = field(default_factory=list)
    finds: list[FindDecl] = field(default_factory=list)
    where: list[Expr] = field(default_factory=list)   # each must be BOOL
    constraints: list[Expr] = field(default_factory=list)  # each must be BOOL
    objective: Optional[Objective] = None
    branching_on: list[Expr] = field(default_factory=list)  # variables for solver search order
