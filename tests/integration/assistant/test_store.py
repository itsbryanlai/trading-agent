"""The Assistant's store against a real database (specs/013 tasks T010; research A15).

The store connects as a login created here, a member of ta_assistant only. The data is built
with the storage factories and committed to a database of this module's own.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from tests.integration.storage import chain
from trading_agent.assistant.menu import ENTRIES, ValidChoice
from trading_agent.assistant.store import ET, PLANS, PostgresStore, StoreError
from trading_agent.storage.migrate import apply_migrations

SGT = ZoneInfo("Asia/Singapore")
LONG = "x" * 400


@pytest.fixture(scope="module")
def world(make_database, server_url):
    url = make_database()
    apply_migrations(url)
    login, password = f"ta_test_assistant_{secrets.token_hex(4)}", secrets.token_hex(8)
    admin = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE ta_assistant").format(
            sql.Identifier(login), sql.Literal(password)
        )
    )
    ids = _seed(admin)
    yield {
        "admin": admin,
        "admin_url": url,
        "login_url": make_conninfo(url, user=login, password=password),
        "ids": ids,
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def _seed(conn) -> dict:
    ids = {}
    ids["r_research"] = chain.insert_report(conn, "research", "AAPL")
    ids["r_oi"] = chain.insert_report(conn, "opportunistic_identifier", "MU")
    ids["d1"] = chain.insert_decision(conn, [ids["r_research"]], "AAPL")
    ids["d2"] = chain.insert_decision(conn, [ids["r_oi"]], "MU")
    conn.execute("UPDATE decisions SET reasoning_md = %s WHERE id = %s", (LONG, ids["d1"]))
    ids["v1"] = chain.insert_verdict(conn, ids["d1"], approved=True)
    ids["o1"] = chain.insert_order(conn, ids["v1"])
    conn.execute("UPDATE orders SET broker_reason = %s WHERE id = %s", (LONG, ids["o1"]))
    ids["v2"] = chain.insert_verdict(conn, ids["d2"], approved=False)
    ids["d3"] = chain.insert_decision(conn, [], "AAPL")
    ids["v3"] = chain.insert_verdict(conn, ids["d3"], approved=True)
    conn.execute(
        "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
        "VALUES (%s, 'trading_paused', %s::jsonb, now())",
        (ids["v3"], json.dumps({"note": LONG})),
    )
    ids["t1"] = chain.insert_trigger(conn, "MU")
    conn.execute("UPDATE stop_loss_triggers SET observed_at = now() WHERE id = %s", (ids["t1"],))
    ids["v4"] = chain.insert_verdict(conn, None, approved=True, trigger_id=ids["t1"])
    ids["d5"] = chain.insert_decision(conn, [], "AAPL")
    ids["v5"] = chain.insert_verdict(conn, ids["d5"], approved=True)  # in flight: no outcome yet
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('NVDA', 3, 100)")
    conn.execute(
        "INSERT INTO account_snapshots (equity, cash, buying_power, taken_at) VALUES"
        " (1000, 500, 900, now() - interval '1 hour'), (1100, 600, 950, now())"
    )
    conn.execute(
        "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,"
        " per_agent_attribution) VALUES ((now() AT TIME ZONE 'America/New_York')::date, 1, 2, %s,"
        " '{}'::jsonb)",
        (LONG,),
    )
    conn.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, outcome, detail)"
        " VALUES ('research', (now() AT TIME ZONE 'America/New_York')::date, 'scheduled', 's1',"
        " 'skipped', %s)",
        (LONG,),
    )
    conn.execute(
        "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic,"
        " market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) VALUES"
        " ('TSLA', '2026-01-02', 'common_stock', 'XNAS', 1, 1, 1),"
        " ('TSLA', '2026-01-03', 'common_stock', 'XNAS', 2, 2, 2)"
    )
    return ids


def store(world, *, max_rows=50, text=1500, timeout=10, url_key="login_url"):
    return PostgresStore(None, world[url_key], SGT, timeout, max_rows, text)


def choice(name, *, symbol="", agent="", section=""):
    now = datetime.now(UTC)
    dated = ENTRIES[name].default_days is not None
    return ValidChoice(
        name, symbol, agent, section,
        date.today() if dated else None, date.today() if dated else None,
        now - timedelta(days=3) if dated else None, now + timedelta(days=1) if dated else None,
    )


def past(name, days_ago):
    now = datetime.now(UTC)
    start = now - timedelta(days=days_ago)
    end = start + timedelta(days=1)
    return ValidChoice(name, "", "", "", start.date(), start.date(), start, end)


def run(world, name, **kw):
    options = {k: kw.pop(k) for k in ("max_rows", "text") if k in kw}
    (result,) = store(world, **options).run([choice(name, **kw)])
    return result


def parts(result, tag):
    return [row for row in result.rows if row.get("part") == tag]


def test_every_database_entry_runs_and_returns_its_fields(world):
    assert set(PLANS) == set(ENTRIES) - {"settings"}
    expected = {
        "portfolio_now": {"part"},
        "account_history": {"equity", "cash", "taken_at"},
        "decisions": {"id", "generated_at", "symbol", "direction", "size_pct", "quote_at_decision",
                      "quote_time", "reasoning_md", "verdict", "rejection_rule", "evaluated_at",
                      "order_status", "fill_price", "fill_qty", "order_updated_at",
                      "cited_reports"},
        "reports": {"id", "generated_at", "agent", "symbol", "direction", "conviction",
                    "suggested_size_pct", "expires_at", "status", "rationale_md", "sources"},
        "orders": {"part"},
        "stop_losses": {"id", "symbol", "observed_price", "observed_at", "verdict",
                        "rejection_rule", "evaluated_at", "order_status", "fill_price",
                        "fill_qty"},
        "journal": {"trading_day", "equity_open", "equity_close", "summary_md",
                    "per_agent_attribution", "written_at"},
        "agent_runs": {"id", "agent", "trading_day", "reason", "slot_key", "slot_at",
                       "started_at", "finished_at", "outcome", "detail"},
        "universe": {"symbol", "trading_day", "security_type", "exchange_mic", "market_cap_usd",
                     "avg_daily_dollar_volume_usd", "share_price_usd", "fetched_at"},
    }
    for name, keys in expected.items():
        result = run(world, name, symbol="TSLA" if name == "universe" else "")
        assert result.name == name and not result.cut
        assert result.rows, name
        for row in result.rows:
            if keys == {"part"}:
                continue
            assert set(row) == keys, name


def test_portfolio_now_has_positions_account_and_state(world):
    result = run(world, "portfolio_now")
    (position,) = parts(result, "position")
    assert set(position) == {"part", "symbol", "qty", "avg_entry_price", "updated_at"}
    (account,) = parts(result, "account_snapshot")
    assert account["equity"] == "1100.00"  # the latest snapshot
    assert set(account) == {"part", "equity", "cash", "buying_power", "taken_at"}
    (state,) = parts(result, "system_state")
    assert set(state) == {
        "part", "trading_paused", "daily_loss_halt_active", "daily_starting_equity",
        "current_trading_date", "updated_at", "halt_triggered_on", "baseline_trading_day",
    }
    assert state["trading_paused"] is False


def test_orders_has_orders_refusals_and_in_flight(world):
    result = run(world, "orders")
    (order,) = parts(result, "order")
    assert (order["symbol"], order["side"], order["qty"]) == ("AAPL", "buy", "10")
    assert set(order) == {"part", "id", "submitted_at", "symbol", "side", "qty", "limit_price",
                          "status", "fill_price", "fill_qty", "broker_reason", "updated_at"}
    (refusal,) = parts(result, "execution_refusal")
    assert refusal["reason"] == "trading_paused"
    assert set(refusal) == {"part", "id", "refused_at", "reason", "details", "symbol", "side",
                            "qty"}
    flight = parts(result, "in_flight_order")
    assert {row["symbol"] for row in flight} == {"AAPL"} and len(flight) >= 2  # v1 and v5
    assert set(flight[0]) == {"part", "trading_day", "symbol", "side", "unsettled_qty",
                              "limit_price"}


def test_decisions_carry_verdict_order_and_cited_reports(world):
    result = run(world, "decisions")
    by_id = {row["id"]: row for row in result.rows}
    d1 = by_id[str(world["ids"]["d1"])]
    assert d1["verdict"] == "approved" and d1["order_status"] == "submitted"
    assert d1["cited_reports"] == [
        {"id": str(world["ids"]["r_research"]), "agent": "research", "direction": "buy"}
    ]
    d2 = by_id[str(world["ids"]["d2"])]
    assert d2["verdict"] == "rejected" and d2["rejection_rule"] == "max_position_pct"
    assert d2["order_status"] is None


def test_filters_by_symbol_and_agent(world):
    assert {r["symbol"] for r in run(world, "decisions", symbol="MU").rows} == {"MU"}
    assert {r["agent"] for r in run(world, "reports", agent="research").rows} == {"research"}
    assert {r["symbol"] for r in run(world, "stop_losses", symbol="MU").rows} == {"MU"}
    assert run(world, "stop_losses", symbol="AAPL").rows == []
    assert parts(run(world, "orders", symbol="MU"), "order") == []
    assert [r["agent"] for r in run(world, "agent_runs", agent="research").rows] == ["research"]
    assert run(world, "agent_runs", agent="portfolio_manager").rows == []


def test_stop_loss_trigger_has_its_verdict(world):
    (row,) = run(world, "stop_losses").rows
    assert row["verdict"] == "approved" and row["order_status"] is None


def test_the_span_filters_on_the_named_column(world):
    (result,) = store(world).run([past("decisions", 9)])
    assert result.rows == []


def test_trading_day_entries_use_the_et_dates_of_the_span(world):
    et_today = datetime.now(UTC).astimezone(ET).date()
    assert store(world).run([past("journal", 9)])[0].rows == []
    assert [r["trading_day"] for r in run(world, "journal").rows] == [et_today.isoformat()]


def test_universe_returns_the_latest_row(world):
    (row,) = run(world, "universe", symbol="TSLA").rows
    assert row["trading_day"] == "2026-01-03"
    assert run(world, "universe", symbol="NOPE").rows == []


def test_timestamps_come_back_in_both_zones(world):
    row = run(world, "decisions", symbol="MU").rows[0]
    stamp = row["generated_at"]
    assert set(stamp) == {"owner", "et"}
    assert stamp["owner"].endswith("+08:00")
    assert stamp["et"][-6:] in ("-04:00", "-05:00")
    assert datetime.fromisoformat(stamp["owner"]) == datetime.fromisoformat(stamp["et"])
    assert run(world, "decisions").parameters["span"]["start"]["owner"].endswith("+08:00")


def test_cut_is_set_at_max_rows_plus_one(world):
    exact = run(world, "decisions", max_rows=4)  # four decisions exist
    assert len(exact.rows) == 4 and not exact.cut
    more = run(world, "decisions", max_rows=3)
    assert len(more.rows) == 3 and more.cut
    newest = run(world, "decisions", max_rows=50).rows[0]["generated_at"]["owner"]
    assert run(world, "decisions", max_rows=1).rows[0]["generated_at"]["owner"] == newest


def test_untrusted_text_is_cut_to_the_cap(world):
    decisions = run(world, "decisions", text=100)
    assert {len(r["reasoning_md"]) for r in decisions.rows} == {100, len("both analysts converged")}
    assert len(run(world, "journal", text=100).rows[0]["summary_md"]) == 100
    assert len(run(world, "agent_runs", text=100).rows[0]["detail"]) == 100
    orders = run(world, "orders", text=100)
    assert len(parts(orders, "order")[0]["broker_reason"]) == 100
    details = parts(orders, "execution_refusal")[0]["details"]
    assert len(details) == 100 and details.startswith('{"note":"xxx')
    sources = run(world, "reports", text=5).rows[0]["sources"]
    assert all(len(v) <= 5 for v in sources[0].values() if isinstance(v, str))


def test_sources_keep_only_the_known_keys(world):
    row = run(world, "reports", agent="research").rows[0]
    assert row["sources"][0]["url"] == "https://example.com/aapl-q3"
    assert set(row["sources"][0]) <= {"title", "publisher", "url", "relevance", "published_at"}
    assert row["status"] == "consumed"


def test_known_symbols_come_from_every_source(world):
    assert store(world).known_symbols() == {"AAPL", "MU", "NVDA", "TSLA"}


def test_known_returns_only_the_asked_symbols_that_exist(world):
    live = store(world)
    assert live.known(["AAPL", "NVDA", "ZZZZ"]) == {"AAPL", "NVDA"}
    assert live.known([]) == set()


def test_read_question_looks_up_validates_and_reads_in_one_connection_and_transaction(world):
    connections, seen = [], []
    real = PostgresStore(None, world["login_url"], SGT, 10, 50, 1500)

    def counting(url):
        connection = psycopg.connect(url, row_factory=dict_row)
        connections.append(connection)
        return connection

    counted = PostgresStore(counting, world["login_url"], SGT, 10, 50, 1500)

    def validate(known):
        seen.append(set(known))
        return [choice("decisions", symbol="AAPL")], []

    valid, dropped, results = counted.read_question({"AAPL", "ZZZZ"}, validate)
    assert len(connections) == 1 and seen == [{"AAPL"}] and dropped == []
    assert [c.name for c in valid] == ["decisions"] and [r.name for r in results] == ["decisions"]
    assert real.run([choice("decisions", symbol="AAPL")])[0].rows == results[0].rows


def test_read_question_skips_settings_and_keeps_the_order(world):
    def validate(known):
        return [choice("settings", section="risk"), choice("portfolio_now")], []

    valid, _, results = store(world).read_question({"AAPL"}, validate)
    assert [c.name for c in valid] == ["settings", "portfolio_now"]
    assert [r.name for r in results] == ["portfolio_now"]


def test_the_transaction_is_read_only_even_for_a_role_that_could_write(world):
    admin = store(world, url_key="admin_url")
    with pytest.raises(StoreError):
        admin._in_transaction(
            lambda cur: cur.execute("INSERT INTO positions (symbol, qty, avg_entry_price)"
                                    " VALUES ('ZZZ', 1, 1)")
        )
    assert world["admin"].execute("SELECT count(*) AS n FROM positions").fetchone()["n"] == 1


def test_the_login_cannot_write(world):
    with pytest.raises(StoreError):
        store(world)._in_transaction(
            lambda cur: cur.execute("UPDATE system_state SET trading_paused = true")
        )


def test_the_statement_timeout_applies(world):
    with pytest.raises(StoreError):
        store(world, timeout=1)._in_transaction(lambda cur: cur.execute("SELECT pg_sleep(3)"))


def test_a_failed_connection_is_a_store_error_without_the_url(world):
    bad = PostgresStore(None, "postgresql://nobody:secret-pw@127.0.0.1:1/x", SGT, 5, 50, 100)
    with pytest.raises(StoreError) as caught:
        bad.known_symbols()
    assert "secret-pw" not in str(caught.value) and "secret-pw" not in repr(bad)


def test_settings_is_not_a_database_entry(world):
    with pytest.raises(ValueError):
        store(world).run([choice("settings", section="risk")])
