"""Sample notices for judges and first-time users: one forged, one genuine, one altered.

The forged warrant is F1 from the synthetic test set. The demo summons and its altered copy are
files made with the issuer portal and tools/demo_alter_amount.py, named in DEMO_SUMMONS_PATH and
DEMO_ALTERED_PATH. A sample whose file is missing is left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import ROOT, settings

F1_WARRANT = ROOT / "data" / "test-set" / "png" / "F1_sc_warrant_en.png"


@dataclass(frozen=True)
class Sample:
    slug: str  # used in the download URL
    title: str
    hint: str
    path: Path | None


def samples() -> list[Sample]:
    return [
        Sample("forged-warrant", "A forged “Supreme Court” warrant", "The classic digital-arrest scam.", F1_WARRANT),
        Sample("summons", "A genuine court summons", "Issued through Trinetra by Pune District Court (demo).",
               settings.demo_summons_path),
        Sample("summons-altered", "The same summons, altered", "Someone changed the amount. The QR code is real.",
               settings.demo_altered_path),
    ]


def available() -> list[Sample]:
    return [s for s in samples() if s.path is not None and s.path.is_file()]


def sample_paths() -> list[Path]:
    return [s.path for s in available()]
