"""The owner's dashboard: a server-rendered web service with one guarded write.

Specs: specs/014-dashboard. The dashboard is the system's only public address, so it signs
the owner in (ADR 0026), reads through a read-only login, and writes only the pause flag
through a separate login; the pause's start time is recorded by the database (ADR 0027).
"""
