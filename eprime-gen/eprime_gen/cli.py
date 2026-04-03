# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
"""
Command-line interface for the EPrime test generator.

Usage:
    python -m eprime_gen [options]

Examples:
    # Generate 10 random test pairs into ./out/
    python -m eprime_gen -n 10 -o out/

    # Reproducible run with seed
    python -m eprime_gen -n 5 --seed 42 -o out/

    # Deeper expressions, more variables
    python -m eprime_gen --depth 5 --finds 6 -n 3 -o out/

    # Run Savile Row on each generated pair (needs SR on PATH or --sr-path)
    python -m eprime_gen -n 5 -o out/ --run-sr --sr-path /path/to/savilerow
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

from .generator import generate_model, GenConfig
from .printer import model as print_model, param as print_param


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate random Essence Prime model/param test pairs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-n", "--count", type=int, default=1,
                   help="Number of test pairs to generate (default: 1)")
    p.add_argument("-o", "--output", default=".", metavar="DIR",
                   help="Output directory (default: current dir)")
    p.add_argument("--seed", type=int, default=None,
                   help="Base random seed (each test uses seed+i)")
    p.add_argument("--depth", type=int, default=4,
                   help="Maximum expression depth (default: 4)")
    p.add_argument("--finds", type=int, default=4,
                   help="Max decision variables (default: 4)")
    p.add_argument("--givens", type=int, default=3,
                   help="Max given parameters (default: 3)")
    p.add_argument("--constraints", type=int, default=5,
                   help="Max top-level constraints (default: 5)")
    p.add_argument("--prefix", default="test",
                   help="Filename prefix (default: 'test')")

    sr = p.add_argument_group("Savile Row integration")
    sr.add_argument("--run-sr", action="store_true",
                    help="Run Savile Row on each generated pair")
    sr.add_argument("--sr-path", default="savilerow",
                    help="Path to the savilerow script (default: 'savilerow')")
    sr.add_argument("--sr-solver", default="-minion",
                    help="Solver flag to pass to SR (default: -minion)")
    sr.add_argument("--sr-timeout", type=int, default=30,
                    help="Seconds per SR invocation (default: 30)")

    return p.parse_args(argv)


def run_savile_row(sr_path: str, eprime: Path, param: Path,
                   solver_flag: str, timeout: int) -> tuple[int, str, str]:
    cmd = [sr_path, str(eprime), str(param), solver_flag]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError:
        return -2, "", f"savilerow not found at: {sr_path}"


def main(argv=None):
    args = parse_args(argv)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = GenConfig(
        max_depth=args.depth,
        max_find_vars=args.finds,
        max_given_params=args.givens,
        max_constraints=args.constraints,
    )

    passed = failed = errors = 0

    for i in range(args.count):
        seed = (args.seed + i) if args.seed is not None else None
        stem = f"{args.prefix}_{i:04d}"

        m, param_vals = generate_model(seed=seed, cfg=cfg)

        eprime_path = out_dir / f"{stem}.eprime"
        param_path = out_dir / f"{stem}.param"

        eprime_path.write_text(print_model(m))
        param_path.write_text(print_param(m.givens, param_vals))

        if args.run_sr:
            rc, stdout, stderr = run_savile_row(
                args.sr_path, eprime_path, param_path,
                args.sr_solver, args.sr_timeout
            )
            if rc == 0:
                passed += 1
                status = "PASS"
            elif rc == -1:
                errors += 1
                status = "TIMEOUT"
            elif rc == -2:
                errors += 1
                status = "ERROR (sr not found)"
                print(stderr, file=sys.stderr)
                break
            else:
                failed += 1
                status = f"FAIL (rc={rc})"
                if stderr.strip():
                    print(f"  stderr: {stderr.strip()[:200]}", file=sys.stderr)
            print(f"{stem}: {status}")
        else:
            print(f"Written: {eprime_path}  {param_path}")

    if args.run_sr and args.count > 0:
        total = args.count
        print(f"\nResults: {passed}/{total} passed, {failed} failed, {errors} errors/timeouts")
        if failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
