"""Guard test for the deployed shape (specs/010-observe-only-deployment, research R11).

It reads `.railway/railway.ts` as text and checks it against
`contracts/service-layout.md`, so a change that would hand a service a credential it
must not hold fails in CI before anything is applied.

Approach, kept deliberately simple: strip comments, then find each `service("name", {`
and take its balanced `{ ... }` block (a small scanner that skips string literals).
Inside a block, the variables are the lines of its `env: { ... }` body, one
`NAME: value,` per line (a line that doesn't fit that shape is itself a violation).
Everything else is a regular expression over the block or the whole file.

`violations()` returns plain strings, so the same checks run on the shipped file (which
must have none) and on deliberately broken copies (each of which must have some).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
RAILWAY_TS = ROOT / ".railway" / "railway.ts"
ENV_EXAMPLE = ROOT / ".env.example"
SCHEDULE = ROOT / "config" / "schedule.yaml"

SOURCE = 'github("itsbryanlai/trading-agent", { branch: "release/prod" })'

# contracts/service-layout.md, as data: service -> (start command, variable names).
CONTRACT: dict[str, tuple[str, set[str]]] = {
    "orchestrator": (
        "python -m trading_agent.orchestrator",
        {
            "ORCHESTRATOR_DATABASE_URL",
            "RESEARCH_DATABASE_URL",
            "RESEARCH_FINNHUB_API_KEY",
            "RESEARCH_DASHSCOPE_API_KEY",
            "RESEARCH_QWEN_BASE_URL",
            "RESEARCH_ANTHROPIC_API_KEY",
            "PORTFOLIO_MANAGER_DATABASE_URL",
            "PORTFOLIO_MANAGER_FINNHUB_API_KEY",
            "PORTFOLIO_MANAGER_DASHSCOPE_API_KEY",
            "PORTFOLIO_MANAGER_QWEN_BASE_URL",
            "PORTFOLIO_MANAGER_ANTHROPIC_API_KEY",
        },
    ),
    "risk-gate": ("python -m trading_agent.risk", {"RISK_GATE_DATABASE_URL"}),
    "reference-data": (
        "python -m trading_agent.reference",
        {"REFERENCE_DATA_DATABASE_URL", "REFERENCE_DATA_FINNHUB_API_KEY"},
    ),
    "execution": (
        "python -m trading_agent.execution",
        {"EXECUTION_DATABASE_URL", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_BASE_URL"},
    ),
}

_QUOTES = "\"'`"


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, leaving string literals alone."""
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in _QUOTES:
            quote = ch
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def balanced(text: str, open_at: int) -> str:
    """The `{ ... }` starting at `open_at` (inclusive), braces in strings ignored."""
    depth, quote, i = 0, "", open_at
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in _QUOTES:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_at : i + 1]
        i += 1
    raise ValueError("unbalanced braces")


def service_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(r'\bservice\(\s*"([^"]+)"\s*,\s*\{', text):
        blocks[match.group(1)] = balanced(text, match.end() - 1)
    return blocks


def env_entries(block: str) -> tuple[dict[str, str], list[str]]:
    """Variable name -> value text, plus any env line that isn't `NAME: value,`."""
    match = re.search(r"\benv\s*:\s*\{", block)
    if match is None:
        return {}, []
    body = balanced(block, match.end() - 1)[1:-1]
    entries: dict[str, str] = {}
    odd: list[str] = []
    for line in (ln.strip() for ln in body.splitlines()):
        if not line:
            continue
        parsed = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?),?", line)
        if parsed is None:
            odd.append(line)
        else:
            entries[parsed.group(1)] = parsed.group(2)
    return entries, odd


