"""The Assistant (specs/013-assistant; ADRs 0023, 0024 and 0025).

A read-only Telegram bot that answers the owner's questions about the portfolio, the agents'
reports and the system's settings. It reads through a fixed menu of queries under its own
database login, never writes state or places orders, explains refusals of action requests,
and removes any link the owner's data did not supply.
"""
