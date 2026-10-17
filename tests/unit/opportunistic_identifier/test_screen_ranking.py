"""screen.shortlist: open names out first, two ordinal ranks averaged, lowest scores kept
(specs/011 research O6; spec FR-008)."""

from __future__ import annotations

from decimal import Decimal

from trading_agent.opportunistic_identifier.screen import Candidate, shortlist


def cand(symbol, move, high) -> Candidate:
    return Candidate(symbol, Decimal(str(move)), Decimal(str(high)))


# A hand-computed example. Move, largest fall first: C -0.08, E -0.08 (tie, by symbol),
# A -0.05, B -0.02, F -0.02 (tie), D 0.01 -> ranks C1 E2 A3 B4 F5 D6.
# Below the high, largest first: B 0.5, F 0.5 (tie), E 0.4, A 0.3, D 0.2, C 0.1
# -> ranks B1 F2 E3 A4 D5 C6.
# Scores: A 3.5, B 2.5, C 3.5, D 5.5, E 2.5, F 3.5.
SIX = [
    cand("A", -0.05, 0.30),
    cand("B", -0.02, 0.50),
    cand("C", -0.08, 0.10),
    cand("D", 0.01, 0.20),
    cand("E", -0.08, 0.40),
    cand("F", -0.02, 0.50),
]


def test_a_hand_computed_six_name_example():
    got = shortlist(SIX, frozenset(), 4)
    assert [c.symbol for c in got.candidates] == ["B", "E", "A", "C"]
    by_symbol = {c.symbol: c for c in shortlist(SIX, frozenset(), 6).candidates}
    assert {s: (c.rank_move, c.rank_high, c.score) for s, c in by_symbol.items()} == {
        "A": (3, 4, Decimal("3.5")),
        "B": (4, 1, Decimal("2.5")),
        "C": (1, 6, Decimal("3.5")),
        "D": (6, 5, Decimal("5.5")),
        "E": (2, 3, Decimal("2.5")),
        "F": (5, 2, Decimal("3.5")),
    }
    assert [c.symbol for c in shortlist(SIX, frozenset(), 6).candidates] == list("BEACFD")


def test_the_input_order_does_not_matter():
    assert shortlist(list(reversed(SIX)), frozenset(), 4) == shortlist(SIX, frozenset(), 4)


def test_open_names_are_left_out_first_and_counted():
    got = shortlist(SIX, frozenset({"B", "E"}), 4)
    assert got.already_open == 2 and got.eligible == 4
    assert [c.symbol for c in got.candidates] == ["A", "F", "C", "D"]
    # Ranks are of the names that remain: the open names never take a rank.
    assert {c.symbol: (c.rank_move, c.rank_high) for c in got.candidates} == {
        "C": (1, 4),
        "A": (2, 2),
        "F": (3, 1),
        "D": (4, 3),
    }


def test_open_symbols_not_among_the_candidates_are_not_counted():
    assert shortlist(SIX, frozenset({"ZZZ"}), 4).already_open == 0


def test_fewer_candidates_than_the_size_returns_them_all():
    got = shortlist(SIX[:2], frozenset(), 20)
    assert len(got.candidates) == 2 and got.eligible == 2


def test_no_candidates_gives_an_empty_shortlist():
    got = shortlist([], frozenset(), 20)
    assert (got.candidates, got.already_open, got.eligible) == ((), 0, 0)


def test_everything_open_gives_an_empty_shortlist_and_the_count():
    got = shortlist(SIX, frozenset("ABCDEF"), 20)
    assert (got.candidates, got.already_open, got.eligible) == ((), 6, 0)


def test_a_name_above_its_high_ranks_last_on_that_measure():
    names = [cand("A", -0.01, -0.04), cand("B", -0.01, 0.10)]
    got = {c.symbol: c for c in shortlist(names, frozenset(), 2).candidates}
    assert got["B"].rank_high == 1 and got["A"].rank_high == 2
