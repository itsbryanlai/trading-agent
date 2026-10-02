"""When an article is about a symbol (specs/007-research-agent spec Clarifications
2026-10-02; research R6): tagged, naming the company, or giving the ticker in a
standard form."""

from __future__ import annotations

import pytest

from tests.unit.research.support import THU_0830, article, config
from trading_agent.research.answer import cites, company_name
from trading_agent.research.selection import select


def one(headline="", summary="", related=()):
    raw = article("x", related=related, summary=summary)
    raw = raw.__class__(**{**raw.__dict__, "headline": headline or raw.headline})
    (art,) = select([raw], {}, THU_0830, config()).articles
    return art


@pytest.mark.parametrize(
    ("listing", "name"),
    [
        ("APPLE INC", "APPLE"),
        ("MICROSOFT CORP", "MICROSOFT"),
        ("AMERICAN AIRLINES GROUP INC", "AMERICAN AIRLINES"),
        ("BERKSHIRE HATHAWAY INC-CL B", "BERKSHIRE HATHAWAY"),
        ("THE HOME DEPOT INC", "HOME DEPOT"),
        ("Alphabet Inc. Class A", "ALPHABET"),
        ("AT&T INC", None),  # "AT T": too short to match safely
        ("GE AEROSPACE", "GE AEROSPACE"),
        ("", None),
        ("INC", None),
    ],
)
def test_company_names_drop_corporate_words(listing, name):
    assert company_name(listing) == name


@pytest.mark.parametrize(
    "text",
    [
        "Apple beats estimates",
        "Shares of APPLE rose",
        "apple's new phone",
        "Why $AAPL is up",
        "Apple Inc. (AAPL) reports",
        "Apple (NASDAQ: AAPL) reports",
        "listed as NASDAQ:AAPL today",
    ],
)
def test_an_article_naming_the_company_or_its_ticker_cites_it(text):
    assert cites(one(headline=text), "AAPL", "APPLE INC")


@pytest.mark.parametrize(
    "text",
    [
        "Applebee's opens new stores",  # whole words only
        "Pineapple prices rise",
        "AAPL mentioned bare",  # a bare ticker isn't enough
        "The MSFT and AAPLX funds",
        "Microsoft beats estimates",
    ],
)
def test_other_text_does_not(text):
    assert not cites(one(headline=text), "AAPL", "APPLE INC")


def test_the_summary_counts_as_well_as_the_headline():
    assert cites(one(headline="Markets today", summary="Apple led gains."), "AAPL", "APPLE INC")


def test_a_tag_is_still_enough():
    assert cites(one(headline="Markets today", related=("AAPL",)), "AAPL", "")


def test_short_names_rely_on_tags_or_ticker_forms():
    art = one(headline="AT T raises its dividend")
    assert not cites(art, "T", "AT&T INC")
    assert cites(one(headline="AT&T (NYSE: T) raises its dividend"), "T", "AT&T INC")
    assert not cites(one(headline="T bills rally"), "T", "AT&T INC")


def test_share_class_tickers_are_matched_literally():
    assert cites(one(headline="Buying $BRK.B"), "BRK.B", "BERKSHIRE HATHAWAY INC-CL B")
    assert not cites(one(headline="Buying $BRKXB"), "BRK.B", "")


def test_naming_the_company_is_primary_even_when_also_tagged():
    from trading_agent.research.answer import relevance

    assert relevance(one(headline="Apple beats", related=("AAPL",)), "AAPL", "APPLE INC") == (
        "primary"
    )
    assert relevance(one(headline="Chip stocks rally", related=("AAPL",)), "AAPL", "") == (
        "secondary"
    )
    assert relevance(one(headline="Chip stocks rally"), "AAPL", "APPLE INC") is None


@pytest.mark.parametrize(
    ("symbol", "listing", "headline"),
    [
        ("AI", "C3.AI INC-A", "Artificial intelligence (AI) spending rises"),
        ("TGT", "TARGET CORP", "Analysts lift their price target"),
        ("NWSA", "NEWS CORP - CLASS A", "Markets react to the news"),
        ("SQ", "BLOCK INC", "A block of shares changed hands"),
    ],
)
def test_ordinary_words_dont_make_a_source_primary(symbol, listing, headline):
    """Review M2."""
    from trading_agent.research.answer import relevance

    assert relevance(one(headline=headline), symbol, listing) is None
    assert relevance(one(headline=headline, related=(symbol,)), symbol, listing) == "secondary"


def test_short_tickers_still_count_as_dollar_or_exchange_forms():
    from trading_agent.research.answer import relevance

    assert relevance(one(headline="Why $AI jumped"), "AI", "C3.AI INC-A") == "primary"
    assert relevance(one(headline="C3 (NYSE: AI) jumped"), "AI", "C3.AI INC-A") == "primary"


def test_dot_com_names_match_without_the_com():
    from trading_agent.research.answer import company_name, relevance

    assert company_name("AMAZON.COM INC") == "AMAZON"
    assert relevance(one(headline="Amazon cuts prices"), "AMZN", "AMAZON.COM INC") == "primary"


def test_a_ticker_of_three_or_more_letters_in_parentheses_is_primary_alone():
    from trading_agent.research.answer import relevance

    assert relevance(one(headline="Shares (AAPL) rose"), "AAPL", "") == "primary"
