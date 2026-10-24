"""Judge mode (samples, /try), feedback buttons, usage stats and concurrent chats."""

import csv
import dataclasses
import json
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import console as console_mod
from app import main, samples
from app.channels.dispatch import Dispatcher
from app.channels.telegram import TelegramAPI, TelegramSender, parse_update
from app.channels.telegram_poll import run_once
from app.intake import CONSENT, CONSENT_SHORT, SAMPLE_CAPTION, WELCOME, Inbound, Intake, Out
from app.store import Store
from tests.test_testset import TEST_SET

TEXTS = {r["id"]: r["text"] for r in csv.DictReader(open(TEST_SET / "texts.csv", encoding="utf-8"))}
F1 = TEST_SET / "png" / "F1_sc_warrant_en.png"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.fixture
def sample_files(tmp_path):
    summons, altered = tmp_path / "summons.png", tmp_path / "altered.png"
    summons.write_bytes(b"\x89PNG summons")
    altered.write_bytes(b"\x89PNG altered")
    return [F1, summons, altered]


@pytest.fixture
def intake(store, tmp_path, sample_files):
    return Intake(store, salt="s", data_dir=tmp_path, samples=lambda: sample_files)


def say(intake, text=None, chat="1", **kw) -> list:
    out: list = []
    intake.handle(Inbound("telegram", chat, text, **kw), out.append)
    return out


# --- consent and samples ---------------------------------------------------------------------


def test_consent_says_it_is_a_student_prototype_in_both_languages():
    for text in (CONSENT["en"], CONSENT_SHORT["en"]):
        assert "Trinetra is a student prototype. It does not replace official advice. If in doubt, call 1930." in text
    for text in (CONSENT["hi"], CONSENT_SHORT["hi"]):
        assert "Trinetra एक छात्र प्रोटोटाइप है। यह आधिकारिक सलाह का विकल्प नहीं है। संदेह हो तो 1930 पर कॉल करें।" in text


def test_welcome_offers_a_sample(intake):
    [welcome] = say(intake, "YES")
    assert welcome == WELCOME["en"] and ("Try a sample", "samples") in welcome.buttons


@pytest.mark.parametrize("command", ["samples", "/samples"])
def test_samples_are_sent_as_photos_with_the_caption(intake, sample_files, command):
    say(intake, "YES")
    out = say(intake, command)
    photos = [o for o in out if isinstance(o, Out) and o.photo]
    assert len(photos) == 3
    assert all(str(p) == SAMPLE_CAPTION["en"] == "Forward this back to me to check it" for p in photos)
    assert [p.photo for p in photos] == [f.read_bytes() for f in sample_files]


def test_missing_sample_files_are_skipped(store, tmp_path):
    intake = Intake(store, salt="s", data_dir=tmp_path, samples=lambda: [F1, tmp_path / "missing.png"])
    say(intake, "YES")
    assert len([o for o in say(intake, "/samples") if getattr(o, "photo", None)]) == 1


def test_samples_need_consent(intake):
    assert say(intake, "/samples") == [CONSENT_SHORT["en"]]


def test_sample_settings_come_from_env_paths(monkeypatch, tmp_path):
    summons = tmp_path / "s.png"
    summons.write_bytes(b"\x89PNG")
    configured = dataclasses.replace(samples.settings, demo_summons_path=summons, demo_altered_path=tmp_path / "no.png")
    monkeypatch.setattr(samples, "settings", configured)
    assert [s.slug for s in samples.available()] == ["forged-warrant", "summons"]


# --- feedback ------------------------------------------------------------------------------------


def test_every_verdict_has_feedback_buttons(intake):
    say(intake, "YES")
    verdict = say(intake, TEXTS["T1"])[-1]
    assert [label for label, data in verdict.buttons if data.startswith("fb:")] == ["Helpful", "Not helpful"]
    assert all(len(data.encode()) <= 64 for _, data in verdict.buttons)
    cached = say(intake, TEXTS["T1"])[-1]  # answers from the cache get them too
    assert cached.buttons == verdict.buttons


def test_feedback_stores_only_hash_verdict_and_vote(intake, store):
    say(intake, "YES")
    data = next(d for _, d in say(intake, TEXTS["T1"])[-1].buttons if d.startswith("fb:d:"))
    [thanks] = say(intake, data, callback_id="cb1", message_id=7)
    assert thanks.toast and str(thanks) == "Thank you for your feedback."
    with store._db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM feedback")]
        columns = [c["name"] for c in db.execute("PRAGMA table_info(feedback)")]
    assert columns == ["id", "sha256", "verdict", "vote"]
    from app.pipeline import sha256_text

    assert rows == [{"id": 1, "sha256": sha256_text(TEXTS["T1"]), "verdict": "likely_fake", "vote": "not_helpful"}]


