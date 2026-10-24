"""End to end through the real Telegram update path: getUpdates payloads, run_once, the sender, the HTTP API.

Covers the consent flow on a flaky network, button presses, and the poll settings.
"""

import csv
import json

import httpx
import pytest

from app.channels.telegram import POLL_TIMEOUT, READ_TIMEOUT, TelegramAPI
from app.channels.telegram_poll import run_once
from app.intake import CONSENT, MESSAGES, Intake
from app.store import Store
from tests.test_testset import TEST_SET

TEXTS = {r["id"]: r["text"] for r in csv.DictReader(open(TEST_SET / "texts.csv", encoding="utf-8"))}
F1 = (TEST_SET / "png" / "F1_sc_warrant_en.png").read_bytes()


class Telegram:
    """A fake api.telegram.org: serves queued updates and files, records every call in order."""

    def __init__(self):
        self.updates: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.failures: dict[str, int] = {}  # method -> how many more times to fail with a connection error
        self._next_id = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "/file/bot" in request.url.path:
            self.calls.append(("download", {}))
            return httpx.Response(200, content=F1)
        method = request.url.path.rsplit("/", 1)[1]
        if self.failures.get(method):
            self.failures[method] -= 1
            raise httpx.ConnectError("network is unreachable")
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content) if is_json else {}
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": batch})
        self.calls.append((method, body))
        result = {"file_path": "photos/f1.jpg"} if method == "getFile" else {}
        return httpx.Response(200, json={"ok": True, "result": result})

    def _update(self, payload: dict) -> None:
        self._next_id += 1
        self.updates.append({"update_id": self._next_id, **payload})

    def text(self, chat: int, text: str, lang: str = "en") -> None:
        self._update({"message": {"message_id": self._next_id, "date": 0, "chat": {"id": chat, "type": "private"},
                                  "from": {"id": chat, "language_code": lang}, "text": text}})

    def photo(self, chat: int, lang: str = "en") -> None:
        self._update({"message": {"message_id": self._next_id, "date": 0, "chat": {"id": chat, "type": "private"},
                                  "from": {"id": chat, "language_code": lang},
                                  "photo": [{"file_id": "f1", "file_size": len(F1), "width": 1191, "height": 1685}]}})

    def tap(self, chat: int, data: str, lang: str = "en") -> None:
        self._update({"callback_query": {"id": f"cb{self._next_id}", "data": data,
                                         "from": {"id": chat, "language_code": lang},
                                         "message": {"message_id": 500, "date": 0,
                                                     "chat": {"id": chat, "type": "private"}}}})

    def sent(self, chat: int) -> list[dict]:
        return [b for m, b in self.calls if m == "sendMessage" and b["chat_id"] == str(chat)]

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


@pytest.fixture
def tg(tmp_path, monkeypatch):
    monkeypatch.setattr("app.channels.telegram.time.sleep", lambda s: None)  # retries without waiting
    fake = Telegram()
    api = TelegramAPI("1:TOKEN", client=httpx.Client(transport=httpx.MockTransport(fake)))
    intake = Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path, samples=lambda: [])

    def poll():
        run_once(api, intake, None, timeout=0)

    fake.poll = poll
    return fake


def verdict_of(tg: Telegram, chat: int) -> dict:
    return tg.sent(chat)[-1]


# --- an item sent before consent is checked on Agree, exactly as if sent after -------------------------


def test_text_before_consent_then_agree_matches_text_after_consent(tg):
    tg.text(1, TEXTS["T1"])
    tg.poll()
    assert tg.sent(1)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "agree"
    tg.tap(1, "agree")
    tg.poll()

    tg.text(2, "YES")
    tg.text(2, TEXTS["T1"])
    tg.poll()

    first, second = verdict_of(tg, 1), verdict_of(tg, 2)
    assert first["text"].startswith("Likely fake. Do not pay.")
    assert first["text"] == second["text"] and first["reply_markup"] == second["reply_markup"]


def test_photo_before_consent_then_agree_matches_photo_after_consent(tg):
    tg.photo(1)
    tg.poll()
    assert "download" not in tg.methods() and "getFile" not in tg.methods()  # nothing fetched before consent
    tg.tap(1, "agree")
    tg.poll()

    tg.text(2, "YES")
    tg.photo(2)
    tg.poll()

    first, second = verdict_of(tg, 1), verdict_of(tg, 2)
    assert first["text"].startswith("Likely fake. Do not pay.")
    assert first["text"] == second["text"] and first["reply_markup"] == second["reply_markup"]


