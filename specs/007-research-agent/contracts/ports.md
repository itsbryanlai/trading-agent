# Contract: Research's ports

Both ports are the only way Research reaches the network. Tests replace them with fakes (`tests/fakes/news.py`, `tests/fakes/model.py`). Neither port can write anything or reach a broker.

## `NewsSource` (`research/finnhub.py` implements it)

```python
class NewsSource(Protocol):
    def general_news(self) -> list[RawArticle]: ...
    def company_news(self, symbol: str, start: date, end: date) -> list[RawArticle]: ...
    def us_symbols(self) -> dict[str, str]: ...  # symbol → company name ("" if unknown)
```

**`RawArticle`**: `url`, `headline`, `summary`, `source`, `published_at` (aware datetime or None), `related` (tuple). An item with no URL, a URL that isn't `http://` or `https://`, no headline or no time is skipped at this boundary, because it can't be cited safely.

**Errors**:
- `KeyRejected`: 401, or a 403 on a call that isn't per symbol.
- `NotPermitted`: 403 on one symbol's company news.
- `RateLimited`: 429.
- `ProviderUnavailable`: a timeout, a network or server error, or unreadable JSON.

The service decides what each means for the run (research R3, R8).

## `ModelClient` (`research/qwen.py`, `research/anthropic_client.py`)

```python
class ModelClient(Protocol):
    def complete(self, system: str, user: str, schema: dict) -> ModelReply: ...
```

**`ModelReply`**: `text` (the answer as returned), `input_tokens`, `output_tokens` (each an int or None when not reported), `finish` (the provider's stop or finish reason).

**Errors**: `ModelKeyRejected`, `ModelRejected` (any other 4xx), `ModelUnavailable`, `ModelRefused`, `ModelTruncated`.

**Neither adapter**:
- retries beyond what research R5 states;
- logs the prompt or the answer;
- puts a key in a URL, a log line or an exception message.

`__repr__` hides the key, as `FinnhubProvider` does.
