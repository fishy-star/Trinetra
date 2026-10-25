"""Download the speech-to-text model once, into models/ (gitignored).

    python tools/download_models.py

After this, transcription runs fully offline: app/extract/speech.py loads the model from this folder
and never downloads anything itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extract.speech import MODEL_DIR, MODEL_SIZE  # noqa: E402


def main() -> None:
    from faster_whisper import download_model

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = download_model(MODEL_SIZE, output_dir=str(MODEL_DIR))
    size = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    print(f"faster-whisper {MODEL_SIZE} model in {Path(path).relative_to(ROOT)} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
