"""Source check: did it come from who it claims?

Issuer-code signals (proof, altered, invalid and missing codes) are in checks/issuer.py.
source.signature_valid arrives with PDF signatures. source.unknown_case_format is left out until
we have real case-number formats.
"""

from __future__ import annotations

from typing import Any

from app.checks import lexicon
from app.extract.fields import is_mobile
from app.models import Fields, PointsTo, Signal, Strength
from app.registry import GOVERNMENT_TYPES, OFFICIAL_TYPES, is_official_domain, is_official_payee, normalize_payee

FREE_EMAIL = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com",
        "outlook.com", "hotmail.com", "live.com", "msn.com", "rediffmail.com", "aol.com",
        "icloud.com", "me.com", "protonmail.com", "proton.me", "gmx.com", "zoho.com", "mail.com",
    }
)


def _domain(url: str) -> str:
    return url.split("/", 1)[0]


def _issuer_payees(issuer: dict[str, Any] | None) -> set[str]:
    return {normalize_payee(p) for p in (issuer or {}).get("payees", [])}


def _issuer_domain(host: str, issuer: dict[str, Any] | None) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in (issuer or {}).get("domains", []))


def payee_mismatch(fields: Fields, issuer: dict[str, Any] | None = None) -> Signal | None:
    if fields.sender_type not in GOVERNMENT_TYPES or not fields.demands_payment:
        return None
    # Any UPI ID or account named by a government body is non-government, unless it is on official_payees.json
    # or is one of the payees the issuer registered in the issuer portal.
    own = _issuer_payees(issuer)
    if not any(not is_official_payee(p) and normalize_payee(p) not in own for p in fields.payees):
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


def non_official_contact(fields: Fields, issuer: dict[str, Any] | None = None) -> Signal | None:
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
    if any(not is_official_domain(d) and not _issuer_domain(d, issuer) for d in domains):
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


def unverifiable_identity(fields: Fields) -> Signal | None:
    who = fields.claimed_identity
    if not who:
        return None
    if who == lexicon.RELATIVE_IDENTITY:
        en = ("It sounds like a relative in trouble asking for money. A voice cannot prove who is speaking: "
              "voices can be copied.")
        hi = ("यह किसी मुसीबत में फँसे रिश्तेदार जैसा लगता है जो पैसे माँग रहा है। आवाज़ से साबित नहीं होता कि कौन बोल रहा है: "
              "आवाज़ की नकल की जा सकती है।")
    elif who == "your bank":
        en = "It says it is from your bank but never says which bank, so you cannot check it."
        hi = "यह कहता है कि यह आपके बैंक से है, लेकिन बैंक का नाम नहीं बताता, इसलिए आप इसकी जाँच नहीं कर सकते।"
    else:
        en = f"It claims to be {who}, which a message cannot prove. Famous names are a common scam lure."
        hi = f"यह {who} होने का दावा करता है, जिसे कोई संदेश साबित नहीं कर सकता। मशहूर नाम ठगी का आम तरीका हैं।"
    return Signal("source", "source.claimed_identity_unverifiable", Strength.WEAK, PointsTo.FAKE, en, hi)


def run(fields: Fields, issuer: dict[str, Any] | None = None) -> list[Signal]:
    """`issuer`: the portal issuer the notice comes from or claims, whose own domains and payees count as official."""
    found = (payee_mismatch(fields, issuer), non_official_contact(fields, issuer), unverifiable_identity(fields))
    return [s for s in found if s]
