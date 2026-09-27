"""The rule names in code and in contracts/rejection-rules.md must never drift."""

import re
from pathlib import Path

from trading_agent.risk import rules

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "002-risk-gate"
    / "contracts"
    / "rejection-rules.md"
)


def _contract_rule_names() -> set[str]:
    # Table rows look like: | 3 | `no_account_snapshot_today` | ... |
    return set(re.findall(r"^\|\s*\d+\s*\|\s*`([a-z_]+)`", CONTRACT.read_text(), re.MULTILINE))


def _code_rule_names() -> set[str]:
    return {
        value for name, value in vars(rules).items() if name.isupper() and isinstance(value, str)
    }


def test_contract_and_code_name_exactly_the_same_rules():
    assert _contract_rule_names() == _code_rule_names()


def test_the_contract_parse_is_not_vacuous():
    assert len(_contract_rule_names()) >= 17
