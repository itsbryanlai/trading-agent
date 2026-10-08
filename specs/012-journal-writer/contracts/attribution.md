# Contract: `journal.per_agent_attribution`

The JSON object each row stores (research J5, J9, J11). Readers: the next journal run, the dashboard's Journal view, and the Assistant. Never the PM, the Risk Gate or Execution.

Numbers are JSON strings of decimals, so no reader loses precision to floating point. Dates are ISO `YYYY-MM-DD`; times are ISO 8601 with offset, UTC.

```json
{
  "schema_version": 1,
  "trading_day": "2026-10-09",
  "previous_trading_day": "2026-10-08",
  "sessions_covered": 1,
  "missed_sessions": [],
  "holding_sessions": 5,
  "summary_version": "0.1",
  "account": {
    "return": "0.000000",
    "base": "previous_close",
    "close_taken_at": "2026-10-09T19:30:04+00:00"
  },
  "agents": {
    "research": {
      "started_on": "2026-10-09",
      "index": "100.00000000",
      "day_return": "0.000000",
      "holdings": {
        "AAPL": {"weight_pct": "5.000000", "ref_price": "229.1500", "support_session": "2026-10-09"}
      },
      "scaled_by": null,
      "late_reports": 0,
      "unpriced": [],
      "skipped_targets": [],
      "exited": {"sell": [], "holding_limit": []},
      "usage": {"written": 3, "argued": 2, "no_action": 1, "cited": 1,
                "cited_decisions": 1, "approved": 1, "filled": 0}
    }
  }
}
```

| Field | Meaning |
|---|---|
| `schema_version` | `1`. A run refuses a previous row with a version it doesn't know (`unknown_schema`). |
| `previous_trading_day` | the row this one continued from, or null on the first-ever run |
| `sessions_covered` | sessions from `previous_trading_day` (exclusive) to `trading_day` (inclusive); 1 normally, 1 on the first-ever run |
| `missed_sessions` | the sessions in between, at most 30 listed |
| `holding_sessions` | the limit in force for this row (config) |
| `account.return` | close to close; `base` is `previous_close`, or `open` on the first-ever run; null if the base is 0 |
| `account.close_taken_at` | when the snapshot used as `equity_close` was taken (research J7) |
| `agents` | one key per agent with a book: every agent in the previous row, plus any agent with a report in this row's window. Agents are never removed. |
| `started_on` | the agent's first valued day; its index was 100 then |
| `index` | 8 decimal places |
| `day_return` | the book's return over `sessions_covered`, as a fraction, 6 decimal places |
| `holdings` | after today's reports, exits and scaling: what the next run values. `ref_price` is today's close, or the last known price if unpriced. `support_session` is the session of the agent's latest buy or hold report on it. |
| `scaled_by` | the factor applied when weights summed over 100 (J5 step 6), else null |
| `late_reports` | reports applied today whose support session (research J4) is before today |
| `unpriced` | held symbols carried at their last price |
| `skipped_targets` | buy or hold targets not entered for want of a price |
| `exited` | symbols that left today, by cause |
| `usage` | research J9 |

Readers MUST treat every symbol as untrusted text and render it escaped.
