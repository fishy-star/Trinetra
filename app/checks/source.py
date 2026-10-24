"""Source check: did it come from who it claims?

Proof signals (signature_valid, registry_match) arrive with PDF signatures and the notice
registry. source.unknown_case_format is left out until we have real case-number formats.
"""

from __future__ import annotations

from app.extract.fields import is_mobile
from app.models import Fields, PointsTo, Signal, Strength
from app.registry import GOVERNMENT_TYPES, OFFICIAL_TYPES, is_official_domain, is_official_payee

FREE_EMAIL = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com",
        "outlook.com", "hotmail.com", "live.com", "msn.com", "rediffmail.com", "aol.com",
        "icloud.com", "me.com", "protonmail.com", "proton.me", "gmx.com", "zoho.com", "mail.com",
    }
)


def _domain(url: str) -> str:
    return url.split("/", 1)[0]


def payee_mismatch(fields: Fields) -> Signal | None:
    if fields.sender_type not in GOVERNMENT_TYPES or not fields.demands_payment:
        return None
    # Any UPI ID or account named by a government body is non-government, unless it is on official_payees.json.
    if not any(not is_official_payee(p) for p in fields.payees):
        return None
    return Signal(
        "source",
        "source.payee_identity_mismatch",
        Strength.STRONG,
        PointsTo.FAKE,
        "It claims to come from a government body but asks you to pay a private UPI ID or bank account, "
        "not a government account.",
        "यह किसी सरकारी संस्था के नाम से है, लेकिन आपसे किसी निजी UPI ID या बैंक खाते में भुगतान करने को कहता है, "
        "सरकारी खाते में नहीं।",
    )


def non_official_contact(fields: Fields) -> Signal | None:
    if fields.sender_type not in OFFICIAL_TYPES:
        return None
    found_en: list[str] = []
    found_hi: list[str] = []
    emails = [e.rsplit("@", 1)[1].lower() for e in fields.emails]
    if any(d in FREE_EMAIL for d in emails):
        found_en.append("a free email address")
        found_hi.append("मुफ़्त ईमेल पता")
    if any(is_mobile(p) for p in fields.phones):
        found_en.append("a personal mobile or WhatsApp number")
        found_hi.append("निजी मोबाइल या WhatsApp नंबर")
    domains = [_domain(u) for u in fields.urls] + [d for d in emails if d not in FREE_EMAIL]
    if any(not is_official_domain(d) for d in domains):
        found_en.append("a website that is not an official government or bank site")
        found_hi.append("ऐसी वेबसाइट जो आधिकारिक सरकारी या बैंक साइट नहीं है")
    if not found_en:
        return None
    return Signal(
        "source",
        "source.non_official_contact",
        Strength.STRONG,
        PointsTo.FAKE,
        f"It claims to come from an official body but gives {' and '.join(found_en)} as the contact. "
        "Official bodies do not use these.",
        f"यह किसी आधिकारिक संस्था के नाम से है, लेकिन संपर्क के लिए {' और '.join(found_hi)} देता है। "
        "आधिकारिक संस्थाएँ इनका इस्तेमाल नहीं करतीं।",
    )


def run(fields: Fields) -> list[Signal]:
    return [s for s in (payee_mismatch(fields), non_official_contact(fields)) if s]
