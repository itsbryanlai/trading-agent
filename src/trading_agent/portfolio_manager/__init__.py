"""The Portfolio Manager (specs/008-portfolio-manager; ADR 0002, 0011, 0016, 0018, 0019).

Turns the analysts' unexpired reports, fresh quotes and the account's state into
decisions. It writes only its own decisions and their report links; it never reads
config/risk.yaml, verdicts or orders, and it has no broker.
"""