def test_bad_feedback_data_is_ignored(intake, store):
    say(intake, "YES")
    for data in ("fb:x:L:abc", "fb:u:Q:abc", "fb:u:L:<script>", "fb:u"):
        assert say(intake, data) == []
    assert store.stats()["feedback"] == {}


def test_feedback_does_not_cancel_a_pending_report(intake, store):
    say(intake, "YES")
    verdict = say(intake, TEXTS["T1"])[-1]
    say(intake, "1")
    say(intake, next(d for _, d in verdict.buttons if d.startswith("fb:u:")))
    assert say(intake, "YES")[0].startswith("Reported.")


# --- Telegram ------------------------------------------------------------------------------------


class FakeTelegram:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        is_json = request.headers.get("content-type", "").startswith("application/json")
        self.calls.append((method, json.loads(request.content) if is_json else request.content))
        return httpx.Response(200, json={"ok": True, "result": {"file_path": "x"} if method == "getFile" else {}})


def api_with(fake):
    return TelegramAPI("1:T", client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_button_press_becomes_a_message():
    msg = parse_update({"update_id": 1, "callback_query": {
        "id": "cb9", "data": "samples", "from": {"language_code": "hi"},
        "message": {"message_id": 5, "chat": {"id": 42, "type": "private"}}}})
    assert (msg.chat_id, msg.text, msg.callback_id, msg.message_id, msg.lang_hint) == ("42", "samples", "cb9", 5, "hi")
    assert parse_update({"update_id": 1, "callback_query": {"id": "x", "data": "samples",
                                                            "message": {"chat": {"id": 1, "type": "group"}}}}) is None


def test_sender_shows_buttons_photos_and_toasts():
    fake = FakeTelegram()
    api = api_with(fake)
    press = Inbound("telegram", "42", "fb:u:L:abc", callback_id="cb1", message_id=5)
    TelegramSender(api, press)(Out("Thanks", toast=True))
    assert fake.calls == [("answerCallbackQuery", {"callback_query_id": "cb1", "text": "Thanks"}),
                          ("editMessageReplyMarkup", {"chat_id": "42", "message_id": 5, "reply_markup": {"inline_keyboard": []}})]

    fake.calls.clear()
    sender = TelegramSender(api, Inbound("telegram", "42", "samples", callback_id="cb2"))
    sender(Out("Pick one", buttons=[("A", "a"), ("B", "b"), ("C", "c")]))
    sender(Out("caption", photo=b"\x89PNG data"))
    assert fake.calls[0] == ("answerCallbackQuery", {"callback_query_id": "cb2"})  # stop the spinner once
    assert fake.calls[1] == ("sendMessage", {"chat_id": "42", "text": "Pick one", "reply_markup": {
        "inline_keyboard": [[{"text": "A", "callback_data": "a"}, {"text": "B", "callback_data": "b"}],
                            [{"text": "C", "callback_data": "c"}]]}})
    method, body = fake.calls[2]
    assert method == "sendPhoto" and b"\x89PNG data" in body and b"caption" in body
    assert len(fake.calls) == 3


def test_polling_asks_for_button_presses():
    fake = FakeTelegram()
    api_with(fake).get_updates(None, timeout=0)
    assert fake.calls[0][1]["allowed_updates"] == ["message", "callback_query"]


# --- concurrency ------------------------------------------------------------------------------------


def test_one_slow_chat_does_not_block_another():
    dispatcher = Dispatcher(workers=4)
    release, done = threading.Event(), []
    dispatcher.submit("slow", lambda: (release.wait(5), done.append("slow")))
    dispatcher.submit("fast", lambda: done.append("fast"))
    deadline = time.time() + 5
    while "fast" not in done and time.time() < deadline:
        time.sleep(0.01)
    assert done == ["fast"]  # finished while the slow chat was still busy
    release.set()
    dispatcher.shutdown()
    assert done == ["fast", "slow"]


def test_messages_in_one_chat_stay_in_order():
    dispatcher = Dispatcher(workers=4)
    seen = []
    for i in range(20):
        dispatcher.submit("chat", lambda i=i: (time.sleep(0.001 * (i % 3)), seen.append(i)))
    dispatcher.shutdown()
    assert seen == list(range(20))


def test_poller_uses_the_dispatcher(tmp_path):
    class Updates(FakeTelegram):
        def __call__(self, request):
            if request.url.path.endswith("getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [
                    {"update_id": 3, "message": {"chat": {"id": 1, "type": "private"}, "text": "hi"}},
                    {"update_id": 4, "message": {"chat": {"id": 2, "type": "private"}, "text": "hi"}}]})
            return super().__call__(request)

    fake = Updates()
    dispatcher = Dispatcher()
    intake = Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path)
    assert run_once(api_with(fake), intake, None, timeout=0, dispatcher=dispatcher) == 5
    dispatcher.shutdown()
    assert sorted(c[1]["chat_id"] for c in fake.calls if c[0] == "sendMessage") == ["1", "2"]


