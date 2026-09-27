# Contract: Broker Port

The only way Execution talks to the broker. One real implementation
(`trading_agent.execution.alpaca`) and one fake (`tests/fakes/broker.py`), both satisfying the same
`Protocol` in `trading_agent.execution.broker`. Decisions: [research.md](../research.md) E1, E2, E7,
E9.

## Values

All frozen dataclasses; every price and quantity is a `Decimal`, every time timezone-aware.

| Value | Fields |
|---|---|
| `Account` | `account_number`, `equity`, `cash`, `buying_power` |
| `BrokerPosition` | `symbol`, `qty`, `avg_entry_price` |
| `Quote` | `symbol`, `ask`, `timestamp` (ask `0` means no active ask) |
| `Trade` | `symbol`, `price`, `timestamp` |
| `OrderRequest` | `client_order_id`, `symbol`, `side` (`buy` \| `sell`), `qty` (whole shares), `order_type` (`limit` \| `market`), `limit_price` (limit only), `time_in_force` (always `day`) |
| `BrokerOrder` | `broker_order_id`, `client_order_id`, `symbol`, `side`, `qty`, `order_type`, `limit_price` (limit orders; none for market), `status` (the broker's raw status string), `filled_qty`, `filled_avg_price` (or none), `submitted_at`, `reason` (on rejection, or none) |

## Calls

| Call | Returns | Raises |
|---|---|---|
| `get_account()` | `Account` | `BrokerUnavailable` |
| `get_positions()` | `list[BrokerPosition]` | `BrokerUnavailable` |
| `get_latest_ask(symbol)` | `Quote` | `BrokerUnavailable` |
| `get_latest_trade(symbol)` | `Trade` | `BrokerUnavailable` |
| `find_order(client_order_id)` | `BrokerOrder` or `None` if the broker has none | `BrokerUnavailable` |
| `get_order(broker_order_id)` | `BrokerOrder` | `BrokerUnavailable` |
| `submit_order(request)` | `BrokerOrder` | `OrderRejected(reason)` when the broker refuses it outright (e.g. HTTP 403 insufficient buying power, 422); `BrokerUnavailable` on a timeout or network error, meaning *the order may or may not have been placed* |

There is deliberately no cancel, replace, or close-position call: Execution never does any of those
(spec Non-goals; no working orders across sessions).

## Guarantees the real adapter gives

- **Paper only** (E2). Construction takes the keys and the optional configured base URL; it raises
  `NotPaperTrading` if that URL is set and isn't the fixed paper address, before building any
  client. `verify_paper()` performs the one authenticated account read at the paper address and
  raises `NotPaperTrading` on any failure. The service calls it once at startup, before anything
  else.
- **IEX feed** for both price calls (E9).
- **No SDK types escape**: strings from the SDK are converted to `Decimal` at the boundary; an
  unparseable value is `BrokerUnavailable`, never a zero.
- **No retries inside the adapter.** Retrying is the tick's job, so that a timeout on
  `submit_order` is always followed by `find_order`, never by a blind resubmission (E5).

## What the fake must model

Every behaviour a test relies on: scripted account values and positions; quotes and trades with
timestamps (including stale and zero-ask cases); submissions recorded in order; `find_order` by
client id; filling an order fully or partly on command, updating positions and cash the way the
broker would; closing out day orders at "the close"; outright rejection; optionally rejecting a
duplicate client order id (to exercise the double-check in research E5); and raising
`BrokerUnavailable` from any chosen call, including *after* recording a submission (the "maybe
placed" timeout).
