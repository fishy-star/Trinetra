"""Issuer portal and verification codes: issuing, verifying, altered notices, invalid and missing codes."""

import csv
import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import issuers as codes
from app import portal as portal_mod
from app.intake import Inbound, Intake
from app.issuing import IssueError, issue_notice
from app.main import app
from app.models import Kind, PointsTo, Signal, Strength, Verdict
from app.pipeline import check_file, check_text
from app.store import Store
from app.verdict import decide
from tests.test_testset import TEST_SET, _notice_sources
from tools.demo_alter_amount import alter
from tools.make_demo_summons import draw
from tools.seed_demo_issuers import DEMO_ISSUERS

TEXTS = {r["id"]: r["text"] for r in csv.DictReader(open(TEST_SET / "texts.csv", encoding="utf-8"))}
BASE = "http://localhost:8000"
SUMMONS_TEXT = (
    "PUNE DISTRICT COURT (DEMO)\nIN THE COURT OF THE CIVIL JUDGE (SENIOR DIVISION), PUNE\nSUMMONS TO DEFENDANT\n"
    "Civil Suit No. CS/PUNE/2026/0412 · Dated 20-10-2026\nTo: Mr. Rohan Kulkarni, Kothrud, Pune (Defendant)\n"
    "Whereas the plaintiff has instituted a suit against you for recovery of Rs. 2,40,000, being the security "
    "deposit of a rented flat, you are hereby summoned to appear in this Court in person or by a pleader on "
    "14-12-2026 at 11:00 a.m. to answer the claim.\nBy order of the Court\nAssistant Superintendent, "
    "Pune District Court (demo)"
)


def seeded(store: Store) -> dict[str, dict]:
    out = {}
    for d in DEMO_ISSUERS:
        iid = store.upsert_issuer(d["name"], d["type"], d["domains"], d["contacts"], d["payees"], [d["name"]], True)
        store.set_issuer_user(d["user"], iid, codes.hash_password("pw-" + d["user"]))
        out[d["user"]] = store.issuer(iid)
    return out


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.fixture
def court(store):
    return seeded(store)["court-demo"]


def issue_text(store, issuer, tmp_path, text=SUMMONS_TEXT):
    return issue_notice(store, issuer, "court-demo", "Summons", tmp_path / "issued", BASE, text=text)


# --- codes ------------------------------------------------------------------------------


def test_codes_are_random_and_well_formed():
    made = {codes.new_code() for _ in range(2000)}
    assert len(made) == 2000
    assert all(re.fullmatch(r"TRN-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}", c) for c in made)


def test_codes_are_found_in_links_and_text():
    assert codes.find_codes("visit https://x.example/v/TRN-7K2Q-9XFD now") == ["TRN-7K2Q-9XFD"]
    assert codes.find_codes("code trn 7k2q 9xfd") == ["TRN-7K2Q-9XFD"]
    assert codes.find_codes("TRN-0000-1111") == []  # 0 and 1 are never used


def test_stamp_lines_are_not_read_as_the_notice():
    issued = codes.stamp_text("Pay at the court counter only.", "TRN-7K2Q-9XFD", f"{BASE}/v/TRN-7K2Q-9XFD")
    assert codes.strip_stamp(issued).strip() == "Pay at the court counter only."


def test_passwords_are_salted_hashes():
    a, b = codes.hash_password("secret"), codes.hash_password("secret")
    assert a != b and "secret" not in a
    assert codes.check_password("secret", a) and not codes.check_password("Secret", a)


# --- rule table ---------------------------------------------------------------------------


def sig(code, strength, points_to=PointsTo.FAKE, check="source"):
    return Signal(check, code, strength, points_to, "r", "र")


def test_r1a_official_notice_altered():
    assert decide(Kind.NOTICE, [sig("source.official_notice_altered", Strength.STRONG)], False, False, True, altered=True) == (
        Verdict.LIKELY_FAKE, "R1A")


def test_r1_comes_before_r1a_and_r0_before_both():
    assert decide(Kind.NOTICE, [], True, False, True, altered=True) == (Verdict.LIKELY_FAKE, "R1")
    assert decide(Kind.NOTICE, [], False, False, False, altered=True) == (Verdict.COULD_NOT_CHECK, "R0")


