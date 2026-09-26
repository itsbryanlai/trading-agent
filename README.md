# trading-agent

An autonomous trading agent: research, strategy, execution, and risk management, designed and built collaboratively with AI coding assistants.

## Status

Design phase. This repo currently holds documentation only — no application code has been written yet. We're using this phase to nail down architecture, module boundaries, and specs before implementation starts.

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
├── prompts/                — reusable prompt templates for recurring tasks
└── .claude/
    └── commands/           — Claude Code slash commands specific to this project
```

## Where to start reading

1. [`docs/architecture/overview.md`](docs/architecture/overview.md) — the system as a whole
2. [`docs/adr/`](docs/adr/README.md) — key decisions and their rationale
3. [`docs/specs/`](docs/specs/README.md) — per-agent/service specs
4. [`docs/policy/agent-management.md`](docs/policy/agent-management.md) — how new agents get added, permissioned, and retired

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for the rules AI assistants (and contributors) follow in this repo.
