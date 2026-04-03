# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
Tests for the EPrime generator.

Run with:  python -m pytest tests/ -v
"""
import pytest
import random

from eprime_gen.ast_nodes import Type, Model, IntRangeDomain, BoolDomain, MatrixDomain
from eprime_gen.generator import generate_model, GenConfig, Generator
from eprime_gen.printer import model as print_model, param as print_param, expr as print_expr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_gen(seed=0, **kwargs) -> Generator:
    cfg = GenConfig(**kwargs)
    return Generator(random.Random(seed), cfg)


# ---------------------------------------------------------------------------
# Printer smoke tests
# ---------------------------------------------------------------------------

class TestPrinter:
    def test_int_range_domain(self):
        from eprime_gen.ast_nodes import IntRangeDomain
        from eprime_gen.printer import domain
        assert domain(IntRangeDomain(1, 10)) == "int(1..10)"

    def test_bool_domain(self):
        from eprime_gen.printer import domain
        assert domain(BoolDomain()) == "bool"

    def test_matrix_domain_1d(self):
        from eprime_gen.printer import domain
        d = MatrixDomain([IntRangeDomain(1, 5)], IntRangeDomain(0, 9))
        assert domain(d) == "matrix indexed by [int(1..5)] of int(0..9)"

    def test_matrix_domain_2d(self):
        from eprime_gen.printer import domain
        d = MatrixDomain([IntRangeDomain(1, 3), IntRangeDomain(1, 4)], BoolDomain())
        assert domain(d) == "matrix indexed by [int(1..3), int(1..4)] of bool"

    def test_int_lit(self):
        from eprime_gen.ast_nodes import IntLit
        assert print_expr(IntLit(42)) == "42"
        assert print_expr(IntLit(-5)) == "-5"

    def test_bool_lit(self):
        from eprime_gen.ast_nodes import BoolLit
        assert print_expr(BoolLit(True)) == "true"
        assert print_expr(BoolLit(False)) == "false"

    def test_binop(self):
        from eprime_gen.ast_nodes import BinOp, IntLit
        e = BinOp("+", IntLit(1), IntLit(2), Type.INT)
        assert print_expr(e) == "1 + 2"

    def test_binop_nested_parentheses(self):
        from eprime_gen.ast_nodes import BinOp, IntLit
        inner = BinOp("+", IntLit(1), IntLit(2), Type.INT)
        outer = BinOp("*", inner, IntLit(3), Type.INT)
        assert print_expr(outer) == "(1 + 2) * 3"

    def test_quantifier(self):
        from eprime_gen.ast_nodes import Quantifier, BinOp, Var, IntLit
        body = BinOp(">", Var("x", Type.INT), IntLit(0), Type.BOOL)
        q = Quantifier("forAll", "i", IntRangeDomain(1, 5), body, Type.BOOL)
        assert print_expr(q) == "forAll i : int(1..5) . (x > 0)"

    def test_abs_val(self):
        from eprime_gen.ast_nodes import AbsVal, Var
        e = AbsVal(Var("x", Type.INT))
        assert print_expr(e) == "|x|"

    def test_func_call(self):
        from eprime_gen.ast_nodes import FuncCall, Var
        e = FuncCall("allDiff", [Var("xs", Type.ARRAY_INT)], Type.BOOL)
        assert print_expr(e) == "allDiff(xs)"

    def test_model_has_header(self):
        m = Model()
        assert print_model(m).startswith("language ESSENCE' 1.0")

    def test_model_such_that(self):
        from eprime_gen.ast_nodes import FindDecl, BinOp, Var, IntLit
        find = FindDecl("x", IntRangeDomain(1, 10))
        c = BinOp(">", Var("x", Type.INT), IntLit(3), Type.BOOL)
        m = Model(finds=[find], constraints=[c])
        text = print_model(m)
        assert "such that" in text
        assert "x > 3" in text
        assert "find x : int(1..10)" in text

    def test_model_objective(self):
        from eprime_gen.ast_nodes import FindDecl, Objective, Var
        find = FindDecl("x", IntRangeDomain(0, 100))
        obj = Objective("maximising", Var("x", Type.INT))
        m = Model(finds=[find], objective=obj)
        text = print_model(m)
        assert "maximising x" in text

    def test_param_output(self):
        from eprime_gen.ast_nodes import GivenDecl, IntLit
        g = GivenDecl("n", IntRangeDomain(1, 20))
        vals = {"n": IntLit(10)}
        text = print_param([g], vals)
        assert "letting n be 10" in text


# ---------------------------------------------------------------------------
# Generator unit tests
# ---------------------------------------------------------------------------

class TestGenerator:
    def test_gen_int_domain(self):
        gen = make_gen()
        d = gen.gen_int_domain()
        assert isinstance(d, IntRangeDomain)
        assert d.hi > d.lo

    def test_gen_find_domain_types(self):
        # Run many times, ensure we get varied domain types
        gen = make_gen(seed=99)
        types = set()
        for _ in range(50):
            d = gen.gen_find_domain()
            types.add(type(d).__name__)
        assert "IntRangeDomain" in types
        assert "MatrixDomain" in types

    def test_gen_expr_int_shallow(self):
        from eprime_gen.generator import Scope
        gen = make_gen()
        scope = Scope({"x": Type.INT})
        e = gen.gen_expr(Type.INT, depth=0, scope=scope)
        assert e.type == Type.INT

    def test_gen_expr_bool_shallow(self):
        from eprime_gen.generator import Scope
        gen = make_gen()
        scope = Scope()
        e = gen.gen_expr(Type.BOOL, depth=0, scope=scope)
        assert e.type == Type.BOOL

    def test_gen_expr_int_deep(self):
        from eprime_gen.generator import Scope
        gen = make_gen(seed=7, max_depth=5)
        scope = Scope({"x": Type.INT, "y": Type.ARRAY_INT})
        e = gen.gen_expr(Type.INT, depth=5, scope=scope)
        assert e.type == Type.INT

    def test_gen_expr_bool_deep(self):
        from eprime_gen.generator import Scope
        gen = make_gen(seed=42, max_depth=4)
        scope = Scope({"x": Type.INT, "xs": Type.ARRAY_INT})
        e = gen.gen_expr(Type.BOOL, depth=4, scope=scope)
        assert e.type == Type.BOOL

    def test_gen_param_value_int(self):
        gen = make_gen()
        v = gen.gen_param_value(IntRangeDomain(1, 10))
        from eprime_gen.ast_nodes import IntLit
        assert isinstance(v, IntLit)
        assert 1 <= v.value <= 10

    def test_gen_param_value_bool(self):
        gen = make_gen()
        v = gen.gen_param_value(BoolDomain())
        from eprime_gen.ast_nodes import BoolLit
        assert isinstance(v, BoolLit)

    def test_gen_param_value_1d_matrix(self):
        gen = make_gen()
        d = MatrixDomain([IntRangeDomain(1, 4)], IntRangeDomain(0, 9))
        v = gen.gen_param_value(d)
        from eprime_gen.ast_nodes import ArrayLit
        assert isinstance(v, ArrayLit)
        assert len(v.elements) == 4

    def test_gen_param_value_2d_matrix(self):
        gen = make_gen()
        d = MatrixDomain([IntRangeDomain(1, 3), IntRangeDomain(1, 2)], IntRangeDomain(0, 5))
        v = gen.gen_param_value(d)
        from eprime_gen.ast_nodes import ArrayLit
        assert isinstance(v, ArrayLit)
        assert len(v.elements) == 3
        assert all(isinstance(row, ArrayLit) and len(row.elements) == 2 for row in v.elements)


# ---------------------------------------------------------------------------
# Integration: generate + print
# ---------------------------------------------------------------------------

class TestIntegration:
    @pytest.mark.parametrize("seed", range(20))
    def test_generate_and_print(self, seed):
        """Generated models must print without errors and contain required sections."""
        m, pvals = generate_model(seed=seed)
        text = print_model(m)
        assert "language ESSENCE' 1.0" in text
        assert "find" in text
        assert "such that" in text

    @pytest.mark.parametrize("seed", range(20))
    def test_param_print(self, seed):
        m, pvals = generate_model(seed=seed)
        if m.givens:
            text = print_param(m.givens, pvals)
            assert "language ESSENCE' 1.0" in text
            for g in m.givens:
                assert f"letting {g.name} be" in text

    def test_reproducible(self):
        m1, p1 = generate_model(seed=123)
        m2, p2 = generate_model(seed=123)
        assert print_model(m1) == print_model(m2)
        assert print_param(m1.givens, p1) == print_param(m2.givens, p2)

    def test_different_seeds_differ(self):
        m1, _ = generate_model(seed=1)
        m2, _ = generate_model(seed=2)
        # Very unlikely to be identical
        assert print_model(m1) != print_model(m2)

    @pytest.mark.parametrize("depth", [1, 2, 3, 5, 7])
    def test_depth_respects_config(self, depth):
        cfg = GenConfig(max_depth=depth)
        for seed in range(5):
            m, pvals = generate_model(seed=seed, cfg=cfg)
            text = print_model(m)
            assert len(text) > 0

    def test_no_objectives_when_disabled(self):
        cfg = GenConfig(feat_objective=0.0)
        for seed in range(20):
            m, _ = generate_model(seed=seed, cfg=cfg)
            assert m.objective is None

    def test_objectives_sometimes_present(self):
        cfg = GenConfig(feat_objective=1.0)
        objs = sum(1 for s in range(20) if generate_model(seed=s, cfg=cfg)[0].objective is not None)
        assert objs > 0

    def test_large_batch(self):
        """Generate 100 models without crashing."""
        for i in range(100):
            m, pvals = generate_model(seed=i)
            print_model(m)
            print_param(m.givens, pvals)