def test_r1a_beats_a_proven_source():
    assert decide(Kind.NOTICE, [], False, True, True, altered=True) == (Verdict.LIKELY_FAKE, "R1A")


def test_valid_code_is_proof_for_r2():
    proof = sig("source.issuer_code_valid", Strength.PROOF, PointsTo.GENUINE)
    assert decide(Kind.NOTICE, [proof], False, True, True) == (Verdict.VERIFIED_OFFICIAL, "R2")


def test_invalid_or_missing_code_alone_is_r4_and_with_a_strong_ask_r3():
    for code in ("source.invalid_code", "source.missing_code"):
        assert decide(Kind.NOTICE, [sig(code, Strength.STRONG)], False, False, True) == (Verdict.SUSPICIOUS, "R4")
        both = [sig(code, Strength.STRONG), sig("ask.payment_to_non_official_payee", Strength.STRONG, check="ask")]
        assert decide(Kind.NOTICE, both, False, False, True) == (Verdict.LIKELY_FAKE, "R3")


# --- pipeline ------------------------------------------------------------------------------


def test_issued_genuine_summons_is_verified_naming_the_issuer(store, court, tmp_path):
    issued = issue_text(store, court, tmp_path)
    forwarded = issued.stamped_path.read_text(encoding="utf-8")
    result = check_text(forwarded, kind=Kind.NOTICE, issuers=store)
    assert (result.verdict, result.rule_row) == (Verdict.VERIFIED_OFFICIAL, "R2")
    assert "valid Trinetra code from Pune District Court (demo)" in result.reply
    assert "मान्य Trinetra कोड" in check_text(forwarded, kind=Kind.NOTICE, issuers=store, lang="hi").reply


def test_same_summons_with_a_changed_amount_is_likely_fake(store, court, tmp_path):
    forwarded = issue_text(store, court, tmp_path).stamped_path.read_text(encoding="utf-8")
    altered = forwarded.replace("2,40,000", "9,40,000")
    result = check_text(altered, kind=Kind.NOTICE, issuers=store)
    assert (result.verdict, result.rule_row) == (Verdict.LIKELY_FAKE, "R1A")
    assert "This notice was issued by Pune District Court (demo), but it has been changed since." in result.reply
    assert "Changed: the amount." in result.reply


@pytest.mark.parametrize("old,new,what", [
    ("CS/PUNE/2026/0412", "CS/PUNE/2026/0999", "the case number"),
    ("14-12-2026", "16-11-2026", "a date"),
])
def test_other_key_field_changes_are_altered(store, court, tmp_path, old, new, what):
    forwarded = issue_text(store, court, tmp_path).stamped_path.read_text(encoding="utf-8")
    result = check_text(forwarded.replace(old, new), kind=Kind.NOTICE, issuers=store)
    assert result.rule_row == "R1A" and f"Changed: {what}." in result.reply


def test_added_payee_is_altered(store, court, tmp_path):
    forwarded = issue_text(store, court, tmp_path).stamped_path.read_text(encoding="utf-8")
    scam = forwarded.replace("to answer the claim.", "to answer the claim. Pay the deposit to court.fees@ybl today.")
    result = check_text(scam, kind=Kind.NOTICE, issuers=store)
    assert result.verdict is Verdict.LIKELY_FAKE and "the payee" in result.reply


def test_real_code_on_a_different_notice_is_altered(store, court, tmp_path):
    code = issue_text(store, court, tmp_path).code
    other = _notice_sources()["F2"] + f"\nVerify this notice at {BASE}/v/{code}"
    assert check_text(other, kind=Kind.NOTICE, issuers=store).rule_row == "R1A"


def test_partial_copy_is_not_verified_but_points_to_the_code_page(store, court, tmp_path):
    issued = issue_text(store, court, tmp_path)
    first_half = "\n".join(SUMMONS_TEXT.splitlines()[:4]) + f"\nCode {issued.code}"
    result = check_text(first_half, kind=Kind.NOTICE, issuers=store)
    assert result.verdict is Verdict.CANNOT_VERIFY
    assert f"{BASE}/v/{issued.code}" in result.reply and "could not read it clearly enough" in result.reply


