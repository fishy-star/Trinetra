"""Voice transcripts: whisper's sound-alike mistakes, spoken amounts and the family-emergency pattern."""

import pytest

from app.extract.speech import Transcript
from app.extract.transcript import normalise
from app.models import Verdict
from app.pipeline import check_audio, check_text

# A real Hindi voice note, "बेटा, मैं अस्पताल में हूँ, तुरंत बीस हज़ार रुपये भेज दो, किसी को मत बताना", as whisper heard it.
GARBLED = "बेटा मैं अस्पताल में हु तुरंत बीस हजा रूपै बेज तो किसी को मदद मैं किसी को मद बताना"


def voice(text: str, lang: str = "hi"):
    return check_audio(b"recording", sha256="0" * 64, transcribe=lambda data: Transcript(text, lang))


def codes(result) -> set[str]:
    return {s.code for s in result.signals}


def test_the_garbled_hindi_voice_note_is_suspicious():
    r = voice(GARBLED)
    assert r.verdict is Verdict.SUSPICIOUS
    assert "source.claimed_identity_unverifiable" in codes(r)
    assert codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request"}
    assert r.heard.startswith("बेटा मैं अस्पताल में हु")  # the reply shows what was heard, not the fixed copy


def test_the_same_note_as_whisper_heard_it_on_another_run_is_suspicious():
    # Punctuated this time, with बेता for बेटा and आस्पताल for अस्पताल.
    r = voice("बेता, मैं आस्पताल में हूँ, तुरंत भीस हाजार रूपे भेज दो, किसी को मत बताना")
    assert r.verdict is Verdict.SUSPICIOUS
    assert {"source.claimed_identity_unverifiable", "ask.secrecy"} <= codes(r)
    assert codes(r) & {"ask.payment_to_non_official_payee", "ask.money_request"}


def test_a_harmless_hindi_voice_note_is_cannot_verify():
    r = voice("कल लंच पर मिलते हैं")
    assert (r.verdict, r.signals) == (Verdict.CANNOT_VERIFY, [])


def test_a_spoken_amount_in_english_counts_as_a_money_request():
    r = voice("Hi, this is your son. I'm at the police station, I need twenty thousand rupees.", "en")
    assert r.verdict is Verdict.SUSPICIOUS and "ask.money_request" in codes(r)


def test_a_relative_asking_for_money_without_trouble_is_not_flagged():
    assert voice("Mom, can you send me two thousand rupees for the books, whenever you can.", "en").verdict \
        is Verdict.CANNOT_VERIFY


def test_a_relative_in_trouble_asking_for_money_is_suspicious_without_a_deadline():
    r = voice("मम्मी मैं मुसीबत में हूँ बीस हज़ार रुपये भेज दो")
    assert r.verdict is Verdict.SUSPICIOUS
    assert {"source.claimed_identity_unverifiable", "ask.money_request"} <= codes(r)
    assert "voice cannot prove" in next(s.reason_en for s in r.signals if s.code.startswith("source."))


def test_it_never_reaches_likely_fake_on_ask_and_identity_alone():
    # The identity signal is WEAK: likely fake still needs strong signals from two different checks.
    assert voice(GARBLED).verdict is not Verdict.LIKELY_FAKE


def test_roman_script_hindi_voice_note():
    r = voice("Papa main hospital mein hoon jaldi bees hazaar rupay bej do mummy ko mad batana")
    assert r.verdict is Verdict.SUSPICIOUS and "ask.money_request" in codes(r)


@pytest.mark.parametrize("heard,fixed", [
    ("बेज दो", "भेज दो। "), ("भेझ तो", "भेज दो। "), ("बेता", "बेटा"), ("आस्पताल", "अस्पताल"), ("मद बताना", "मत बताना"), ("रूपै", "रुपये"), ("रुपए", "रुपये"),
    ("हाजार", "हजार"), ("bej do", "bhej do. "), ("rupay", "rupaye"), ("hajar", "hazaar"), ("mad batana", "mat batana"),
])
def test_sound_alike_rules(heard, fixed):
    assert normalise(heard) == normalise(fixed) or normalise(heard) == fixed


@pytest.mark.parametrize("words,digits", [
    ("twenty thousand", "20000"), ("बीस हज़ार", "20000"), ("bees hazaar", "20000"), ("five hundred", "500"),
    ("one lakh fifty thousand", "150000"), ("दो लाख", "200000"), ("डेढ़ लाख", "150000"), ("15 thousand", "15000"),
])
def test_spoken_amounts_become_digits(words, digits):
    assert normalise(f"send {words} now").split()[1] == digits


@pytest.mark.parametrize("text", ["मदद करो", "तो फिर कल मिलते हैं", "do it now", "ek baar call karo", "मैं दो बजे आऊँगा",
                                  "we are two of us", "मत जाना"])
def test_common_words_are_left_alone(text):
    out = normalise(text)
    assert not any(ch.isdigit() for ch in out)
    assert ("मदद" in out) == ("मदद" in text) and ("तो" in out) == ("तो" in text)


def test_typed_text_is_never_normalised():
    # The same words typed are checked as typed: the transcript rules only run on voice notes.
    typed = check_text(GARBLED)
    assert "source.claimed_identity_unverifiable" not in codes(typed)
    assert "source.claimed_identity_unverifiable" in codes(voice(GARBLED))
