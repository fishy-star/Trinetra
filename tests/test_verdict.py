from app.models import Kind, PointsTo, Signal, Strength, Verdict
from app.verdict import decide, needs_review


def sig(check: str, strength: Strength, points_to: PointsTo = PointsTo.FAKE) -> Signal:
    return Signal(check, f"{check}.test", strength, points_to, "reason", "कारण")


STRONG_SOURCE = sig("source", Strength.STRONG)
STRONG_ASK = sig("ask", Strength.STRONG)
WEAK_ASK = sig("ask", Strength.WEAK)
WEAK_MEDIA = sig("media", Strength.WEAK)


def run(signals=(), kind=Kind.NOTICE, known=False, proven=False, readable=True):
    return decide(kind, signals, known_fake_match=known, source_proven=proven, readable=readable)


def test_r0_unreadable():
    assert run([STRONG_SOURCE, STRONG_ASK], known=True, readable=False) == (Verdict.COULD_NOT_CHECK, "R0")


def test_r1_known_fake():
    assert run(known=True, proven=True) == (Verdict.LIKELY_FAKE, "R1")


def test_r2_proven_source():
    assert run([WEAK_ASK], proven=True) == (Verdict.VERIFIED_OFFICIAL, "R2")


def test_r3_strong_from_two_checks():
    assert run([STRONG_SOURCE, STRONG_ASK]) == (Verdict.LIKELY_FAKE, "R3")


def test_r3_needs_two_different_checks():
    assert run([STRONG_ASK, sig("ask", Strength.STRONG)]) == (Verdict.SUSPICIOUS, "R4")


def test_r4_one_strong():
    assert run([STRONG_SOURCE]) == (Verdict.SUSPICIOUS, "R4")


def test_r4_two_weak():
    assert run([WEAK_ASK, sig("ask", Strength.WEAK)]) == (Verdict.SUSPICIOUS, "R4")


def test_r5_otherwise():
    assert run([]) == (Verdict.CANNOT_VERIFY, "R5")
    assert run([WEAK_ASK]) == (Verdict.CANNOT_VERIFY, "R5")


def test_proven_source_with_strong_fake_signal_falls_through_and_is_flagged():
    signals = [STRONG_SOURCE]
    assert run(signals, proven=True) == (Verdict.SUSPICIOUS, "R4")
    assert needs_review(signals, source_proven=True)
    assert not needs_review(signals, source_proven=False)


def test_voice_is_never_verified():
    assert run(kind=Kind.VOICE, proven=True) == (Verdict.CANNOT_VERIFY, "R5")


def test_media_alone_never_goes_past_suspicious():
    strong_media = sig("media", Strength.STRONG)
    assert run([strong_media, WEAK_MEDIA]) == (Verdict.SUSPICIOUS, "R4")
    assert run([strong_media, STRONG_SOURCE]) == (Verdict.SUSPICIOUS, "R4")


def test_genuine_signals_do_not_count_as_fake():
    proof = sig("source", Strength.PROOF, PointsTo.GENUINE)
    assert run([proof, proof]) == (Verdict.CANNOT_VERIFY, "R5")
