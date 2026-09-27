# Contract: `config/risk.yaml`

The one file of risk limits. It is edited only through code review (Constitution Principle V). No
agent may write it, and the Portfolio Manager must never read it. Loading rules are from
[research.md](../research.md) G6.

## Schema

```yaml
max_position_pct: 8                 # (0, 100]  per-symbol ceiling, share of equity
cash_reserve_pct: 20                # [0, 100)  cash floor, share of equity
stop_loss_pct: 20                   # (0, 100)  exit a position this far below avg entry (ADR 0010)
max_orders_per_day: 5               # int >= 0  exposure-increasing orders per trading day
daily_loss_halt_pct: 20             # (0, 100)  halt new exposure this far below the day's baseline
max_buy_price_tolerance_pct: 1      # [0, 10]   buy price ceiling above the PM's quote

universe:
  listing: us_common_equity         # only value accepted
  min_market_cap_usd: 500000000     # number >= 0
  min_avg_daily_dollar_volume_usd: 10000000   # number >= 0
  min_share_price_usd: 5            # number > 0
```

## Loading rules

- Parsed with a safe YAML loader. Values are converted to exact decimals (never floats).
- **Every key above is required**, and **no other key is allowed**, at either level. A misspelled
  limit is an error, not a silently missing one.
- Any violation raises `RiskConfigError` with the dotted path of the offending setting (e.g.
  `universe.min_share_price_usd`). The gate then approves nothing (FR-014).
- `max_buy_price_tolerance_pct` is capped at 10 so a typo can't turn the buy ceiling into no
  ceiling at all.

## Version

`config_version` = the first 12 hex characters of the SHA-256 of the file's raw bytes. It is
written onto every verdict (FR-015). Any edit, including a comment, produces a new version. That
is intentional: the version identifies the file, not just the numbers.

## Changing a limit

Follow `docs/policy/agent-management.md` → *Changing risk limits*. Loosening a limit (raising
`max_position_pct`, `stop_loss_pct`, `daily_loss_halt_pct`, `max_orders_per_day`,
`max_buy_price_tolerance_pct`; lowering `cash_reserve_pct` or a universe floor) deserves a
deliberate pause before merging.