def test_revoked_code_is_a_strong_source_signal(store, court, tmp_path):
    issued = issue_text(store, court, tmp_path)
    assert store.revoke_notice(court["id"], issued.notice_id, "court-demo")
    result = check_text(issued.stamped_path.read_text(encoding="utf-8"), kind=Kind.NOTICE, issuers=store)
    assert "source.invalid_code" in [s.code for s in result.signals]
    assert result.verdict is Verdict.SUSPICIOUS and "fake or has been cancelled" in result.reply


def test_made_up_code_is_a_strong_source_signal(store, court):
    result = check_text("Your parcel is held. Code TRN-7K2Q-9XFD. Pay Rs 500 to parcel.desk@ybl today.", issuers=store)
    assert "source.invalid_code" in [s.code for s in result.signals]


def test_forged_issuer_notice_without_a_code_is_at_least_suspicious(store, court):
    forged = (
        "PUNE DISTRICT COURT (DEMO)\nNOTICE: You are summoned. Appear before the court on 02-11-2026 "
        "or a warrant will be issued against you."
    )
    result = check_text(forged, kind=Kind.NOTICE, issuers=store)
    assert result.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_FAKE)
    assert "Real notices from Pune District Court (demo) always carry a Trinetra code" in result.reply


def test_bank_issuer_payee_is_official(store, tmp_path):
    bank = seeded(store)["bank-demo"]
    notice = ("Navdeep Bank (demo)\nYour EMI of Rs. 12,500 is due today. Pay to loanrepay.navdeepbank@nvdp "
              "or at any branch. Loan account ending 4471. Dated 01-11-2026.")
    issued = issue_notice(store, bank, "bank-demo", "Demand letter", tmp_path / "issued", BASE, text=notice)
    result = check_text(issued.stamped_path.read_text(encoding="utf-8"), kind=Kind.NOTICE, issuers=store)
    assert result.verdict is Verdict.VERIFIED_OFFICIAL


def test_issuer_cannot_issue_a_stamped_or_empty_notice(store, court, tmp_path):
    stamped = issue_text(store, court, tmp_path).stamped_path.read_text(encoding="utf-8")
    with pytest.raises(IssueError):
        issue_text(store, court, tmp_path, text=stamped)
    with pytest.raises(IssueError):
        issue_text(store, court, tmp_path, text="  ")


def test_image_notice_round_trip_with_qr(store, court, tmp_path):
    original = tmp_path / "summons.png"
    draw(original)
    issued = issue_notice(store, court, "court-demo", "Summons", tmp_path / "issued", BASE, data=original.read_bytes())
    stamped = issued.stamped_path.read_bytes()

    phone = Image.open(io.BytesIO(stamped))
    phone.thumbnail((1280, 1280))  # what a chat app does to a photo
    buf = io.BytesIO()
    phone.convert("RGB").save(buf, "JPEG", quality=80)
    assert check_file(buf.getvalue(), issuers=store).verdict is Verdict.VERIFIED_OFFICIAL

    buf = io.BytesIO()
    alter(Image.open(io.BytesIO(stamped)), "2,40,000", "9,40,000").save(buf, "PNG")
    assert check_file(buf.getvalue(), issuers=store).rule_row == "R1A"


def test_test_set_is_unchanged_with_demo_issuers_present(store, court):
    labels = list(csv.DictReader(open(TEST_SET / "labels.csv", encoding="utf-8")))
    for row in labels:
        if row["split"] != "dev":
            continue
        if row["kind"] == "text":
            item = row["file"].split("#")[1]
            got = check_text(TEXTS[item], issuers=store).verdict.value
        elif row["format"] == "png":
            item = row["file"].split("/")[1].split("_")[0]
            got = check_text(_notice_sources()[item], kind=Kind.NOTICE, issuers=store).verdict.value
        else:
            continue
        assert got == row["expected_verdict"], row["file"]


