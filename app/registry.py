"""Read-only access to data/registry: official domains, agencies, known fakes."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


# Reviewer-confirmed fakes. Module-level so tests can point it at a temporary copy.
KNOWN_FAKES_PATH = REGISTRY_DIR / "known_fakes.json"
_known_fakes_lock = threading.Lock()


def known_fakes() -> dict[str, list[str]]:
    # Not cached: the review console changes this file while the bot runs in another process.
    return json.loads(KNOWN_FAKES_PATH.read_text(encoding="utf-8"))


def update_known_fakes(add_hashes=(), add_payees=(), remove_hashes=(), remove_payees=()) -> None:
    """Add or remove confirmed fakes. Written to a temporary file and renamed, so readers never see half a file."""
    with _known_fakes_lock:
        data = known_fakes()
        hashes = [h for h in data.get("hashes", []) if h not in set(remove_hashes)]
        payees = [p for p in data.get("payees", []) if p not in set(remove_payees)]
        data["hashes"] = list(dict.fromkeys([*hashes, *add_hashes]))
        data["payees"] = list(dict.fromkeys([*payees, *add_payees]))
        tmp = KNOWN_FAKES_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(KNOWN_FAKES_PATH)
