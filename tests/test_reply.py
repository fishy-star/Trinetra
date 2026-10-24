import csv
from pathlib import Path

import pytest

from app.intake import Inbound, Intake
from app.models import Kind
from app.pipeline import check_text
from app.reply import ACTION_FAKE, sender_group
from app.store import Store

TEXTS = {
    r["id"]: r["text"]
    for r in csv.DictReader(open(Path(__file__).parent.parent / "data/test-set/texts.csv", encoding="utf-8"))
}
BANK_EN = "Your bank will never ask you to pay a fee to a UPI ID or update KYC through a link."
BANK_HI = "आपका बैंक कभी भी किसी UPI ID पर शुल्क भेजने"
AUTHORITY_EN = "No police, CBI, ED or court arrests anyone on a video call"
AUTHORITY_HI = "पुलिस, CBI, ED या कोई न्यायालय वीडियो कॉल पर गिरफ़्तारी नहीं करता"
FAMILY_SCAM = "Hi beta, I am stuck. Send Rs 45,000 to quickhelp.cash@ybl today. Don't tell anyone at home."


@pytest.mark.parametrize(
    "sender_type,sender_id,group",
    [
        ("bank", "bank", "finance"),
        ("financial", "mutual_fund", "finance"),
        ("regulator", "rbi", "finance"),
        ("financial", "npci", "finance"),
        ("police", "cbi", "authority"),
        ("court", "supreme_court", "authority"),
        ("regulator", "trai", "authority"),
        ("government", "ed", "authority"),
        (None, None, "general"),
    ],
)
def test_sender_group(sender_type, sender_id, group):
    assert sender_group(sender_type, sender_id) == group


def test_bank_sms_gets_bank_line_not_arrest_line():
    reply = check_text(TEXTS["T1"]).reply
    assert BANK_EN in reply
    assert AUTHORITY_EN not in reply


def test_rbi_notice_gets_bank_line():
    text = "RESERVE BANK OF INDIA\nPay the penalty of Rs. 12,500 within 3 hours to rbi.penalty.desk@paytm."
    assert BANK_EN in check_text(text, kind=Kind.NOTICE).reply


def test_agency_claim_keeps_arrest_line_in_hindi():
    reply = check_text(TEXTS["T2"]).reply
    assert AUTHORITY_HI in reply
    assert BANK_HI not in reply


def test_bank_line_in_hindi():
    assert BANK_HI in check_text(TEXTS["T1"], lang="hi").reply


def test_no_claimed_sender_gets_general_line():
    reply = check_text(FAMILY_SCAM).reply
    assert reply.startswith("Suspicious")
    assert ACTION_FAKE["general"]["en"] in reply
    assert ACTION_FAKE["general"]["hi"] in check_text(FAMILY_SCAM, lang="hi").reply


def test_every_action_line_offers_report_and_already_paid():
    for group in ACTION_FAKE.values():
        assert "Reply 1" in group["en"] and "Reply 2" in group["en"]
        assert "1 भेजें" in group["hi"] and "2 भेजें" in group["hi"]


def test_cached_reply_keeps_the_tailored_line(tmp_path):
    intake = Intake(Store(tmp_path / "t.db"), salt="s")
    out: list[str] = []
    for text in ("YES", TEXTS["T1"], TEXTS["T1"]):
        intake.handle(Inbound("telegram", "1", text), out.append)
    first, cached = out[2], out[3]
    assert cached == first
    assert BANK_EN in cached


# --- verify routes ------------------------------------------------------------------

DIN_EN = "Genuine Income Tax notices carry a DIN."
CNR_EN = "Look up the case by its CNR number on ecourts.gov.in."
CNR_HI = "ecourts.gov.in पर CNR नंबर से मामला खोजें।"


def test_routes_file_is_marked_unconfirmed():
    import json

    raw = json.loads((Path(__file__).parent.parent / "data/registry/verify_routes.json").read_text(encoding="utf-8"))
    assert raw["_status"] == "URLs to be confirmed by the team"


def test_income_tax_claim_gets_din_route():
    text = "INCOME TAX DEPARTMENT\nA mismatch was found in your return. Respond within 30 days."
    reply = check_text(text, kind=Kind.NOTICE).reply
    assert reply.startswith("We could not verify this") and DIN_EN in reply


def test_court_claim_gets_cnr_route_in_both_languages():
    text = "IN THE COURT OF THE CIVIL JUDGE, PUNE\nYou are summoned to appear on 14-12-2026."
    assert CNR_EN in check_text(text, kind=Kind.NOTICE).reply
    assert CNR_HI in check_text(text, kind=Kind.NOTICE, lang="hi").reply


def test_route_shown_for_suspicious_but_not_for_likely_fake():
    suspicious = "SUPREME COURT OF INDIA\nContact the registrar on WhatsApp +91 90000 12345 immediately."
    assert check_text(suspicious, kind=Kind.NOTICE).reply.startswith("Suspicious")
    assert CNR_EN in check_text(suspicious, kind=Kind.NOTICE).reply
    assert CNR_EN not in check_text(TEXTS["T2"]).reply  # CBI, likely fake: no route


def test_no_route_without_a_matching_sender():
    assert "ecourts" not in check_text(TEXTS["T4"]).reply and "DIN" not in check_text(TEXTS["T3"]).reply


def test_cached_reply_keeps_the_route(tmp_path):
    intake = Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path)
    out: list[str] = []
    text = "INCOME TAX DEPARTMENT\nA mismatch was found in your return. Respond within 30 days."
    for t in ("YES", text, text):
        intake.handle(Inbound("telegram", "1", t), out.append)
    assert out[3] == out[2] and DIN_EN not in out[3]  # the short reply keeps the verifier link behind "Why?"
    why = next(data for _, data in out[3].buttons if data.startswith("why:"))
    intake.handle(Inbound("telegram", "1", why), out.append)
    assert DIN_EN in out[-1]
