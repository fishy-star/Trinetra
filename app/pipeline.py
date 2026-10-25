"""item -> extract -> checks -> verdict -> reply."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable

from app import issuers as issuer_codes
from app import reply, verdict
from app.checks import ask, source
from app.checks import items as lone_items
from app.checks import issuer as issuer_check
from app.checks.issuer import IssuerLookup
from app.config import ENGINE_VERSION, settings
from app.extract import fields as field_extractor
from app.checks import lexicon
from app.extract import ocr, speech, transcript
from app.extract.mask import mask_fields, mask_text
from app.models import Fields, Kind, Result, Signal
from app.registry import known_fakes, normalize_payee

log = logging.getLogger(__name__)

# The synthetic test set marks every notice with this footer. No rule may see it.
_TEST_FOOTER = re.compile(r"^.*SYNTHETIC TEST SAMPLE.*$", re.MULTILINE | re.IGNORECASE)


def strip_test_footer(text: str) -> str:
    return _TEST_FOOTER.sub("", text)


def is_readable(text: str) -> bool:
    """At least two words. Devanagari counts in full: its vowel signs are not letters to Python's \\w."""
    return len(re.findall(r"(?:[^\W\d_]|[\u0900-\u0963\u0970-\u097F]){2,}", text)) >= 2


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# A valid issuer code, but the copy was too unclear to confirm: send the person to the code's page.
UNCLEAR_CODE = {
    "en": "It carries a Trinetra code from {issuer}, but we could not read it clearly enough to confirm the details. "
    "Check it at {url}.",
    "hi": "इस पर {issuer} का Trinetra कोड है, लेकिन हम विवरण की पुष्टि करने लायक इसे साफ़ नहीं पढ़ सके। इसे {url} पर जाँचें।",
}

# Reviewer-approved fakes live in the runtime database; the caller passes its lookup, e.g. Store.is_known_fake.
KnownFake = Callable[[str, list[str]], bool]


def is_known_fake(sha256: str, payees: list[str] = (), known: KnownFake | None = None) -> bool:
    """Approved in the database, or in the read-only seed list data/registry/known_fakes.json."""
    seed = known_fakes()
    seed_payees = {normalize_payee(p) for p in seed.get("payees", [])}
    seed_domains = {f"domain:{d.lower()}" for d in seed.get("domains", [])}
    if sha256 in seed.get("hashes", []) or any(
        p in seed_domains if p.startswith("domain:") else normalize_payee(p) in seed_payees for p in payees
    ):
        return True
    return known is not None and known(sha256, list(payees))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_file(data: bytes, **options) -> Result:
    """A notice as an image (PNG/JPEG) or a PDF. The caller has already checked the type by magic bytes.

    Options are those of analyse_text: sha256, lang, fallback_lang, known, issuers.
    """
    return analyse_file(data, **options)[0]


def check_text(text: str, **options) -> Result:
    """Run the full check on text: a forwarded message, or text extracted from a notice or voice note."""
    return analyse_text(text, **options)[0]


def analyse_file(data: bytes, *, sha256: str | None = None, **options) -> tuple[Result, Fields]:
    sha256 = sha256 or sha256_bytes(data)
    try:
        text = ocr.file_text(data)
    except ocr.UnreadableFile as e:
        log.info("could not read file sha256=%s: %s", sha256[:12], e)
        text = ""
    codes = issuer_codes.find_codes(" ".join(ocr.qr_texts(data)))
    return analyse_text(text, kind=Kind.NOTICE, sha256=sha256, codes=codes, **options)


def check_audio(data: bytes, **options) -> Result:
    """A voice note, audio file or video. Raises speech.AudioError when the recording cannot be checked."""
    return analyse_audio(data, **options)[0]


