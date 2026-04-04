#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Contributors
#
# Download and extract OR-Tools CP-SAT (fzn-cp-sat) for use with the fuzzer.
#
# Usage:
#   ./setup-ortools.sh
#
# Downloads OR-Tools into the current directory.  The fuzzer (fuzz.py)
# automatically searches or-tools_*/bin/fzn-cp-sat at startup.

set -euo pipefail

ORTOOLS_URL="https://github.com/google/or-tools/releases/download/v9.12/or-tools_amd64_ubuntu-24.04_cpp_v9.12.4544.tar.gz"
ORTOOLS_TARBALL="or-tools_amd64_ubuntu-24.04_cpp_v9.12.4544.tar.gz"

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
if [[ -f "$ORTOOLS_TARBALL" ]]; then
    echo "Tarball already exists: ${ORTOOLS_TARBALL} (skipping download)"
else
    echo "Downloading OR-Tools CP-SAT..."
    curl -L -o "$ORTOOLS_TARBALL" "$ORTOOLS_URL"
    echo "Downloaded: ${ORTOOLS_TARBALL}"
fi

# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------
echo "Extracting..."
tar xzf "$ORTOOLS_TARBALL"

# Find the extracted directory (name varies with version)
ORTOOLS_DIR=""
for d in or-tools_*/; do
    if [[ -d "$d" ]]; then
        ORTOOLS_DIR="${d%/}"
        break
    fi
done

if [[ -z "$ORTOOLS_DIR" ]]; then
    echo "ERROR: Could not find extracted OR-Tools directory."
    exit 1
fi

echo "OR-Tools directory: ${ORTOOLS_DIR}"

# ---------------------------------------------------------------------------
# Verify fzn-cp-sat exists
# ---------------------------------------------------------------------------
FZN_BIN=""
for candidate in \
    "${ORTOOLS_DIR}/bin/fzn-cp-sat" \
    "${ORTOOLS_DIR}/fzn-cp-sat" \
    "${ORTOOLS_DIR}/bin/fzn-or-tools"; do
    if [[ -x "$candidate" ]]; then
        FZN_BIN="$candidate"
        break
    fi
done

if [[ -z "$FZN_BIN" ]]; then
    echo "ERROR: Could not find fzn-cp-sat binary in ${ORTOOLS_DIR}/"
    echo "Contents of ${ORTOOLS_DIR}/bin/ (if it exists):"
    ls "${ORTOOLS_DIR}/bin/" 2>/dev/null || echo "  (no bin/ directory)"
    echo ""
    echo "Searching for fzn-* binaries anywhere in the archive:"
    find "$ORTOOLS_DIR" -name 'fzn-*' -type f 2>/dev/null || echo "  (none found)"
    exit 1
fi

# Quick sanity check
if "${FZN_BIN}" --help >/dev/null 2>&1 || "${FZN_BIN}" --version >/dev/null 2>&1 || [[ -x "$FZN_BIN" ]]; then
    echo ""
    echo "SUCCESS: fzn-cp-sat is ready at ${FZN_BIN}"
else
    echo ""
    echo "WARNING: ${FZN_BIN} exists but may not be executable."
fi

echo ""
echo "The fuzzer will find it automatically.  Usage:"
echo "  python3 fuzz.py --diff-test --backends minion,sat,or-tools -n 100"
