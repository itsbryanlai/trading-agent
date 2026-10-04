# Contract: the PM's ports, and the shared `trading_agent.llm` package

The three ports are the only ways the PM reaches the network or the database. Tests replace each with a fake. None can reach a broker.

## `QuoteSource`

```python
class QuoteSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...
```

Implemented by the existing `trading_agent.reference.finnhub.FinnhubProvider` (research P3), unchanged. `Quote` is `reference.provider.Quote(symbol, current, previous_close, timestamp)`. Errors are the reference adapter's: `KeyRejected`, `NotPermitted`, `RateLimited`, `ProviderUnavailable`. The PM decides what each means (research P3). Fake: `tests/fakes/market_data.py`, which already fakes this port for feature 004, extended if needed.

## `ModelClient` (moved to `trading_agent.llm`)

```python
class ModelClient(Protocol):
    def complete(self, system: str, user: str, schema: dict) -> ModelReply: ...
```

Exactly [Research's port](../../007-research-agent/contracts/ports.md#modelclient-researchqwenpy-researchanthropic_clientpy), moved without behaviour change (research P5):

| `trading_agent.llm` module | Holds |
|---|---|
| `ports.py` | `ModelError` (with `status`), `ModelKeyRejected`, `ModelRejected`, `ModelUnavailable`, `ModelRefused`, `ModelTruncated`, `ModelReply`, `ModelClient` |
| `qwen.py` | `QwenClient(api_key, *, model, max_output_tokens, timeout, base_url, schema_name, opener=None)` |
| `anthropic_client.py` | `AnthropicClient(client, settings)`, `AnthropicClient.from_key(key, settings)`; the only module importing `anthropic` |
| `settings.py` | `ModelSettings(provider, name, max_output_tokens, timeout_seconds, anthropic_effort)`; `parse_model_settings(section, *, timeout_bounds) -> ModelSettings` (raises `ModelSettingsError` naming the key); `provider_variables(prefix, provider) -> tuple[str, ...]`; `require_https_base_url(value, variable) -> str` |

Research imports these in place of its own copies; its config keys, behaviour, logs and exit codes don't change. Fake: `tests/fakes/model.py`, already shared.

**Neither adapter** retries beyond what 007 R5 states, logs a prompt or an answer, or puts a key in a URL, a log line or an exception message. `__repr__` hides the key.

## `Store`

```python
class Store(Protocol):
    def read_inputs(self, run_start: datetime, *, journal_entries: int) -> Inputs: ...
    def write(self, decisions: Sequence[CheckedDecision]) -> None: ...
```

- **`read_inputs`**: one read-only, repeatable-read transaction (research P6). `Inputs` holds the reports, positions, the snapshot (or none), the journal entries and the earlier decisions today. Raises `StoreError` on any database failure (exit 3).
- **`write`**: one transaction for every decision and its `decision_reports` rows (research P10). All or nothing; raises `StoreError` on failure.
- **Implementations**: `PostgresStore` (as `ta_portfolio_manager`) and a fake in `tests/fakes/pm_store.py`.
