# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
Convert AST nodes to Essence Prime source text.
"""
from __future__ import annotations
from .ast_nodes import *


def _domain_needs_parens(d) -> bool:
    """DomainBinOp sub-expressions need parens when nested inside another DomainBinOp."""
    return isinstance(d, DomainBinOp)


def _needs_parens(node: Expr) -> bool:
    return isinstance(node, (BinOp, UnaryOp, Quantifier, MultiVarQuantifier, InDomain))


def _p(node: Expr) -> str:
    s = expr(node)
    if _needs_parens(node):
        return f"({s})"
    return s


def domain(d) -> str:
    if isinstance(d, IntRangeDomain):
        return f"int({d.lo}..{d.hi})"
    if isinstance(d, BoolDomain):
        return "bool"
    if isinstance(d, MatrixDomain):
        idx = ", ".join(domain(i) for i in d.index_domains)
        return f"matrix indexed by [{idx}] of {domain(d.element_domain)}"
    if isinstance(d, CompositeIntDomain):
        bits = []
        for p in d.parts:
            bits.append(str(p.lo) if p.lo == p.hi else f"{p.lo}..{p.hi}")
        return f"int({', '.join(bits)})"
    if isinstance(d, IndexOfDomain):
        return f"indexOf({expr(d.array)})"
    if isinstance(d, DomainBinOp):
        def _dp(sub) -> str:
            s = domain(sub)
            return f"({s})" if _domain_needs_parens(sub) else s
        return f"{_dp(d.left)} {d.op} {_dp(d.right)}"
    raise TypeError(f"Unknown domain: {d!r}")


def expr(e: Expr) -> str:
    if isinstance(e, IntLit):
        return str(e.value)
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, Var):
        return e.name
    if isinstance(e, BinOp):
        return f"{_p(e.left)} {e.op} {_p(e.right)}"
    if isinstance(e, UnaryOp):
        if e.op == "!":
            return f"!{_p(e.operand)}"
        return f"{e.op}{_p(e.operand)}"
    if isinstance(e, AbsVal):
        return f"|{expr(e.expr)}|"
    if isinstance(e, Index):
        idx = ", ".join(expr(i) for i in e.indices)
        return f"{_p(e.array)}[{idx}]"
    if isinstance(e, Slice):
        row_s = ".." if e.row is None else expr(e.row)
        col_s = ".." if e.col is None else expr(e.col)
        return f"{_p(e.matrix)}[{row_s},{col_s}]"
    if isinstance(e, ArrayLit):
        elems = ", ".join(expr(el) for el in e.elements)
        return f"[{elems}]"
    if isinstance(e, Comprehension):
        cond_part = ""
        if e.condition is not None:
            cond_part = f", {expr(e.condition)}"
        return f"[{expr(e.expr)} | {e.var} : {domain(e.domain)}{cond_part}]"
    if isinstance(e, Quantifier):
        return f"{e.kind} {e.var} : {domain(e.domain)} . {_p(e.body)}"
    if isinstance(e, MultiVarQuantifier):
        vars_s = ", ".join(e.vars)
        return f"{e.kind} {vars_s} : {domain(e.domain)} . {_p(e.body)}"
    if isinstance(e, FuncCall):
        args_s = ", ".join(expr(a) for a in e.args)
        return f"{e.name}({args_s})"
    if isinstance(e, Slice3D):
        parts = ["..", "..", ".."]
        parts[e.fixed_dim] = expr(e.fixed_idx)
        return f"{_p(e.matrix)}[{', '.join(parts)}]"
    if isinstance(e, RowComprehension):
        cols = ", ".join(expr(c) for c in e.col_exprs)
        vars_part = ", ".join(f"{v} : {domain(d)}" for v, d in e.iter_vars)
        cond_part = f", {expr(e.condition)}" if e.condition is not None else ""
        return f"[[{cols}] | {vars_part}{cond_part}]"
    if isinstance(e, TableConstraint):
        keyword = "tableshort" if e.short else "table"
        return f"{keyword}({expr(e.vars_expr)}, {expr(e.table_expr)})"
    if isinstance(e, MultiVarComprehension):
        vars_part = ", ".join(f"{v} : {domain(d)}" for v, d in e.iter_vars)
        cond_part = f", {expr(e.condition)}" if e.condition is not None else ""
        return f"[{expr(e.expr)} | {vars_part}{cond_part}]"
    if isinstance(e, InDomain):
        d_str = domain(e.domain)
        if isinstance(e.domain, DomainBinOp):
            d_str = f"({d_str})"
        return f"{_p(e.expr)} in {d_str}"
    raise TypeError(f"Unknown expr type: {e!r}")


def model(m: Model) -> str:
    lines: list[str] = ["language ESSENCE' 1.0", ""]

    for g in m.givens:
        lines.append(f"given {g.name} : {domain(g.domain)}")
    if m.givens:
        lines.append("")

    for dl in m.domain_lettings:
        lines.append(f"letting {dl.name} be domain {domain(dl.domain)}")
    if m.domain_lettings:
        lines.append("")

    for l in m.lettings:
        lines.append(f"letting {l.name} be {expr(l.expr)}")
    if m.lettings:
        lines.append("")

    for f_ in m.finds:
        dom_str = f_.letting_name if f_.letting_name else domain(f_.domain)
        lines.append(f"find {f_.name} : {dom_str}")
    if m.finds:
        lines.append("")

    if m.where:
        lines.append("where")
        for i, w in enumerate(m.where):
            sep = "," if i < len(m.where) - 1 else ""
            lines.append(f"    {expr(w)}{sep}")
        lines.append("")

    if m.objective:
        lines.append(f"{m.objective.direction} {expr(m.objective.expr)}")
        lines.append("")

    if m.branching_on:
        items = ", ".join(expr(v) for v in m.branching_on)
        lines.append(f"branching on [{items}]")
        lines.append("")

    if m.constraints:
        lines.append("such that")
        for i, c in enumerate(m.constraints):
            sep = "," if i < len(m.constraints) - 1 else ""
            lines.append(f"    {expr(c)}{sep}")
        lines.append("")

    return "\n".join(lines)


def param(givens: list[GivenDecl], values: dict[str, Expr]) -> str:
    lines = ["language ESSENCE' 1.0", ""]
    for g in givens:
        val = values[g.name]
        lines.append(f"letting {g.name} be {expr(val)}")
    lines.append("")
    return "\n".join(lines)
