"""Milestone 4: report flow, cases, evidence, media expiry, DELETE, review console and packets."""

import csv
import io
import json
import re
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import console as console_mod
from app.evidence import EvidenceStore, IntegrityError, LockedError
from app.intake import MESSAGES, Inbound, Intake
from app.jobs import purge_media
from app.main import app
from app.packet import FOOTER
from app.pipeline import check_file, sha256_bytes, sha256_text
from app.store import Store, now
from tests.test_testset import TEST_SET

F1_PNG = (TEST_SET / "png/F1_sc_warrant_en.png").read_bytes()
F3_PNG = (TEST_SET / "png/F3_ed_freeze_bi.png").read_bytes()
TEXTS = {r["id"]: r["text"] for r in csv.DictReader(open(TEST_SET / "texts.csv", encoding="utf-8"))}
EN = MESSAGES["en"]


class User:
    def __init__(self, intake: Intake, chat_id: str):
        self.intake, self.chat_id = intake, chat_id
        self.say("YES")

    def say(self, text=None, photo: bytes | None = None) -> list[str]:
        out: list[str] = []
        fetch = (lambda: photo) if photo is not None else None
        self.intake.handle(Inbound("telegram", self.chat_id, text, "photo" if photo else None, "en", None, None, fetch), out.append)
        return out

    def report(self) -> list[str]:
        assert self.say("1") == [EN["report_confirm"]]
        return self.say("YES")


@pytest.fixture
def intake(tmp_path):
    return Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path)


def case_number(message: str) -> int:
    return int(re.search(r"case number is (\d+)", message).group(1))


# --- report flow ----------------------------------------------------------------


def test_report_flow_stores_evidence_and_opens_a_case(intake, tmp_path):
    u = User(intake, "1")
    u.say(photo=F1_PNG)
    done = u.report()
    assert done == [EN["report_done"].format(case=1)]

    sha = sha256_bytes(F1_PNG)
    evidence = tmp_path / "evidence" / f"{sha}.png"
    assert evidence.read_bytes() == F1_PNG
    assert not intake.evidence.is_locked(evidence)  # locked only when a reviewer approves the case

    [report] = intake.store.case_reports(1)
    assert report["sha256"] == sha and report["size"] == len(F1_PNG) and report["verdict"] == "likely_fake"
    fields = json.loads(report["fields_json"])
    assert fields["accounts"][0]["number"] == "50200077118843"  # full payee, kept only after consent
    assert intake.store.case_payees(1) == ["50200077118843"]
    assert [a["action"] for a in intake.store.audit_entries()][0] == "report"


def test_anything_but_yes_cancels_the_report(intake):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.say("1")
    assert u.say("no thanks")[0] != EN["report_done"].format(case=1)
    assert intake.store.cases() == []
    assert u.say("YES")[0].startswith("Thank you.")  # plain YES is no longer a report confirmation


def test_nothing_to_report(intake):
    assert User(intake, "1").say("1") == [EN["report_nothing"]]


def test_expired_file_cannot_be_reported(intake):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    for path in intake.store.expire_media(now() + timedelta(hours=25)):
        Path(path).unlink()
    assert u.say("1") == [EN["report_expired"]]


def test_same_user_cannot_report_twice(intake):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    u.say(TEXTS["T1"])
    assert u.say("1") == [EN["already_reported"].format(case=1)]


def test_text_report_keeps_the_text_as_evidence(intake, tmp_path):
    u = User(intake, "1")
    u.say(TEXTS["T2"])
    u.report()
    path = tmp_path / "evidence" / f"{sha256_text(TEXTS['T2'])}.txt"
    assert path.read_text(encoding="utf-8") == TEXTS["T2"]


# --- clustering ----------------------------------------------------------------


def test_same_file_from_two_users_is_one_case(intake):
    a, b = User(intake, "1"), User(intake, "2")
    for u in (a, b):
        u.say(photo=F1_PNG)
        u.report()
    [case] = intake.store.cases()
    assert case["reports"] == 2


def test_same_payee_in_different_items_is_one_case(intake):
    # T2 (a CBI text) and F3 (an ED notice) both ask for money to A/c 000912345678.
    a, b = User(intake, "1"), User(intake, "2")
    a.say(TEXTS["T2"])
    assert case_number(a.report()[0]) == 1
    b.say(photo=F3_PNG)
    assert case_number(b.report()[0]) == 1
    assert intake.store.case_payees(1) == ["000912345678"]


