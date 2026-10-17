"""The Opportunistic Identifier (specs/011-opportunistic-identifier; ADR 0002, 0016-0018).

Scans an owner-supplied list of US names for undervaluation and turns what it finds
into buy reports for the Portfolio Manager. It writes only its own buy reports: no
portfolio, no decisions, no broker. Everything the model says is checked before it is
written.
"""
