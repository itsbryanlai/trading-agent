-- Feature 009 (the Risk Gate counts orders still in flight), ADR 0020. Decisions:
-- specs/009-pending-orders/research.md I1, data-model.md.

-- One row per approval that hasn't ended: Execution hasn't acted on it yet (no
-- order, no refusal), or its order is submitted or partially filled (the two
-- statuses Execution's own _OPEN_STATUSES treats as open). unsettled_qty is the
-- approved quantity less what has filled; rows with nothing left are left out.
--
-- Deliberately NOT security_invoker (like reference_candidate_symbols, 0009): it
-- reads the base tables with its owner's rights, so ta_risk_gate needs no grant
-- on orders or execution_refusals. It sees quantities and ceilings it wrote
-- itself plus fill_qty, never broker ids, fill prices or refusal reasons.
CREATE VIEW in_flight_orders AS
    SELECT v.trading_day,
           v.approved_order->>'symbol' AS symbol,
           v.approved_order->>'side' AS side,
           (v.approved_order->>'qty')::numeric - coalesce(o.fill_qty, 0) AS unsettled_qty,
           (v.approved_order->>'limit_price')::numeric AS limit_price
    FROM risk_verdicts v
    LEFT JOIN orders o ON o.risk_verdict_id = v.id
    LEFT JOIN execution_refusals r ON r.risk_verdict_id = v.id
    WHERE v.verdict = 'approved'
      AND (   (o.id IS NULL AND r.id IS NULL)
           OR o.status IN ('submitted', 'partially_filled'))
      AND (v.approved_order->>'qty')::numeric - coalesce(o.fill_qty, 0) > 0;

-- Grants: exactly specs/001-data-model/contracts/role-grants.md (amended by 009).
-- The Assistant and dashboard read everything (Constitution VII).
GRANT SELECT ON in_flight_orders TO ta_risk_gate, ta_assistant, ta_dashboard;
