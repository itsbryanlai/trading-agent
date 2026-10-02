"""`python -m trading_agent.research` (contracts/research-interface.md; research R9, R10).

One run, then exit; the orchestrator owns the schedule and the timeout. Reads only
RESEARCH_* variables (ADR 0015), and only the configured provider's model key.
Variables are reported by name, never by value.

Exit codes: 0 ran (or nothing to do outside the window); 1 wrote a failure
no_action; 2 refused to start; 3 database unreachable or a read or write failed;
4 crashed (never Python's default 1, so a crash isn't mistaken for a recorded failure).

`--dry-run` does everything except write: it prints the would-be rows and the
dropped proposals as JSON lines. The database is optional there (read-only).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from trading_agent.research.config import (
    DEFAULT_CONFIG_PATH,
    ModelConfig,
    ResearchConfigError,
    load_config,
)
from trading_agent.research.finnhub import FinnhubNews
from trading_agent.research.service import (
    PgResearchStore,
    ReportRow,
    ResearchRun,
    RunOutcome,
)
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.research")

DATABASE_VARIABLE = "RESEARCH_DATABASE_URL"
NEWS_KEY_VARIABLE = "RESEARCH_FINNHUB_API_KEY"
# Qwen's endpoint: pay-as-you-go and Token Plan keys each have their own (the owner
# uses a Token Plan key). Kept out of the source, and https only, since the key is
# sent to it.
QWEN_BASE_URL_VARIABLE = "RESEARCH_QWEN_BASE_URL"

EXIT_OK = 0
EXIT_FAILURE_RECORDED = 1
EXIT_REFUSED = 2
EXIT_DATABASE = 3
EXIT_CRASHED = 4


def build_model(provider: str, key: str, model: ModelConfig, *, base_url: str | None = None):
    if provider == "anthropic":
        from trading_agent.research.anthropic_client import AnthropicClient

        return AnthropicClient.from_key(key, model)
    from trading_agent.research.qwen import QwenClient

    return QwenClient(
        key,
        model=model.name,
        max_output_tokens=model.max_output_tokens,
        timeout=model.timeout_seconds,
        base_url=base_url,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    news_factory: Callable = FinnhubNews,
    model_factory: Callable = build_model,
    connect: Callable = psycopg.connect,
    config_path: Path = DEFAULT_CONFIG_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> int:
    try:
        return _main(
            argv, news_factory, model_factory, connect, config_path, clock, sleep, monotonic, out
        )
    except Exception as exc:  # a crash: never Python's default exit 1 (research R9)
        log.critical("research: crashed: %s", type(exc).__name__)
        return EXIT_CRASHED


def _main(argv, news_factory, model_factory, connect, config_path, clock, sleep, monotonic, out):
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = args == ["--dry-run"]
    if args and not dry_run:
        log.critical("research: unknown arguments %s", args)
        return EXIT_REFUSED

    try:
        cfg = load_config(config_path)
        news_key = require_env(NEWS_KEY_VARIABLE)
        model_key = require_env(cfg.provider_key_variable)
        database_url = _optional(DATABASE_VARIABLE) if dry_run else require_env(DATABASE_VARIABLE)
        base_url = _qwen_base_url() if cfg.model.provider == "qwen" else None
    except (ConfigError, ResearchConfigError) as exc:
        log.critical("research: refusing to start: %s", exc)
        return EXIT_REFUSED

    conn = None
    if database_url is not None:
        try:
            conn = connect(database_url, autocommit=True, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            log.critical("research: database unreachable: %s", type(exc).__name__)
            return EXIT_DATABASE

    try:
        if dry_run:
            store = DryRunStore(conn, out)
        else:
            store = PgResearchStore(conn)
        run = ResearchRun(
            news_factory(news_key),
            model_factory(cfg.model.provider, model_key, cfg.model, base_url=base_url),
            store,
            cfg,
            clock=clock,
            sleep=sleep,
            monotonic=monotonic,
            dry_run=dry_run,
        )
        try:
            outcome = run.run()
        except psycopg.Error as exc:
            log.critical("research: database error: %s", type(exc).__name__)
            return EXIT_DATABASE
    finally:
        if conn is not None:
            conn.close()

    if dry_run:
        _print_dry_run(outcome, out)
    return EXIT_FAILURE_RECORDED if outcome.failure is not None else EXIT_OK


def _qwen_base_url() -> str:
    value = require_env(QWEN_BASE_URL_VARIABLE)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or value != value.strip():
        # Named, never echoed: the value is configuration, but stays out of logs.
        raise ConfigError(f"{QWEN_BASE_URL_VARIABLE} must be an https:// URL")
    return value


def _optional(name: str) -> str | None:
    try:
        return require_env(name)
    except ConfigError:
        return None


class DryRunStore:
    """Reads open reports if a database is given; prints instead of writing."""

    def __init__(self, conn, out: Callable[[str], None]) -> None:
        self._reader = PgResearchStore(conn) if conn is not None else None
        self._out = out
        if conn is None:
            out(json.dumps({"note": "no database: open reports not read"}))

    def open_reports(self, now):
        return self._reader.open_reports(now) if self._reader is not None else []

    def write(self, rows: list[ReportRow]) -> None:
        for row in rows:
            self._out(json.dumps({"would_write": _row_json(row)}, ensure_ascii=False))


def _row_json(row: ReportRow) -> dict:
    return {
        "symbol": row.symbol,
        "direction": row.direction,
        "conviction": row.conviction,
        "suggested_size_pct": None
        if row.suggested_size_pct is None
        else str(row.suggested_size_pct),
        "sources": row.sources,
        "rationale_md": row.rationale_md,
        "expires_at": row.expires_at.isoformat(),
    }


def _print_dry_run(outcome: RunOutcome, out) -> None:
    for art in outcome.articles:  # public headlines, to see what the model was given
        out(
            json.dumps(
                {
                    "article": {
                        "id": art.id,
                        "published_at": art.published_at.isoformat(),
                        "title": art.title,
                        "related": list(art.related),
                    }
                },
                ensure_ascii=False,
            )
        )
    for drop in outcome.drops:
        out(
            json.dumps(
                {"dropped": {"index": drop.index, "symbol": drop.symbol, "reason": drop.reason}}
            )
        )
    out(
        json.dumps(
            {
                "summary": {
                    "failure": outcome.failure,
                    "missing_news": outcome.missing,
                    "input_chars": outcome.input_chars,
                    "tagged_articles": outcome.tagged_articles,
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                }
            }
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
