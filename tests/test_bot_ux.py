"""The bot's experience for older, non-technical users: consent without friction, buttons, short replies."""

import csv
import json
import re

import httpx
import pytest

from app import intake as intake_mod
from app import reply
from app.channels.telegram import COMMANDS, TelegramAPI, register_commands
from app.intake import CONSENT, CONSENT_SHORT, HELP, MESSAGES, NOTHING_STORED, WELCOME, Inbound, Intake
from app.models import Verdict
from app.store import Store
from tests.test_testset import TEST_SET

TEXTS = {r["id"]: r["text"] for r in csv.DictReader(open(TEST_SET / "texts.csv", encoding="utf-8"))}
F1 = (TEST_SET / "png" / "F1_sc_warrant_en.png").read_bytes()
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class Phone:
    """One person's Telegram chat with the bot."""

    def __init__(self, intake: Intake, chat: str = "1", app_lang: str = "en"):
        self.intake, self.chat, self.app_lang = intake, chat, app_lang
        self.fetches = 0

    def send(self, text=None, photo: bytes | None = None) -> list:
        def fetch():
            self.fetches += 1
            return photo

        out: list = []
        msg = Inbound("telegram", self.chat, text, "photo" if photo else None, self.app_lang,
                      fetch=fetch if photo else None)
        self.intake.handle(msg, out.append)
        return out

    def tap(self, out_message, startswith: str) -> list:
        data = next(d for _, d in out_message.buttons if d.startswith(startswith))
        out: list = []
        self.intake.handle(Inbound("telegram", self.chat, data, lang_hint=self.app_lang, callback_id="cb"), out.append)
        return out


@pytest.fixture
def intake(tmp_path):
    return Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path, samples=lambda: [])


@pytest.fixture
def phone(intake):
    p = Phone(intake)
    p.send("YES")
    return p


# --- consent without friction ------------------------------------------------------------


def test_first_item_is_held_and_checked_on_agree_without_resending(intake, tmp_path):
    p = Phone(intake)
    [consent] = p.send(TEXTS["T1"])
    assert consent == CONSENT_SHORT["en"]
    assert [label for label, _ in consent.buttons] == ["Agree and check", "Privacy details"]
    # Nothing is stored before consent: no user, no item, no media file.
    assert intake.store.user(intake.user_hash("telegram", "1")) is None
    assert not (tmp_path / "media").exists()
    out = p.tap(consent, "agree")
    assert out[0] == MESSAGES["en"]["checking"]
    assert out[1].startswith("Likely fake. Do not pay.")


def test_first_photo_is_not_downloaded_until_agree(intake):
    p = Phone(intake)
    [consent] = p.send(photo=F1)
    assert p.fetches == 0
    out = p.tap(consent, "agree")
    assert p.fetches == 1 and out[-1].startswith("Likely fake")


def test_typed_yes_also_checks_the_held_item(intake):
    p = Phone(intake)
    p.send(TEXTS["T2"])
    assert p.send("YES")[-1].startswith("संभवतः नकली। कोई भुगतान न करें।")


def test_a_greeting_is_not_held(intake):
    p = Phone(intake)
    [consent] = p.send("hello")
    assert [label for label, _ in consent.buttons][0] == "Agree"
    assert p.tap(consent, "agree") == [WELCOME["en"]]


def test_privacy_details_show_the_full_text_in_one_language(intake):
    p = Phone(intake, app_lang="hi")
    [consent] = p.send(TEXTS["T1"])  # an English message, from a phone set to Hindi
    assert consent == CONSENT_SHORT["hi"]
    [details] = p.tap(consent, "privacy")
    assert details == CONSENT["hi"] and "Trinetra एक छात्र प्रोटोटाइप है।" in details
    assert details.buttons == (("सहमत हूँ, जाँचें", "agree"),)
    assert p.tap(details, "agree")[-1].startswith("संभवतः नकली")


def test_held_item_expires(intake, monkeypatch):
    p = Phone(intake)
    [consent] = p.send(TEXTS["T1"])
    monkeypatch.setattr(intake_mod, "FIRST_ITEM_TTL_SECONDS", -1)
    assert p.tap(consent, "agree") == [WELCOME["en"]]


def test_delete_before_consent_drops_the_held_item(intake):
    p = Phone(intake)
    p.send(TEXTS["T1"])
    assert p.send("DELETE") == [NOTHING_STORED["en"]]
    assert p.send("YES") == [WELCOME["en"]]


# --- short replies -------------------------------------------------------------------------


def test_short_reply_shape(phone):
    verdict = phone.send(TEXTS["T1"])[-1]
    blocks = verdict.split("\n\n")
    assert len(blocks) == 4
    line, reason, action, footer = blocks
    assert line == "Likely fake. Do not pay."
    assert len(re.findall(r"[.!?](\s|$)", reason)) == 1  # one sentence
    assert action == reply.CREDENTIAL_ACTION["en"]  # T1 asks you to update your PAN through a link
    assert footer == "Automated check · not yet reviewed by a person"
    assert "Reply 1" not in verdict and "Reply 2" not in verdict


