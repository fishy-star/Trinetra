"""Mask Aadhaar, PAN and long account numbers before anything is stored."""

from __future__ import annotations

import re

from app.models import Account, Fields

_ACCOUNT_CONTEXT = re.compile(
    r"((?:a/?c|acct|account)\.?\s*(?:no\.?|number|#)?\s*[:.\-]?\s*|खाता\s*(?:संख्या|नं\.?)?\s*[:.\-]?\s*)(\d{9,18})(?!\d)",
    re.IGNORECASE,
)
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?(\d{4})(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}(\d{4})[A-Z]\b")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{9,18}(?!\d)")
_MOBILE = re.compile(r"(?:91|0)?[6-9]\d{9}")


def mask_account(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return "X" * (len(digits) - 4) + digits[-4:]


def mask_text(text: str) -> str:
    # Account numbers next to an account keyword first, so a 12-digit account is not read as Aadhaar.
    text = _ACCOUNT_CONTEXT.sub(lambda m: m.group(1) + mask_account(m.group(2)), text)
    text = _AADHAAR.sub(lambda m: f"XXXX XXXX {m.group(1)}", text)
    text = _PAN.sub(lambda m: f"XXXXX{m.group(1)}X", text)
    # Remaining long numbers are treated as accounts, except bare mobile numbers.
    return _LONG_NUMBER.sub(lambda m: m.group(0) if _MOBILE.fullmatch(m.group(0)) else mask_account(m.group(0)), text)


def mask_upi(upi: str) -> str:
    name, _, handle = upi.partition("@")
    return f"{name[:2]}***@{handle}"


def mask_fields(fields: Fields) -> Fields:
    """Copy of `fields` safe to store. The full payee goes to the evidence vault only after a report."""
    return Fields(
        **{
            **fields.__dict__,
            "accounts": [Account(mask_account(a.number), a.ifsc) for a in fields.accounts],
            "upi_ids": [mask_upi(u) for u in fields.upi_ids],
            "case_numbers": [mask_text(c) for c in fields.case_numbers],
        }
    )