# --- bot ------------------------------------------------------------------------------------


def test_bot_says_verified_for_an_issued_notice(store, court, tmp_path):
    forwarded = issue_text(store, court, tmp_path).stamped_path.read_text(encoding="utf-8")
    intake = Intake(store, salt="s", data_dir=tmp_path)
    out: list[str] = []
    for text in ("YES", forwarded):
        intake.handle(Inbound("telegram", "1", text), out.append)
    assert out[-1].startswith("Verified: this matches an official source.")
    assert "Pune District Court (demo)" in out[-1]


def test_revoking_clears_cached_verified_answers(store, court, tmp_path):
    issued = issue_text(store, court, tmp_path)
    forwarded = issued.stamped_path.read_text(encoding="utf-8")
    intake = Intake(store, salt="s", data_dir=tmp_path)
    out: list[str] = []
    intake.handle(Inbound("telegram", "1", "YES"), out.append)
    intake.handle(Inbound("telegram", "1", forwarded), out.append)
    store.revoke_notice(court["id"], issued.notice_id, "court-demo")
    store.clear_results()  # the portal does this on revoke
    intake.handle(Inbound("telegram", "1", forwarded), out.append)
    assert out[-1].startswith("Suspicious")


# --- portal ----------------------------------------------------------------------------------


@pytest.fixture
def portal(store, tmp_path):
    seeded(store)
    deps = portal_mod.Portal(store, tmp_path / "issued", b"p" * 32, BASE)
    app.dependency_overrides[portal_mod.portal_deps] = lambda: deps
    portal_mod.limiter.clear()
    portal_mod._lookups.clear()
    yield deps
    app.dependency_overrides.pop(portal_mod.portal_deps, None)


def staff_client(user: str) -> TestClient:
    client = TestClient(app)
    resp = client.post("/issuer/login", data={"username": user, "password": f"pw-{user}"}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def csrf_of(client: TestClient, path: str = "/issuer") -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(path).text).group(1)


def issue_via_portal(client: TestClient, text: str = SUMMONS_TEXT) -> int:
    resp = client.post("/issuer/notices", data={"notice_type": "Summons", "text": text, "csrf": csrf_of(client)},
                       follow_redirects=False)
    assert resp.status_code == 303
    return int(resp.headers["location"].rsplit("/", 1)[1])


def test_portal_needs_login_and_rejects_wrong_password(portal):
    client = TestClient(app)
    assert 'type="password"' in client.get("/issuer").text
    assert client.get("/issuer/notices/1", follow_redirects=False).status_code == 303
    resp = client.post("/issuer/login", data={"username": "court-demo", "password": "nope"})
    assert "Wrong username or password" in resp.text
    assert portal.store.audit_entries()[0]["action"] == "issuer_login_failed"


def test_reviewer_and_issuer_sessions_are_separate(portal):
    client = staff_client("court-demo")
    assert client.get("/console/case/1", follow_redirects=False).status_code == 303


def test_issue_download_and_revoke_are_audited(portal):
    client = staff_client("court-demo")
    notice_id = issue_via_portal(client)
    page = client.get(f"/issuer/notices/{notice_id}").text
    code = re.search(r"TRN-[A-Z0-9]{4}-[A-Z0-9]{4}", page).group(0)
    assert client.get(f"/issuer/notices/{notice_id}/stamped?download=1").text.endswith(f"(Code {code})")
    assert client.get(f"/issuer/notices/{notice_id}/qr.png").headers["content-type"] == "image/png"
    csrf = csrf_of(client, f"/issuer/notices/{notice_id}")
    client.post(f"/issuer/notices/{notice_id}/revoke", data={"csrf": csrf})
    assert portal.store.notice_by_code(code)["status"] == "revoked"
    actions = [a["action"] for a in portal.store.audit_entries()]
    for expected in ("issuer_login", "issue_notice", "download_stamped", "download_qr", "revoke_notice"):
        assert expected in actions
    assert all(a["actor"].startswith(("issuer:", "system")) for a in portal.store.audit_entries())