def test_cannot_verify_never_reads_as_safe(phone):
    verdict = phone.send(TEXTS["T3"])[-1]
    assert verdict.startswith("We couldn't verify this.")
    assert "That does not mean it is safe." in verdict


def test_verdict_buttons(phone):
    verdict = phone.send(TEXTS["T1"])[-1]
    assert [[label for label, _ in row] for row in verdict.rows] == [
        ["Why?", "Report"], ["I already paid"], ["Helpful", "Not helpful", "हिन्दी"]]
    assert all(len(data.encode()) <= 64 for _, data in verdict.buttons)


def test_why_shows_every_reason_and_the_review_status(phone):
    verdict = phone.send(TEXTS["T2"])[-1]
    [why] = phone.tap(verdict, "why:")
    assert why.startswith("Why we said this:")
    assert why.count("• ") >= 3
    assert why.endswith(reply.FOOTER_UNREVIEWED["en"])


def test_why_survives_a_restart_through_the_cache(intake, tmp_path):
    p = Phone(intake)
    p.send("YES")
    verdict = p.send(TEXTS["T2"])[-1]
    fresh = Intake(intake.store, salt="s", data_dir=tmp_path)  # a restarted bot: nothing in memory
    p.intake = fresh
    assert p.tap(verdict, "why:")[0].startswith("Why we said this:")
    intake.store.clear_results()
    p.intake = Intake(intake.store, salt="s", data_dir=tmp_path)
    assert p.tap(verdict, "why:") == [MESSAGES["en"]["why_expired"]]


def test_already_paid_button(phone):
    verdict = phone.send(TEXTS["T1"])[-1]
    assert phone.tap(verdict, "paid") == [reply.ALREADY_PAID["en"]]


def test_report_button_asks_once_then_reports(phone, intake):
    verdict = phone.send(TEXTS["T1"])[-1]
    [ask] = phone.tap(verdict, "rep:")
    assert ask == MESSAGES["en"]["report_ask"]
    assert [label for label, _ in ask.buttons] == ["Yes, report it", "Cancel"]
    assert phone.tap(ask, "repy:")[0].startswith("Reported. Your case number is 1.")
    assert len(intake.store.case_reports(1)) == 1


def test_report_cancel(phone, intake):
    verdict = phone.send(TEXTS["T1"])[-1]
    [ask] = phone.tap(verdict, "rep:")
    [cancelled] = phone.tap(ask, "repn")
    assert cancelled.toast and intake.store.cases() == []
    assert phone.send("YES")[0] == WELCOME["en"]  # the pending report is gone


def test_report_button_reports_the_item_it_was_under(phone, intake):
    first = phone.send(TEXTS["T1"])[-1]
    phone.send(TEXTS["T2"])
    [ask] = phone.tap(first, "rep:")
    phone.tap(ask, "repy:")
    [report] = intake.store.case_reports(1)
    assert json.loads(report["fields_json"])["upi_ids"] == ["navdeep.kyc@ybl"]  # T1, not the later T2


def test_language_toggle_resends_the_verdict_in_hindi(phone, intake):
    verdict = phone.send(TEXTS["T1"])[-1]
    [hindi] = phone.tap(verdict, "lang:hi")
    assert hindi.startswith("संभवतः नकली") and "English" in [label for label, _ in hindi.buttons]
    assert intake.store.user(intake.user_hash("telegram", "1"))["lang"] == "hi"
    [english] = phone.tap(hindi, "lang:en")
    assert english == verdict


# --- typed commands still work ---------------------------------------------------------------


def test_typed_commands(phone, intake):
    assert phone.send("HINDI") == [MESSAGES["hi"]["lang_set"]]
    assert phone.send("ENGLISH") == [MESSAGES["en"]["lang_set"]]
    assert phone.send("/language") == [MESSAGES["hi"]["lang_set"]]
    phone.send("/language")
    phone.send(TEXTS["T1"])
    assert phone.send("1") == [MESSAGES["en"]["report_confirm"]]
    assert phone.send("YES")[0].startswith("Reported.")
    assert phone.send("2") == [reply.ALREADY_PAID["en"]]
    assert phone.send("/delete") == [MESSAGES["en"]["deleted"]]


def test_help_works_before_and_after_consent(intake):
    p = Phone(intake)
    [help_] = p.send("/help")
    assert help_ == HELP["en"] and "WhatsApp" in help_ and "Instagram or SMS" in help_
    assert help_.buttons == (("Try a sample", "samples"),)
    p.send("YES")
    assert p.send("/help") == [HELP["en"]]


def test_every_message_is_in_one_language(intake):
    p = Phone(intake)
    for step in (TEXTS["T1"], "agree", "/help", "/samples", TEXTS["T3"], "2", "1", "YES"):
        for message in p.send(step):
            assert not DEVANAGARI.search(message), message
    hi = Phone(intake, chat="2", app_lang="hi")
    for message in hi.send(TEXTS["T1"]) + hi.send("agree") + hi.send("/help"):
        assert "Before I check" not in message and "Likely fake" not in message and "How to check" not in message


