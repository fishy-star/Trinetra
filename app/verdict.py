"""The verdict rule table. Pure function: no I/O, no model calls.

Rows are evaluated in order and the first match wins.

R0  Nothing readable could be extracted                          -> could_not_check
R1  Matches a reviewer-confirmed known fake                      -> likely_fake
R1A Official notice altered: a valid issuer code, but the notice  -> likely_fake
    no longer matches what the issuer issued
R2  Not voice, source proven, and no STRONG fake signal          -> verified_official
R3  STRONG fake signals from two different checks                -> likely_fake
R4  Exactly one check has a STRONG fake signal, or >= 2 WEAK     -> suspicious
R5  Anything else                                                -> cannot_verify
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models import Kind, PointsTo, Signal, Strength, Verdict


def _is_strong_fake(s: Signal) -> bool:
    # Media detectors are always weak evidence, whatever strength they report.
    return s.points_to is PointsTo.FAKE and s.strength is Strength.STRONG and s.check != "media"


def _is_weak_fake(s: Signal) -> bool:
    return s.points_to is PointsTo.FAKE and not _is_strong_fake(s)


def decide(
    kind: Kind,
    signals: Iterable[Signal],
    known_fake_match: bool,
    source_proven: bool,
    readable: bool,
    altered: bool = False,
) -> tuple[Verdict, str]:
    signals = list(signals)
    strong_checks = {s.check for s in signals if _is_strong_fake(s)}
    weak_count = sum(1 for s in signals if _is_weak_fake(s))

    if not readable:
        return Verdict.COULD_NOT_CHECK, "R0"
    if known_fake_match:
        return Verdict.LIKELY_FAKE, "R1"
    if altered:
        return Verdict.LIKELY_FAKE, "R1A"
    if kind is not Kind.VOICE and source_proven and not strong_checks:
        return Verdict.VERIFIED_OFFICIAL, "R2"
    if len(strong_checks) >= 2:
        return Verdict.LIKELY_FAKE, "R3"
    if len(strong_checks) == 1 or weak_count >= 2:
        return Verdict.SUSPICIOUS, "R4"
    return Verdict.CANNOT_VERIFY, "R5"


def needs_review(signals: Iterable[Signal], source_proven: bool) -> bool:
    """A proven source that also carries a STRONG fake signal is flagged for a person to look at."""
    return source_proven and any(_is_strong_fake(s) for s in signals)
