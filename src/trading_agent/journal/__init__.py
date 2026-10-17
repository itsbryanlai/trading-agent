"""The journal writer (specs/012-journal-writer; ADR 0022).

Runs once after each close and writes that session's `journal` row: the account's equity,
each analyst agent's virtual book valued at the close, and a fixed, bounded summary for the
Portfolio Manager. It reads the trading tables and a market-data quote; it writes only its
own row, and never sends agent-written text into the summary.
"""
