"""`python -m trading_agent.opportunistic_identifier`: startup, exit codes, and never
printing a variable's value (specs/011 contracts/oi-interface.md; FR-017, FR-020)."""

from __future__ import annotations

import logging

import pytest
import yaml

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.unit.opportunistic_identifier.conftest import (
    FAKE_ANTHROPIC,
    FAKE_DASHSCOPE,
    FAKE_FINNHUB,
    SECRETS,
)
from tests.unit.opportunistic_identifier.support import ROOT, Clock, FakeConn
from trading_agent.opportunistic_identifier import __main__ as runner

SHIPPED = ROOT / "config" / "opportunistic_identifier.yaml"


@pytest.fixture
def config_path(tmp_path):
    """The shipped config with a one-name scan list."""
    data = yaml.safe_load(SHIPPED.read_text())
    data["scan_universe"] = ["AAA"]
    path = tmp_path / "oi.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def proposal(symbol="AAA") -> dict:
    return {
        "symbol": symbol,
        "direction": "buy",
        "conviction": 4,
        "suggested_size_pct": 5,
        "rationale": "Cheap after its fall.",
    }


def run(args=(), *, config_path, conn=None, connect_error=None, answer=None, seen=None, **kw):
    conn = conn if conn is not None else FakeConn()
    clock = Clock()
    seen = seen if seen is not None else {}

    def connect(url, **options):
        seen["connect"] = (url, options)
        if connect_error is not None:
            raise connect_error
        return conn

    def market_factory(key):
        seen["market_key"] = key
        data = FakeOIMarketData()
        data.add("AAA", current="190")
        return data

    def model_factory(provider, key, model_cfg, *, base_url=None):
        seen["model"] = (provider, key, model_cfg.name)
        seen["base_url"] = base_url
        return FakeModel(answer if answer is not None else {"proposals": [proposal()]})

    code = runner.main(
        list(args),
        market_factory=market_factory,
        model_factory=model_factory,
        connect=connect,
        config_path=config_path,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kw,
    )
    return code, conn, seen


def _no_secret_in(caplog):
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in SECRETS:
        assert secret not in text


def test_a_happy_run_exits_zero_with_its_own_credentials(env, config_path, caplog):
    caplog.set_level(logging.INFO)
    code, conn, seen = run(config_path=config_path)
    assert code == runner.EXIT_OK
    assert len(conn.written) == 1 and conn.closed
    assert seen["connect"][1]["autocommit"] is True
    assert seen["connect"][1]["connect_timeout"] == 10
    assert seen["market_key"] == FAKE_FINNHUB
    assert seen["model"] == ("qwen", FAKE_DASHSCOPE, "qwen3.7-plus")
    assert seen["base_url"] == "https://qwen.example.test/compatible-mode/v1"
    _no_secret_in(caplog)


@pytest.mark.parametrize(
    "missing",
    [
        "OPPORTUNISTIC_IDENTIFIER_DATABASE_URL",
        "OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY",
        "OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY",
        "OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL",
    ],
)
def test_a_missing_variable_refuses_to_start_naming_it_without_a_value(
    env, config_path, monkeypatch, caplog, missing
):
    monkeypatch.delenv(missing)
    code, _, seen = run(config_path=config_path)
    assert code == runner.EXIT_REFUSED
    assert missing in caplog.text and "connect" not in seen
    _no_secret_in(caplog)


@pytest.mark.parametrize("url", ["http://qwen.example.test/v1", "ftp://x", " https://x.test/v1"])
def test_a_non_https_qwen_url_refuses_to_start_without_echoing_it(
    env, config_path, monkeypatch, caplog, url
):
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL", url)
    code, *_ = run(config_path=config_path)
    assert code == runner.EXIT_REFUSED
    assert "OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL" in caplog.text and url not in caplog.text


def test_only_the_anthropic_key_is_needed_when_the_provider_is_anthropic(
    env, config_path, monkeypatch
):
    data = yaml.safe_load(config_path.read_text())
    data["model"].update(provider="anthropic", name="claude-sonnet-5-5")
    config_path.write_text(yaml.safe_dump(data))
    monkeypatch.delenv("OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY")
    monkeypatch.delenv("OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL")
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    code, _, seen = run(config_path=config_path)
    assert code == runner.EXIT_OK
    assert seen["model"] == ("anthropic", FAKE_ANTHROPIC, "claude-sonnet-5-5")
    assert seen["base_url"] is None


@pytest.mark.parametrize(
    ("missing", "other"),
    [
        ("OPPORTUNISTIC_IDENTIFIER_DATABASE_URL", "RESEARCH_DATABASE_URL"),
        ("OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY", "FINNHUB_API_KEY"),
        ("OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY", "RESEARCH_FINNHUB_API_KEY"),
        ("OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY", "RESEARCH_DASHSCOPE_API_KEY"),
        ("OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY"),
    ],
)
def test_another_agents_or_unprefixed_variable_is_never_a_fallback(
    env, config_path, monkeypatch, caplog, missing, other
):
    # FR-020: with only this one of its own variables unset, a look-alike doesn't stand in.
    monkeypatch.delenv(missing)
    monkeypatch.setenv(other, "fake-not-real-" + other.lower())
    code, _, seen = run(config_path=config_path)
    assert code == runner.EXIT_REFUSED and "connect" not in seen
    assert missing in caplog.text
    _no_secret_in(caplog)


def test_a_bad_config_refuses_to_start(env, tmp_path):
    path = tmp_path / "oi.yaml"
    path.write_text("scan_universe: []\n")
    assert run(config_path=path)[0] == runner.EXIT_REFUSED


def test_a_bad_risk_file_refuses_to_start(env, config_path, tmp_path):
    bad = tmp_path / "risk.yaml"
    bad.write_text("max_position_pct: 8\n")
    assert run(config_path=config_path, risk_path=bad)[0] == runner.EXIT_REFUSED


@pytest.mark.parametrize("args", [["--now"], ["--dry-run", "extra"], ["x"]])
def test_unknown_arguments_refuse_to_start(env, config_path, args):
    code, _, seen = run(args, config_path=config_path)
    assert code == runner.EXIT_REFUSED and "connect" not in seen


def test_a_recorded_failure_is_exit_one(env, config_path):
    code, conn, _ = run(config_path=config_path, answer="not json")
    assert code == runner.EXIT_FAILURE_RECORDED
    assert len(conn.written) == 1  # the failure no_action row
