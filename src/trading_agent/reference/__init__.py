"""The universe reference-data job (ADR 0010 §3; runs its own loop per ADR 0013).

Records, once per symbol per XNYS trading day, the facts the Risk Gate's universe
check needs: security type, exchange, market cap, average daily dollar volume and
share price. It judges nothing; the gate does. It holds a read-only market-data
key and the ta_reference_data login, never a broker credential.
Behaviour: specs/004-reference-data.
"""
