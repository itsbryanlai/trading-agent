"""Choosing articles deterministically (specs/007-research-agent research R4)."""

from __future__ import annotations

import random
from datetime import timedelta

from tests.unit.research.support import (
    MON_0830,
    PREVIOUS_CLOSE,
    THU_0830,
    article,
    config,
)
from trading_agent.research.selection import select, window_start


def test_window_starts_at_the_previous_close():
    assert window_start(THU_0830) == PREVIOUS_CLOSE


def test_articles_before_the_previous_close_are_excluded():
    before = article("before", at=PREVIOUS_CLOSE - timedelta(minutes=1))
    after = article("after", at=PREVIOUS_CLOSE + timedelta(minutes=1))
    chosen = select([before, after], {}, THU_0830, config())
    assert [a.url for a in chosen.articles] == [after.url]
    assert chosen.in_window == 1


def test_future_dated_articles_are_excluded():
    future = article("future", at=THU_0830 + timedelta(minutes=5))
    assert select([future], {}, THU_0830, config()).articles == ()


def test_monday_covers_the_weekend():
    saturday = article("sat", at=MON_0830 - timedelta(days=2))
    assert [a.url for a in select([saturday], {}, MON_0830, config()).articles] == [saturday.url]


def test_general_news_is_capped_newest_first():
    items = [article(f"g{i}", at=THU_0830 - timedelta(minutes=i)) for i in range(30)]
    chosen = select(items, {}, THU_0830, config(general_news_max_articles=20))
    assert [a.url for a in chosen.articles] == [items[i].url for i in range(20)]


def test_company_news_is_capped_per_symbol_in_watchlist_order():
    msft = [article(f"m{i}", at=THU_0830 - timedelta(minutes=i)) for i in range(7)]
    aapl = [article(f"a{i}", at=THU_0830 - timedelta(minutes=i)) for i in range(2)]
    cfg = config(watchlist=("MSFT", "AAPL"), articles_per_symbol=5)
    chosen = select([], {"AAPL": aapl, "MSFT": msft}, THU_0830, cfg)
    assert [a.url for a in chosen.articles] == [x.url for x in msft[:5] + aapl]


def test_ties_break_on_the_url():
    same = THU_0830 - timedelta(hours=1)
    b, a = article("b", at=same), article("a", at=same)
    assert [x.url for x in select([b, a], {}, THU_0830, config()).articles] == [a.url, b.url]


def test_identifiers_follow_the_final_order():
    general = [article("g")]
    cfg = config(watchlist=("MSFT",))
    chosen = select(general, {"MSFT": [article("m")]}, THU_0830, cfg)
    assert [(a.id, a.url) for a in chosen.articles] == [
        ("A1", general[0].url),
        ("A2", "https://news.example.com/m"),
    ]


def test_a_feed_symbol_is_added_to_the_articles_tags():
    chosen = select([], {"MSFT": [article("m", related=())]}, THU_0830, config(watchlist=("MSFT",)))
    assert chosen.articles[0].related == ("MSFT",)


def test_the_same_url_in_two_feeds_is_kept_once_as_the_general_copy():
    shared = article("shared", related=("AAPL",))
    company_copy = article("shared", related=("MSFT",))
    cfg = config(watchlist=("MSFT",))
    chosen = select([shared], {"MSFT": [company_copy]}, THU_0830, cfg)
    assert len(chosen.articles) == 1
    only = chosen.articles[0]
    assert only.id == "A1" and only.related == ("AAPL", "MSFT")


def test_summaries_are_cut_to_the_limit():
    long = article("long", summary="x" * 5000)
    chosen = select([long], {}, THU_0830, config(article_summary_max_chars=100))
    assert len(chosen.articles[0].summary) == 100


def test_the_same_news_in_any_order_gives_the_same_selection():
    general = [article(f"g{i}", at=THU_0830 - timedelta(minutes=i % 4)) for i in range(12)]
    msft = [article(f"m{i}", at=THU_0830 - timedelta(minutes=i % 3)) for i in range(8)]
    cfg = config(watchlist=("MSFT",))
    first = select(general, {"MSFT": msft}, THU_0830, cfg)
    shuffled_general, shuffled_msft = general[:], msft[:]
    random.Random(7).shuffle(shuffled_general)
    random.Random(8).shuffle(shuffled_msft)
    assert select(shuffled_general, {"MSFT": shuffled_msft}, THU_0830, cfg) == first


def test_a_symbol_with_no_company_news_contributes_nothing():
    chosen = select([article("g")], {}, THU_0830, config(watchlist=("MSFT",)))
    assert [a.id for a in chosen.articles] == ["A1"]
