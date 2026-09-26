# Architecture overview

Status: not yet written. This is a placeholder created while scaffolding the repo.

To be filled in during the specs-design session:

- What the system does end to end (data in → decision → order out)
- Major components and their boundaries (data ingestion, strategy/signal generation, risk management, execution, monitoring)
- How components talk to each other (in-process calls, message queue, REST, etc.)
- Where state lives (positions, orders, market data cache)
- Deployment shape (single process, services, scheduled jobs)

Each major decision behind this overview should have a corresponding entry in [`docs/adr/`](../adr/README.md).
