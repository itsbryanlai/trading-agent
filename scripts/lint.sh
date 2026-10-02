#!/usr/bin/env bash
# Modularity gate: style + complexity (ruff), module boundaries, module size.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=.venv/bin
$BIN/ruff check .
$BIN/lint-imports
python3 scripts/check_module_size.py
