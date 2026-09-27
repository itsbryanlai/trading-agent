# Quickstart: Validating the Risk Gate

How to prove this feature works. Behavior: [spec.md](spec.md). Interfaces:
[contracts/](contracts/). Schema changes: [data-model.md](data-model.md).

## Prerequisites

Same as feature 001 ([quickstart](../001-data-model/quickstart.md)): Python 3.12, the project
installed with dev extras (`pip install -e ".[dev]"`, now also bringing PyYAML,
exchange-calendars, and Hypothesis), and a disposable Postgres 16 for the integration suite. No
broker, model, or news credential is needed. The gate uses none.

## 1. Offline suite: the rules, with no database

```bash
python -m pytest tests/ -q
```

**Expected**: all pass. This covers:

| Check | Spec |
|---|---|
| Every rule's accept/reject cases, with exact quantities (e.g. 5% of $100,000 at $200 with a $202 ceiling gives 24 shares) | US1–US4, FR-003 to FR-012 |
| Precedence: when several rules apply, the first listed in `contracts/rejection-rules.md` is named | FR-016 |
| Hypothesis, ≥10,000 generated states: no approved buy breaches the ceiling or reserve at any fill up to its limit price | SC-001 |
| Hypothesis: the same inputs give the same result | SC-002 |
| Hypothesis: exits pass every hard stop except market closed; buys under a hard stop are rejected | SC-003 |
| Config: valid file loads; missing, unknown key, bad type, out of range each raise naming the setting | FR-014, SC-005 |
| Calendar: a weekday session, a weekend, an NYSE holiday, an early close | FR-013 |
| Baseline helper: stored value wins; else pre-open snapshot; else none | FR-013 |
| Import scan: only `trading_agent.risk` imports the config loader | spec Assumptions |

## 2. Integration suite: the gate against a real database

```bash
export TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres
python -m pytest tests/integration -m integration -q
```

**Expected**: all pass, including everything from feature 001 (the extended grants matrix covers
`stop_loss_triggers`, `instrument_reference`, and the new `ta_reference_data` role). New checks:

| Check | Spec |
|---|---|
| `evaluate_decision` writes one verdict with `trading_day` and `config_version`; a second call returns it and writes nothing | FR-001, FR-015, FR-017, FR-019 |
| `hold` decision: `None`, nothing written | FR-001 |
| Loss line crossed: halt recorded against today, buy rejected, no position touched | FR-009, SC-004 |
| First evaluation of the day records the baseline from the pre-open snapshot | FR-013 |
| Stop-loss trigger: breach approved as a full market sell even with the cap reached and the halt on; non-breach rejected | FR-012 |
| A verdict with neither a decision nor a trigger, or both, is rejected by the database | G12 |
| Two connections evaluating buys concurrently with one slot left under the cap: exactly one is approved | FR-010, G10 |
| Missing config: `RiskConfigError`, no verdict written | FR-014, SC-005 |

## 3. Manual check (optional)

After the integration suite, or against a scratch database with migrations applied:

```bash
psql "$ADMIN_DATABASE_URL" -c "\d risk_verdicts"   # decision_id nullable, stop_loss_trigger_id,
                                                  # trading_day, config_version, the XOR check
```

(No local `psql`? Use `docker exec ta-pg psql -U postgres -d <db> -c "\d risk_verdicts"`.)

## What this does *not* validate

The reference-data job, Execution's monitor and pre-open snapshot, and Execution's live check
before a buy. Those are later features. Until the reference job runs against a database, every
buy correctly fails with `universe_no_reference_data`.
