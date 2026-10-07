# Contract: the Opportunistic Identifier's ports

Two ports are the only way the OI reaches the network. Tests replace them with fakes (`tests/fakes/market_data.py`, new; `tests/fakes/model.py`, existing). Neither port can write anything or reach a broker.

## `MarketData` (`opportunistic_identifier/finnhub.py` implements it)

```python
class MarketData(Protocol):
    def us_listings(self) -> dict[str, Listing]: ...        # XNYS, XNAS, XASE merged
    def quote(self, symbol: str) -> Quote: ...              # reference.provider.Quote
    def profile(self, symbol: str) -> CompanyProfile: ...   # market cap (millions), currency, industry
    def fundamentals(self, symbol: str) -> Fundamentals: ...
```

- **`Listing`**: `symbol`, `type`, `mic`, `description`, and `conflicting`, as feature 004's `Listing` but with the company name added. A test asserts that `to_reference()` gives the exact `reference.provider.Listing` that `normalize` expects.
- **`CompanyProfile.to_reference()`** and **`Fundamentals.to_metrics()`** likewise give `reference.provider.Profile` and `reference.provider.Metrics`, for `reference.normalize.normalize`.

**Errors**: the four `reference.provider` errors, reused:
- `KeyRejected`: 401, or a 403 on the symbol list;
- `NotPermitted`: a 403 on one symbol;
- `RateLimited`: 429;
- `ProviderUnavailable`: a timeout, a network or server error, or unreadable JSON.

**Rules**:
- The key goes only in the `X-Finnhub-Token` header, never in a URL, a log line, an exception message or `__repr__`.
- Redirects are refused.
- There are no retries inside the adapter. Pacing, backoff and the deadline are the service's job.
- An unusable number becomes `None`, never zero.

## `ModelClient` (`trading_agent.llm`, unchanged)

The same port Research and the PM use: `complete(system, user, schema) -> ModelReply` (`text`, `input_tokens`, `output_tokens`, `finish`), with errors `ModelKeyRejected`, `ModelRejected`, `ModelUnavailable`, `ModelRefused` and `ModelTruncated`. The OI builds it from `llm.settings` with prefix `OPPORTUNISTIC_IDENTIFIER_`.
