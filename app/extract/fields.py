"""Regex field extractor. It always runs, and must pass every test on its own.

The optional LLM extractor (later) fills the same Fields and is merged into this result.
"""

from __future__ import annotations

import re

from app.checks import lexicon
from app.models import Account, Fields
from app.registry import agencies

EMAIL = re.compile(r"[\w.+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+", re.IGNORECASE)
# name@handle with no dot after the handle (that would be an email).
UPI = re.compile(r"(?<![\w.\-@])[a-z0-9][\w.\-]{1,255}@[a-z][a-z0-9]{1,30}(?!\.?[\w@\-])", re.IGNORECASE)
URL = re.compile(
    r"(?<![\w@.\-/])(?:https?://)?((?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w\-])(/[^\s,;)]*)?",
    re.IGNORECASE,
)
MOBILE = re.compile(r"(?<![\d+])(?:\+91[\s\-]?|0)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")
SERVICE_NUMBER = re.compile(r"(?<!\d)(?:1800[\s\-]?\d{3}[\s\-]?\d{4}|1930)(?!\d)")
ACCOUNT = re.compile(
    r"(?:\ba/?c|\bacct|\baccount|खाता)\.?\s*(?:no\.?|number|#|संख्या)?\s*[:.\-]?\s*(\d{9,18})(?!\d)",
    re.IGNORECASE,
)
IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
AMOUNT = re.compile(
    r"(?:\brs\.?|\binr\b|₹)\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:crore|lakh|करोड|लाख))?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:rupees|रुपये|रुपए)"
    r"|\b(?:[a-z]+[\s\-])?(?:thousand|lakh|lakhs|crore)\b"
    rf"|(?<![{lexicon.DEV}])[{lexicon.DEV}]+\s+(?:हजार|लाख|करोड)(?:\s+(?:रुपये|रुपए))?",
    re.IGNORECASE,
)
DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")
CASE_NUMBER = re.compile(
    r"\b(?:case|fir|order|ref|suit|notice)\.?\s*(?:no\.?|number)?\s*[:.]?\s*(?=[A-Z/\-]*\d)([A-Z0-9][A-Z0-9/\-]{2,})"
    r"|(?:मामला|सूचना|संदर्भ)\s*(?:संख्या)?\s*(?=[A-Z/\-]*\d)([A-Z0-9][A-Z0-9/\-]{2,})",
    re.IGNORECASE,
)


def _dates(text: str) -> list[str]:
    out = []
    for d, m, y in DATE.findall(text):
        if 1 <= int(d) <= 31 and 1 <= int(m) <= 12:
            out.append(f"{int(d):02d}-{int(m):02d}-{y if len(y) == 4 else '20' + y}")
    return _unique(out)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _sender(text: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """Earliest-named body is the claimed sender: (name, id, type, all names). Ties go to the more specific entry."""
    hits: list[tuple[int, int, str, str, str]] = []
    for priority, agency in enumerate(agencies()):
        starts = [m for p in agency.patterns if (m := p.search(text))]
        if starts:
            first = min(starts, key=lambda m: m.start())
            hits.append((first.start(), priority, agency.name or first.group(0).strip(), agency.id, agency.type))
    if not hits:
        return None, None, None, []
    hits.sort()
    _, _, name, agency_id, kind = hits[0]
    return name, agency_id, kind, _unique([h[2] for h in hits])


def extract(text: str) -> Fields:
    text = lexicon.normalize(text)

    emails = _unique([m.group(0).rstrip(".") for m in EMAIL.finditer(text)])
    upi_ids = _unique([m.group(0) for m in UPI.finditer(text)])
    # Remove emails and UPI IDs so their parts are not read as web domains.
    rest = UPI.sub(" ", EMAIL.sub(" ", text))
    urls = _unique([m.group(1).lower() + (m.group(2) or "").rstrip(".,:!?'\"") for m in URL.finditer(rest)])

    accounts: list[Account] = []
    for m in ACCOUNT.finditer(text):
        ifsc = IFSC.search(text, m.end(), m.end() + 40)
        accounts.append(Account(m.group(1), ifsc.group(0) if ifsc else None))

    amounts = _unique([m.group(0).strip() for m in AMOUNT.finditer(text)])
    phones = _unique([m.group(0) for m in MOBILE.finditer(text)] + [m.group(0) for m in SERVICE_NUMBER.finditer(text)])
    sender, sender_id, sender_type, named = _sender(text)

    payee_or_amount = bool(upi_ids or accounts or amounts)
    demands_payment = payee_or_amount and bool(lexicon.payment_sentences(text))
    return Fields(
        claimed_sender=sender,
        sender_id=sender_id,
        sender_type=sender_type,
        agencies=named,
        case_numbers=_unique([m.group(1) or m.group(2) for m in CASE_NUMBER.finditer(text)]),
        amounts=amounts,
        upi_ids=upi_ids,
        accounts=list(dict.fromkeys(accounts)),
        urls=urls,
        emails=emails,
        phones=phones,
        dates=_dates(text),
        demands_payment=demands_payment,
        threatens_user=lexicon.threatens_arrest(text, demands_payment or bool(upi_ids or accounts)),
        asks_secrecy=lexicon.asks_secrecy(text),
        deadline_hours=lexicon.deadline_hours(text),
    )


def is_mobile(phone: str) -> bool:
    return bool(MOBILE.fullmatch(phone))