# --- command menu ------------------------------------------------------------------------------


def test_command_menu_is_registered_in_english_and_hindi():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    register_commands(TelegramAPI("1:T", client=httpx.Client(transport=httpx.MockTransport(handler))))
    assert [c.get("language_code") for c in calls] == [None, "hi"]
    assert [c["command"] for c in calls[0]["commands"]] == ["help", "samples", "language", "delete"]
    assert calls[1]["commands"][0]["description"] == COMMANDS["hi"][0][1]


def test_checks_and_verdicts_are_unchanged(phone):
    """The overhaul changes wording only: the web/API reply and the verdicts stay as they were."""
    from app.pipeline import check_text

    for item, verdict in (("T1", Verdict.LIKELY_FAKE), ("T2", Verdict.LIKELY_FAKE), ("T3", Verdict.CANNOT_VERIFY),
                          ("T4", Verdict.CANNOT_VERIFY)):
        result = check_text(TEXTS[item])
        assert result.verdict is verdict
        assert result.reply.startswith(reply.VERDICT_LINE[result.lang][verdict])


# --- which language a verdict is in ------------------------------------------------------------


@pytest.mark.parametrize("app_lang,item,expected", [
    ("hi", "T1", "संभवतः नकली"),   # 2. a Hindi phone gets Hindi, even for an English message
    ("hi", "T2", "संभवतः नकली"),
    ("en", "T2", "संभवतः नकली"),   # 3. otherwise the message's own language
    ("en", "T1", "Likely fake"),
])
def test_verdict_language_from_phone_then_message(intake, app_lang, item, expected):
    p = Phone(intake, app_lang=app_lang)
    p.send("YES")
    assert p.send(TEXTS[item])[-1].startswith(expected)


@pytest.mark.parametrize("app_lang,choice,item,expected", [
    ("hi", "ENGLISH", "T2", "Likely fake"),  # 1. an explicit choice beats the phone and the message
    ("en", "HINDI", "T1", "संभवतः नकली"),
])
def test_explicit_language_choice_wins(intake, app_lang, choice, item, expected):
    p = Phone(intake, app_lang=app_lang)
    p.send("YES")
    p.send(choice)
    assert p.send(TEXTS[item])[-1].startswith(expected)


def test_hindi_phone_gets_hindi_for_an_english_photo(intake):
    p = Phone(intake, app_lang="hi")
    p.send("YES")
    assert p.send(photo=F1)[-1].startswith("संभवतः नकली")


def test_bot_verdicts_and_buttons_use_plain_words(intake):
    """No emoji in verdict lines or buttons, in either language."""
    emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    for app_lang in ("en", "hi"):
        p = Phone(intake, chat=f"plain-{app_lang}", app_lang=app_lang)
        messages = p.send(TEXTS["T1"])
        messages += p.tap(messages[-1], "agree")
        messages += p.send("/help") + p.send("😀 👍")
        for m in messages:
            assert not emoji.search(m.split("\n")[0]), m
            for label, _ in getattr(m, "buttons", ()):
                assert not emoji.search(label), label
    assert [reply.SHORT_VERDICT["en"][v] for v in (Verdict.LIKELY_FAKE, Verdict.SUSPICIOUS, Verdict.CANNOT_VERIFY,
                                                  Verdict.VERIFIED_OFFICIAL)] == [
        "Likely fake. Do not pay.", "Suspicious. Check before you pay.", "We couldn't verify this.", "Verified official."]


# --- "could not check": a short text is not an unreadable file ------------------------------------


def test_short_text_says_it_is_too_short(phone):
    reply_ = phone.send("Ok")[-1]  # one word: nothing readable to check (R0)
    assert reply_.split("\n\n")[:2] == ["This message is too short to check.",
                                       "Forward the full message, or a screenshot of it."]
    assert "file" not in reply_


def test_short_text_in_hindi(intake):
    p = Phone(intake, chat="hi-short", app_lang="hi")
    p.send("YES")
    assert p.send("ठीक")[-1].startswith("यह संदेश जाँच के लिए बहुत छोटा है।\n\nपूरा संदेश या उसका स्क्रीनशॉट फ़ॉरवर्ड करें।")


def test_unreadable_photo_still_says_file(phone):
    import io

    from PIL import Image

    blank = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(blank, "PNG")
    assert phone.send(photo=blank.getvalue())[-1].startswith("We couldn't read this file.")


def test_web_page_short_text_says_too_short():
    from fastapi.testclient import TestClient

    from app.main import app

    page = TestClient(app).post("/check", data={"text": "ok"}).text
    assert "This message is too short to check. Forward the full message, or a screenshot of it." in page
    assert "couldn&#x27;t read this file" not in page and "could not read this file" not in page


def test_web_page_short_hindi_text_gets_hindi():
    from app.pipeline import check_text

    assert check_text("ठीक").reply.startswith("यह संदेश जाँच के लिए बहुत छोटा है।")
    assert check_text("😀 👍").reply.startswith("This message is too short to check.")
