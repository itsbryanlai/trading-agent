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


def top_level_keys(block: str) -> list[str]:
    """Keys of a `{ ... }` object literal at its own depth, in order, repeats kept."""
    depth, quote, flat, i = 0, "", [], 0
    while i < len(block):
        ch = block[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in _QUOTES:
            quote = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif depth == 1:
            flat.append(ch)
        i += 1
    return re.findall(r"(?:^|[,\s])([A-Za-z_]\w*)\s*:", "".join(flat))


def env_body(block: str) -> str:
    """The first env block's `{ ... }`, or an empty object when there is none."""
    match = re.search(r"\benv\s*:\s*\{", block)
    return "{}" if match is None else balanced(block, match.end() - 1)


def env_entries(block: str) -> tuple[dict[str, str], list[str]]:
    """Variable name -> value text, plus any env line that isn't `NAME: value,`."""
    body = env_body(block)[1:-1]
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


def _whole_file(text: str, blocks: dict[str, str]) -> list[str]:
    found: list[str] = []
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
    return found


def _shape_rules(text: str) -> list[str]:
    """Rules that stop the scanner above from being slipped past."""
    found: list[str] = []
    calls = len(re.findall(r"\bservice\(", text))
    literal = len(re.findall(r'\bservice\(\s*"[^"]+"\s*,\s*\{', text))
    if calls != literal:
        found.append("every service( call must take a double-quoted name and an object literal")
    projects = list(re.finditer(r"\bproject\(\s*\"[^\"]+\"\s*,\s*\{", text))
    if len(projects) != 1 or len(re.findall(r"\bproject\(", text)) != 1:
        found.append("exactly one project(name, { ... }) call is required")
    else:
        keys = top_level_keys(balanced(text, projects[0].end() - 1))
        if keys != ["resources"]:
            found.append(f"project() may hold only resources, found {keys}")
    return found


def _one_service(name: str, block: str) -> tuple[list[str], set[str]]:
    start, names = CONTRACT[name]
    entries, odd = env_entries(block)
    found = [f"{name}: key {k} appears twice" for k in _repeats(top_level_keys(block))]
    if len(re.findall(r"\benv\s*:\s*\{", block)) != 1:
        found.append(f"{name}: exactly one env block is required")
    body = env_body(block)
    found += [f"{name}: variable {k} is set twice" for k in _repeats(top_level_keys(body))]
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
    return found, set(entries)


def _repeats(keys: list[str]) -> list[str]:
    return sorted({k for k in keys if keys.count(k) > 1})


def _against_the_repo(
    blocks: dict[str, str], declared: set[str], env_example: str, schedule: dict
) -> list[str]:
    found: list[str] = []
    example = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, re.MULTILINE))
    if declared - example:
        found.append(f"declared but missing from .env.example: {sorted(declared - example)}")
    agents = [cfg for cfg in schedule.values() if isinstance(cfg, dict) and cfg.get("enabled")]
    wanted = {var for cfg in agents for var in cfg["env"]}
    have = set(env_entries(blocks.get("orchestrator", ""))[0]) - {"ORCHESTRATOR_DATABASE_URL"}
    if wanted != have:
        found.append(f"orchestrator agent variables differ from schedule.yaml: {wanted ^ have}")
    if schedule["portfolio_manager"].get("run_while_paused") is not True:
        found.append("config/schedule.yaml must have portfolio_manager.run_while_paused: true")
    return found


def violations(raw: str, env_example: str, schedule: dict) -> list[str]:
    text = strip_comments(raw)
    blocks = service_blocks(text)
    found = _whole_file(text, blocks) + _shape_rules(text)
    declared: set[str] = set()
    for name, block in blocks.items():
        if name in CONTRACT:
            problems, names = _one_service(name, block)
            found += problems
            declared |= names
    return found + _against_the_repo(blocks, declared, env_example, schedule)


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


def test_a_service_name_that_is_not_a_double_quoted_literal_is_caught(shipped):
    # Single quotes, a template literal and a variable all hide a service from the
    # block scanner, so each must be refused outright.
    for name in ("'extra'", "`extra`", "some_name"):
        assert _broken(
            shipped,
            "export default",
            f'const extra = service({name}, {{ start: "x" }});\nexport default',
        ), name


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("replicas: 1,", "replicas: 1,\n    replicas: 1,"),
        (
            'start: "python -m trading_agent.risk",',
            'start: "python -m trading_agent.risk",\n    start: "python -m trading_agent.risk",',
        ),
        (
            "RISK_GATE_DATABASE_URL: preserve(),",
            "RISK_GATE_DATABASE_URL: preserve(),\n      RISK_GATE_DATABASE_URL: preserve(),",
        ),
        (
            "RISK_GATE_DATABASE_URL: preserve(),\n    },",
            "RISK_GATE_DATABASE_URL: preserve(),\n    },\n    source: " + SOURCE + ",",
        ),
    ],
    ids=["replicas", "start", "env variable", "source"],
)
def test_a_repeated_key_in_a_service_block_is_caught(shipped, old, new):
    assert _broken(shipped, old, new)


def test_a_second_env_block_is_caught(shipped):
    # Nested after the real one, so it is not a repeated top-level key and the first
    # block still matches the contract: only the env count catches it.
    assert _broken(
        shipped,
        "RISK_GATE_DATABASE_URL: preserve(),\n    },",
        "RISK_GATE_DATABASE_URL: preserve(),\n    },\n    other: { env: {} },",
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("resources: [", 'description: "x",\n    resources: ['),
        ("resources: [", "env: { X: preserve() },\n    resources: ["),
        ("export default", 'const p2 = project("other", { resources: [] });\nexport default'),
    ],
    ids=["extra key", "project env", "second project"],
)
def test_project_may_hold_only_resources(shipped, old, new):
    assert _broken(shipped, old, new)