def test_agree_is_acknowledged_before_the_check_runs(tg):
    tg.text(1, TEXTS["T1"])
    tg.poll()
    tg.calls.clear()
    tg.tap(1, "agree")
    tg.poll()
    assert tg.methods()[0] == "answerCallbackQuery"
    assert [b["text"] for b in tg.sent(1)][0] == MESSAGES["en"]["checking"]


# --- a flaky network never loses the item (the bug found on the phone) --------------------------------


def test_failed_acknowledgement_does_not_stop_the_check(tg):
    tg.text(1, TEXTS["T1"])
    tg.poll()
    tg.failures["answerCallbackQuery"] = 99  # Telegram unreachable for acknowledgements
    tg.tap(1, "agree")
    tg.poll()
    assert verdict_of(tg, 1)["text"].startswith("Likely fake")


def test_a_dropped_send_is_retried(tg):
    tg.text(1, "YES")
    tg.poll()
    tg.failures["sendMessage"] = 2  # the first two attempts never reach Telegram
    tg.text(1, TEXTS["T1"])
    tg.poll()
    texts = [b["text"] for b in tg.sent(1)]
    assert texts.count(MESSAGES["en"]["checking"]) == 1 and texts[-1].startswith("Likely fake")


def test_an_old_button_tapped_before_consent_does_not_replace_the_item(tg):
    tg.text(1, "YES")
    tg.text(1, TEXTS["T1"])
    tg.poll()
    why = verdict_of(tg, 1)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    tg.text(1, "DELETE")
    tg.text(1, TEXTS["T2"])  # held
    tg.tap(1, why)  # an old [Why?] from before DELETE
    tg.tap(1, "agree")
    tg.poll()
    assert verdict_of(tg, 1)["text"].startswith("संभवतः नकली")  # T2 (Hindi) was checked, not the button


def test_button_data_is_never_checked_as_a_message(tg):
    tg.text(1, "YES")
    tg.poll()
    for data in ("privacy", "agree", "why:zzz", "something-unknown"):
        tg.tap(1, data)
    tg.poll()
    texts = [b["text"] for b in tg.sent(1)]
    assert MESSAGES["en"]["checking"] not in texts
    assert CONSENT["en"] in texts  # Privacy details after consent shows the privacy text


# --- replies ------------------------------------------------------------------------------------------


def test_could_not_read_offers_only_help_and_feedback(tg):
    tg.text(1, "YES")
    tg.text(1, "😀 👍")
    tg.poll()
    reply = verdict_of(tg, 1)
    assert reply["text"].startswith("This message is too short to check.")
    labels = [[b["text"] for b in row] for row in reply["reply_markup"]["inline_keyboard"]]
    assert labels == [["Help"], ["Helpful", "Not helpful"]]


def test_delete_says_forward_anything_to_check_again(tg):
    tg.text(1, "YES")
    tg.text(1, "DELETE")
    tg.text(2, "हिंदी")
    tg.poll()
    assert verdict_of(tg, 1)["text"].endswith("Forward anything to check it again.")
    assert "YES" not in MESSAGES["en"]["deleted"] and "YES" not in MESSAGES["hi"]["deleted"]
    assert MESSAGES["hi"]["deleted"].endswith("किसी भी चीज़ की फिर से जाँच के लिए उसे फ़ॉरवर्ड करें।")


# --- poll settings ---------------------------------------------------------------------------------------


def test_long_polling_settings():
    assert 20 <= POLL_TIMEOUT <= 30 and READ_TIMEOUT > POLL_TIMEOUT
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})

    api = TelegramAPI("1:T", client=httpx.Client(transport=httpx.MockTransport(handler)))
    api.get_updates(None)
    assert seen["timeout"] == POLL_TIMEOUT and seen["allowed_updates"] == ["message", "callback_query"]
    assert TelegramAPI("1:T")._client.timeout.read == READ_TIMEOUT


def test_get_updates_is_not_retried_inside_the_call(tg):
    """The poll loop already waits and retries; the call itself fails fast so the loop can log it."""
    tg.failures["getUpdates"] = 1
    with pytest.raises(Exception):
        tg.poll()
