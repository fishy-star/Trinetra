"""Read-only access to data/registry: official domains, agencies, known fakes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.checks.lexicon import normalize
from app.config import REGISTRY_DIR

GOVERNMENT_TYPES = frozenset({"government", "court", "regulator", "police"})
OFFICIAL_TYPES = GOVERNMENT_TYPES | {"bank", "financial"}


@dataclass(frozen=True)
class Agency:
    id: str
    name: str | None  # None for generic patterns; the matched text is used instead
    type: str
    patterns: tuple[re.Pattern[str], ...]


@lru_cache
def official_domains(registry_dir: Path = REGISTRY_DIR) -> tuple[str, ...]:
    lines = (registry_dir / "official_domains.txt").read_text(encoding="utf-8").splitlines()
    return tuple(ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#"))


def is_official_domain(host: str) -> bool:
    host = host.lower().rstrip(".").removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in official_domains())


@lru_cache
def agencies(registry_dir: Path = REGISTRY_DIR) -> tuple[Agency, ...]:
    raw = json.loads((registry_dir / "agencies.json").read_text(encoding="utf-8"))["agencies"]
    return tuple(
        Agency(
            id=a["id"],
            name=a["name"],
            type=a["type"],
            patterns=tuple(re.compile(normalize(p), re.IGNORECASE) for p in a["patterns"]),
        )
        for a in raw
    )


@lru_cache
def official_payees(registry_dir: Path = REGISTRY_DIR) -> frozenset[str]:
    raw = json.loads((registry_dir / "official_payees.json").read_text(encoding="utf-8"))["payees"]
    return frozenset(p.strip().lower() for p in raw)


def is_official_payee(payee: str) -> bool:
    return payee.strip().lower() in official_payees()


def normalize_payee(payee: str) -> str:
    """UPI IDs compare case-insensitively; account numbers by digits only."""
    payee = payee.strip().lower()
    return payee if "@" in payee else "".join(c for c in payee if c.isdigit())


# A read-only seed list of fakes confirmed outside this deployment. The app never writes it:
# fakes approved in the review console are kept in the runtime database (Store.known_fakes).
KNOWN_FAKES_PATH = REGISTRY_DIR / "known_fakes.json"


@lru_cache
def known_fakes() -> dict[str, list[str]]:
    return json.loads(KNOWN_FAKES_PATH.read_text(encoding="utf-8"))


@lru_cache
def verify_routes() -> tuple[dict[str, Any], ...]:
    """Official places to check a notice, by claimed sender (data/registry/verify_routes.json)."""
    raw = json.loads((REGISTRY_DIR / "verify_routes.json").read_text(encoding="utf-8"))
    return tuple(raw["routes"])


def verify_route(sender_type: str | None, sender_id: str | None) -> dict[str, Any] | None:
    for route in verify_routes():
        if sender_id in route.get("sender_ids", ()) or sender_type in route.get("sender_types", ()):
            return route
    return None
