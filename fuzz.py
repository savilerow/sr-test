#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
Fuzz Savile Row by generating random EPrime models and running SR on each,
flagging any output that indicates a crash or unexpected internal error.

Usage:
    python3 fuzz.py [options]

Examples:
    python3 fuzz.py
    python3 fuzz.py -n 500 --seed 42
    python3 fuzz.py -n 1000 --depth 7 --finds 8 --workers 4
    python3 fuzz.py --keep-failures -o fuzz-out/
    python3 fuzz.py --diff-test -n 200
    python3 fuzz.py --diff-test --backends minion,sat,chuffed -n 200
"""
import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

# Ensure the eprime_gen package is importable when running from this directory
sys.path.insert(0, str(Path(__file__).parent / "eprime-gen"))

from eprime_gen import generate_model, GenConfig
from eprime_gen.printer import model as print_model, param as print_param
from eprime_gen.ast_nodes import IntLit, Var, BinOp

# ---------------------------------------------------------------------------
# Backend definitions
# ---------------------------------------------------------------------------

# Map backend name → extra SR flags (appended before -run-solver)
BACKENDS: dict[str, list[str]] = {
    "minion":  [],
    "sat":     ["-sat"],
    "chuffed": ["-chuffed"],
}

# ---------------------------------------------------------------------------
# Crash detection
# ---------------------------------------------------------------------------

# These patterns in SR output indicate a genuine internal failure worth
# investigating, as opposed to a clean "model is invalid" rejection.
CRASH_PATTERNS = [
    "Exception in thread",
    "java.lang.AssertionError",
    "java.lang.NullPointerException",
    "java.lang.ArrayIndexOutOfBoundsException",
    "java.lang.ClassCastException",
    "java.lang.StackOverflowError",
    "java.lang.OutOfMemoryError",
    "java.lang.RuntimeException",
    "INTERNAL ERROR",
    "Assertion failed",
    "at savilerow.",           # any stack frame from SR code
]


def is_crash(output: str) -> bool:
    low = output.lower()
    for pat in CRASH_PATTERNS:
        if pat.lower() in low:
            return True
    return False


# ---------------------------------------------------------------------------
# Solution-file parsing and objective evaluation (used for differential tests)
# ---------------------------------------------------------------------------

def parse_solution_file(path: Path) -> Optional[dict[str, int]]:
    """Parse a .solution file into a name→value dict.

    Returns None if the file does not exist (indicating UNSAT).
    Non-integer values (e.g. matrices) are silently skipped.
    """
    if not path.exists():
        return None
    vals: dict[str, int] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        # Format: "letting <name> be <value>"
        if line.startswith("letting ") and " be " in line:
            toks = line.split(None, 3)
            if len(toks) >= 4:
                try:
                    vals[toks[1]] = int(toks[3])
                except ValueError:
                    pass   # skip matrices / booleans
    return vals


def eval_int_expr(expr, solution: dict[str, int]) -> Optional[int]:
    """Evaluate a simple int expression against a solution mapping.

    Handles: IntLit, Var, BinOp(+, -, *).  Returns None for anything more
    complex (e.g. array sums, function calls).  This is sufficient for
    objectives generated with force_simple_objective=True.
    """
    if isinstance(expr, IntLit):
        return expr.val
    if isinstance(expr, Var):
        return solution.get(expr.name)
    if isinstance(expr, BinOp) and expr.op in ("+", "-", "*"):
        lv = eval_int_expr(expr.left,  solution)
        rv = eval_int_expr(expr.right, solution)
        if lv is None or rv is None:
            return None
        if expr.op == "+":
            return lv + rv
        if expr.op == "-":
            return lv - rv
        return lv * rv
    return None


# ---------------------------------------------------------------------------
# Running one test — standard (crash-detection) mode
# ---------------------------------------------------------------------------

def run_one(args_tuple) -> dict:
    seed, sr_path, cfg, out_dir, keep_failures, timeout = args_tuple

    with tempfile.TemporaryDirectory() as tmp:
        eprime = Path(tmp) / "test.eprime"
        param  = Path(tmp) / "test.param"

        m, pvals = generate_model(seed=seed, cfg=cfg)
        eprime.write_text(print_model(m))
        param.write_text(print_param(m.givens, pvals))

        try:
            result = subprocess.run(
                [sr_path, str(eprime), str(param)],
                capture_output=True, text=True, timeout=timeout,
            )
            rc     = result.returncode
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return {"seed": seed, "status": "timeout", "output": "", "eprime": None, "param": None}
        except Exception as e:
            return {"seed": seed, "status": "error", "output": str(e), "eprime": None, "param": None}

        crashed = is_crash(output)

        if crashed and keep_failures and out_dir:
            dst = Path(out_dir)
            dst.mkdir(parents=True, exist_ok=True)
            stem = f"crash_seed{seed}"
            shutil.copy(eprime, dst / f"{stem}.eprime")
            shutil.copy(param,  dst / f"{stem}.param")
            (dst / f"{stem}.log").write_text(output)
            saved_eprime = str(dst / f"{stem}.eprime")
            saved_param  = str(dst / f"{stem}.param")
        else:
            saved_eprime = saved_param = None

        return {
            "seed":    seed,
            "status":  "crash" if crashed else ("fail" if rc != 0 else "pass"),
            "rc":      rc,
            "output":  output,
            "eprime":  saved_eprime,
            "param":   saved_param,
        }


# ---------------------------------------------------------------------------
# Running one test — differential mode
# ---------------------------------------------------------------------------

def _run_backend(sr_path: str, eprime: Path, param: Path,
                 extra_flags: list[str], timeout: int) -> dict:
    """Run SR with one backend.  Returns a result dict for that backend."""
    cmd = [sr_path, str(eprime), str(param)] + extra_flags + ["-run-solver"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"sat": None, "obj": None, "status": "timeout", "output": ""}
    except Exception as e:
        return {"sat": None, "obj": None, "status": "error",   "output": str(e)}

    if is_crash(output):
        return {"sat": None, "obj": None, "status": "crash", "output": output}

    # Find the .solution file SR wrote into the same directory
    sol_files = list(eprime.parent.glob("*.solution"))
    solution  = parse_solution_file(sol_files[0]) if sol_files else None

    return {"sat": solution is not None, "obj": None,
            "status": "ok", "output": output, "solution": solution}


def run_one_diff(args_tuple) -> dict:
    """Generate one model and run it through each requested backend."""
    seed, sr_path, cfg, backend_names, out_dir, keep_failures, timeout = args_tuple

    m, pvals = generate_model(seed=seed, cfg=cfg)
    model_text = print_model(m)
    param_text = print_param(m.givens, pvals)

    backend_results: dict[str, dict] = {}

    for name in backend_names:
        # Each backend gets its own temp directory so solution files don't clash
        with tempfile.TemporaryDirectory() as tmp:
            eprime = Path(tmp) / "test.eprime"
            param  = Path(tmp) / "test.param"
            eprime.write_text(model_text)
            param.write_text(param_text)

            br = _run_backend(sr_path, eprime, param, BACKENDS[name], timeout)

            # Evaluate objective from solution if possible
            if br["status"] == "ok" and br["sat"] and m.objective:
                br["obj"] = eval_int_expr(m.objective.expr,
                                          br.get("solution") or {})

            backend_results[name] = br

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------
    # Only compare backends that completed without crash/timeout/error
    comparable = {n: r for n, r in backend_results.items() if r["status"] == "ok"}

    overall_status = "pass"

    # 1. Any crashes?
    crashed_backends = [n for n, r in backend_results.items() if r["status"] == "crash"]
    if crashed_backends:
        overall_status = "crash"

    # 2. SAT/UNSAT disagreement?
    if overall_status == "pass" and len(comparable) >= 2:
        sat_values = {n: r["sat"] for n, r in comparable.items()}
        if len(set(sat_values.values())) > 1:
            overall_status = "disagree_sat"

    # 3. Objective value disagreement?
    if overall_status == "pass" and len(comparable) >= 2:
        obj_values = {n: r["obj"] for n, r in comparable.items()
                      if r.get("obj") is not None}
        if len(obj_values) == len(comparable) and len(set(obj_values.values())) > 1:
            overall_status = "disagree_obj"

    # Save artefacts for interesting findings
    saved_eprime = saved_param = None
    if overall_status in ("disagree_sat", "disagree_obj") and keep_failures and out_dir:
        dst = Path(out_dir)
        dst.mkdir(parents=True, exist_ok=True)
        stem = f"diff_{overall_status}_seed{seed}"
        (dst / f"{stem}.eprime").write_text(model_text)
        (dst / f"{stem}.param").write_text(param_text)
        log = "\n".join(
            f"=== {n} (sat={r.get('sat')}, obj={r.get('obj')}) ===\n{r.get('output','')}"
            for n, r in backend_results.items()
        )
        (dst / f"{stem}.log").write_text(log)
        saved_eprime = str(dst / f"{stem}.eprime")
        saved_param  = str(dst / f"{stem}.param")

    return {
        "seed":    seed,
        "status":  overall_status,
        "backends": backend_results,
        "eprime":  saved_eprime,
        "param":   saved_param,
        "model_text": model_text,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Fuzz Savile Row with randomly generated EPrime models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__),
    )
    p.add_argument("-n", "--count",   type=int, default=200,
                   help="Number of tests to run (default: 200)")
    p.add_argument("--seed",          type=int, default=0,
                   help="Starting seed; test i uses seed+i (default: 0)")
    p.add_argument("--sr-path",       default=None,
                   help="Path to the savilerow script (auto-detected if omitted)")
    p.add_argument("--timeout",       type=int, default=30,
                   help="Seconds per SR invocation (default: 30)")
    p.add_argument("--workers",       type=int, default=1,
                   help="Parallel workers (default: 1)")
    p.add_argument("--depth",         type=int, default=5,
                   help="Max expression depth (default: 5)")
    p.add_argument("--finds",         type=int, default=5,
                   help="Max decision variables (default: 5)")
    p.add_argument("--givens",        type=int, default=3,
                   help="Max given parameters (default: 3)")
    p.add_argument("--constraints",   type=int, default=6,
                   help="Max top-level constraints (default: 6)")
    p.add_argument("--keep-failures", action="store_true",
                   help="Save crashing/.diff .eprime/.param/.log files to --output dir")
    p.add_argument("-o", "--output",  default="fuzz-crashes",
                   help="Directory for saved failure files (default: fuzz-crashes/)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print a line for every test, not just findings")
    # Differential testing
    p.add_argument("--diff-test",     action="store_true",
                   help="Enable differential testing: run each model through "
                        "multiple SR backends and flag disagreements")
    p.add_argument("--backends",      default="minion,sat",
                   help="Comma-separated backends to compare in --diff-test mode "
                        "(choices: minion, sat, chuffed; default: minion,sat)")
    return p.parse_args()


def find_sr(hint: str | None) -> str:
    if hint:
        return hint
    here = Path(__file__).parent
    candidate = here / "savilerow-1.11.1-linux" / "savilerow"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("savilerow")
    if found:
        return found
    sys.exit("ERROR: could not find savilerow. Use --sr-path to specify it.")


def main():
    args = parse_args()
    sr   = find_sr(args.sr_path)

    cfg = GenConfig(
        max_depth=args.depth,
        max_find_vars=args.finds,
        max_given_params=args.givens,
        max_constraints=args.constraints,
    )

    # Differential-test mode: force objectives and keep them simple so we can
    # evaluate them in Python from the solution file.
    if args.diff_test:
        cfg.feat_objective          = 1.0
        cfg.force_simple_objective  = True

    # Parse and validate backends
    backend_names = [b.strip() for b in args.backends.split(",")]
    for b in backend_names:
        if b not in BACKENDS:
            sys.exit(f"ERROR: unknown backend '{b}'. Choose from: {', '.join(BACKENDS)}")

    out_dir = Path(args.output) if args.keep_failures else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if args.diff_test:
        tasks = [
            (args.seed + i, sr, cfg, backend_names,
             str(out_dir) if out_dir else None, args.keep_failures, args.timeout)
            for i in range(args.count)
        ]
    else:
        tasks = [
            (args.seed + i, sr, cfg, str(out_dir) if out_dir else None,
             args.keep_failures, args.timeout)
            for i in range(args.count)
        ]

    if args.diff_test:
        counts = {"pass": 0, "crash": 0, "disagree_sat": 0, "disagree_obj": 0,
                  "timeout": 0, "error": 0, "skip": 0}
    else:
        counts = {"pass": 0, "fail": 0, "crash": 0, "timeout": 0, "error": 0}

    findings: list[dict] = []   # crashes + disagreements
    start = time.time()

    print(f"Savile Row : {sr}")
    print(f"Tests      : {args.count}  (seeds {args.seed}–{args.seed + args.count - 1})")
    print(f"Workers    : {args.workers}  |  timeout: {args.timeout}s per test")
    print(f"Depth      : {args.depth}  finds: {args.finds}  givens: {args.givens}  "
          f"constraints: {args.constraints}")
    if args.diff_test:
        print(f"Mode       : differential  backends: {', '.join(backend_names)}")
    print()

    executor_cls = (concurrent.futures.ProcessPoolExecutor
                    if args.workers > 1
                    else concurrent.futures.ThreadPoolExecutor)

    worker_fn = run_one_diff if args.diff_test else run_one

    with executor_cls(max_workers=args.workers) as ex:
        futures = {ex.submit(worker_fn, t): t[0] for t in tasks}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            done += 1
            status = r["status"]
            counts[status] = counts.get(status, 0) + 1

            if args.diff_test:
                if status == "crash":
                    findings.append(r)
                    print(f"[{done:>5}/{args.count}] CRASH  seed={r['seed']}")
                    for name, br in r["backends"].items():
                        if br["status"] == "crash":
                            for line in br["output"].splitlines():
                                if any(p.lower() in line.lower() for p in CRASH_PATTERNS):
                                    print(f"          [{name}] {line.strip()}")
                    print()

                elif status in ("disagree_sat", "disagree_obj"):
                    findings.append(r)
                    label = "DIFF-SAT" if status == "disagree_sat" else "DIFF-OBJ"
                    print(f"[{done:>5}/{args.count}] {label}  seed={r['seed']}")
                    for name, br in r["backends"].items():
                        if br["status"] == "ok":
                            obj_s = f"  obj={br['obj']}" if br.get("obj") is not None else ""
                            print(f"          [{name}] sat={br['sat']}{obj_s}")
                    if r.get("eprime"):
                        print(f"          saved → {r['eprime']}")
                    print()

                elif status == "timeout":
                    print(f"[{done:>5}/{args.count}] TIMEOUT  seed={r['seed']}")
                elif args.verbose:
                    print(f"[{done:>5}/{args.count}] {status.upper():12}  seed={r['seed']}")

            else:
                if status == "crash":
                    findings.append(r)
                    print(f"[{done:>5}/{args.count}] CRASH  seed={r['seed']}")
                    for line in r["output"].splitlines():
                        if any(p.lower() in line.lower() for p in CRASH_PATTERNS):
                            print(f"          {line.strip()}")
                    if r["eprime"]:
                        print(f"          saved → {r['eprime']}")
                    print()
                elif status == "timeout":
                    print(f"[{done:>5}/{args.count}] TIMEOUT seed={r['seed']}")
                elif status == "error":
                    print(f"[{done:>5}/{args.count}] ERROR  seed={r['seed']}  "
                          f"{r['output'][:120]}")
                elif args.verbose:
                    print(f"[{done:>5}/{args.count}] {status.upper():5}  seed={r['seed']}")

    elapsed = time.time() - start
    print("-" * 60)
    print(f"Done in {elapsed:.1f}s  ({elapsed/args.count:.2f}s/test)")

    if args.diff_test:
        print(f"  pass={counts['pass']}  crash={counts['crash']}  "
              f"disagree_sat={counts['disagree_sat']}  "
              f"disagree_obj={counts['disagree_obj']}  "
              f"timeout={counts.get('timeout',0)}  "
              f"skip={counts.get('skip',0)}")
    else:
        print(f"  pass={counts['pass']}  fail={counts['fail']}  "
              f"crash={counts['crash']}  timeout={counts['timeout']}  "
              f"error={counts['error']}")

    if findings:
        crash_seeds = [f["seed"] for f in findings if f["status"] == "crash"]
        diff_seeds  = [f["seed"] for f in findings
                       if f["status"] in ("disagree_sat", "disagree_obj")]
        if crash_seeds:
            print(f"\n{len(crash_seeds)} CRASH(ES) — seeds: {crash_seeds}")
        if diff_seeds:
            print(f"\n{len(diff_seeds)} DISAGREEMENT(S) — seeds: {diff_seeds}")
            for f in findings:
                if f["status"] not in ("disagree_sat", "disagree_obj"):
                    continue
                print(f"\n  seed={f['seed']} ({f['status']})")
                for name, br in f["backends"].items():
                    if br["status"] == "ok":
                        obj_s = f"  obj={br['obj']}" if br.get("obj") is not None else ""
                        print(f"    {name}: sat={br['sat']}{obj_s}")
        sys.exit(1)


if __name__ == "__main__":
    main()