def analyse_audio(
    data: bytes, *, sha256: str | None = None, transcribe: Callable[[bytes], speech.Transcript] | None = None,
    **options,
) -> tuple[Result, Fields]:
    """Transcribe, mask, then the same text checks and rule table as a forwarded message, as kind VOICE.

    There is no fake-voice detector: the only media signal is the optional AUDIO_SERVICE_URL, and it is WEAK.
    """
    sha256 = sha256 or sha256_bytes(data)
    heard = (transcribe or speech.transcribe)(data)
    # The checks read the transcript with common speech-to-text mistakes folded and spoken amounts as digits;
    # the reply shows what was actually heard. The reply follows the words' own script; with no words, the
    # caller's fallback language, not a guess.
    text = transcript.normalise(heard.text)
    extra = speech.spoof_signal(heard.pcm) if is_readable(text) else []
    identity = lexicon.RELATIVE_IDENTITY if lexicon.family_emergency(text) else None
    return analyse_text(text, kind=Kind.VOICE, sha256=sha256, extra_signals=extra, claimed_identity=identity,
                        heard=reply.heard_snippet(mask_text(heard.text)), **options)


def analyse_text(
    text: str,
    *,
    kind: Kind = Kind.TEXT,
    sha256: str | None = None,
    lang: str | None = None,
    fallback_lang: str = "en",
    known: KnownFake | None = None,
    issuers: IssuerLookup | None = None,
    codes: list[str] = (),
    issuer_context: dict | None = None,
    extra_signals: list[Signal] = (),
    heard: str = "",
    claimed_identity: str | None = None,
) -> tuple[Result, Fields]:
    """The check, plus the unmasked fields. Only a report, after the user's consent, may keep those.

    `codes` are issuer codes read from QR codes; codes printed in the text are found here too.
    `issuer_context` is the portal issuer checking its own notice before issuing it: its registered
    domains and payees count as official.
    """
    sha256 = sha256 or sha256_text(text)
    text = strip_test_footer(text)
    codes = list(dict.fromkeys([*codes, *issuer_codes.find_codes(text)]))
    # Trinetra's own stamp (the verify line and link) is not part of the notice.
    text = issuer_codes.strip_stamp(text)
    fields = field_extractor.extract(text) if text.strip() else Fields()
    # A lone link, UPI ID or phone number has little text, but there is something to check and explain.
    lone = lone_items.bare_kind(text, fields) if text.strip() else None
    readable = is_readable(text) or lone is not None
    if not readable:
        fields = Fields()
    elif claimed_identity and not fields.claimed_identity:
        fields.claimed_identity = claimed_identity  # e.g. a voice note from "a relative": a claim it cannot prove
    # A text too short to check still shows its script, so a one-word Hindi message is answered in Hindi.
    lang = lang or (reply.detect_lang(text) if readable or (kind is Kind.TEXT and text.strip()) else fallback_lang)

    issued = issuer_check.run(text, fields, codes, issuers) if readable else issuer_check.IssuerCheck()
    issuer = issued.issuer or issuer_context
    signals = issued.signals + source.run(fields, issuer) + ask.run(fields, text, issuer) if readable else []
    signals += lone_items.notes(lone, fields, issuers)
    signals += list(extra_signals) if readable else []
    domains = [f"domain:{lone_items.domain(u)}" for u in fields.urls]
    matched = readable and is_known_fake(sha256, fields.payees + domains, known)

    v, row = verdict.decide(kind, signals, matched, issued.proven, readable, altered=issued.altered)
    group = reply.sender_group(fields.sender_type, fields.sender_id)
    if issued.unclear_code:
        url = issuer_codes.verify_url(settings.public_base_url, issued.unclear_code)
        verify_lines = {lg: UNCLEAR_CODE[lg].format(issuer=issued.issuer_name, url=url) for lg in ("en", "hi")}
    else:
        verify_lines = {lg: reply.verify_line(fields.sender_type, fields.sender_id, lg) for lg in ("en", "hi")}
    verify = verify_lines[lang]
    result = Result(
        kind=kind,
        sha256=sha256,
        verdict=v,
        rule_row=row,
        signals=signals,
        fields=mask_fields(fields),
        lang=lang,
        reply=reply.build(v, signals, kind, lang, group, reviewed=row == "R1", verify=verify, heard=heard),
        engine_version=ENGINE_VERSION,
        needs_review=verdict.needs_review(signals, issued.proven),
        reviewed=row == "R1",
        verify=verify_lines,
        heard=heard if readable else "",
    )
    return result, fields
