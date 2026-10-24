"""Lone inputs judges and users try: a bare link, a bare UPI ID or a bare phone number.

There is nothing to read in them, so instead of "could not check" we say what we found about the
link, payee or number. These are INFO signals: they explain the reply and never count toward a verdict.
A link or payee from a reviewer-confirmed fake is caught separately, by the known-fakes match (R1).
"""

from __future__ import annotations

import re
from typing import Any

from app.checks.ask import VALID_HANDLE
from app.extract import fields as field_extractor
from app.models import Fields, PointsTo, Signal, Strength
from app.registry import is_official_domain, is_official_payee, normalize_payee

MAX_OTHER_WORDS = 3  # "is this real?" plus a link is still a lone link
_WORD = re.compile(r"(?:[^\W\d_]|[ऀ-ॣ॰-ॿ]){2,}")


def domain(url: str) -> str:
    return url.split("/", 1)[0].lower().removeprefix("www.")


def bare_kind(text: str, fields: Fields) -> str | None:
    """"link", "upi" or "phone" when the message is little more than one of those; otherwise None."""
    rest = field_extractor.EMAIL.sub(" ", text)
    rest = field_extractor.UPI.sub(" ", rest)
    rest = field_extractor.URL.sub(" ", rest)
    rest = field_extractor.MOBILE.sub(" ", field_extractor.SERVICE_NUMBER.sub(" ", rest))
    if len(_WORD.findall(rest)) > MAX_OTHER_WORDS:
        return None
    if fields.urls:
        return "link"
    if fields.upi_ids:
        return "upi"
    if fields.phones:
        return "phone"
    return None


def _note(code: str, en: str, hi: str) -> Signal:
    return Signal("source", f"source.{code}", Strength.INFO, PointsTo.NEUTRAL, en, hi)


def notes(kind: str | None, fields: Fields, issuers: Any = None) -> list[Signal]:
    if kind == "link":
        host = domain(fields.urls[0])
        if is_official_domain(host):
            return [_note(
                "link_official",
                f"The link goes to {host}, an official government or bank website. A link alone does not prove "
                "who sent it.",
                f"यह लिंक {host} पर जाता है, जो एक आधिकारिक सरकारी या बैंक वेबसाइट है। सिर्फ़ लिंक से यह साबित नहीं "
                "होता कि इसे किसने भेजा।",
            )]
        return [_note(
            "link_not_official",
            f"The link goes to {host}, which is not an official government or bank website. Don't enter any "
            "details there.",
            f"यह लिंक {host} पर जाता है, जो कोई आधिकारिक सरकारी या बैंक वेबसाइट नहीं है। वहाँ अपनी कोई जानकारी न डालें।",
        )]
    if kind == "upi":
        upi = fields.upi_ids[0]
        issuer_payees = {normalize_payee(p) for i in (issuers.issuers() if issuers else []) for p in i["payees"]}
        if VALID_HANDLE.search(upi):
            return [_note("payee_validated",
                          "This is a SEBI-validated UPI handle, used by registered investment businesses.",
                          "यह SEBI-मान्य UPI हैंडल है, जिसका इस्तेमाल पंजीकृत निवेश कंपनियाँ करती हैं।")]
        if is_official_payee(upi) or normalize_payee(upi) in issuer_payees:
            return [_note("payee_official", "This UPI ID is on our list of official payees.",
                          "यह UPI ID हमारी आधिकारिक भुगतान सूची में है।")]
        return [_note(
            "payee_unconfirmed",
            "We can't confirm who owns this UPI ID. Only pay if you know the person and have checked with them on a "
            "number you trust.",
            "हम पुष्टि नहीं कर सकते कि यह UPI ID किसका है। तभी भुगतान करें जब आप उस व्यक्ति को जानते हों और भरोसेमंद "
            "नंबर पर उनसे पुष्टि कर ली हो।",
        )]
    if kind == "phone":
        if re.sub(r"\D", "", fields.phones[0]) == "1930":
            return [_note("phone_helpline", "1930 is India's national cybercrime helpline.",
                          "1930 भारत की राष्ट्रीय साइबर अपराध हेल्पलाइन है।")]
        return [_note(
            "phone_unconfirmed",
            "We can't confirm who owns this number. Call back only on a number you already trust, such as one from "
            "an official website.",
            "हम पुष्टि नहीं कर सकते कि यह नंबर किसका है। केवल उसी नंबर पर कॉल करें जिस पर आपको पहले से भरोसा है, "
            "जैसे किसी आधिकारिक वेबसाइट पर दिया नंबर।",
        )]
    return []
