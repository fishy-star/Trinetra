import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.channels.telegram import TelegramAPI, TelegramError, parse_update
from app.channels.telegram_poll import run_once
from app.intake import CONSENT_SHORT, Intake
from app.store import Store

TOKEN = "123456:SECRET-TOKEN-never-log-me"


def update(update_id=1, chat_id=42, chat_type="private", **message):
    return {
        "update_id": update_id,
        "message": {"message_id": 1, "chat": {"id": chat_id, "type": chat_type}, "from": {"language_code": "en"}, **message},
    }


class FakeTelegram:
    """Stands in for api.telegram.org. Records sendMessage calls and serves queued updates."""

    def __init__(self, updates=(), fail_with=None):
        self.sent: list[dict] = []
        self.updates = list(updates)
        self.fail_with = fail_with

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"ok": False, "description": "Conflict"})
        if method == "sendMessage":
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": batch})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})


def api_with(fake: FakeTelegram) -> TelegramAPI:
    return TelegramAPI(TOKEN, client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_parse_text_and_media():
    assert parse_update(update(text="hello")).text == "hello"
    assert parse_update(update(photo=[{"file_id": "x"}])).media == "photo"
    assert parse_update(update(voice={"file_id": "x"})).media == "voice"
    assert parse_update(update(document={"file_id": "x"})).media == "document"
    assert parse_update(update(sticker={"file_id": "x"})).media == "other"


def test_groups_and_non_messages_are_ignored():
    assert parse_update(update(chat_type="group", text="hi")) is None
    assert parse_update({"update_id": 1, "edited_message": {}}) is None


def test_send_message():
    fake = FakeTelegram()
    api_with(fake).send_message("42", "hi")
    assert fake.sent == [{"chat_id": "42", "text": "hi"}]


def test_errors_never_contain_the_token():
    def explode(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    api = TelegramAPI(TOKEN, client=httpx.Client(transport=httpx.MockTransport(explode)))
    with pytest.raises(TelegramError) as info:
        api.send_message("42", "hi")
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert TOKEN not in repr(api)


def test_conflict_status_is_reported():
    with pytest.raises(TelegramError) as info:
        api_with(FakeTelegram(fail_with=409)).get_updates(None)
    assert info.value.status == 409


def test_httpx_does_not_log_urls_at_info():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_poll_once_answers_and_advances_offset(tmp_path):
    fake = FakeTelegram(updates=[update(7, text="hello"), update(8, chat_type="group", text="x")])
    intake = Intake(Store(tmp_path / "t.db"), salt="s")
    assert run_once(api_with(fake), intake, None, timeout=0) == 9
    assert [m["text"] for m in fake.sent] == [CONSENT_SHORT["en"]]
    assert fake.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "agree"


@pytest.fixture
def webhook(tmp_path):
    fake = FakeTelegram()
    deps = main.TelegramDeps(api_with(fake), Intake(Store(tmp_path / "t.db"), salt="s"), secret="hook-secret")
    main.app.dependency_overrides[main.telegram_deps] = lambda: deps
    yield TestClient(main.app), fake
    main.app.dependency_overrides.clear()


def test_webhook_rejects_wrong_secret(webhook):
    client, fake = webhook
    assert client.post("/telegram/webhook", json=update(text="hi")).status_code == 403
    assert client.post("/telegram/webhook", json=update(text="hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "no"}).status_code == 403
    assert fake.sent == []


def test_webhook_handles_message(webhook):
    client, fake = webhook
    resp = client.post("/telegram/webhook", json=update(text="hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"})
    assert resp.status_code == 200
    assert [m["text"] for m in fake.sent] == [CONSENT_SHORT["en"]]
    assert fake.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "agree"


def test_webhook_is_off_without_configuration():
    main.app.dependency_overrides[main.telegram_deps] = lambda: None
    try:
        assert TestClient(main.app).post("/telegram/webhook", json=update(text="hi")).status_code == 404
    finally:
        main.app.dependency_overrides.clear()
