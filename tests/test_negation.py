"""A negation cancels only the verb it negates: "don't tell Dad" is secrecy, not a cancelled payment."""

import pytest

from app.checks import lexicon
from app.extract.speech import Transcript
from app.models import Verdict
from app.pipeline import check_audio, check_text


def codes(result) -> set[str]:
    return {s.code for s in result.signals}


def test_dont_tell_dad_does_not_cancel_the_money_request():
    r = check_text("send 15000 rupees today and don't tell Dad")
    assert r.verdict is Verdict.SUSPICIOUS and "ask.secrecy" in codes(r)
    assert codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request"}


def test_the_spoken_version_is_suspicious():
    said = "Hi Mom, I'm in hospital, please send fifteen thousand rupees today, don't tell Dad"
    r = check_audio(b"recording", sha256="0" * 64, transcribe=lambda data: Transcript(said, "en"))
    assert r.verdict is Verdict.SUSPICIOUS
    assert codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request"}


@pytest.mark.parametrize("text", [
    "Never share your OTP with anyone",
    "don't send money to unknown numbers",
    "paise mat bhejo kisi ko, aaj fraud bahut ho raha hai",
    "किसी अनजान नंबर पर पैसे मत भेजो।",
    "Do not transfer money to anyone claiming to be from CBI.",
])
def test_negating_the_payment_itself_still_cancels_it(text):
    r = check_text(text)
    assert r.verdict is Verdict.CANNOT_VERIFY
    assert not codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request", "ask.credential_request"}


@pytest.mark.parametrize("text", [
    "Papa jaldi 20000 rupaye bhej do, kisi ko mat batana",
    "बेटा, तुरंत 20000 रुपये भेज दो किसी को मत बताना",
    "Transfer Rs 50,000 today to cbi.case@ybl, do not tell your family",
    "Pay Rs 5000 today, don't worry, it will be refunded after verification",
])
def test_secrecy_and_reassurance_in_the_same_sentence_keep_the_ask(text):
    r = check_text(text)
    assert r.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_FAKE)
    assert codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request"}


def test_secrecy_alone_is_not_flagged():
    r = check_text("Don't tell Mom, it's a surprise party on Sunday!")
    assert r.verdict is Verdict.CANNOT_VERIFY and codes(r) == {"ask.secrecy"}


@pytest.mark.parametrize("sentence,negated", [
    ("send money and don't tell anyone", False),
    ("kisi ko mat batana, paise bhej do", False),
    ("don't send money", True),
    ("never share your OTP", True),
    ("paise mat bhejo", True),
    ("don't tell anyone, and don't pay", True),
])
def test_negated(sentence, negated):
    assert lexicon.negated(lexicon.normalize(sentence)) is negated
