"""A small Telegram Bot API client (specs/013-assistant research A1, A2).

Four methods, each one HTTPS POST with a JSON body. The bot token is part of the URL, so no
exception here holds or formats the URL, a request, or a response body; the client's
`repr` hides the token. Failures are typed so the loop can decide what to do (A2).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request

from trading_agent.no_redirect import open_without_redirects

API_ROOT = "https://api.telegram.org"
TOKEN_PATTERN = re.compile(r"\d+:[A-Za-z0-9_-]+")


def is_valid_token(value: str) -> bool:
    """True for the shape `123456:ABC-def_1`. Checked at start; the value is never echoed."""
    return isinstance(value, str) and TOKEN_PATTERN.fullmatch(value) is not None


class TelegramError(Exception):
    """Base of the typed failures. Messages are fixed text, never a URL or a body."""


class TokenRejected(TelegramError):
    """HTTP 401 or 404: Telegram does not know this token."""


class Conflict(TelegramError):
    """HTTP 409: another process is polling the same bot (A3)."""


class FloodControl(TelegramError):
    """HTTP 429: wait `retry_after` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("telegram: flood control")
        self.retry_after = retry_after


class TelegramUnavailable(TelegramError):
    """A network failure, a timeout, a 5xx, or any other `ok: false`."""


class TelegramClient:
    def __init__(self, token: str, *, opener: Callable | None = None, timeout: float) -> None:
        self._token = token
        self._open = opener or open_without_redirects()
        self._timeout = timeout

    def __repr__(self) -> str:
        return "TelegramClient(<token hidden>)"

    __str__ = __repr__

    def get_updates(
        self, offset: int, timeout: int, allowed_updates: list[str], limit: int
    ) -> list[dict]:
        result = self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": allowed_updates,
                "limit": limit,
            },
        )
        if not isinstance(result, list):
            raise TelegramUnavailable("telegram: unexpected response shape")
        return [update for update in result if isinstance(update, dict)]

    def send_message(self, chat_id: int, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}},
        )

    def get_me(self) -> dict:
        result = self._call("getMe", {})
        if not isinstance(result, dict):
            raise TelegramUnavailable("telegram: unexpected response shape")
        return result

    def get_webhook_info(self) -> dict:
        result = self._call("getWebhookInfo", {})
        if not isinstance(result, dict):
            raise TelegramUnavailable("telegram: unexpected response shape")
        return result

    def _call(self, method: str, body: dict):
        # The typed error is raised after the `except` blocks, so it carries no __context__
        # or __cause__: the original exception's text and traceback hold the token's URL.
        failure: TelegramError | None = None
        raw = b""
        try:
            request = Request(
                f"{API_ROOT}/bot{self._token}/{method}",
                data=json.dumps(body).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            failure = _failure(exc.code, _payload(_read(exc)))
        except (OSError, ValueError, HTTPException) as exc:  # InvalidURL is an HTTPException
            failure = TelegramUnavailable(f"telegram: {type(exc).__name__}")
        if failure is not None:
            raise failure
        payload = _payload(raw)
        if payload.get("ok") is True and "result" in payload:
            return payload["result"]
        status = payload.get("error_code")
        raise _failure(status if isinstance(status, int) else 0, payload)


def _read(exc: HTTPError) -> bytes:
    try:
        return exc.read()
    except (OSError, ValueError, HTTPException):
        return b""


def _payload(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _failure(status: int, payload: dict) -> TelegramError:
    if status in (401, 404):
        return TokenRejected(f"telegram: HTTP {status}")
    if status == 409:
        return Conflict("telegram: HTTP 409")
    if status == 429:
        parameters = payload.get("parameters")
        retry = parameters.get("retry_after") if isinstance(parameters, dict) else None
        ok = isinstance(retry, int) and not isinstance(retry, bool) and retry >= 0
        return FloodControl(retry if ok else 1)
    return TelegramUnavailable(f"telegram: HTTP {status}" if status else "telegram: not ok")
