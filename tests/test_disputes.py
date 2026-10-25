"""Disputes ("this message is actually genuine"), and the web page's report and already-paid."""

import csv
import re

import pytest
from fastapi.testclient import TestClient

from app import console as console_mod
from app import main
from app.intake import DISPUTE_ASK, DISPUTE_OUTCOME, DISPUTE_THANKS, MESSAGES, NOTE_ASK, NOTE_THANKS, Inbound, Intake
from app.pipeline import check_text, sha256_text
from app.reply import ALREADY_PAID
from app.store import Store
from tests.test_judge_mode import TEXTS


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.fixture
def intake(store, tmp_path):
    return Intake(store, salt="s", data_dir=tmp_path)


def say(intake, text=None, chat="1", **kw) -> list:
    out: list = []
    intake.handle(Inbound("telegram", chat, text, **kw), out.append)
    return out


def button(out, prefix: str) -> str:
    return next(d for _, d in out.buttons if d.startswith(prefix))


def not_helpful(intake, text=TEXTS["T1"], chat="1"):
    """Check a message, tap Not helpful, and return the follow-up question."""
    verdict = say(intake, text, chat=chat)[-1]
    out = say(intake, button(verdict, "fb:d:"), chat=chat, callback_id="cb")
    return out[-1]


# --- the bot -------------------------------------------------------------------------------------


def test_not_helpful_asks_if_the_message_was_genuine(intake):
    say(intake, "YES")
    ask = not_helpful(intake)
    assert str(ask) == DISPUTE_ASK["en"]
    assert [label for label, _ in ask.buttons] == ["Yes, it's genuine", "No, other reason"]
    assert all(len(d.encode()) <= 64 for _, d in ask.buttons)


def test_helpful_does_not_ask(intake):
    say(intake, "YES")
    verdict = say(intake, TEXTS["T1"])[-1]
    out = say(intake, button(verdict, "fb:u:"), callback_id="cb")
    assert [str(o) for o in out] == ["Thank you for your feedback."]


def test_yes_its_genuine_opens_a_dispute_with_the_verdict_and_signals(intake, store):
    say(intake, "YES")
    ask = not_helpful(intake)
    [thanks] = say(intake, button(ask, "dsp:y:"), callback_id="cb2")
    assert str(thanks) == DISPUTE_THANKS["en"]
    [d] = store.disputes()
    expected = check_text(TEXTS["T1"])
    assert (d["status"], d["verdict"], d["rule_row"], d["kind"]) == ("open", "likely_fake", expected.rule_row, "text")
    assert d["sha256"] == sha256_text(TEXTS["T1"]) and d["channel"] == "telegram" and d["chat_id"] == "1"
    assert "ask.credential_request" in d["signals_json"]
    # No message text or media in the dispute: the verdict and the signals only.
    with store._db() as db:
        columns = [c["name"] for c in db.execute("PRAGMA table_info(disputes)")]
    assert not {"text", "media_path", "evidence_path"} & set(columns)
    assert TEXTS["T1"][:40] not in str(dict(d))
    assert "dispute_opened" in [a["action"] for a in store.audit_entries()]
    # Tapping again does not open a second one.
    say(intake, button(ask, "dsp:y:"), callback_id="cb3")
    assert len(store.disputes()) == 1


def test_dispute_thanks_in_hindi(intake):
    say(intake, "YES")
    say(intake, "HINDI")
    ask = not_helpful(intake)
    assert str(ask) == DISPUTE_ASK["hi"]
    assert [str(o) for o in say(intake, button(ask, "dsp:y:"), callback_id="cb")] == [DISPUTE_THANKS["hi"]]


def test_no_other_reason_keeps_an_optional_masked_line(intake, store):
    say(intake, "YES")
    ask = not_helpful(intake)
    assert [str(o) for o in say(intake, button(ask, "dsp:n:"), callback_id="cb")] == [NOTE_ASK["en"]]
    assert [str(o) for o in say(intake, "the bank told me my PAN ABCDE1234F is fine")] == [NOTE_THANKS["en"]]
    with store._db() as db:
        note = db.execute("SELECT note FROM feedback WHERE vote = 'not_helpful'").fetchone()[0]
    assert "ABCDE1234F" not in note and "PAN" in note
    assert store.disputes() == []


def test_the_note_is_optional(intake, store):
    say(intake, "YES")
    ask = not_helpful(intake)
    say(intake, button(ask, "dsp:n:"), callback_id="cb")
    # A new message to check is checked as usual, not taken as the note.
    out = say(intake, TEXTS["T1"] + "\nSecond line")
    assert str(out[-1]).startswith("Likely fake")
    with store._db() as db:
        assert db.execute("SELECT note FROM feedback").fetchone()[0] is None