def test_issuers_cannot_see_or_revoke_each_others_notices(portal):
    court_client = staff_client("court-demo")
    notice_id = issue_via_portal(court_client)
    bank_client = staff_client("bank-demo")
    assert bank_client.get(f"/issuer/notices/{notice_id}").status_code == 404
    assert bank_client.get(f"/issuer/notices/{notice_id}/stamped").status_code == 404
    assert bank_client.get(f"/issuer/notices/{notice_id}/qr.png").status_code == 404
    resp = bank_client.post(f"/issuer/notices/{notice_id}/revoke", data={"csrf": csrf_of(bank_client)})
    assert resp.status_code == 404
    assert "TRN-" not in bank_client.get("/issuer").text.split("Issued notices")[1]
    assert portal.store.issuer_notices(portal.store.issuer_user("court-demo")["issuer_id"])[0]["status"] == "valid"


def test_issuing_needs_the_form_token(portal):
    client = staff_client("court-demo")
    resp = client.post("/issuer/notices", data={"notice_type": "Summons", "text": SUMMONS_TEXT, "csrf": "x"})
    assert resp.status_code == 403


def test_public_page_shows_status_without_personal_data(portal):
    client = staff_client("court-demo")
    notice_id = issue_via_portal(client)
    code = portal.store.issuer_notice(portal.store.issuer_user("court-demo")["issuer_id"], notice_id)["code"]
    public = TestClient(app).get(f"/v/{code}")
    assert public.status_code == 200
    for shown in ("Valid", "Pune District Court (demo)", "Summons"):
        assert shown in public.text
    for hidden in ("Rohan", "Kulkarni", "2,40,000", "CS/PUNE"):
        assert hidden not in public.text
    client.post(f"/issuer/notices/{notice_id}/revoke", data={"csrf": csrf_of(client, f"/issuer/notices/{notice_id}")})
    assert "Cancelled" in TestClient(app).get(f"/v/{code}").text


def test_public_page_unknown_code_and_rate_limit(portal):
    client = TestClient(app)
    assert client.get("/v/TRN-7K2Q-9XFD").status_code == 404
    for _ in range(portal_mod.VERIFY_LIMIT - 1):
        client.get("/v/TRN-7K2Q-9XFD")
    assert client.get("/v/TRN-7K2Q-9XFD").status_code == 429


# --- pre-issue checks ----------------------------------------------------------------------

F1_PNG = (TEST_SET / "png/F1_sc_warrant_en.png").read_bytes()
OTHER_BODY = (
    "CENTRAL BUREAU OF INVESTIGATION\nNotice to appear for questioning at our Mumbai office on 05-11-2026 "
    "at 11:00 a.m. Bring a copy of this notice."
)
SUSPICIOUS_SUMMONS = SUMMONS_TEXT + "\nFor your hearing slot, message the registry on WhatsApp +91 90000 12345."


def issue_as(store, issuer, tmp_path, *, text=None, data=None, confirmed=False):
    return issue_notice(store, issuer, "staff", "Summons", tmp_path / "issued", BASE, data=data, text=text,
                        confirmed=confirmed)


def test_forged_f1_warrant_is_refused(store, court, tmp_path):
    with pytest.raises(IssueError) as refused:
        issue_as(store, court, tmp_path, data=F1_PNG)
    assert refused.value.reason == "likely_fake"
    assert "likely fake, so it cannot be issued" in str(refused.value)
    assert "private UPI ID or bank account" in str(refused.value)  # it says why
    assert store.issuer_notices(court["id"]) == []


def test_demo_summons_issues_fine(store, court, tmp_path):
    original = tmp_path / "summons.png"
    draw(original)
    issued = issue_as(store, court, tmp_path, data=original.read_bytes())
    assert issued.confirmed_signals == ()
    assert store.notice_by_code(issued.code)["status"] == "valid"


def test_notice_naming_another_body_is_refused(store, court, tmp_path):
    with pytest.raises(IssueError) as refused:
        issue_as(store, court, tmp_path, text=OTHER_BODY)
    assert refused.value.reason == "other_body"
    assert "names CBI, not Pune District Court (demo)" in str(refused.value)


