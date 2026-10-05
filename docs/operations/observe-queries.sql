-- Observation and post-deploy queries for feature 010 (observe-only deployment).
--
-- Run these as ta_owner_read_login (a member of ta_dashboard, read-only), for
-- example by pasting a block into psql. Nothing here writes. Every statement
-- reads only objects ta_dashboard may read (specs/001-data-model/contracts/role-grants.md).
--
-- File format, relied on by tests/integration/storage/test_observe_queries.py:
-- statements end with a semicolon, and comment lines start with two dashes.
-- Keep semicolons out of comments and string literals.
--
-- "Today" is the New York trading day, the same day the Risk Gate and the
-- orchestrator use: (now() AT TIME ZONE 'America/New_York')::date.


-- ===========================================================================
-- 1. Today's reports
-- ===========================================================================
SELECT generated_at, agent, symbol, direction, conviction, status
FROM reports_with_status
WHERE (generated_at AT TIME ZONE 'America/New_York')::date
    = (now() AT TIME ZONE 'America/New_York')::date
ORDER BY generated_at;


-- ===========================================================================
-- 2. Today's decisions, with the reports each one cites
-- ===========================================================================
SELECT d.generated_at, d.symbol, d.direction, d.size_pct, d.quote_at_decision,
       count(dr.report_id) AS cited_reports,
       array_agg(r.agent || ':' || r.direction ORDER BY r.generated_at)
           FILTER (WHERE r.id IS NOT NULL) AS cited
FROM decisions d
LEFT JOIN decision_reports dr ON dr.decision_id = d.id
LEFT JOIN reports r ON r.id = dr.report_id
WHERE (d.generated_at AT TIME ZONE 'America/New_York')::date
    = (now() AT TIME ZONE 'America/New_York')::date
GROUP BY d.id
ORDER BY d.generated_at;


-- ===========================================================================
-- 3. Each decision's verdict and reason
-- ===========================================================================
SELECT d.generated_at, d.symbol, d.direction, v.verdict, v.rejection_rule,
       v.evaluated_at, v.config_version
FROM decisions d
JOIN risk_verdicts v ON v.decision_id = d.id
WHERE (d.generated_at AT TIME ZONE 'America/New_York')::date
    = (now() AT TIME ZONE 'America/New_York')::date
ORDER BY d.generated_at;


-- ===========================================================================
-- 4. Decisions with no verdict yet (should be empty a minute after each pass)
-- ===========================================================================
SELECT d.id, d.generated_at, d.symbol, d.direction
FROM decisions d
LEFT JOIN risk_verdicts v ON v.decision_id = d.id
WHERE v.id IS NULL
ORDER BY d.generated_at;


-- ===========================================================================
-- 5. Account snapshots today (Execution writes one about every 30 minutes)
-- ===========================================================================
SELECT taken_at, equity, cash, buying_power
FROM account_snapshots
WHERE (taken_at AT TIME ZONE 'America/New_York')::date
    = (now() AT TIME ZONE 'America/New_York')::date
ORDER BY taken_at;


-- ===========================================================================
-- 6. Execution's refusals, with reasons
-- ===========================================================================
SELECT refused_at, reason, details, risk_verdict_id
FROM execution_refusals
ORDER BY refused_at DESC
LIMIT 50;


-- ===========================================================================
-- 7. Orchestrator run records today: outcomes and skips
-- ===========================================================================
SELECT trading_day, agent, reason, slot_key, started_at, finished_at, outcome, detail
FROM orchestrator_runs
WHERE trading_day = (now() AT TIME ZONE 'America/New_York')::date
ORDER BY coalesce(started_at, slot_at), agent;


-- ===========================================================================
-- 8. Today's instrument_reference rows (the reference-data job's output)
-- ===========================================================================
SELECT count(*) AS instrument_reference_rows_today,
       max(fetched_at) AS last_fetched_at
FROM instrument_reference
WHERE trading_day = (now() AT TIME ZONE 'America/New_York')::date;


-- ===========================================================================
-- 9. PRE-OPEN CHECK. Run right after the first deploy, before the next open.
--    Pass means both rows read ok = true. If either is false, remove the
--    execution service from .railway/railway.ts and apply before the open.
--    Also confirm zero open orders in Alpaca's own interface.
-- ===========================================================================
SELECT 'trading_paused is true' AS check_name,
       coalesce((SELECT trading_paused FROM system_state), false) AS ok,
       (SELECT trading_paused::text FROM system_state) AS observed
UNION ALL
SELECT 'positions is empty',
       NOT EXISTS (SELECT 1 FROM positions),
       (SELECT count(*)::text FROM positions);


-- ===========================================================================
-- 10. FIRST TRADING DAY CHECK. Run after the first full trading day.
--     Pass means every row reads ok = true.
--     A buy rejection counts as expected when its rule is trading_paused,
--     market_closed or decision_stale (the gate checks market_closed, then
--     decision_stale, then trading_paused).
-- ===========================================================================
SELECT 'trading_paused is true' AS check_name,
       coalesce((SELECT trading_paused FROM system_state), false) AS ok,
       (SELECT trading_paused::text FROM system_state) AS observed
UNION ALL
SELECT 'positions is empty',
       NOT EXISTS (SELECT 1 FROM positions),
       (SELECT count(*)::text FROM positions)
UNION ALL
SELECT 'an account snapshot exists from today',
       EXISTS (SELECT 1 FROM account_snapshots
               WHERE (taken_at AT TIME ZONE 'America/New_York')::date
                   = (now() AT TIME ZONE 'America/New_York')::date),
       (SELECT count(*)::text FROM account_snapshots
        WHERE (taken_at AT TIME ZONE 'America/New_York')::date
            = (now() AT TIME ZONE 'America/New_York')::date)
UNION ALL
SELECT 'at least one decision exists',
       EXISTS (SELECT 1 FROM decisions),
       (SELECT count(*)::text FROM decisions)
UNION ALL
SELECT 'every decision has a verdict',
       NOT EXISTS (SELECT 1 FROM decisions d
                   LEFT JOIN risk_verdicts v ON v.decision_id = d.id
                   WHERE v.id IS NULL),
       (SELECT count(*)::text FROM decisions d
        LEFT JOIN risk_verdicts v ON v.decision_id = d.id
        WHERE v.id IS NULL)
UNION ALL
SELECT 'no buy verdict is approved',
       NOT EXISTS (SELECT 1 FROM decisions d
                   JOIN risk_verdicts v ON v.decision_id = d.id
                   WHERE d.direction = 'buy' AND v.verdict = 'approved'),
       (SELECT count(*)::text FROM decisions d
        JOIN risk_verdicts v ON v.decision_id = d.id
        WHERE d.direction = 'buy' AND v.verdict = 'approved')
UNION ALL
SELECT 'every buy rejection is trading_paused, market_closed or decision_stale',
       NOT EXISTS (SELECT 1 FROM decisions d
                   JOIN risk_verdicts v ON v.decision_id = d.id
                   WHERE d.direction = 'buy' AND v.verdict = 'rejected'
                     AND v.rejection_rule NOT IN
                         ('trading_paused', 'market_closed', 'decision_stale')),
       (SELECT count(*)::text FROM decisions d
        JOIN risk_verdicts v ON v.decision_id = d.id
        WHERE d.direction = 'buy' AND v.verdict = 'rejected'
          AND v.rejection_rule NOT IN
              ('trading_paused', 'market_closed', 'decision_stale'))
UNION ALL
SELECT 'orders is empty',
       NOT EXISTS (SELECT 1 FROM orders),
       (SELECT count(*)::text FROM orders);
