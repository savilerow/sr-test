# Savile Row Fuzzing Tool

A randomised test-generation and differential-testing framework for
[Savile Row](https://savilerow.cs.st-andrews.ac.uk/), a constraint modelling
compiler for the Essence Prime language.

The tool generates thousands of unique, seeded Essence Prime models, runs them
through Savile Row with multiple solver backends, and flags crashes and solver
disagreements.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Getting Savile Row](#getting-savile-row)
3. [Quick Start](#quick-start)
4. [Project Layout](#project-layout)
5. [The Fuzzer (`fuzz.py`)](#the-fuzzer-fuzzpy)
6. [The Generator (`eprime-gen`)](#the-generator-eprime-gen)
7. [Known Limitations](#known-limitations)
8. [Extending the Generator](#extending-the-generator)
9. [Licence](#licence)

---

## Requirements

| Dependency | Version tested | Notes |
|---|---|---|
| Python | 3.10+ | Standard library only — no third-party packages needed |
| Java | 15+ | Required to run Savile Row |
| Savile Row | 1.11.1 | Must be obtained separately (see below) |
| Minion | bundled with SR | Included in the Savile Row distribution |
| kissat (SAT) | bundled with SR | Included in the Savile Row distribution |

---

## Getting Savile Row

Savile Row is **not** included in this repository.  Download it from the
official site and build it before running the fuzzer.

```bash
# Download from https://savilerow.cs.st-andrews.ac.uk/
# (Linux build used during development: savilerow-1.11.1-linux.tar.gz)

tar xf savilerow-1.11.1-linux.tar.gz

# Build the JAR (requires Java 15+ and javac on PATH)
cd savilerow-1.11.1-linux
./compile.sh
cd ..
```

Then either add the `savilerow` wrapper script to your `PATH`, or pass its
location explicitly to `fuzz.py` via `--sr-path`:

```bash
export PATH="$PWD/savilerow-1.11.1-linux:$PATH"

# or
python3 fuzz.py --sr-path ./savilerow-1.11.1-linux/savilerow --diff-test -n 100
```

---

## Quick Start

```bash
# 1. Clone this repository
git clone <repo-url>
cd <repo>

# 2. Obtain and build Savile Row (see above)

# 3. Run 200 differential tests (Minion vs SAT, seeds 0–199)
python3 fuzz.py --diff-test -n 200

# 4. Run 1000 tests and save any failures to fuzz-out/
python3 fuzz.py --diff-test -n 1000 --keep-failures -o fuzz-out/

# 5. Just generate 10 model/param pairs into ./out/ (no solving)
python3 -m eprime_gen -n 10 -o out/
```

---

## Project Layout

```
.
├── fuzz.py                     # Main fuzzer entry point
├── eprime-gen/                 # Random EPrime model generator (Python package)
│   └── eprime_gen/
│       ├── __init__.py         # Public API: generate_model, GenConfig, print_model, print_param
│       ├── ast_nodes.py        # AST node dataclasses for all EPrime constructs
│       ├── generator.py        # Random model generator (Generator class, GenConfig)
│       ├── printer.py          # AST → EPrime source text
│       └── cli.py              # python -m eprime_gen command-line interface
├── fuzz-out/                   # Saved failure artefacts (created by --keep-failures)
└── CLAUDE.md                   # Developer notes (architecture, known bugs, feature backlog)
```

---

## The Fuzzer (`fuzz.py`)

### Modes

**Crash detection (default):** runs each model through one SR backend and
flags any Java exception or internal error in the output.

**Differential testing (`--diff-test`):** runs each model through two or more
SR backends (Minion, SAT, Chuffed) and flags:
- any backend crashing with a Java exception
- SAT/UNSAT disagreement between backends
- objective-value disagreement between backends (when an objective is present)

Differential testing is the more powerful mode — it catches bugs where one
backend silently produces a wrong answer rather than crashing.

### Usage

```
python3 fuzz.py [options]

Options:
  -n, --count N          Number of tests (default: 200)
  --seed N               Starting seed; test i uses seed+i (default: 0)
  --sr-path PATH         Path to the savilerow script (auto-detected if on PATH)
  --timeout N            Seconds per SR invocation (default: 30)
  --workers N            Parallel workers; use 1 for sequential (default: 1)
  --depth N              Max expression depth (default: 5)
  --finds N              Max decision variables per model (default: 5)
  --givens N             Max given parameters per model (default: 3)
  --constraints N        Max top-level constraints per model (default: 6)
  --keep-failures        Save failing model/param/log files to --output
  -o, --output DIR       Directory for saved failures (default: fuzz-crashes/)
  -v, --verbose          Print a line for every test, not just findings

Differential testing:
  --diff-test            Enable differential mode
  --backends B1,B2,...   Backends to compare: minion, sat, chuffed (default: minion,sat)
```

### Examples

```bash
# 1000 sequential differential tests, seeds 0–999
python3 fuzz.py --diff-test -n 1000 --workers 1

# Start from a different seed range (to extend coverage)
python3 fuzz.py --diff-test -n 1000 --seed 1000

# Save all crash/disagreement artefacts
python3 fuzz.py --diff-test -n 1000 --keep-failures -o fuzz-out/

# Include Chuffed as a third backend
python3 fuzz.py --diff-test --backends minion,sat,chuffed -n 500

# Crash-detection only (no differential comparison)
python3 fuzz.py -n 500 --keep-failures
```

### Output

Each finding is printed immediately as it is discovered:

```
[  104/1000] CRASH  seed=103
          [minion] java.lang.ArrayIndexOutOfBoundsException: Index 14 out of bounds for length 4
          [minion] at savilerow.expression.Circuit.simplify(Circuit.java:76)

[  806/1000] DIFF-SAT  seed=805
          [minion] sat=False
          [sat] sat=True  obj=29
          saved → fuzz-out/diff_disagree_sat_seed805.eprime
```

A summary is printed at the end:

```
Done in 367.4s  (0.37s/test)
  pass=988  crash=11  disagree_sat=1  disagree_obj=0  timeout=0  skip=0

11 CRASH(ES) — seeds: [103, 306, 442, ...]
1 DISAGREEMENT(S) — seeds: [805]
```

### Saved artefacts

With `--keep-failures`, disagreements are saved as three files per finding:

| File | Contents |
|---|---|
| `diff_disagree_sat_seedN.eprime` | The EPrime model |
| `diff_disagree_sat_seedN.param` | The parameter file |
| `diff_disagree_sat_seedN.log` | Full SR output from both backends |

To reproduce a specific finding manually:

```bash
savilerow fuzz-out/diff_disagree_sat_seed805.eprime \
          fuzz-out/diff_disagree_sat_seed805.param -run-solver
```

---

## The Generator (`eprime-gen`)

The generator produces fully self-consistent Essence Prime model/parameter
pairs by building a random AST and printing it to EPrime source text.  Every
model is deterministic given its seed.

### Public API

```python
from eprime_gen import generate_model, GenConfig, print_model, print_param

# Generate with default settings
model, param_values = generate_model(seed=42)
print(print_model(model))
print(print_param(model.givens, param_values))

# Customise generation
cfg = GenConfig(
    max_depth=5,
    max_find_vars=6,
    max_given_params=3,
    max_constraints=8,
    feat_circuit=0.0,    # disable circuit constraints
    feat_gcc=0.8,        # increase gcc frequency
)
model, param_values = generate_model(seed=0, cfg=cfg)
```

### `GenConfig` reference

All fields are optional and have sensible defaults.

**Size limits:**

| Field | Default | Meaning |
|---|---|---|
| `max_depth` | 4 | Maximum expression nesting depth |
| `max_find_vars` | 4 | Maximum number of `find` variables |
| `max_given_params` | 3 | Maximum number of `given` parameters |
| `max_constraints` | 5 | Maximum number of top-level constraints |
| `max_array_len` | 6 | Maximum 1D array length |
| `max_matrix_rows` | 4 | Maximum 2D matrix rows |
| `max_matrix_cols` | 4 | Maximum 2D matrix columns |
| `max_table_rows` | 8 | Maximum rows in a generated table |
| `max_table_arity` | 4 | Maximum columns in a generated table |
| `int_range` | (-5, 20) | Range for generated integer literals |

**Feature weights** (set to `0.0` to disable, higher = more frequent):

| Field | Default | Feature |
|---|---|---|
| `feat_allDiff` | 1.0 | `allDiff(arr)` |
| `feat_quantifier` | 1.0 | `forAll`/`exists` quantifiers |
| `feat_sum_agg` | 1.0 | `sum` quantifier |
| `feat_matrix_slice` | 0.6 | `M[i,..]` / `M[..,j]` slices |
| `feat_abs_val` | 0.5 | `\|x\|` absolute value |
| `feat_implication` | 0.8 | `=>` / `<->` |
| `feat_objective` | 0.5 | minimising/maximising objective |
| `feat_bool_vars` | 0.5 | Boolean `find` variables |
| `feat_2d_matrix` | 0.6 | 2D matrix `find` variables |
| `feat_comprehension` | 0.5 | `[expr \| v : d]` comprehensions |
| `feat_multi_comprehension` | 0.35 | `[expr \| i : d1, j : d2]` multi-var comprehensions |
| `feat_toInt` | 0.4 | `toInt(bool)` |
| `feat_count` | 0.4 | `count(arr, val)` |
| `feat_table` | 0.8 | `table(vars, matrix)` constraints |
| `feat_div_mod` | 0.5 | `/` and `%` operators |
| `feat_power` | 0.4 | `**` operator |
| `feat_factorial` | 0.3 | `factorial(x)` |
| `feat_product` | 0.4 | `product(arr)` |
| `feat_lex` | 0.5 | `<=lex` / `<lex` etc. |
| `feat_atleast_atmost` | 0.6 | `atleast`/`atmost` |
| `feat_alldiff_except` | 0.4 | `alldifferent_except` |
| `feat_arr_logic` | 0.5 | `and(arr)` / `or(arr)` |
| `feat_circuit` | 0.6 | `circuit(X)` |
| `feat_inverse` | 0.5 | `inverse(X, Y)` |
| `feat_gcc` | 0.5 | `gcc(X, Vals, C)` |
| `feat_if_expr` | 0.5 | `if(cond, a, b)` |
| `feat_min_max_scalar` | 0.5 | `min(x,y)` / `max(x,y)` |
| `feat_popcount` | 0.3 | `popcount(x)` |
| `feat_multi_quantifier` | 0.4 | `forAll i, j : d . body` |
| `feat_composite_domain` | 0.25 | `int(1..3, 7, 10..12)` domains |
| `feat_cumulative` | 0.4 | `cumulative(S, D, R, Cap)` |
| `feat_disjunctive` | 0.4 | `disjunctive(S, D)` |
| `feat_diffn` | 0.3 | `diffn(X, Y, W, H)` |
| `feat_where` | 0.4 | `where` clauses on parameters |
| `feat_in_domain` | 0.6 | `x in int(lo..hi)` / `x in int(a..b, c)` |
| `feat_toset` | 0.5 | `x in toSet(arr)` |
| `feat_flatten` | 0.5 | `flatten(M)` — 2D matrix to 1D array |
| `feat_list` | 0.4 | `list(e1, e2, ...)` — 1D array from scalars |
| `feat_cat_1d` | 0.4 | `cat(arr1, arr2)` — concatenate 1D arrays |
| `feat_domain_letting` | 0.3 | `letting D be domain ...` aliases |
| `feat_unary_minus` | 0.4 | Unary minus `-x` |
| `feat_sum_arr` | 0.5 | `sum(arr)` — array aggregation |
| `feat_indexOf` | 0.35 | `indexOf(arr)` as quantifier domain |
| `feat_domain_binop` | 0.35 | `D1 union D2` / `D1 intersect D2` / `D1 - D2` domain expressions |
| `feat_branching_on` | 0.25 | `branching on [...]` search ordering |
| `force_simple_objective` | False | Force sum-of-scalars objective (needed for differential objective comparison) |

### Command-line interface

The generator can also be used standalone to write model/param files to disk:

```bash
# Generate 5 pairs into ./out/ with seed 42
python3 -m eprime_gen -n 5 --seed 42 -o out/

# Generate and immediately run through Savile Row
python3 -m eprime_gen -n 10 -o out/ --run-sr \
    --sr-path /path/to/savilerow

# Larger, deeper models
python3 -m eprime_gen -n 20 --depth 6 --finds 8 --constraints 10 -o out/
```

### Architecture

The generator follows a three-layer design:

```
GenConfig  ──►  Generator.generate()  ──►  Model AST
                     │
                     ▼
              _gen_bool / _gen_int / _gen_array
              (recursive, depth-bounded, scope-aware)
                     │
                     ▼
              printer.model() / printer.param()
                     │
                     ▼
              EPrime source text (.eprime / .param)
```

**`ast_nodes.py`** defines dataclasses for every EPrime construct: domains
(`IntRangeDomain`, `BoolDomain`, `MatrixDomain`, `CompositeIntDomain`),
expressions (`BinOp`, `FuncCall`, `Quantifier`, `Comprehension`, …), and
top-level declarations (`FindDecl`, `GivenDecl`, `Model`, …).

**`generator.py`** builds random ASTs.  The `Scope` object tracks which
variables are in scope and their types.  The three core methods
(`_gen_int`, `_gen_bool`, `_gen_array`) build weighted choice lists from
the `GenConfig` feature weights and the current scope, then recurse.

**`printer.py`** converts AST nodes to EPrime source text via a single
`expr()` dispatch function and a `model()` top-level function.

### Unit tests

```bash
cd eprime-gen
python -m pytest tests/ -v
```

---

## Known Limitations

### Expected (non-bug) rejections

Roughly 10% of generated models are rejected by Savile Row with a clean error
message.  These are intentional — the generator does not filter them out
because they exercise error-handling paths.  The two most common causes:

- **`factorial` on a decision variable** (~9%): EPrime requires `factorial`
  arguments to be ground (parameter/constant only).
- **Negative value in `<v1, v2>` tuple syntax** (~1%): SR's lexer reads `<-`
  as a less-than operator rather than the start of a negative-valued tuple,
  causing a parse error.  This is itself a tokeniser bug in SR.

---

## Extending the Generator

To add a new EPrime language feature:

1. **Add an AST node** in `eprime_gen/ast_nodes.py` if the existing nodes
   (`FuncCall`, `BinOp`, etc.) cannot represent it.

2. **Add a printer case** in `eprime_gen/printer.py` inside `expr()` (and
   `_needs_parens()` if the new node needs parenthesisation when nested).

3. **Add a `feat_X` weight** to `GenConfig` in `eprime_gen/generator.py`.

4. **Add generation logic** in `_gen_bool`, `_gen_int`, or `_gen_array`:
   - Add `"my_feature"` to the `choices` list and its weight to `weights`
     (guarded by the feature flag and any scope preconditions).
   - Add the corresponding `if pick == "my_feature": ...` handler.

5. **Verify** by running a quick generation check:
   ```python
   from eprime_gen import generate_model, GenConfig
   from eprime_gen.printer import model as print_model
   cfg = GenConfig()
   found = sum(1 for s in range(500)
               if "my_func(" in print_model(generate_model(seed=s, cfg=cfg)[0]))
   print(f"Found in {found}/500 seeds")
   ```

6. **Update `CLAUDE.md`** to move the feature from "missing" to "implemented".

Features already implemented and available to copy as examples:

| Feature | Location | Notes |
|---|---|---|
| `toSet` | `_gen_bool` `"in_toset"` branch | Uses `BinOp("in", ...)` + `FuncCall("toSet", ...)` |
| `list(...)` | `_gen_array` `"list"` branch | Simple `FuncCall` with scalar int args |
| `cat(a,b)` | `_gen_array` `"cat_1d"` branch | Recursive `_gen_array` calls |
| `flatten(M)` | `_gen_array` `"flatten"` branch | Requires `MATRIX_INT` vars in scope |
| `gcc(X,V,C)` | `_gen_bool` `"gcc"` branch | Intentionally allows duplicate `V` entries to expose SR bugs |

---

## Licence

BSD 3-Clause — see [LICENSE](LICENSE).
