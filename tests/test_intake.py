import csv
from pathlib import Path

import pytest

from app import intake as intake_mod
from app import pipeline
from app.intake import CONSENT, CONSENT_SHORT, MESSAGES, NOTHING_STORED, WELCOME, Inbound, Intake
from app.reply import ALREADY_PAID
from app.store import Store

TEXTS = {
    r["id"]: r["text"]
    for r in csv.DictReader(open(Path(__file__).parent.parent / "data/test-set/texts.csv", encoding="utf-8"))
}


class Chat:
    """One user talking to the bot; records everything the bot sends back."""

    def __init__(self, intake: Intake, chat_id: str = "1001", lang_hint: str | None = "en"):
        self.intake, self.chat_id, self.lang_hint = intake, chat_id, lang_hint

    def say(self, text: str | None = None, media: str | None = None) -> list[str]:
        out: list[str] = []
        self.intake.handle(Inbound("telegram", self.chat_id, text, media, self.lang_hint), out.append)
        return out


@pytest.fixture
def calls():
    return []


@pytest.fixture
def intake(tmp_path, calls):
    def counting_check(*args, **kwargs):
        calls.append(1)
        return pipeline.check_text(*args, **kwargs)

    return Intake(Store(tmp_path / "t.db"), salt="test-salt", check=counting_check)


@pytest.fixture
def chat(intake):
    c = Chat(intake)
    c.say("YES")
    return c


def test_first_message_gets_consent_and_is_not_checked(intake, calls):
    c = Chat(intake)
    assert c.say(TEXTS["T1"]) == [CONSENT_SHORT["en"]]
    assert calls == []
    assert intake.store.user(intake.user_hash("telegram", "1001")) is None


def test_consent_notice_is_in_both_languages():
    # One language per message: each version stands alone.
    assert "Reply YES" in CONSENT["en"] and "जारी रखने के लिए YES भेजें" in CONSENT["hi"]
    assert not any("\u0900" <= ch <= "\u097f" for ch in CONSENT["en"] + CONSENT_SHORT["en"])


def test_yes_gives_consent(intake):
    c = Chat(intake)
    assert c.say("yes") == [WELCOME["en"]]
    assert intake.store.user(intake.user_hash("telegram", "1001"))["consent_at"]


def test_checking_then_verdict(chat):
    out = chat.say(TEXTS["T1"])
    assert out[0] == MESSAGES["en"]["checking"]
    assert out[1].startswith("Likely fake. Do not pay.")


def test_repeat_forward_answered_from_cache(chat, calls):
    first = chat.say(TEXTS["T1"])
    second = chat.say(TEXTS["T1"])
    assert calls == [1]
    assert second == [first[1]]  # no "Checking…", same answer


def test_cache_is_shared_across_users(intake, chat, calls):
    chat.say(TEXTS["T2"])
    other = Chat(intake, chat_id="2002")
    other.say("YES")
    assert other.say(TEXTS["T2"])[0].startswith("संभवतः नकली")
    assert calls == [1]


def test_hindi_and_english_commands(chat):
    assert chat.say("HINDI") == [MESSAGES["hi"]["lang_set"]]
    assert chat.say(TEXTS["T1"])[1].startswith("संभवतः नकली")
    assert chat.say("english") == [MESSAGES["en"]["lang_set"]]
    assert chat.say(TEXTS["T1"])[0].startswith("Likely fake")


def test_delete_erases_and_asks_for_consent_again(intake, chat, calls):
    chat.say(TEXTS["T1"])
    uid = intake.user_hash("telegram", "1001")
    assert chat.say("DELETE") == [MESSAGES["en"]["deleted"]]
    assert intake.store.user(uid) is None
    assert intake.store.items_since(uid, intake_mod.timedelta(days=1)) == 0
    assert intake.store.cached_result(pipeline.sha256_text(TEXTS["T1"]), intake_mod.ENGINE_VERSION) is None
    assert chat.say(TEXTS["T1"]) == [CONSENT_SHORT["en"]]


def test_delete_keeps_cache_entries_other_users_still_need(intake, chat):
    other = Chat(intake, chat_id="2002")
    other.say("YES")
    other.say(TEXTS["T1"])
    chat.say(TEXTS["T1"])
    chat.say("DELETE")
    assert intake.store.cached_result(pipeline.sha256_text(TEXTS["T1"]), intake_mod.ENGINE_VERSION) is not None


def test_delete_before_consent(intake):
    assert Chat(intake).say("DELETE") == [NOTHING_STORED["en"]]


def test_voice_and_video_are_coming_soon_in_users_language(chat):
    assert chat.say(media="video") == [MESSAGES["en"]["media_soon"]]
    chat.say("HINDI")
    assert chat.say(media="voice") == [MESSAGES["hi"]["media_soon"]]


def test_media_language_falls_back_to_client_language(intake):
    c = Chat(intake, lang_hint="hi")
    c.say("YES")
    assert c.say(media="voice") == [MESSAGES["hi"]["media_soon"]]


def test_already_paid(chat):
    assert chat.say("2") == [ALREADY_PAID["en"]]


def test_rate_limit_per_hour(chat, calls):
    for i in range(10):
        assert len(chat.say(f"message number {i} about my bank account")) == 2
    assert chat.say("one more message about my account") == [MESSAGES["en"]["limit_hour"]]
    assert len(calls) == 10


def test_rate_limit_per_day(intake, chat, monkeypatch):
    monkeypatch.setattr(intake.store, "items_since", lambda uid, since: 50 if since.days == 1 else 0)
    assert chat.say(TEXTS["T3"]) == [MESSAGES["en"]["limit_day"]]


def test_message_text_is_not_stored(intake, chat):
    chat.say(TEXTS["T2"])
    raw = (intake.store.path).read_bytes()
    assert "ड्रग्स".encode() not in raw
    assert b"000912345678" not in raw


def test_chat_id_is_not_stored(intake, chat):
    chat.say(TEXTS["T1"])
    assert b"1001" not in intake.store.path.read_bytes()


def test_check_error_is_reported_without_crashing(tmp_path):
    def broken(*a, **k):
        raise RuntimeError("boom")

    c = Chat(Intake(Store(tmp_path / "t.db"), salt="s", check=broken))
    c.say("YES")
    assert c.say(TEXTS["T1"]) == [MESSAGES["en"]["checking"], MESSAGES["en"]["error"]]


def test_missing_salt_is_refused(tmp_path):
    with pytest.raises(ValueError):
        Intake(Store(tmp_path / "t.db"), salt="")
