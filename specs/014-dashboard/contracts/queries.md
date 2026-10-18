# Contract: the read queries

Every query the dashboard runs, all in `dashboard/store.py`, as `ta_dashboard_login`, inside one `READ ONLY` transaction per request with a statement timeout (research D12). Parameters are bound, never formatted into the SQL. The shapes below are binding; column lists may grow only with a spec change. Lists fetch `page_size + 1` rows to know whether a next page exists.

## State (every signed-in page)

```sql
SELECT s.trading_paused, s.trading_paused_changed_at,
       e.daily_loss_halt_active, e.current_trading_date
FROM system_state s CROSS JOIN system_state_effective e;
```

## Overview

```sql
SELECT symbol, qty, avg_entry_price, updated_at FROM positions ORDER BY symbol;

SELECT taken_at, equity, cash, buying_power
FROM account_snapshots ORDER BY taken_at DESC LIMIT 1;
```

Equity curve, `1d` and `1w` (every point; `:since` is the start of the range's first NYSE day, from `risk.calendar`):

```sql
SELECT taken_at, equity FROM account_snapshots
WHERE taken_at >= :since ORDER BY taken_at;
```

`1m` and `all` (the last snapshot of each NYSE trading day; `all` has no lower bound):

```sql
SELECT DISTINCT ON ((taken_at AT TIME ZONE 'America/New_York')::date) taken_at, equity
FROM account_snapshots WHERE taken_at >= :since
ORDER BY (taken_at AT TIME ZONE 'America/New_York')::date, taken_at DESC;
```

## Order history

One row per verdict, newest first, filtered by `symbol` (the decision's or trigger's) and `evaluated_at` date range:

```sql
SELECT v.id, v.evaluated_at, v.trading_day, v.verdict, v.rejection_rule, v.approved_order,
       d.id AS decision_id, d.generated_at AS decided_at, d.symbol AS d_symbol,
       d.direction, d.size_pct, d.quote_at_decision, d.quote_time,
       t.id AS trigger_id, t.symbol AS t_symbol, t.observed_price, t.observed_at,
       o.id AS order_id, o.status, o.limit_price, o.fill_qty, o.fill_price,
       o.broker_reason, o.submitted_at,
       r.reason AS refusal_reason, r.details AS refusal_details, r.refused_at
FROM risk_verdicts v
LEFT JOIN decisions d ON d.id = v.decision_id
LEFT JOIN stop_loss_triggers t ON t.id = v.stop_loss_trigger_id
LEFT JOIN orders o ON o.risk_verdict_id = v.id
LEFT JOIN execution_refusals r ON r.risk_verdict_id = v.id
WHERE (:symbol IS NULL OR COALESCE(d.symbol, t.symbol) = :symbol)
  AND (:from IS NULL OR v.evaluated_at >= :from)
  AND (:to IS NULL OR v.evaluated_at < :to)
ORDER BY v.evaluated_at DESC, v.id DESC
LIMIT :limit OFFSET :offset;
```

`:from` and `:to` are the owner-zone days' starts as UTC times; `:to` is the day after the chosen end date.

## Agent activity

Three queries with the same filters, merged in Python newest first, then paged. Each fetches `offset + page_size + 1` rows so the merge is exact.

```sql
SELECT id, agent, generated_at, symbol, direction, conviction, suggested_size_pct,
       sources, rationale_md
FROM reports WHERE … ORDER BY generated_at DESC, id DESC LIMIT :n;

SELECT d.id, d.generated_at, d.symbol, d.direction, d.size_pct, d.reasoning_md,
       d.quote_at_decision, d.quote_time,
       COALESCE(array_agg(dr.report_id) FILTER (WHERE dr.report_id IS NOT NULL), '{}') AS report_ids
FROM decisions d LEFT JOIN decision_reports dr ON dr.decision_id = d.id
WHERE … GROUP BY d.id ORDER BY d.generated_at DESC, d.id DESC LIMIT :n;

SELECT id, agent, trading_day, reason, slot_key, slot_at, started_at, finished_at,
       outcome, detail
FROM orchestrator_runs
WHERE … AND (:problems = false OR outcome IN ('failed', 'timed_out', 'skipped', 'interrupted'))
ORDER BY COALESCE(started_at, slot_at) DESC NULLS LAST, id DESC LIMIT :n;

-- only when a decision on the page cites a report that isn't on it (its owner-zone day link)
SELECT id, generated_at FROM reports WHERE id = ANY(:ids::uuid[]);
```

`agent=research` or `opportunistic_identifier` shows that agent's reports and runs; `portfolio_manager` shows decisions and PM runs; `runs` shows runs only. `problems=1` shows runs only, problem outcomes only. A decision links to its cited reports by anchor on the same page when they are shown, and otherwise by `/activity?…` for that report's day.

## Journal

```sql
SELECT trading_day, equity_open, equity_close, summary_md, per_agent_attribution
FROM journal WHERE trading_day >= :since ORDER BY trading_day DESC LIMIT :limit OFFSET :offset;
```

The chart and usage totals read every row in the range (at most about 260 a year), not just the page:

```sql
SELECT trading_day, per_agent_attribution FROM journal
WHERE trading_day >= :since ORDER BY trading_day;
```

## Toggle evidence (ADR 0028)

```sql
SELECT outcome, detail, slot_key, COALESCE(started_at, slot_at) AS at
FROM orchestrator_runs WHERE agent = 'portfolio_manager'
ORDER BY COALESCE(started_at, slot_at) DESC NULLS LAST, id DESC LIMIT 1;
```

Resume needs `outcome = 'skipped' AND detail = 'trading paused'` (research D23).

## Health check

```sql
SELECT 1;
```

## `--check`

```sql
SELECT current_user AS login, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles WHERE rolname = current_user;

SELECT rolname FROM pg_roles
WHERE rolname LIKE 'ta\_%' AND rolname NOT LIKE '%\_login'
  AND pg_has_role(current_user, oid, 'MEMBER');
```

The read login must be exactly `ta_dashboard_login`, hold no `rol*` privilege above, and be a member (directly or not) of exactly `ta_dashboard` among the `ta_*` group roles; the control login likewise `ta_dashboard_control_login` and `ta_dashboard_control` (research D16).

## Indexes

`account_snapshots.taken_at` and `stop_loss_triggers.observed_at` have indexes; whether the other ordering columns do is checked in implementation. At a year of rows (about 3,500 snapshots, a few thousand reports, decisions and verdicts, 250 journal rows) a sequential scan is still fast, so no index is added unless the integration test that times each query on a seeded year shows one is needed (SC-007). Adding one would go into migration `0015` and be flagged.
