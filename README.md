# trading-agent

An autonomous trading agent: research, strategy, execution, and risk management, designed and built collaboratively with AI coding assistants.

## Status

Implementation phase ([ADR 0009](docs/adr/0009-implementation-phase-started.md)). Built so far: the shared data model — the Postgres schema, per-component database roles, and the grants that enforce what each component may read and write ([`specs/001-data-model/`](specs/001-data-model/spec.md)). Features are built foundation-first, one spec-kit feature at a time.

## Repo layout

```
trading-agent/
├── README.md              — you are here
├── CLAUDE.md               — repo-wide rules for AI coding assistants working in this repo
├── docs/
│   ├── architecture/       — system design: components, data flow, boundaries
│   ├── specs/              — per-module specs (what to build, inputs/outputs, edge cases)
│   ├── adr/                — Architecture Decision Records (why we chose X over Y)
│   ├── policy/             — rules for creating, granting, and retiring agents
│   └── research/           — strategy research, market notes, backtesting findings
├── specs/                  — spec-kit features: spec, plan, contracts, tasks (one dir per feature)
├── src/trading_agent/      — application code
│   └── storage/            — connection helper, migration runner, SQL migrations
├── tests/
│   ├── unit/               — no network, no database
│   └── integration/        — against a disposable Postgres (TEST_DATABASE_URL)
├── prompts/                — reusable prompt templates for recurring tasks
└── .claude/
    └── commands/           — Claude Code slash commands specific to this project
```

## Development

Python 3.12 and, for the integration suite, Docker.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Tests.** The default run is offline and needs nothing else:

```bash
python -m pytest tests/ -q
```

The integration suite needs a *disposable* Postgres 16 whose user is a superuser — it creates
cluster-wide `ta_*` roles and throwaway databases, and switches into each role to prove what the
database allows. Never point it at the Railway database.

```bash
docker run --rm -d --name ta-pg -e POSTGRES_PASSWORD=dev -p 5433:5432 postgres:16
export TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres
python -m pytest tests/integration -m integration -q
```

Unset `TEST_DATABASE_URL` and the suite reports *skipped*, not failed.

**Migrations** are forward-only numbered SQL files in `src/trading_agent/storage/migrations/`,
applied as a deploy step with the admin credential — never on a component's startup, since no
component's role can change the schema or its own permissions:

```bash
ADMIN_DATABASE_URL=... python -m trading_agent.storage.migrate
```

A committed migration is never edited; a change is a new file. Each component then connects
through its own login role, created once by an operator as a member of its group role — see
[`specs/001-data-model/quickstart.md`](specs/001-data-model/quickstart.md) for the full
walkthrough and [`contracts/role-grants.md`](specs/001-data-model/contracts/role-grants.md) for
what each role may do.

```bash
ruff check src tests && ruff format --check src tests
```

## Where to start reading

1. [`docs/architecture/overview.md`](docs/architecture/overview.md) — the system as a whole
2. [`docs/adr/`](docs/adr/README.md) — key decisions and their rationale
3. [`docs/specs/`](docs/specs/README.md) — per-agent/service specs
4. [`docs/policy/agent-management.md`](docs/policy/agent-management.md) — how new agents get added, permissioned, and retired

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for the rules AI assistants (and contributors) follow in this repo.
