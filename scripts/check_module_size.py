#!/usr/bin/env python3
"""Fail when a source module grows past MAX_LINES.

Modules already over the limit are listed in BASELINE with their current
size: they may not grow. Shrink or split them, then lower or remove the entry.
"""
import sys
from pathlib import Path

MAX_LINES = 600
BASELINE = {
    "src/trading_agent/execution/service.py": 1003,
}

root = Path(__file__).resolve().parent.parent
failed = False
for path in sorted((root / "src").rglob("*.py")):
    rel = path.relative_to(root).as_posix()
    n = len(path.read_text().splitlines())
    limit = BASELINE.get(rel, MAX_LINES)
    if n > limit:
        print(f"{rel}: {n} lines (limit {limit}); split it into smaller modules")
        failed = True
sys.exit(1 if failed else 0)
