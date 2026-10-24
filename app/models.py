"""Plain data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Kind(str, Enum):
    NOTICE = "notice"
    VOICE = "voice"
    TEXT = "text"


class Verdict(str, Enum):
    LIKELY_FAKE = "likely_fake"
    SUSPICIOUS = "suspicious"
    CANNOT_VERIFY = "cannot_verify"
    VERIFIED_OFFICIAL = "verified_official"
    COULD_NOT_CHECK = "could_not_check"


class Strength(str, Enum):
    STRONG = "strong"
    WEAK = "weak"
    PROOF = "proof"


class PointsTo(str, Enum):
    FAKE = "fake"
    GENUINE = "genuine"


@dataclass(frozen=True)
class Signal:
    check: str  # "source" | "ask" | "media"
    code: str  # e.g. "source.non_official_contact"
    strength: Strength
    points_to: PointsTo
    reason_en: str
    reason_hi: str


@dataclass(frozen=True)
class Account:
    number: str
    ifsc: str | None = None


@dataclass
class Fields:
    claimed_sender: str | None = None
    sender_id: str | None = None  # agency id from agencies.json, e.g. "rbi", "bank"
    sender_type: str | None = None  # agency type from agencies.json, e.g. "court", "bank"
    agencies: list[str] = field(default_factory=list)
    case_numbers: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    upi_ids: list[str] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)  # dd-mm-yyyy
    demands_payment: bool = False
    threatens_user: bool = False
    asks_secrecy: bool = False
    deadline_hours: float | None = None

    @property
    def payees(self) -> list[str]:
        return self.upi_ids + [a.number for a in self.accounts]


@dataclass
class Item:
    kind: Kind
    sha256: str
    text: str | None = None  # forwarded text, or text extracted from media
    data: bytes | None = None
    filename: str | None = None
    mime: str | None = None
    received_at: datetime | None = None


@dataclass
class Result:
    kind: Kind
    sha256: str
    verdict: Verdict
    rule_row: str
    signals: list[Signal]
    fields: Fields  # masked
    lang: str  # "en" | "hi"
    reply: str
    engine_version: str
    needs_review: bool = False
    reviewed: bool = False
    verify: dict[str, str | None] = field(default_factory=dict)  # where to check at the source, by language


@dataclass
class Case:
    id: str
    item_hashes: list[str] = field(default_factory=list)
    payees: list[str] = field(default_factory=list)
    status: str = "open"  # open | approved_fake | not_fake | needs_info
    created_at: datetime | None = None