def test_delete_erases_disputes(intake, store):
    say(intake, "YES")
    say(intake, button(not_helpful(intake), "dsp:y:"), callback_id="cb")
    say(intake, "DELETE")
    assert store.disputes() == []


def test_bad_dispute_data_is_ignored(intake, store):
    say(intake, "YES")
    for data in ("dsp:y:L", "dsp:y:L:<x>", "dsp:y:L:abc:def"):
        say(intake, data, callback_id="cb")
    assert store.disputes() == []


# --- the console -------------------------------------------------------------------------------


@pytest.fixture
def console(store, tmp_path):
    sent: list = []
    deps = console_mod.Console(store, None, tmp_path / "packets", ("asha",), "pw", b"k" * 32,
                               notify=lambda *a: sent.append(a), disputed_csv=tmp_path / "disputed.csv")
    main.app.dependency_overrides[console_mod.console_deps] = lambda: deps
    console_mod.limiter.clear()
    client = TestClient(main.app)
    yield client, deps, sent
    main.app.dependency_overrides.pop(console_mod.console_deps, None)


def open_dispute(intake, chat="1", lang=None):
    say(intake, "YES", chat=chat)
    if lang:
        say(intake, lang, chat=chat)
    say(intake, button(not_helpful(intake, chat=chat), "dsp:y:"), chat=chat, callback_id="cb")


def resolve(client, dispute_id, action, note):
    page = client.get("/console/disputes").text
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    return client.post(f"/console/dispute/{dispute_id}/resolve", data={"action": action, "note": note, "csrf": token},
                       follow_redirects=False)


def test_disputes_queue_is_for_reviewers_only(console):
    client, _, _ = console
    assert client.get("/console/disputes", follow_redirects=False).status_code == 303
    assert client.post("/console/dispute/1/resolve", data={"action": "kept", "note": "x"},
                       follow_redirects=False).status_code == 303