def test_unrelated_items_get_separate_cases(intake):
    a, b = User(intake, "1"), User(intake, "2")
    a.say(TEXTS["T1"])
    a.report()
    b.say(photo=F1_PNG)
    assert case_number(b.report()[0]) == 2


def test_merchant_in_a_debit_alert_does_not_cluster(intake):
    a = User(intake, "1")
    a.say(TEXTS["T3"])  # mentions shop.ram@okaxis but asks for nothing
    a.report()
    assert intake.store.case_payees(1) == []


def test_a_report_linking_two_open_cases_merges_them(intake):
    a, b, c = User(intake, "1"), User(intake, "2"), User(intake, "3")
    a.say(TEXTS["T1"])
    a.report()  # case 1: navdeep.kyc@ybl
    b.say(TEXTS["T2"])
    b.report()  # case 2: 000912345678
    c.say("CBI notice. Pay Rs 5,000 today to navdeep.kyc@ybl or A/c 000912345678 IFSC XYZB0007733.")
    assert case_number(c.report()[0]) == 1
    [case] = intake.store.cases()
    assert case["reports"] == 3


# --- media expiry and DELETE ---------------------------------------------------------


def test_unreported_media_is_deleted_after_expiry_and_reported_media_kept(intake):
    a, b = User(intake, "1"), User(intake, "2")
    a.say(TEXTS["T1"])
    b.say(TEXTS["T4"])
    a.report()
    assert len(list(intake.media.root.glob("*"))) == 2
    gone = intake.store.expire_media(now() + timedelta(hours=25))
    for p in gone:
        Path(p).unlink()
    assert [Path(p).name for p in gone] == [f"{sha256_text(TEXTS['T4'])}.txt"]
    assert intake.media.root.joinpath(f"{sha256_text(TEXTS['T1'])}.txt").exists()


def test_purge_job_audits(intake):
    User(intake, "1").say(TEXTS["T4"])
    assert purge_media(intake.store, intake.media) == 0  # nothing expired yet
    with intake.store._db() as db:
        db.execute("UPDATE items SET expires_at = ?", ((now() - timedelta(seconds=1)).isoformat(),))
    assert purge_media(intake.store, intake.media) == 1
    assert list(intake.media.root.glob("*")) == []
    assert intake.store.audit_entries()[0]["action"] == "media_purged"


def test_delete_erases_unapproved_report_and_its_evidence(intake, tmp_path):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    u.say("DELETE")
    assert intake.store.cases() == []
    assert list((tmp_path / "evidence").glob("*")) == []
    assert list(intake.media.root.glob("*")) == []


def test_delete_keeps_approved_evidence_but_unlinks_the_user(intake, tmp_path):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    intake.store.review(1, "asha", "approve_fake")
    u.say("DELETE")
    [report] = intake.store.case_reports(1)
    assert report["user_hash"] is None
    assert (tmp_path / "evidence" / f"{sha256_text(TEXTS['T1'])}.txt").exists()


# --- evidence store ----------------------------------------------------------------------


def test_evidence_is_create_once_and_verified(tmp_path):
    store = EvidenceStore(tmp_path)
    sha = sha256_bytes(b"notice")
    path = store.put(b"notice", sha, ".txt")
    assert store.put(b"notice", sha, ".txt") == path  # same bytes again: no-op
    with pytest.raises(IntegrityError):
        store.put(b"other", sha, ".txt")
    path.write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        store.get(path)


def test_evidence_is_locked_on_approval_and_cannot_then_be_erased(tmp_path):
    store = EvidenceStore(tmp_path)
    path = store.put(b"notice", sha256_bytes(b"notice"), ".txt")
    assert store.lock(path) is True and store.lock(path) is False
    assert not path.stat().st_mode & stat.S_IWUSR
    with pytest.raises(PermissionError):
        path.write_bytes(b"tampered")
    with pytest.raises(LockedError):
        store.erase(path)
    assert path.exists()


def test_unlocked_evidence_can_be_erased(tmp_path):
    store = EvidenceStore(tmp_path)
    path = store.put(b"notice", sha256_bytes(b"notice"), ".txt")
    store.erase(path)
    assert not path.exists()


# --- console --------------------------------------------------------------------------


@pytest.fixture
def console(intake, tmp_path):
    deps = console_mod.Console(
        intake.store, intake.evidence, tmp_path / "packets", ("asha",), "correct horse", b"k" * 32
    )
    app.dependency_overrides[console_mod.console_deps] = lambda: deps
    console_mod._failed.clear()
    yield TestClient(app), deps
    app.dependency_overrides.pop(console_mod.console_deps, None)


