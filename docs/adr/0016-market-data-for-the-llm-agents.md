# 0016. Market data for the LLM agents comes from read-only Finnhub keys

Status: accepted

Accepted 2026-10-01 after the owner ran `python -m trading_agent.reference --check AAPL`
during the session (about 10:03 ET). Finnhub's free `/quote` returned
`t=2026-10-01T14:03:17+00:00`, seconds before the run, with `c` (329.05) moved off
`pc` (333.02). So the quote is live, not delayed, at least for AAPL.

## Context

The Portfolio Manager must fetch a live quote itself rather than trust one in a
report (Constitution II, `docs/specs/portfolio-manager-agent.md`). It records it
as `decisions.quote_at_decision`. The Opportunistic Identifier needs prices and
fundamentals across the universe (`docs/specs/opportunistic-identifier-agent.md`).
No document says where either comes from.

The only market data in the system today is Alpaca's, and Alpaca's keys can both
read and trade. Sharing them with an agent would give LLM output a credential
that reaches the broker, breaking Constitution I and III.

That quote is more than context for the PM's reasoning. The Risk Gate sizes
orders from it: a buy's price ceiling is the quote plus
`max_buy_price_tolerance_pct`, and buy and sell quantities are worked out from it
(`src/trading_agent/risk/gate.py`). Execution then compares the ceiling with its
own live IEX ask from Alpaca and skips a buy priced above it. So a wrong quote
plays out like this:

- **Too low** (stale, the price has since risen): the ceiling is below the
  market, and Execution skips the buy. This fails safe.
- **Too high**: a buy still pays the live ask, but the gate sizes it smaller. A
  partial sell keeps fewer shares, so it sells more than the target intended, up
  to the whole position.

Research needs no prices: its input is news, already from Finnhub
([0008](0008-dashboard-stack-and-research-provider.md)).

## Decision

1. **The Portfolio Manager and the Opportunistic Identifier each get their own
   read-only Finnhub key**: `PORTFOLIO_MANAGER_FINNHUB_API_KEY` and
   `OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY`, named with each agent's prefix as
   [0015](0015-orchestrator-starts-agents-with-their-own-credentials.md)
   requires. A Finnhub key can't trade, which is the same reasoning as 0008 and
   the reference-data job ([0010](0010-stop-loss-monitor-and-universe-reference-data.md) §3).
2. **The PM's quote comes from Finnhub's `/quote`.** The OI's prices and
   fundamentals come from Finnhub's free endpoints (for example
   `/stock/metric` and `/stock/profile2`, which feature 004 already uses). The
   OI's universe and pre-screen design are left to its own feature.
3. **Alpaca's market data stays Execution's alone.** No agent receives an Alpaca
   credential, read-only or otherwise.
4. **A stale quote means no decision on that symbol.** The PM must not decide
   on a quote that isn't from the current session or that is older than a
   freshness limit. The PM's spec sets the limit, and the quote's own
   timestamp is recorded with the decision.
5. **Separate key names, a shared account allowed.** Whether the components
   share one Finnhub account is the owner's choice. If they do, they share its
   rate limit, and each component's call pacing must leave room for the others,
   as feature 004 already does.

## Alternatives considered

- **A separate data-only vendor.** Not chosen: a new vendor, credential and
  cost, and the OI would still need fundamentals that Finnhub already provides.
  Revisit if Finnhub's free quote proves delayed or too thin.
- **Execution publishes quotes to a table** for the agents to read. Not chosen:
  - Execution would gain a new write.
  - Execution would need to read reports to know which symbols to quote.
  - The PM would depend on Execution being up.
  - The PM would trust a table rather than fetch the quote itself.
  - It provides no fundamentals for the OI.
  
  Its one advantage is that the PM's quote and Execution's check would use the
  same feed.
- **A second Alpaca paper account used only for data.** Rejected: its keys are
  still broker credentials, and Constitution III grants those to Execution alone.
- **Sharing Execution's Alpaca keys.** Rejected outright: it would give LLM
  output a path to the broker (Constitution I, non-negotiable).

## Consequences

- **Different feeds.** The PM's quote (Finnhub) and Execution's live ask
  (Alpaca IEX) can differ slightly, against a 1% tolerance. Some buys will be
  skipped because of this. That is the safe direction. If it happens often, it
  is a reason to revisit this ADR, not to widen the tolerance quietly. Changing
  the tolerance is a change to `config/risk.yaml` and is reviewed as one.
- **Assumptions about Finnhub's free tier.**
  - **The quote is live during the session:** observed for AAPL on 2026-10-01
    (see Status), but not stated in Finnhub's own documentation.
  - **60 calls a minute:** unconfirmed, which is why call pacing is
    configurable.
- **The orchestrator's service holds two more agent keys**, which is the shared
  environment 0015 already accepts. Neither key can trade.
- **The PM feature inherits work:**
  - fetching and timestamping the quote;
  - the freshness rule;
  - probably a quote-time column on `decisions` (a migration, decided in that
    feature).
- **The OI feature inherits work:** the call budget for its pre-screen under the
  rate limit, together with its Qwen ADR and the constitution amendment.
- **No constitution amendment is needed.** Finnhub is already a named credential
  in its Technology section, and Principle II already requires the PM to fetch
  its own quote.
