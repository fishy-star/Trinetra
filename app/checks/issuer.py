"""Issuer codes: proof of source for notices issued through the Trinetra issuer portal.

source.issuer_code_valid       PROOF, genuine   valid code, and the text and key fields match what was issued
source.official_notice_altered STRONG, fake     valid code, but key fields changed (or a different notice)
source.invalid_code            STRONG, fake     a code that does not exist or was revoked
source.missing_code            STRONG, fake     claims an issuer whose notices always carry a code, and has none
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.issuers import compare, key_fields
from app.models import Fields, PointsTo, Signal, Strength

CHANGED_EN = {"amounts": "the amount", "payees": "the payee", "case_numbers": "the case number", "dates": "a date"}
CHANGED_HI = {"amounts": "राशि", "payees": "भुगतान खाता", "case_numbers": "केस नंबर", "dates": "तारीख"}


class IssuerLookup(Protocol):
    def notice_by_code(self, code: str) -> dict[str, Any] | None: ...
    def issuers(self) -> list[dict[str, Any]]: ...


@dataclass
class IssuerCheck:
    signals: list[Signal] = field(default_factory=list)
    proven: bool = False
    altered: bool = False
    issuer: dict[str, Any] | None = None  # the issuer the notice comes from or claims to
    unclear_code: str | None = None  # a valid code we could not confirm the notice against
    issuer_name: str | None = None


def claimed_issuers(text: str, issuers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issuers the notice names. Matching is on the issuer's full listed names only."""
    low = text.lower()
    return [i for i in issuers if any(name.lower() in low for name in i["match_names"])]


def _signal(code: str, strength: Strength, points_to: PointsTo, en: str, hi: str) -> Signal:
    return Signal("source", f"source.{code}", strength, points_to, en, hi)


def run(text: str, fields: Fields, codes: list[str], lookup: IssuerLookup | None) -> IssuerCheck:
    if lookup is None:
        return IssuerCheck()
    out = IssuerCheck()
    invalid = False
    for code in codes:
        notice = lookup.notice_by_code(code)
        if notice is None or notice["status"] != "valid":
            invalid = True
            continue
        name, date = notice["issuer_name"], notice["issued_at"][:10]
        out.issuer = {"name": name, "type": notice["issuer_type"], "payees": json.loads(notice["payees_json"]),
                      "domains": json.loads(notice["domains_json"])}
        out.issuer_name = name
        result = compare(notice["fingerprint"], json.loads(notice["key_fields_json"]), text, key_fields(fields))
        if result.outcome == "match":
            out.proven = True
            out.signals.append(_signal(
                "issuer_code_valid", Strength.PROOF, PointsTo.GENUINE,
                f"It carries a valid Trinetra code from {name}, issued on {date}, and its details match the notice "
                f"{name} issued.",
                f"इस पर {name} का मान्य Trinetra कोड है, जो {date} को जारी हुआ था, और इसका विवरण {name} द्वारा जारी "
                "नोटिस से मेल खाता है।",
            ))
        elif result.outcome == "altered":
            out.altered = True
            what_en = " and ".join(CHANGED_EN[c] for c in result.changed)
            what_hi = " और ".join(CHANGED_HI[c] for c in result.changed)
            out.signals.append(_signal(
                "official_notice_altered", Strength.STRONG, PointsTo.FAKE,
                f"This notice was issued by {name}, but it has been changed since."
                + (f" Changed: {what_en}." if what_en else " The text no longer matches."),
                f"यह नोटिस {name} ने जारी किया था, लेकिन उसके बाद इसमें बदलाव किया गया है।"
                + (f" बदला गया: {what_hi}।" if what_hi else " इसका पाठ अब मेल नहीं खाता।"),
            ))
        else:
            out.unclear_code = code
        return out

    if invalid:
        out.signals.append(_signal(
            "invalid_code", Strength.STRONG, PointsTo.FAKE,
            "It shows a Trinetra verification code that is fake or has been cancelled.",
            "इस पर ऐसा Trinetra सत्यापन कोड है जो नकली है या रद्द कर दिया गया है।",
        ))
        return out

    for issuer in claimed_issuers(text, lookup.issuers()):
        out.issuer = issuer
        out.issuer_name = issuer["name"]
        if issuer["requires_code"]:
            out.signals.append(_signal(
                "missing_code", Strength.STRONG, PointsTo.FAKE,
                f"Real notices from {issuer['name']} always carry a Trinetra code, and this one has none.",
                f"{issuer['name']} के असली नोटिस पर हमेशा Trinetra कोड होता है, और इस पर कोई कोड नहीं है।",
            ))
        break
    return out
