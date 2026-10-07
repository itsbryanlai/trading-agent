"""`--dry-run`: everything except the write, printed as JSON lines (specs/011 US4; contracts/
oi-interface.md "Invocation"; SC-005)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.unit.opportunistic_identifier.conftest import SECRETS
from tests.unit.opportunistic_identifier.support import Clock, FakeConn
from tests.unit.opportunistic_identifier.test_main import proposal, run
from trading_agent.opportunistic_identifier import __main__ as runner
from trading_agent.opportunistic_identifier import prompt


class NoWriteConn(FakeConn):
    """A connection that fails the test on any write."""

    def transaction(self):
        raise AssertionError("a dry run must not open a transaction")

    def cursor(self):
        raise AssertionError("a dry run must not write")


def two_names() -> FakeOIMarketData:
    data = FakeOIMarketData()
    data.add("AAA", current="190")
    data.add("BBB", current="184")
    data.add("ETFX", type="ETP", mic="ARCX")
    return data


def dry(config_path, *, answer=None, conn=None, **kw):
    printed: list[str] = []
    code, conn, seen = run(
        ["--dry-run"],
        config_path=config_path,
        conn=conn if conn is not None else NoWriteConn(),
        answer=answer,
        out=printed.append,
        **kw,
    )
    return code, conn, seen, printed, [json.loads(line) for line in printed]


def by_key(records, key):
    return [r[key] for r in records if key in r]


@pytest.fixture
def two(config_path):
    import yaml

    data = yaml.safe_load(config_path.read_text())
    data["scan_universe"] = ["AAA", "BBB", "ETFX"]
    config_path.write_text(yaml.safe_dump(data))
    return config_path


def test_a_dry_run_prints_every_line_kind_and_exits_zero(env, two):
    answer = {
        "proposals": [
            proposal("BBB"),
            proposal("ZZZ"),  # dropped: not shortlisted
        ]
    }
    code, _, _, _, records = dry(two, answer=answer, market=two_names())
    assert code == runner.EXIT_OK
    assert [next(iter(r)) for r in records] == [
        "slice",
        "skip",
        "shortlist",
        "shortlist",
        "would_write",
        "dropped",
        "summary",
    ]


def test_the_slice_line_names_the_run_index_batch_and_symbols(env, two):
    _, _, _, _, records = dry(two, market=two_names())
    (line,) = by_key(records, "slice")
    assert line["symbols"] == ["AAA", "BBB", "ETFX"]
    assert line["batch"] == 1 and line["batches"] == 1 and isinstance(line["run_index"], int)


def test_each_skip_names_its_symbol_and_reason(env, two):
    _, _, _, _, records = dry(two, market=two_names())
    assert by_key(records, "skip") == [{"symbol": "ETFX", "reason": "universe_listing"}]


def test_the_shortlist_lines_carry_both_ranks_and_the_score(env, two):
    _, _, _, _, records = dry(two, market=two_names())
    lines = by_key(records, "shortlist")
    assert [(s["symbol"], s["rank_move"], s["rank_high"], s["score"]) for s in lines] == [
        ("BBB", 1, 1, 1.0),
        ("AAA", 2, 2, 2.0),
    ]


def test_would_write_rows_and_dropped_proposals_are_printed(env, two):
    answer = {"proposals": [{**proposal("BBB"), "suggested_size_pct": 7.5}, proposal("ZZZ")]}
    _, _, _, _, records = dry(two, answer=answer, market=two_names())
    (row,) = by_key(records, "would_write")
    assert (row["symbol"], row["direction"], row["conviction"]) == ("BBB", "buy", 4)
    assert row["suggested_size_pct"] == "7.500" and len(row["sources"]) == 3
    assert row["rationale_md"] == "Cheap after its fall."
    assert by_key(records, "dropped") == [
        {"index": 1, "symbol": "ZZZ", "reason": "not_shortlisted"}
    ]


def test_the_summary_has_the_counts_and_the_token_use(env, two):
    _, _, _, _, records = dry(two, answer={"proposals": []}, market=two_names())
    (summary,) = by_key(records, "summary")
    assert (summary["input_tokens"], summary["output_tokens"]) == (1200, 300)
    assert summary["in_slice"] == 3 and summary["fetched"] == 2 and summary["eligible"] == 2
    assert summary["shortlisted"] == 2 and summary["skipped"] == {"universe_listing": 1}
    assert summary["failure"] is None and summary["note"] == "nothing_argued"
    assert records[-1] == {"summary": summary}


def test_a_dry_run_never_writes_even_with_a_database(env, two):
    conn = NoWriteConn(open_rows=[{"symbol": "AAA"}])
    code, conn, seen, _, records = dry(two, conn=conn, market=two_names())
    assert code == 0 and conn.written == [] and conn.closed
    assert by_key(records, "summary")[0]["already_open"] == 1
    assert "connect" in seen


def test_the_store_given_to_the_run_has_no_write_at_all(env, two, monkeypatch):
    seen_stores = []
    original = runner.OIRun

    def spy(market, model, store, *args, **kwargs):
        seen_stores.append(store)
        return original(market, model, store, *args, **kwargs)

    monkeypatch.setattr(runner, "OIRun", spy)
    dry(two, market=two_names())
    (store,) = seen_stores
    assert not hasattr(store, "write")


def test_a_dry_run_ignores_the_trading_window(env, two):
    saturday = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
    market = FakeOIMarketData(quote_time=saturday - timedelta(minutes=5))
    market.add("AAA", current="190")
    code, _, _, _, records = dry(two, market=market, clock=Clock(saturday))
    assert code == 0 and by_key(records, "summary")
    (line,) = by_key(records, "slice")
    assert line["session"] is False and "next session" in line["note"]


def test_on_an_open_day_the_slice_line_has_no_closed_day_note(env, two):
    _, _, _, _, records = dry(two, market=two_names())
    (line,) = by_key(records, "slice")
    assert line["session"] is True and "note" not in line


def test_without_a_database_variable_nothing_counts_as_open_and_it_still_runs(
    env, two, monkeypatch
):
    monkeypatch.delenv("OPPORTUNISTIC_IDENTIFIER_DATABASE_URL")
    code, _, seen, _, records = dry(two, market=two_names())
    assert code == 0 and "connect" not in seen
    assert by_key(records, "note") == ["no database: open reports not read"]
    assert by_key(records, "summary")[0]["already_open"] == 0


@pytest.mark.parametrize(
    "missing",
    [
        "OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY",
        "OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY",
        "OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL",
    ],
)
def test_the_other_variables_are_still_required(env, two, monkeypatch, caplog, missing):
    monkeypatch.delenv(missing)
    code, *_ = dry(two, market=two_names())
    assert code == runner.EXIT_REFUSED and missing in caplog.text


def test_no_line_holds_a_key_the_prompt_or_the_raw_answer(env, two):
    raw_marker = "RAW-ANSWER-MARKER"
    answer = {"proposals": [{**proposal("BBB"), "sources": raw_marker}, proposal("AAA")]}
    _, _, _, printed, records = dry(two, answer=answer, market=two_names())
    text = "\n".join(printed)
    for secret in SECRETS:
        assert secret not in text
    assert raw_marker not in text  # a dropped proposal's fields are not echoed
    assert prompt.SYSTEM_PROMPT[:60] not in text and "paper-trading" not in text
    assert by_key(records, "dropped") == [
        {"index": 0, "symbol": "BBB", "reason": "malformed_answer"}
    ]


def test_a_model_failure_prints_the_summary_and_exits_one(env, two):
    code, conn, _, _, records = dry(two, answer="not json", market=two_names())
    assert code == runner.EXIT_FAILURE_RECORDED and conn.written == []
    (summary,) = by_key(records, "summary")
    assert summary["failure"] == "unusable_answer" and by_key(records, "would_write") == [
        {
            "symbol": None,
            "direction": "no_action",
            "conviction": None,
            "suggested_size_pct": None,
            "sources": [],
            "rationale_md": by_key(records, "would_write")[0]["rationale_md"],
            "expires_at": by_key(records, "would_write")[0]["expires_at"],
        }
    ]


def test_an_empty_scan_list_still_prints_a_summary(env, config_path):
    import yaml

    data = yaml.safe_load(config_path.read_text())
    data["scan_universe"] = []
    config_path.write_text(yaml.safe_dump(data))
    code, _, _, _, records = dry(config_path)
    assert code == 0 and by_key(records, "summary")[0]["note"] == "empty_scan_universe"
    assert by_key(records, "slice")[0]["symbols"] == []


def test_model_is_built_once_and_called_once(env, two):
    model = FakeModel({"proposals": []})
    _, _, _, _, _ = dry(two, market=two_names(), model=model)
    assert len(model.calls) == 1


def test_the_two_ranks_are_printed_separately(env, two):
    data = FakeOIMarketData()
    data.add("AAA", current="190", high_52w="250")  # down 5%, 24% below its high
    data.add("BBB", current="184", high_52w="190")  # down 8%, 3% below its high
    data.add("ETFX", type="ETP", mic="ARCX")
    _, _, _, _, records = dry(two, market=data)
    lines = by_key(records, "shortlist")
    assert [(s["symbol"], s["rank_move"], s["rank_high"], s["score"]) for s in lines] == [
        ("AAA", 2, 1, 1.5),
        ("BBB", 1, 2, 1.5),
    ]
