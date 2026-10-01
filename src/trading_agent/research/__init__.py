"""The Research agent (specs/007-research-agent; ADR 0002, 0008, 0016, 0017, 0018).

Turns the day's news into reports for the Portfolio Manager. It writes only its
own `reports` rows: no prices, no portfolio, no broker. Everything the model says
is checked before it is written (`answer.py`).
"""
