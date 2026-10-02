#!/usr/bin/env python3
"""PreToolUse hook: block a `git commit` that is too big to be one logical change.

Counts code changes only (docs, specs, markdown are exempt). If a commit really
is one change that large, put [large-commit] in the message.
"""
import json
import re
import subprocess
import sys

MAX_FILES = 15
MAX_LINES = 600
EXEMPT = re.compile(r"^(docs/|specs/|\.specify/)|\.md$")
COMMIT = re.compile(r"\bgit\s+(?:-\S+\s+(?:\S+\s+)?)*commit\b")
STAGE_ALL = re.compile(r"\bcommit\b.*\s(-[a-zA-Z]*a[a-zA-Z]*|--all)\b")

command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
if not COMMIT.search(command) or "[large-commit]" in command:
    sys.exit(0)

diff = ["git", "diff", "--numstat"] + (["HEAD"] if STAGE_ALL.search(command) else ["--cached"])
out = subprocess.run(diff, capture_output=True, text=True).stdout
files = lines = 0
for row in out.splitlines():
    added, removed, path = row.split("\t", 2)
    if EXEMPT.search(path):
        continue
    files += 1
    lines += (int(added) if added != "-" else 0) + (int(removed) if removed != "-" else 0)

if files > MAX_FILES or lines > MAX_LINES:
    print(
        f"Blocked: this commit changes {files} code files / {lines} lines "
        f"(limit {MAX_FILES} / {MAX_LINES}; docs and specs not counted). "
        "Commits must be atomic: split it into one logical change per commit "
        "(stage a subset with `git add <paths>`), then commit each. "
        "If it really is a single change, add [large-commit] to the message.",
        file=sys.stderr,
    )
    sys.exit(2)
