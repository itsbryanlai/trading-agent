# Contract: `journal.summary_md`

The fixed template (research J10), `SUMMARY_VERSION = "0.1"`. The PM reads the first 2,000 characters of the last five days' summaries, so every line here is built from numbers, dates and closed sets. No text written by a model or the broker, and no attribution.

```markdown
## 2026-10-09
- Equity: 100000.00 at the open, 100000.00 last recorded (19:30 UTC); change 0.00 (0.00%)
- Daily-loss breaker: not triggered
- Decisions: 3 (buy 2, sell 1, hold 0); approved 1, rejected 2
- Rejected by rule: max_position_pct 1, trading_paused 1
- Orders: 1 submitted; filled 1, partially filled 0, expired 0, rejected 0, canceled 0, open 0
- Execution refusals: none
- Stop-loss exits: 0 triggers; approved 0, rejected 0
- Decided symbols: AAPL, MSFT, NVDA
- Notes: none
```

Rules:
- **Order and fit**: the lines appear in this order. Everything before `Decided symbols` is fixed-length apart from the numbers and the rule list. The rule list holds closed-set codes only and is cut with "and N more" after 8. `Decided symbols` lists at most 30 well-formed tickers, sorted, then "and N more". Malformed ones are counted, as "2 malformed". `Notes` holds the missed sessions (at most 10 dates, then "and N more") and a count of symbols that couldn't be priced. The whole summary is at most 2,000 characters; a test enforces it with a 500-decision day.
- **Breaker**: `triggered` when any of that day's verdicts is `daily_loss_halt`, or any refusal is `daily_loss_line_crossed`. Otherwise `not triggered`.
- **Empty sections** read `none`, or `0`.
- **Counting** (research J10): Decisions and their approved and rejected counts and rules come from today's PM decisions and their own verdicts. Stop-loss verdicts appear only on the stop-loss line. Orders count every order for a verdict dated today, whatever started it.
- **Money**: two decimals, no thousands separator, no currency sign. A percentage has two decimals, or reads `n/a` when the equity at the open is 0.
- **Never**: report rationales, PM reasoning, source titles, broker reasons, refusal details, agent names, returns, indexes or usage counts.
