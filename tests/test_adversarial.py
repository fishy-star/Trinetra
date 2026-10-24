"""Messages judges and friends actually send: celebrity and bank scams, Hinglish, lone links, chat screenshots."""

import io

from PIL import Image, ImageDraw, ImageFont

from app.models import Kind, Verdict
from app.pipeline import check_file, check_text
from app.reply import CREDENTIAL_ACTION, NO_SIGNALS, short
from app.store import Store

A = ("Hello this is Elon Musk. I am very poo right now and I need money fast plz. Give me 3000 indian rupees because "
     "im in India right now ok. And I’m need to get back to my home country to make mars rocket and I will give u 1 "
     "million dollars ok. plz trust me I will give u a free tesla to")
B = ("BANK ALERT: We detected suspicious activity on your debit card. Your account has been temporarily locked. Verify "
     "your identity immediately")


def codes(result) -> set[str]:
    return {s.code for s in result.signals}


def test_a_celebrity_asking_for_money_is_suspicious():
    r = check_text(A)
    assert r.verdict is Verdict.SUSPICIOUS and r.rule_row == "R4"
    assert {"ask.money_request", "source.claimed_identity_unverifiable", "ask.reward_for_payment"} <= codes(r)


def test_b_bank_alert_asking_to_verify_is_suspicious_with_the_card_line():
    r = check_text(B)
    assert r.verdict is Verdict.SUSPICIOUS and r.rule_row == "R4"
    assert {"ask.credential_request", "ask.account_threat", "source.claimed_identity_unverifiable"} <= codes(r)
    assert CREDENTIAL_ACTION["en"] in short(r.verdict, r.signals, Kind.TEXT, "en")


def test_hinglish_money_request():
    r = check_text("Papa main hospital mein hoon. Jaldi 20000 rupaye bhejo, mummy ko mat batana.")
    assert r.verdict is Verdict.SUSPICIOUS and "ask.money_request" in codes(r)


def test_a_friend_splitting_a_bill_is_not_flagged():
    r = check_text("Hey, dinner came to 2400, your share is 600. Send it to my UPI priya.s@okaxis whenever you can, no rush!")
    assert r.verdict is Verdict.CANNOT_VERIFY and "ask.money_request" not in codes(r)


def test_likely_fake_still_needs_two_checks():
    # Strong ask signals alone are one check: suspicious, never likely fake.
    r = check_text("Share the OTP sent to your phone and send Rs 5,000 today to claim your prize.")
    assert r.verdict is Verdict.SUSPICIOUS


def test_a_real_otp_sms_is_not_flagged():
    r = check_text("123456 is your OTP for login to HDFC Bank NetBanking. Valid for 5 minutes. Do not share it with "
                   "anyone. HDFC Bank will never ask for your OTP.")
    assert r.verdict is Verdict.CANNOT_VERIFY
    assert not codes(r) & {"ask.secrecy", "ask.urgent_deadline", "ask.credential_request"}


def test_a_short_hindi_message_is_readable():
    assert check_text("बेटा, खाना खा लिया? शाम को फ़ोन करना।").verdict is Verdict.CANNOT_VERIFY


# --- lone inputs ----------------------------------------------------------------------------


def test_a_lone_link_says_where_it_goes_and_not_to_enter_details():
    r = check_text("https://sbi-kyc-update.xyz/login")
    assert r.verdict is Verdict.CANNOT_VERIFY
    text = short(r.verdict, r.signals, Kind.TEXT, "en")
    assert "sbi-kyc-update.xyz, which is not an official" in text and "Don't enter any details" in text
    assert NO_SIGNALS["en"].split(".")[0] not in text  # we found something, so we say it


def test_a_lone_official_link():
    r = check_text("is this real? https://www.incometax.gov.in/iec/foportal")
    assert "an official government or bank website" in short(r.verdict, r.signals, Kind.TEXT, "en")


def test_a_link_from_a_confirmed_fake_is_caught(tmp_path):
    store = Store(tmp_path / "t.db")
    with store._db() as db:
        db.execute("INSERT INTO cases (id, status, created_at) VALUES (1, 'approved_fake', 'now')")
    store.add_known_fakes(1, "asha", [], [], ["sbi-kyc-update.xyz"])
    r = check_text("http://sbi-kyc-update.xyz", known=store.is_known_fake)
    assert (r.verdict, r.rule_row) == (Verdict.LIKELY_FAKE, "R1")


def test_a_bare_upi_id():
    r = check_text("kbc.lottery@paytm")
    assert r.verdict is Verdict.CANNOT_VERIFY
    assert "We can't confirm who owns this UPI ID" in short(r.verdict, r.signals, Kind.TEXT, "en")
    assert "kbc.lottery" not in r.reply  # the reason never repeats the payee, so stored answers hold no payee


def test_a_bare_validated_upi_handle_and_phone_numbers():
    assert "SEBI-validated" in check_text("navdeepmf.mf@validhdfc").reply
    assert "can't confirm who owns this number" in check_text("+91 98765 43210").reply
    assert "national cybercrime helpline" in check_text("1930").reply


def test_lone_inputs_never_count_toward_a_verdict():
    for item in ("https://sbi-kyc-update.xyz", "kbc.lottery@paytm", "+91 98765 43210"):
        assert all(s.strength.value == "info" for s in check_text(item).signals)


# --- a screenshot of a chat ------------------------------------------------------------------


def chat_screenshot(lines: list[str]) -> bytes:
    image = Image.new("RGB", (900, 1200), (230, 221, 212))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    draw.rectangle([0, 0, 900, 110], fill=(7, 94, 84))
    draw.text((40, 35), "Mom ❤", fill="white", font=ImageFont.load_default(size=36))
    y = 160
    for line in lines:
        draw.rounded_rectangle([30, y - 10, 860, y + 60], radius=16, fill="white")
        draw.text((50, y), line, fill="black", font=font)
        draw.text((760, y + 30), "10:42 pm", fill=(120, 120, 120), font=ImageFont.load_default(size=18))
        y += 100
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_a_whatsapp_style_chat_screenshot_is_read_and_checked():
    data = chat_screenshot([
        "Hi Mom, this is my new number.",
        "My old phone broke. I need money urgently.",
        "Please send 15000 to this account today.",
        "I will explain later, can't talk now.",
    ])
    r = check_file(data)
    assert r.verdict is Verdict.SUSPICIOUS and "ask.money_request" in codes(r)