def login(client):
    resp = client.post("/console/login", data={"username": "asha", "password": "correct horse"}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def csrf(client, case_id):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(f"/console/case/{case_id}").text).group(1)


def test_console_needs_login(console):
    client, _ = console
    assert 'type="password"' in client.get("/console").text
    assert client.get("/console/case/1", follow_redirects=False).status_code == 303
    assert client.get("/console/audit", follow_redirects=False).status_code == 303


def test_wrong_password_and_unknown_reviewer_are_refused(console):
    client, deps = console
    for user, pw in (("asha", "wrong"), ("mallory", "correct horse")):
        resp = client.post("/console/login", data={"username": user, "password": pw}, follow_redirects=False)
        assert resp.status_code == 200 and "Wrong reviewer name or password" in resp.text
    assert [a["action"] for a in deps.store.audit_entries()][:2] == ["login_failed", "login_failed"]


def test_login_is_locked_after_repeated_failures(console):
    client, _ = console
    for _ in range(console_mod.MAX_FAILED_LOGINS):
        client.post("/console/login", data={"username": "asha", "password": "x"})
    resp = client.post("/console/login", data={"username": "asha", "password": "correct horse"}, follow_redirects=False)
    assert "Too many failed attempts" in resp.text


def test_console_off_without_configuration(intake, tmp_path):
    deps = console_mod.Console(intake.store, intake.evidence, tmp_path, (), None, b"k")
    app.dependency_overrides[console_mod.console_deps] = lambda: deps
    try:
        client = TestClient(app)
        assert "console is not set up" in client.get("/console").text
        resp = client.post("/console/login", data={"username": "", "password": ""}, follow_redirects=False)
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(console_mod.console_deps, None)


def test_cases_sorted_by_reports_and_case_page_shows_evidence(intake, console):
    a, b, c = User(intake, "1"), User(intake, "2"), User(intake, "3")
    a.say(TEXTS["T1"])
    a.report()
    for u in (b, c):
        u.say(photo=F1_PNG)
        u.report()
    client = login(console[0])
    listing = client.get("/console").text
    assert listing.index("Case 2") < listing.index("Case 1")
    page = client.get("/console/case/2").text
    assert sha256_bytes(F1_PNG) in page
    assert "source.non_official_contact" in page and "R3" in page and "50200077118843" in page
    report_id = intake.store.case_reports(2)[0]["id"]
    image = client.get(f"/console/report/{report_id}/file")
    assert image.content == F1_PNG and image.headers["content-type"] == "image/png"
    assert intake.store.audit_entries()[0]["action"] == "evidence_view"


def test_console_escapes_scammer_text(intake, console):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    with intake.store._db() as db:
        db.execute("UPDATE reports SET fields_json = ?", (json.dumps({"claimed_sender": "<script>alert(1)</script>"}),))
    page = login(console[0]).get("/console/case/1").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_review_needs_csrf_token(intake, console):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    client = login(console[0])
    resp = client.post("/console/case/1/review", data={"action": "approve_fake", "csrf": "nope"})
    assert resp.status_code == 403
    assert intake.store.case(1)["status"] == "open"


