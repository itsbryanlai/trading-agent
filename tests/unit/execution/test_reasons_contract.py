"""The refusal reasons in code and in contracts/refusal-reasons.md must never drift."""

import re
from pathlib import Path

from trading_agent.execution import reasons

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "003-execution"
    / "contracts"
    / "refusal-reasons.md"
)


def _contract_reason_names() -> set[str]:
    # Table rows look like: | 5 | `daily_loss_line_crossed` | ... |
    return set(re.findall(r"^\|\s*\d+\s*\|\s*`([a-z_]+)`", CONTRACT.read_text(), re.MULTILINE))


def _code_reason_names() -> set[str]:
    return {
        value for name, value in vars(reasons).items() if name.isupper() and isinstance(value, str)
    }


def test_contract_and_code_name_exactly_the_same_reasons():
    assert _contract_reason_names() == _code_reason_names() == reasons.ALL


def test_the_contract_parse_is_not_vacuous():
    assert len(_contract_reason_names()) == 9
