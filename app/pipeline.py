"""item -> extract -> checks -> verdict -> reply."""

from __future__ import annotations

import hashlib
import re

from app import reply, verdict
from app.checks import ask, source
from app.config import ENGINE_VERSION
from app.extract import fields as field_extractor
from app.extract.mask import mask_fields
from app.models import Fields, Kind, Result
from app.registry import known_fakes

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


def check_text(text: str, *, kind: Kind = Kind.TEXT, sha256: str | None = None, lang: str | None = None) -> Result:
    """Run the full check on text: a forwarded message, or text extracted from a notice or voice note."""
    sha256 = sha256 or sha256_text(text)
    text = strip_test_footer(text)
    readable = is_readable(text)
    lang = lang or reply.detect_lang(text)

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
        reply=reply.build(v, signals, kind, lang),
        engine_version=ENGINE_VERSION,
        needs_review=verdict.needs_review(signals, source_proven),
    )