def violations(raw: str, env_example: str, schedule: dict) -> list[str]:
    text = strip_comments(raw)
    found: list[str] = []
    blocks = service_blocks(text)

    if set(blocks) != set(CONTRACT):
        found.append(f"services are {sorted(blocks)}, contract says {sorted(CONTRACT)}")
    if re.findall(r"\bpostgres\(", text) != ["postgres("] or 'postgres("postgres")' not in text:
        found.append('exactly one postgres("postgres") is required')
    for other in re.findall(
        r"\b(fn|redis|mysql|mongo|bucket|volume|database|template|image|empty|group)\(", text
    ):
        found.append(f"resource kind {other}() is not in the contract")

    for forbidden in ("ADMIN_DATABASE_URL", ".env.DATABASE_URL", ".env.DATABASE_PUBLIC_URL"):
        if forbidden in text:
            found.append(f"{forbidden} must not appear")
    if re.search(r"\.env\.PG", text) or re.search(r"\w\.env\.", text):
        found.append("no variable may reference another resource's env (database-owned values)")

    execution = blocks.get("execution")
    outside = text.replace(execution, "") if execution else text
    for prefix in ("ALPACA_", "EXECUTION_"):
        if prefix in outside:
            found.append(f"{prefix}* appears outside the execution service")

    if len(re.findall(r"\bgithub\(", text)) != len(re.findall(re.escape(SOURCE), text)):
        found.append('every github() source must be exactly branch: "release/prod"')

    declared: set[str] = set()
    for name, block in blocks.items():
        if name not in CONTRACT:
            continue
        start, names = CONTRACT[name]
        entries, odd = env_entries(block)
        found += [f"{name}: env line not `NAME: value,`: {line}" for line in odd]
        found += [
            f"{name}: {var} is {value}, not preserve()"
            for var, value in entries.items()
            if value != "preserve()"
        ]
        if set(entries) != names:
            found.append(f"{name}: variables differ from the contract: {set(entries) ^ names}")
        if SOURCE not in block:
            found.append(f"{name}: source is not {SOURCE}")
        if f'start: "{start}"' not in block:
            found.append(f"{name}: start command is not {start!r}")
        if not re.search(r"\breplicas\s*:\s*1\b", block):
            found.append(f"{name}: must run one replica")
        declared |= set(entries)

    example = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, re.MULTILINE))
    if declared - example:
        found.append(f"declared but missing from .env.example: {sorted(declared - example)}")

    agents = {
        name: cfg for name, cfg in schedule.items() if isinstance(cfg, dict) and cfg.get("enabled")
    }
    wanted = {var for cfg in agents.values() for var in cfg["env"]}
    have = set(env_entries(blocks.get("orchestrator", ""))[0]) - {"ORCHESTRATOR_DATABASE_URL"}
    if wanted != have:
        found.append(f"orchestrator agent variables differ from schedule.yaml: {wanted ^ have}")
    if schedule["portfolio_manager"].get("run_while_paused") is not True:
        found.append("config/schedule.yaml must have portfolio_manager.run_while_paused: true")
    return found


@pytest.fixture
def shipped() -> tuple[str, str, dict]:
    return (
        RAILWAY_TS.read_text(),
        ENV_EXAMPLE.read_text(),
        yaml.safe_load(SCHEDULE.read_text()),
    )


def test_the_shipped_definition_matches_the_contract(shipped):
    assert violations(*shipped) == []


def _broken(shipped, old: str, new: str):
    raw, env_example, schedule = shipped
    assert old in raw, f"fixture text {old!r} not in railway.ts"
    return violations(raw.replace(old, new, 1), env_example, schedule)


def test_a_broker_key_on_another_service_is_caught(shipped):
    assert _broken(
        shipped,
        "RISK_GATE_DATABASE_URL: preserve(),",
        "RISK_GATE_DATABASE_URL: preserve(),\n      ALPACA_API_KEY_ID: preserve(),",
    )


def test_a_literal_value_is_caught(shipped):
    assert _broken(
        shipped, "RISK_GATE_DATABASE_URL: preserve(),", 'RISK_GATE_DATABASE_URL: "postgresql://x",'
    )


def test_a_database_reference_is_caught(shipped):
    assert _broken(
        shipped,
        "RISK_GATE_DATABASE_URL: preserve(),",
        "RISK_GATE_DATABASE_URL: db.env.DATABASE_URL,",
    )


def test_a_dropped_branch_is_caught(shipped):
    assert _broken(shipped, SOURCE, 'github("itsbryanlai/trading-agent")')


def test_an_undeclared_service_is_caught(shipped):
    assert _broken(
        shipped, "export default", 'const extra = service("extra", { env: {} });\nexport default'
    )


def test_the_admin_credential_is_caught(shipped):
    assert _broken(
        shipped,
        "RISK_GATE_DATABASE_URL: preserve(),",
        "RISK_GATE_DATABASE_URL: preserve(),\n      ADMIN_DATABASE_URL: preserve(),",
    )


def test_observe_off_in_the_schedule_is_caught(shipped):
    raw, env_example, schedule = shipped
    schedule["portfolio_manager"]["run_while_paused"] = False
    assert violations(raw, env_example, schedule)


def test_a_name_missing_from_env_example_is_caught(shipped):
    raw, env_example, schedule = shipped
    assert violations(raw, env_example.replace("RISK_GATE_DATABASE_URL=", ""), schedule)


def test_comments_may_mention_forbidden_names(shipped):
    raw, env_example, schedule = shipped
    assert not violations(
        "// ADMIN_DATABASE_URL, ALPACA_ db.env.DATABASE_URL\n" + raw, env_example, schedule
    )
