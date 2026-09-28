"""US5: limits come from the reviewed file; a bad file approves nothing; every
verdict names the exact file it was judged against."""

from __future__ import annotations

import pytest
import yaml

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    NOW,
    REPO_CONFIG,
    insert_position,
    make_decision,
    seed_open_day,
    verdict_count,
)
from trading_agent.risk import rules
from trading_agent.risk.config import RiskConfigError
from trading_agent.risk.service import evaluate_decision, evaluate_stop_loss_trigger


def _config_file(tmp_path, name="risk.yaml", *, text=None, **changes):
    data = yaml.safe_load(REPO_CONFIG.read_text())
    data.update(changes)
    path = tmp_path / name
    path.write_text(text if text is not None else yaml.safe_dump(data))
    return path


def test_the_gate_applies_the_files_limits_not_defaults(conn, tmp_path):
    seed_open_day(conn)
    decision = make_decision(conn, target="5")
    tighter = _config_file(tmp_path, max_position_pct=4)
    with as_role(conn, "ta_risk_gate"):
        verdict = evaluate_decision(conn, decision, now=NOW, config_path=tighter)
    assert verdict.order.qty == 19  # floor(4000 / 202): the 4% ceiling from the file
    assert verdict.order.trims == (rules.MAX_POSITION_PCT,)


@pytest.mark.parametrize(
    ("changes", "setting"),
    [({"stop_loss_pct": 150}, "stop_loss_pct"), ({"max_postion_pct": 8}, "max_postion_pct")],
    ids=["out-of-range", "unknown-key"],
)
def test_a_bad_file_stops_both_entry_points_and_writes_nothing(conn, tmp_path, changes, setting):
    seed_open_day(conn)
    insert_position(conn)
    decision = make_decision(conn)
    trigger = conn.execute(
        "INSERT INTO stop_loss_triggers (symbol, observed_price, observed_at) "
        "VALUES ('AAPL', 100, '2026-09-28 14:00+00') RETURNING id"
    ).fetchone()["id"]
    bad = _config_file(tmp_path, **changes)

    with as_role(conn, "ta_risk_gate"):
        with pytest.raises(RiskConfigError, match=setting):
            evaluate_decision(conn, decision, now=NOW, config_path=bad)
        with pytest.raises(RiskConfigError, match=setting):
            evaluate_stop_loss_trigger(conn, trigger, now=NOW, config_path=bad)
    assert verdict_count(conn) == 0


def test_a_comment_only_change_is_a_new_config_version(conn, tmp_path):
    seed_open_day(conn)
    original = _config_file(tmp_path, "a.yaml", text=REPO_CONFIG.read_text())
    commented = _config_file(tmp_path, "b.yaml", text=REPO_CONFIG.read_text() + "\n# reviewed\n")
    first, second = make_decision(conn), make_decision(conn, target="6")
    with as_role(conn, "ta_risk_gate"):
        evaluate_decision(conn, first, now=NOW, config_path=original)
        evaluate_decision(conn, second, now=NOW, config_path=commented)
    versions = {
        row["config_version"]
        for row in conn.execute("SELECT config_version FROM risk_verdicts").fetchall()
    }
    assert len(versions) == 2
