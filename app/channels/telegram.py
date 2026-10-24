"""Telegram Bot API: parse updates, send messages, long-poll for local development.

The bot token is part of every API URL, so it must never reach a log line or an error message.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.intake import Inbound

log = logging.getLogger(__name__)
# httpx logs each request URL at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_BASE = "https://api.telegram.org"
MEDIA_KEYS = ("photo", "document", "voice", "audio", "video", "video_note", "sticker", "animation")


class TelegramError(Exception):
    def __init__(self, method: str, description: str, status: int | None = None):
        super().__init__(f"Telegram {method} failed: {description}")
        self.status = status


class TelegramAPI:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._token = token
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0, read=40.0))

    def __repr__(self) -> str:
        return "TelegramAPI(...)"

    def _call(self, method: str, **params: Any) -> Any:
        try:
            resp = self._client.post(f"{API_BASE}/bot{self._token}/{method}", json=params)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            # `from None`: the original exception carries the URL, and with it the token.
            raise TelegramError(method, type(e).__name__) from None
        if not body.get("ok"):
            raise TelegramError(method, str(body.get("description", "unknown error")), resp.status_code)
        return body["result"]

    def send_message(self, chat_id: str, text: str) -> None:
        self._call("sendMessage", chat_id=chat_id, text=text)

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)


def parse_update(update: dict[str, Any]) -> Inbound | None:
    """Private-chat messages only. Groups, channels and edits are ignored."""
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private":
        return None
    media = next((k for k in MEDIA_KEYS if k in message), None)
    if media in ("video_note", "sticker", "animation"):
        media = "other"
    return Inbound(
        channel="telegram",
        chat_id=str(message["chat"]["id"]),
        text=message.get("text"),
        media=media,
        lang_hint=message.get("from", {}).get("language_code"),
    )