def test_approve_records_known_fakes_locks_evidence_and_makes_a_packet(intake, console):
    u = User(intake, "1")
    u.say(photo=F1_PNG)
    u.report()
    client = login(console[0])
    resp = client.post(
        "/console/case/1/review", data={"action": "approve_fake", "note": "matches SC scam", "csrf": csrf(client, 1)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert intake.store.case(1)["status"] == "approved_fake"

    sha = sha256_bytes(F1_PNG)
    assert intake.store.is_known_fake(sha)
    assert intake.store.is_known_fake("some-other-file", ["50200077118843"])
    assert json.loads(registry_seed().read_text()) == {"hashes": [], "payees": []}  # the seed file is never written
    assert intake.evidence.is_locked(Path(intake.store.case_reports(1)[0]["evidence_path"]))

    pdf = client.get("/console/case/1/packet.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    flat = re.sub(r"\s+", "", text)
    assert sha in flat
    assert "Section 63(4), Bharatiya Sakshya Adhiniyam" in text and "not itself a certificate" in text
    assert "asha" in text and "court-admissible" not in text.lower()

    packet = client.get("/console/case/1/packet.json").json()
    assert packet["files"][0]["sha256"] == sha and packet["files"][0]["size_bytes"] == len(F1_PNG)
    assert packet["reviewer"]["name"] == "asha" and packet["reviewer"]["note"] == "matches SC scam"
    assert packet["footer"] == FOOTER and packet["files"][0]["rule_row"] == "R3"
    assert "user_hash" not in json.dumps(packet)

    actions = [a["action"] for a in intake.store.audit_entries()]
    for expected in ("review_approve_fake", "evidence_locked", "packet_generated", "packet_download"):
        assert expected in actions


def test_mark_not_fake_after_approval_removes_known_fakes(intake, console):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    client = login(console[0])
    client.post("/console/case/1/review", data={"action": "approve_fake", "csrf": csrf(client, 1)})
    assert intake.store.is_known_fake(sha256_text(TEXTS["T1"]))
    client.post("/console/case/1/review", data={"action": "not_fake", "csrf": csrf(client, 1)})
    assert not intake.store.is_known_fake(sha256_text(TEXTS["T1"]), ["navdeep.kyc@ybl"])
    assert intake.evidence.is_locked(Path(intake.store.case_reports(1)[0]["evidence_path"]))  # a lock is never lifted
    assert intake.store.case(1)["status"] == "not_fake"
    assert [r["action"] for r in intake.store.case_reviews(1)] == ["approve_fake", "not_fake"]


def test_needs_info_is_recorded(intake, console):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    client = login(console[0])
    client.post("/console/case/1/review", data={"action": "needs_info", "csrf": csrf(client, 1)})
    assert intake.store.case(1)["status"] == "needs_info"
    assert client.get("/console/case/1/packet.pdf").status_code == 404


# --- after approval -------------------------------------------------------------------


def test_forwarding_an_approved_fake_again_is_answered_at_once_as_reviewed(tmp_path, console):
    calls = []

    def counting_check_file(*a, **k):
        calls.append(1)
        return check_file(*a, **k)

    intake = Intake(console[1].store, salt="s", data_dir=tmp_path, check_file=counting_check_file)
    u = User(intake, "1")
    u.say(photo=F1_PNG)
    u.report()
    client = login(console[0])
    client.post("/console/case/1/review", data={"action": "approve_fake", "csrf": csrf(client, 1)})

    other = User(intake, "2")
    out = other.say(photo=F1_PNG)
    assert len(out) == 1  # no "Checking…"
    assert out[0].startswith("Likely fake. Do not pay.")
    assert "already confirmed as fake" in out[0]
    assert out[0].endswith("Reviewed by the Trinetra team.")
    assert "No police, CBI, ED or court" in out[0]  # the tailored line, from the reported case
    assert calls == [1]  # only the first check ran OCR


def test_same_payee_after_approval_is_r1_and_reviewed(intake):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    intake.store.add_known_fakes(1, "asha", [], ["navdeep.kyc@ybl"])
    out = User(intake, "2").say("Your account is blocked. Pay Rs 99 to NAVDEEP.KYC@ybl.")
    assert out[1].startswith("Likely fake")
    assert out[1].endswith("Reviewed by the Trinetra team.")


def registry_seed() -> Path:
    from app.registry import KNOWN_FAKES_PATH

    return KNOWN_FAKES_PATH


def test_known_fakes_survive_in_the_database_across_restarts(intake, tmp_path):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    intake.store.add_known_fakes(1, "asha", [sha256_text(TEXTS["T1"])], ["navdeep.kyc@ybl"])
    assert Store(tmp_path / "t.db").is_known_fake(sha256_text(TEXTS["T1"]))


def test_web_page_answers_from_approved_fakes(intake):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    intake.store.add_known_fakes(1, "asha", [], ["navdeep.kyc@ybl"])
    from app import main

    main.app.dependency_overrides[main.known_fakes_lookup] = lambda: intake.store.is_known_fake
    body = TestClient(app).post("/api/check", json={"text": "Pay Rs 10 to navdeep.kyc@ybl now."}).json()
    assert body["verdict"] == "likely_fake" and body["reply"].endswith("Reviewed by the Trinetra team.")


def test_delete_keeps_evidence_locked_by_an_approval_that_was_later_reversed(intake, tmp_path):
    u = User(intake, "1")
    u.say(TEXTS["T1"])
    u.report()
    [report] = intake.store.case_reports(1)
    intake.evidence.lock(Path(report["evidence_path"]))  # approved once...
    intake.store.review(1, "asha", "not_fake")  # ...then reversed
    assert u.say("DELETE") == [EN["deleted"]]
    assert Path(report["evidence_path"]).exists()
    assert intake.store.case_reports(1)[0]["user_hash"] is None
