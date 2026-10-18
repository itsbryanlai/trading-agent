"""The client's address for the per-address delay (specs/014 tasks T012; research D8)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.dashboard.address import MAX_LENGTH, client_address


def test_one_entry():
    assert client_address({"x-forwarded-for": "203.0.113.7"}, "10.0.0.1") == "203.0.113.7"


def test_the_last_entry_wins_so_a_forged_leftmost_one_changes_nothing():
    honest = client_address({"x-forwarded-for": "203.0.113.7"}, "10.0.0.1")
    forged = client_address({"x-forwarded-for": "198.51.100.9, 203.0.113.7"}, "10.0.0.1")
    many = client_address({"x-forwarded-for": "1.1.1.1,2.2.2.2 , 3.3.3.3, 203.0.113.7"}, "p")
    assert honest == forged == many == "203.0.113.7"


def test_entries_are_stripped():
    assert client_address({"x-forwarded-for": "  203.0.113.7  "}, "p") == "203.0.113.7"


@pytest.mark.parametrize(
    "header",
    ["", "   ", ",", "198.51.100.9,", "198.51.100.9, ", " , "],
)
def test_a_blank_last_entry_falls_back_to_the_peer(header):
    assert client_address({"x-forwarded-for": header}, "10.0.0.1") == "10.0.0.1"


def test_no_header_uses_the_peer():
    assert client_address({}, "10.0.0.1") == "10.0.0.1"


def test_neither_is_unknown():
    assert client_address({}, None) == "unknown"
    assert client_address({}, "") == "unknown"
    assert client_address({"x-forwarded-for": " "}, None) == "unknown"


def test_the_header_name_is_matched_without_regard_to_case():
    assert client_address({"X-Forwarded-For": "203.0.113.7"}, "p") == "203.0.113.7"


def test_at_most_64_characters_are_kept():
    assert MAX_LENGTH == 64
    assert client_address({"x-forwarded-for": "a" * 500}, "p") == "a" * 64
    assert client_address({}, "b" * 500) == "b" * 64


@given(st.text(max_size=300), st.one_of(st.none(), st.text(max_size=300)))
def test_the_result_is_never_empty_and_never_long(header, peer):
    result = client_address({"x-forwarded-for": header}, peer)
    assert 1 <= len(result) <= 64