def test_database_uses_wal_for_concurrent_access(store):
    with store._db() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# --- stats --------------------------------------------------------------------------------------------


@pytest.fixture
def reviewer(store, tmp_path):
    deps = console_mod.Console(store, None, tmp_path / "packets", ("asha",), "pw", b"k" * 32)
    main.app.dependency_overrides[console_mod.console_deps] = lambda: deps
    console_mod.limiter.clear()
    client = TestClient(main.app)
    yield client
    main.app.dependency_overrides.pop(console_mod.console_deps, None)


def test_stats_are_for_reviewers_only(reviewer):
    assert reviewer.get("/console/stats", follow_redirects=False).status_code == 303


def test_stats_show_counts_and_no_content(reviewer, intake, store):
    say(intake, "YES")
    say(intake, TEXTS["T1"])
    voted = say(intake, TEXTS["T2"])[-1]
    say(intake, next(d for _, d in voted.buttons if d.startswith("fb:u:")))
    say(intake, "YES", chat="2")
    say(intake, TEXTS["T4"], chat="2")
    s = store.stats()
    assert s["users"] == 2 and s["checks"] == 3
    assert s["by_kind"] == {"text": 3} and s["by_channel"] == {"telegram": 3}
    assert s["by_verdict"] == {"likely_fake": 2, "cannot_verify": 1}
    assert s["feedback"] == {"helpful": 1}
    assert sum(s["per_hour"].values()) == 3

    reviewer.post("/console/login", data={"username": "asha", "password": "pw"})
    page = reviewer.get("/console/stats").text
    assert "Unique users" in page and "Checks per hour" in page and "Feedback by verdict" in page
    with store._db() as db:
        hashes = [r[0] for r in db.execute("SELECT sha256 FROM items")]
    for secret in ["Navdeep", "000912345678", "पापा", *hashes, *(h[:12] for h in hashes)]:
        assert secret not in page
    with store._db() as db:
        assert [c["name"] for c in db.execute("PRAGMA table_info(checks)")] == ["id", "at", "kind", "channel", "verdict"]


# --- web -----------------------------------------------------------------------------------------------


@pytest.fixture
def web(store):
    main.app.dependency_overrides[main.web_store] = lambda: store
    main._votes.clear()
    yield TestClient(main.app)
    main.app.dependency_overrides[main.web_store] = lambda: None


def test_web_check_is_counted_and_offers_feedback(web, store):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    assert "Was this helpful?" in page and 'value="helpful"' in page
    assert store.stats()["by_channel"] == {"web": 1}
    sha = re.search(r'name="sha" value="([0-9a-f]{64})"', page).group(1)
    assert "Thank you for your feedback." in web.post("/feedback", data={"sha": sha, "verdict": "likely_fake",
                                                                          "vote": "helpful"}).text
    assert store.stats()["feedback"] == {"helpful": 1}


def test_web_feedback_rejects_junk(web, store):
    for form in ({"sha": "x", "verdict": "likely_fake", "vote": "helpful"},
                 {"sha": "a" * 64, "verdict": "great", "vote": "helpful"},
                 {"sha": "a" * 64, "verdict": "likely_fake", "vote": "love"}):
        assert "could not record" in web.post("/feedback", data=form).text
    assert store.stats()["feedback"] == {}


def test_try_page(web):
    page = web.get("/try").text
    assert "<svg" in page and "t.me/Trinetra_check_bot" in page
    assert page.count('<li><strong>') == 3
    assert "No Telegram app on this computer? Open it on your phone, or use" in page and "web.telegram.org" in page
    assert "/try/samples/forged-warrant.png" in page
    f1 = web.get("/try/samples/forged-warrant.png")
    assert f1.status_code == 200 and f1.content == F1.read_bytes()
    assert web.get("/try/samples/../../.env.png").status_code == 404
    assert web.get("/try/samples/summons.png").status_code == 404  # not configured in tests
