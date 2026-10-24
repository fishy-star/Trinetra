"""Telegram Bot API: parse updates, send messages, long-poll for local development.

The bot token is part of every API URL, so it must never reach a log line or an error message.
"""

from __future__ import annotations

import logging
from functools import partial
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

    def _call(self, method: str, files: dict | None = None, **params: Any) -> Any:
        try:
            url = f"{API_BASE}/bot{self._token}/{method}"
            if files:  # multipart: plain form fields, with any JSON values already encoded
                resp = self._client.post(url, data=params, files=files)
            else:
                resp = self._client.post(url, json=params)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            # `from None`: the original exception carries the URL, and with it the token.
            raise TelegramError(method, type(e).__name__) from None
        if not body.get("ok"):
            raise TelegramError(method, str(body.get("description", "unknown error")), resp.status_code)
        return body["result"]

    def send_message(self, chat_id: str, text: str, buttons: tuple[tuple[str, str], ...] = ()) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = keyboard(buttons)
        self._call("sendMessage", **params)

    def send_photo(self, chat_id: str, photo: bytes, caption: str = "") -> None:
        self._call("sendPhoto", files={"photo": ("sample.png", photo, "image/png")}, chat_id=chat_id, caption=caption)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        """Acknowledge a button press, so the app stops showing a spinner."""
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        self._call("answerCallbackQuery", **params)

    def clear_buttons(self, chat_id: str, message_id: int) -> None:
        self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                   reply_markup={"inline_keyboard": []})

    def download(self, file_id: str) -> bytes:
        """Bots can download files up to 20 MB; Telegram refuses larger ones at getFile."""
        path = self._call("getFile", file_id=file_id)["file_path"]
        try:
            resp = self._client.get(f"{API_BASE}/file/bot{self._token}/{path}")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise TelegramError("download", type(e).__name__) from None
        return resp.content

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)


def keyboard(buttons: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    """Inline buttons, two to a row."""
    row = [{"text": label, "callback_data": data[:64]} for label, data in buttons]
    return {"inline_keyboard": [row[i:i + 2] for i in range(0, len(row), 2)]}


def parse_update(update: dict[str, Any], api: TelegramAPI | None = None) -> Inbound | None:
    """Private-chat messages and button presses only. Groups, channels and edits are ignored.

    With `api`, photos and documents get a `fetch` that downloads them when intake asks.
    A button press becomes a message whose text is the button's data.
    """
    press = update.get("callback_query")
    if press:
        message = press.get("message") or {}
        if message.get("chat", {}).get("type") != "private":
            return None
        return Inbound(
            channel="telegram",
            chat_id=str(message["chat"]["id"]),
            text=press.get("data") or "",
            lang_hint=press.get("from", {}).get("language_code"),
            callback_id=press["id"],
            message_id=message.get("message_id"),
        )
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private":
        return None
    media = next((k for k in MEDIA_KEYS if k in message), None)
    if media in ("video_note", "sticker", "animation"):
        media = "other"

    file: dict[str, Any] = {}
    if media == "photo":
        file = max(message["photo"], key=lambda p: p.get("file_size") or p.get("width", 0) * p.get("height", 0))
    elif media == "document":
        file = message["document"]
    file_id = file.get("file_id")
    return Inbound(
        channel="telegram",
        chat_id=str(message["chat"]["id"]),
        text=message.get("text"),
        media=media,
        lang_hint=message.get("from", {}).get("language_code"),
        mime=file.get("mime_type"),
        file_size=file.get("file_size"),
        fetch=partial(api.download, file_id) if api and file_id else None,
    )


class TelegramSender:
    """Sends intake's replies for one inbound message: text, inline buttons, photos and button acknowledgements."""

    def __init__(self, api: TelegramAPI, msg: Inbound):
        self.api, self.msg = api, msg
        self._answered = False

    def __call__(self, out: str) -> None:
        chat = self.msg.chat_id
        if self.msg.callback_id and getattr(out, "toast", False):
            self.api.answer_callback(self.msg.callback_id, str(out))
            self._answered = True
            if self.msg.message_id:
                self.api.clear_buttons(chat, self.msg.message_id)  # one vote per reply
            return
        if self.msg.callback_id and not self._answered:
            self.api.answer_callback(self.msg.callback_id)
            self._answered = True
        photo = getattr(out, "photo", None)
        if photo:
            self.api.send_photo(chat, photo, caption=str(out))
        else:
            self.api.send_message(chat, str(out), getattr(out, "buttons", ()))
