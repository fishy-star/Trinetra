"""Text shown on screen in a video: about 6 evenly spaced frames, OCR (Hindi + English), unique lines.

Runs alongside transcription, with a 20-second budget for probing, frame grabs and OCR together. On timeout
the lines read so far are used. Nothing here looks at faces or judges whether a video is real.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.extract import ocr

log = logging.getLogger(__name__)

FRAMES = 6
BUDGET_SECONDS = 20.0
MAX_WIDTH = 1280

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen")


def probe(path: Path, timeout: float) -> float | None:
    """The duration in seconds if the file has a real video track (not just cover art), else None."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type:stream_disposition=attached_pic",
           "-of", "json", str(path)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=max(timeout, 0.1), check=False)
        info = json.loads(done.stdout or b"{}")
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
    video = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
             and not s.get("disposition", {}).get("attached_pic")]
    if not video:
        return None
    try:
        return max(float(info.get("format", {}).get("duration", 0)), 0.1)
    except (TypeError, ValueError):
        return 1.0


def grab_frames(path: Path, duration: float, out: Path, timeout: float, n: int = FRAMES) -> list[Path]:
    """n frames spread evenly over the video, as PNG files in `out`."""
    rate = n / duration
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-an",
           "-vf", f"fps={rate:.6f},scale='min({MAX_WIDTH},iw)':-2", "-frames:v", str(n), str(out / "f%02d.png")]
    try:
        subprocess.run(cmd, capture_output=True, timeout=max(timeout, 0.1), check=False)
    except subprocess.TimeoutExpired:
        pass  # use whatever frames were written
    return sorted(out.glob("f*.png"))


def unique_lines(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    lines = []
    for text in texts:
        for line in text.splitlines():
            line = " ".join(line.split())
            key = line.casefold()
            if len(re.findall(r"\w", line)) < 3 or key in seen:
                continue
            seen.add(key)
            lines.append(line)
    return lines


class ScreenReader:
    """Reads on-screen text in the background. `result()` waits until the budget is spent, then returns what it has."""

    def __init__(self, data: bytes, budget: float = BUDGET_SECONDS, read: Callable[[bytes], str] = ocr.image_text):
        self.deadline = time.monotonic() + budget
        self.is_video = False
        self._texts: list[str] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._read = read
        _pool.submit(self._run, data)

    def _left(self) -> float:
        return self.deadline - time.monotonic()

    def _run(self, data: bytes) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="trinetra-frames-") as tmp:
                src = Path(tmp) / "in"
                src.write_bytes(data)
                duration = probe(src, self._left())
                if duration is None:
                    return
                self.is_video = True
                for frame in grab_frames(src, duration, Path(tmp), self._left()):
                    if self._left() <= 0:
                        break
                    try:
                        text = self._read(frame.read_bytes())
                    except ocr.UnreadableFile:
                        continue
                    with self._lock:
                        self._texts.append(text)
        except Exception:  # noqa: BLE001 - on-screen text is extra; the audio is still checked
            log.exception("reading on-screen text failed")
        finally:
            self._done.set()

    def result(self) -> tuple[bool, str]:
        """(is it a video, the unique on-screen lines). Waits at most until the budget is spent."""
        self._done.wait(timeout=max(self._left(), 0))
        with self._lock:
            lines = unique_lines(self._texts)
        # Each caption line is its own statement: a "do not tell anyone" line must not read as negating the line above.
        return self.is_video, "\n".join(line if line[-1] in ".!?।" else f"{line}." for line in lines)
