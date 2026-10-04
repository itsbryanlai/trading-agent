"""`python -m trading_agent.portfolio_manager`, User Story 4: every outcome has its exit
code, a crash is never Python's own exit 1, and the logs say what happened without ever
holding a secret, a prompt, an answer, a rationale or a reasoning
(specs/008-portfolio-manager research P9, P14; FR-007, FR-025)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from tests.fakes.market_data import FakeMarketData
from tests.fakes.model import FakeModel
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.builders import inputs, journal_row, report
from tests.unit.portfolio_manager.conftest import SECRETS
from tests.unit.portfolio_manager.service_support import Clock, answer_of, decide, market
from tests.unit.portfolio_manager.support import NOW
from trading_agent.llm.ports import ModelKeyRejected, ModelUnavailable
from trading_agent.portfolio_manager import __main__ as runner
from trading_agent.portfolio_manager.prompt import SYSTEM_PROMPT
from trading_agent.reference.provider import KeyRejected


class FakeConn:
    def close(self):
        pass


def main(
    *,
    data=None,
    quotes=None,
    model=None,
    store=None,
    clock=None,
    quotes_error=None,
    model_error=None,
):
    clock = clock or Clock()
    data = data or inputs([report("db-aapl", "AAPL")])
    store = store or FakePmStore(data)
    model = model or FakeModel(answer_of(decide()))
    quotes = quotes or market(("AAPL", "200"))

    def quotes_factory(key):
        if quotes_error:
            raise quotes_error
        return quotes

    def model_factory(provider, key, settings, *, base_url=None):
        if model_error:
            raise model_error
        return model

    code = runner.main(
        [],
        quotes_factory=quotes_factory,
        model_factory=model_factory,
        connect=lambda url, **kw: FakeConn(),
        store_factory=lambda conn: store,
        clock=clock,
        sleep=clock.sleep,
    )
    return code, store, model


# --- the exit codes ------------------------------------------------------------------------


def test_decisions_written_exit_zero(env):
    code, store, _ = main()
    assert (code, len(store.written)) == (0, 1)


def test_nothing_to_decide_exits_zero(env):
    for kwargs in (
        {"data": inputs([])},
        {"model": FakeModel(answer_of())},
        {"model": FakeModel(answer_of(decide("XYZ")))},  # everything dropped
    ):
        code, store, _ = main(**kwargs)
        assert (code, store.writes) == (0, [])


def test_a_market_closed_at_start_exits_zero_and_calls_nothing(env):
    saturday = Clock(NOW + timedelta(days=2))
    quotes = market(("AAPL", "200"))
    code, store, model = main(clock=saturday, quotes=quotes)
    assert (code, store.reads, store.writes, model.calls, quotes.calls) == (0, [], [], [], [])


def failing_quotes():
    quotes = market(("AAPL", "200"))
    quotes.fail("get_quote", "AAPL", error=KeyRejected())
    return quotes


FAILURES = [
    ("no snapshot", {"data": inputs([report("a", "AAPL")], account=False)}),
    ("quote key rejected", {"quotes": failing_quotes()}),
    ("no fresh quote", {"quotes": FakeMarketData()}),
    ("model key rejected", {"model": FakeModel(error=ModelKeyRejected(status=401))}),
    ("model unavailable", {"model": FakeModel(error=ModelUnavailable())}),
    ("unusable answer", {"model": FakeModel("not json")}),
    ("internal error", {"model": FakeModel(error=ValueError("boom"))}),
]


@pytest.mark.parametrize(("name", "kwargs"), FAILURES, ids=[f[0] for f in FAILURES])
def test_a_failed_run_exits_one_and_writes_nothing(env, name, kwargs):
    code, store, _ = main(**kwargs)
    assert (code, store.writes) == (1, [])


def test_the_market_closing_during_the_run_exits_one(env):
    clock = Clock()

    class SlowModel(FakeModel):
        def complete(self, system, user, schema):
            clock.advance(6 * 3600 + 60)
            return super().complete(system, user, schema)

    code, store, _ = main(clock=clock, model=SlowModel(answer_of(decide())))
    assert (code, store.writes) == (1, [])


def test_a_database_failure_exits_three(env):
    data = inputs([report("a", "AAPL")])
    for store in (FakePmStore(data, fail_read=True), FakePmStore(data, fail_write=True)):
        assert main(data=data, store=store)[0] == 3


# --- a crash is exit 4, never Python's 1 ---------------------------------------------------


def test_a_crash_escaping_the_run_exits_four(env, monkeypatch, caplog):
    def boom(**kwargs):
        raise RuntimeError("SENTINEL-CRASH-TEXT")

    monkeypatch.setattr(runner.service, "run", boom)
    code, store, _ = main()
    assert (code, store.writes) == (4, [])
    assert "crashed: RuntimeError" in caplog.text
    assert "SENTINEL" not in caplog.text


@pytest.mark.parametrize("which", ["quotes_error", "model_error"])
def test_a_crash_while_building_a_client_exits_four(env, which):
    code, store, _ = main(**{which: RuntimeError("no client")})
    assert (code, store.writes) == (4, [])


# --- the logs ------------------------------------------------------------------------------


def logs_of(caplog, **kwargs):
    caplog.set_level(logging.DEBUG)
    main(**kwargs)
    return caplog.text


def planted():
    source = {"title": "SENTINEL-TITLE", "publisher": "SENTINEL-PUBLISHER", "relevance": "primary"}
    return inputs(
        [report("db-aapl", "AAPL", rationale="SENTINEL-RATIONALE", sources=[source])],
        journal=[journal_row(summary="SENTINEL-JOURNAL")],
    )


def test_logs_never_hold_a_secret_a_prompt_an_answer_a_rationale_or_a_reasoning(env, caplog):
    answer = answer_of(
        decide(reasoning="SENTINEL-REASONING"), decide("XYZ", reasoning="SENTINEL-X")
    )
    text = logs_of(caplog, data=planted(), model=FakeModel(answer))
    for sentinel in ("SENTINEL", SYSTEM_PROMPT[:40], '"decisions"', *SECRETS):
        assert sentinel not in text
    assert "wrote 1 decision(s)" in text  # the run did run, and was logged


def test_a_failed_run_logs_no_answer_and_no_provider_text(env, caplog):
    raw = '{"decisions": "SENTINEL-ANSWER"}'
    text = logs_of(caplog, data=planted(), model=FakeModel(raw))
    assert "unusable_answer" in text
    assert "SENTINEL" not in text and not any(secret in text for secret in SECRETS)
    text = logs_of(caplog, data=planted(), model=FakeModel(error=ModelUnavailable("SENTINEL-HTTP")))
    assert "SENTINEL" not in text


def test_the_logs_say_what_the_run_considered_skipped_used_dropped_and_wrote(env, caplog):
    data = inputs(
        [
            report("a", "AAPL", generated_at=NOW - timedelta(minutes=2)),
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=1), consumed=True),
        ]
    )
    quotes = FakeMarketData()
    quotes.add("AAPL", current="200", quote_time=NOW - timedelta(minutes=1))
    quotes.add("MSFT", current="300", quote_time=NOW - timedelta(minutes=30))
    answer = answer_of(decide("AAPL"), decide("XYZ"))
    text = logs_of(caplog, data=data, quotes=quotes, model=FakeModel(answer))
    for line in (
        "run started (prompt v0.1, provider qwen, model qwen3.7-plus)",
        "2 reports on 2 symbols (1 already decided on); 0 held",
        "1 fresh quotes; skipped: MSFT (quote_stale)",
        "model used 1200 input and 300 output tokens",
        "2 decisions received, 1 accepted, 1 dropped",
        "dropped decision 1 (XYZ): unknown_symbol",
        "wrote 1 decision(s)",
    ):
        assert f"portfolio_manager: {line}" in text


def test_a_failure_is_logged_with_its_category_type_and_status(env, caplog):
    text = logs_of(caplog, model=FakeModel(error=ModelUnavailable("x", status=503)))
    assert "portfolio_manager: model_unavailable: ModelUnavailable (HTTP 503)" in text


def test_a_nothing_to_decide_run_says_why(env, caplog):
    text = logs_of(caplog, data=inputs([]))
    assert "portfolio_manager: nothing to decide (no unexpired report)" in text
    text = logs_of(caplog, model=FakeModel(answer_of()))
    assert "portfolio_manager: nothing to decide (the model proposed none)" in text
