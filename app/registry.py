"""Read-only access to data/registry: official domains, agencies, known fakes."""

from __future__ import annotations

import json
import re
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


def known_fakes(registry_dir: Path = REGISTRY_DIR) -> dict[str, list[str]]:
    # Not cached: the review console adds to this file while the service runs.
    return json.loads((registry_dir / "known_fakes.json").read_text(encoding="utf-8"))
