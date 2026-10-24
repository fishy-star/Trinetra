"""item -> extract -> checks -> verdict -> reply."""

from __future__ import annotations

import hashlib
import logging
import re

from app import reply, verdict
from app.checks import ask, source
from app.config import ENGINE_VERSION
from app.extract import fields as field_extractor
from app.extract import ocr
from app.extract.mask import mask_fields
from app.models import Fields, Kind, Result
from app.registry import known_fakes

log = logging.getLogger(__name__)

# The synthetic test set marks every notice with this footer. No rule may see it.
_TEST_FOOTER = re.compile(r"^.*SYNTHETIC TEST SAMPLE.*$", re.MULTILINE | re.IGNORECASE)


def strip_test_footer(text: str) -> str:
    return _TEST_FOOTER.sub("", text)


def is_readable(text: str) -> bool:
    return len(re.findall(r"[^\W\d_]{2,}", text)) >= 2


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _known_fake(sha256: str, fields: Fields) -> bool:
    fakes = known_fakes()
    payees = {p.lower() for p in fakes.get("payees", [])}
    return sha256 in fakes.get("hashes", []) or any(p.lower() in payees for p in fields.payees)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_file(data: bytes, *, sha256: str | None = None, lang: str | None = None, fallback_lang: str = "en") -> Result:
    """A notice as an image (PNG/JPEG) or a PDF. The caller has already checked the type by magic bytes."""
    sha256 = sha256 or sha256_bytes(data)
    try:
        text = ocr.file_text(data)
    except ocr.UnreadableFile as e:
        log.info("could not read file sha256=%s: %s", sha256[:12], e)
        text = ""
    return check_text(text, kind=Kind.NOTICE, sha256=sha256, lang=lang, fallback_lang=fallback_lang)


def check_text(
    text: str,
    *,
    kind: Kind = Kind.TEXT,
    sha256: str | None = None,
    lang: str | None = None,
    fallback_lang: str = "en",
) -> Result:
    """Run the full check on text: a forwarded message, or text extracted from a notice or voice note."""
    sha256 = sha256 or sha256_text(text)
    text = strip_test_footer(text)
    readable = is_readable(text)
    lang = lang or (reply.detect_lang(text) if readable else fallback_lang)

    fields = field_extractor.extract(text) if readable else Fields()
    signals = source.run(fields) + ask.run(fields, text) if readable else []
    # Proof of source (PDF signature, registry match) is not wired in yet, so nothing is proven.
    source_proven = False
    known = readable and _known_fake(sha256, fields)

    v, row = verdict.decide(kind, signals, known, source_proven, readable)
    return Result(
        kind=kind,
        sha256=sha256,
        verdict=v,
        rule_row=row,
        signals=signals,
        fields=mask_fields(fields),
        lang=lang,
        reply=reply.build(v, signals, kind, lang, reply.sender_group(fields.sender_type, fields.sender_id)),
        engine_version=ENGINE_VERSION,
        needs_review=verdict.needs_review(signals, source_proven),
    )