def test_bank_cannot_issue_an_rbi_notice(store, tmp_path):
    bank = seeded(store)["bank-demo"]
    with pytest.raises(IssueError) as refused:
        issue_as(store, bank, tmp_path, text="RESERVE BANK OF INDIA\nYour KYC review is due on 30-11-2026.")
    assert refused.value.reason == "other_body"


def test_issuers_own_name_and_generic_kind_are_allowed(store, court, tmp_path):
    bank = seeded(store)["bank-demo"]
    issue_as(store, bank, tmp_path, text="Navdeep Bank (demo)\nYour statement for October 2026 is ready at any branch.")
    issue_as(store, court, tmp_path, text="IN THE COURT OF THE CIVIL JUDGE, PUNE\nHearing moved to 20-01-2027.")


def test_suspicious_notice_needs_the_issuers_confirmation(store, court, tmp_path):
    with pytest.raises(IssueError) as refused:
        issue_as(store, court, tmp_path, text=SUSPICIOUS_SUMMONS)
    assert refused.value.reason == "suspicious_unconfirmed"
    assert "I confirm this notice is genuine and its payment details are official" in str(refused.value)
    issued = issue_as(store, court, tmp_path, text=SUSPICIOUS_SUMMONS, confirmed=True)
    assert issued.confirmed_signals == ("source.non_official_contact",)


def test_confirmation_does_not_unlock_a_likely_fake(store, court, tmp_path):
    with pytest.raises(IssueError):
        issue_as(store, court, tmp_path, data=F1_PNG, confirmed=True)


def test_known_fake_is_refused(store, court, tmp_path):
    text = "Pune District Court (demo)\nYour hearing is on 05-12-2026. Pay the filing fee to court.filing@ybl."
    with store._db() as db:  # a case approved as fake in the review console
        db.execute("INSERT INTO cases (id, status, created_at) VALUES (1, 'approved_fake', 'now')")
    store.add_known_fakes(1, "asha", [], ["court.filing@ybl"])
    with pytest.raises(IssueError) as refused:
        issue_as(store, court, tmp_path, text=text)
    assert refused.value.reason == "likely_fake"


def test_portal_refusals_are_audited_and_explained(portal):
    client = staff_client("court-demo")
    resp = client.post("/issuer/notices", data={"notice_type": "Summons", "csrf": csrf_of(client)},
                       files={"file": ("warrant.png", F1_PNG, "image/png")})
    assert resp.status_code == 200 and "likely fake, so it cannot be issued" in resp.text
    resp = client.post("/issuer/notices", data={"notice_type": "Notice", "text": OTHER_BODY, "csrf": csrf_of(client)})
    assert "You can only issue notices from your own organisation" in resp.text
    refused = [a for a in portal.store.audit_entries() if a["action"] == "issue_refused"]
    assert [r["actor"] for r in refused] == ["issuer:court-demo", "issuer:court-demo"]
    assert "reason=other_body" in refused[0]["detail"] and "reason=likely_fake" in refused[1]["detail"]
    assert "Rohan" not in str([dict(r) for r in refused]) and "SUPREME" not in str([dict(r) for r in refused])
    assert portal.store.issuer_notices(portal.store.issuer_user("court-demo")["issuer_id"]) == []


def test_portal_confirmed_suspicious_issue_is_audited(portal):
    client = staff_client("court-demo")
    form = {"notice_type": "Summons", "text": SUSPICIOUS_SUMMONS, "csrf": csrf_of(client)}
    resp = client.post("/issuer/notices", data=form)
    assert "would rate this notice suspicious" in resp.text
    assert SUSPICIOUS_SUMMONS.splitlines()[0] in resp.text  # the pasted text is kept for the second try
    resp = client.post("/issuer/notices", data={**form, "confirm_genuine": "yes"}, follow_redirects=False)
    assert resp.status_code == 303
    actions = [a["action"] for a in portal.store.audit_entries()]
    assert actions[:3] == ["issue_notice", "issue_confirmed_suspicious", "issue_refused"]
    confirmed = portal.store.audit_entries()[1]
    assert "signals=source.non_official_contact" in confirmed["detail"]