def test_confirm_genuine_needs_a_note_and_csrf_then_is_logged_and_added_to_the_csv(console, intake, store, tmp_path):
    client, deps, sent = console
    open_dispute(intake, lang="HINDI")
    client.post("/console/login", data={"username": "asha", "password": "pw"})
    page = client.get("/console/disputes").text
    assert "Dispute 1" in page and "Confirm genuine" in page and "Keep verdict" in page
    assert TEXTS["T1"][:40] not in page
    assert resolve(client, 1, "confirmed_genuine", "  ").status_code == 400
    assert client.post("/console/dispute/1/resolve", data={"action": "kept", "note": "x", "csrf": "bad"},
                       follow_redirects=False).status_code in (400, 403)
    assert store.dispute(1)["status"] == "open"

    assert resolve(client, 1, "confirmed_genuine", "Checked with the bank").status_code == 303
    d = store.dispute(1)
    assert (d["status"], d["reviewer"], d["note"]) == ("confirmed_genuine", "asha", "Checked with the bank")
    actions = [a["action"] for a in store.audit_entries()]
    assert "dispute_confirmed_genuine" in actions and "dispute_user_notified" in actions
    # The person hears the outcome in their language, then their chat id is forgotten.
    assert sent == [("telegram", "1", DISPUTE_OUTCOME["confirmed_genuine"]["hi"])]
    assert d["chat_id"] is None
    rows = list(csv.DictReader(open(tmp_path / "disputed.csv", encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["verdict"] == "likely_fake" and rows[0]["reviewer"] == "asha"
    assert "ask.credential_request" in rows[0]["signal_codes"]
    # Rules never change on their own: the same message gets the same verdict.
    assert check_text(TEXTS["T1"]).verdict.value == "likely_fake"
    # A decided dispute cannot be decided again.
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # the page has no open forms left
    client.post("/console/dispute/1/resolve", data={"action": "kept", "note": "again", "csrf": token})
    assert store.dispute(1)["status"] == "confirmed_genuine" and len(sent) == 1


def test_keep_verdict_tells_the_person_and_adds_nothing_to_the_csv(console, intake, store, tmp_path):
    client, _, sent = console
    open_dispute(intake)
    client.post("/console/login", data={"username": "asha", "password": "pw"})
    resolve(client, 1, "kept", "Payee is a private UPI ID")
    assert store.dispute(1)["status"] == "kept"
    assert sent == [("telegram", "1", DISPUTE_OUTCOME["kept"]["en"])]
    assert not (tmp_path / "disputed.csv").exists()
    assert "dispute_kept" in [a["action"] for a in store.audit_entries()]
    assert "Payee is a private UPI ID" in client.get("/console/disputes").text


def test_stats_show_open_and_resolved_disputes(console, intake, store):
    client, _, _ = console
    open_dispute(intake, chat="1")
    open_dispute(intake, chat="2")
    client.post("/console/login", data={"username": "asha", "password": "pw"})
    resolve(client, 1, "kept", "ok")
    assert store.stats()["disputes"] == {"open": 1, "kept": 1}
    page = client.get("/console/stats").text
    assert re.search(r'<span class="n">1</span><span class="l"><a href="/console/disputes">Open disputes', page)
    assert re.search(r'<span class="n">1</span><span class="l">Resolved disputes', page)


# --- the web page --------------------------------------------------------------------------------


@pytest.fixture
def web(store, tmp_path):
    from app.evidence import EvidenceStore

    main.app.dependency_overrides[main.web_store] = lambda: store
    main.app.dependency_overrides[main.web_evidence] = lambda: EvidenceStore(tmp_path / "evidence")
    main._votes.clear()
    yield TestClient(main.app)
    main.app.dependency_overrides.pop(main.web_evidence, None)


def hidden(page: str, name: str) -> str:
    return re.search(rf'name="{name}" value="([^"]*)"', page).group(1)


def test_home_page_has_a_qr_code_and_a_button_for_the_bot():
    page = TestClient(main.app).get("/").text
    assert '<div class="qr qr-sm"' in page and "<svg" in page
    assert 'href="https://t.me/Trinetra_check_bot"' in page and "Open the Telegram bot" in page


def test_web_result_offers_report_and_already_paid(web):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    assert "Report</summary>" in page and "I already paid</summary>" in page
    assert ALREADY_PAID["en"].replace("'", "&#x27;") in page
    assert 'href="tel:1930"' in page and "cybercrime.gov.in" in page and "Call your bank now" in page
    hi = web.post("/check", data={"text": TEXTS["T1"], "lang": "hi"}).text
    assert ALREADY_PAID["hi"] in hi


def test_web_report_needs_consent_then_becomes_a_case(web, store, tmp_path):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    token = hidden(page, "token")
    assert "Tick the box" in web.post("/report", data={"token": token}).text
    assert store.case_reports(1) == []
    done = web.post("/report", data={"token": token, "consent": "yes"}).text
    assert "Reported. Your report is case 1." in done
    [report] = store.case_reports(1)
    assert report["sha256"] == sha256_text(TEXTS["T1"]) and report["user_hash"] == "web"
    assert (tmp_path / "evidence").exists() and any((tmp_path / "evidence").rglob("*.txt"))
    assert "report" in [a["action"] for a in store.audit_entries()]
    assert "already reported" in web.post("/report", data={"token": token, "consent": "yes"}).text


def test_web_report_with_an_unknown_token_has_expired(web):
    assert "This check has expired" in web.post("/report", data={"token": "nope", "consent": "yes"}).text


def test_web_not_helpful_asks_then_opens_a_dispute(web, store):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    fields = {k: hidden(page, k) for k in ("sha", "verdict", "token")}
    ask = web.post("/feedback", data={**fields, "vote": "not_helpful"}).text
    assert DISPUTE_ASK["en"] in ask and "Yes, it&#x27;s genuine" in ask and "No, other reason" in ask
    done = web.post("/dispute", data={**fields, "answer": "genuine"}).text
    assert "A person will review this" in done
    [d] = store.disputes()
    assert (d["channel"], d["verdict"], d["chat_id"]) == ("web", "likely_fake", None)
    assert d["rule_row"] == check_text(TEXTS["T1"]).rule_row and "ask.credential_request" in d["signals_json"]


def test_web_other_reason_keeps_the_note(web, store):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    fields = {k: hidden(page, k) for k in ("sha", "verdict", "token")}
    web.post("/feedback", data={**fields, "vote": "not_helpful"})
    assert "noted it" in web.post("/dispute", data={**fields, "answer": "other", "note": "wrong language"}).text
    with store._db() as db:
        assert db.execute("SELECT note FROM feedback").fetchone()[0] == "wrong language"
    assert store.disputes() == []


def test_web_helpful_does_not_ask(web):
    page = web.post("/check", data={"text": TEXTS["T1"]}).text
    fields = {k: hidden(page, k) for k in ("sha", "verdict", "token")}
    assert DISPUTE_ASK["en"] not in web.post("/feedback", data={**fields, "vote": "helpful"}).text
