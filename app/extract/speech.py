"""Voice notes and videos: ffmpeg to 16 kHz mono, then speech-to-text with faster-whisper (small, CPU, int8).

The model is loaded from models/ only; it is downloaded once by tools/download_models.py and never at run time.
There is no fake-voice detector here. If AUDIO_SERVICE_URL is set, its spoof_likely answer becomes one WEAK
signal (see spoof_signal); if it is unset or down, it is skipped silently.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from app.config import ROOT, settings
from app.models import PointsTo, Signal, Strength

log = logging.getLogger(__name__)

MODEL_SIZE = "small"
MODEL_DIR = ROOT / "models" / f"whisper-{MODEL_SIZE}"
SAMPLE_RATE = 16000
MAX_SECONDS = 180  # 3 minutes
TIMEOUT_SECONDS = 60  # decoding and transcription together
FFMPEG_TIMEOUT_SECONDS = 30
LANGS = ("hi", "en")

# One transcription at a time: the model is large and uses every CPU core it is given.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")


class AudioError(Exception):
    """A recording we could not check. `key` names the polite message the bot and the web page send."""

    key = "audio_unreadable"


class AudioUnreadable(AudioError):
    key = "audio_unreadable"


class AudioTooLong(AudioError):
    key = "audio_too_long"


class SpeechTimeout(AudioError):
    key = "audio_timeout"


class SpeechUnavailable(AudioError):
    key = "audio_off"


@dataclass(frozen=True)
class Transcript:
    text: str  # empty when no words could be made out
    lang: str  # "hi" | "en"
    pcm: bytes = b""  # the 16 kHz mono audio, for the optional spoof service; never stored


def to_pcm(data: bytes) -> bytes:
    """Any audio or video ffmpeg can read -> 16 kHz mono signed 16-bit PCM. The video track is dropped."""
    with tempfile.TemporaryDirectory(prefix="trinetra-av-") as tmp:
        src = Path(tmp) / "in"
        src.write_bytes(data)
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
               "-ar", str(SAMPLE_RATE), "-t", str(MAX_SECONDS + 1), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
        except FileNotFoundError:
            raise SpeechUnavailable("ffmpeg is not installed") from None
        except subprocess.TimeoutExpired:
            raise AudioUnreadable("ffmpeg took too long") from None
    err = done.stderr.decode("utf-8", "replace")
    if done.returncode != 0:
        if "does not contain any stream" in err or "matches no streams" in err:
            return b""  # a video with no sound: nothing to hear
        raise AudioUnreadable(err.strip().splitlines()[-1][:200] if err.strip() else "ffmpeg failed")
    if len(done.stdout) > (MAX_SECONDS + 0.5) * SAMPLE_RATE * 2:
        raise AudioTooLong(f"longer than {MAX_SECONDS} s")
    return done.stdout


def model_path() -> Path | None:
    return MODEL_DIR if (MODEL_DIR / "model.bin").is_file() else None


@lru_cache(maxsize=1)
def _model():
    path = model_path()
    if path is None:
        raise SpeechUnavailable("the speech model is not downloaded: run tools/download_models.py")
    from faster_whisper import WhisperModel

    return WhisperModel(str(path), device="cpu", compute_type="int8")


def transcribe_pcm(pcm: bytes) -> tuple[str, str]:
    """(text, language) with faster-whisper. The language is Hindi or English, whichever the audio is closer to."""
    import numpy as np

    model = _model()
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    _, _, probs = model.detect_language(audio)
    scores = dict(probs)
    lang = max(LANGS, key=lambda lg: scores.get(lg, 0.0))
    segments, _ = model.transcribe(audio, language=lang, beam_size=5, vad_filter=True,
                                   condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip(), lang


def _decode_and_transcribe(data: bytes) -> Transcript:
    pcm = to_pcm(data)
    if len(pcm) < SAMPLE_RATE // 2:  # under a quarter of a second
        return Transcript("", "en", pcm)
    text, lang = transcribe_pcm(pcm)
    return Transcript(text, lang, pcm)


def available() -> bool:
    return settings.stt_mode == "whisper"


def transcribe(data: bytes, timeout: float = TIMEOUT_SECONDS) -> Transcript:
    """Decode and transcribe off the caller's thread, giving up after `timeout` seconds."""
    if not available():
        raise SpeechUnavailable(f"STT_MODE={settings.stt_mode}")
    future = _pool.submit(_decode_and_transcribe, data)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise SpeechTimeout(f"over {timeout} s") from None


# --- the optional spoof service --------------------------------------------------------------

SPOOF_REASON = {
    "en": "The recording shows some signs of a machine-made voice. This alone proves nothing.",
    "hi": "रिकॉर्डिंग में मशीन से बनी आवाज़ के कुछ संकेत हैं। सिर्फ़ इससे कुछ साबित नहीं होता।",
}


def spoof_signal(pcm: bytes, url: str | None = None, client: httpx.Client | None = None) -> list[Signal]:
    """POST the 16 kHz mono PCM to AUDIO_SERVICE_URL; {"spoof_likely": true} becomes one WEAK signal.

    Unset, down, slow or a bad answer: no signal, silently. A detector alone never moves a verdict past suspicious.
    """
    url = url if url is not None else settings.audio_service_url
    if not url or not pcm:
        return []
    try:
        http = client or httpx.Client(timeout=10)
        resp = http.post(url, content=pcm, headers={"Content-Type": "audio/L16; rate=16000; channels=1"})
        resp.raise_for_status()
        spoof = resp.json().get("spoof_likely") is True
    except Exception as exc:  # noqa: BLE001 - the service is optional
        log.info("audio service skipped (%s)", type(exc).__name__)
        return []
    if not spoof:
        return []
    return [Signal("media", "media.voice_spoof_likely", Strength.WEAK, PointsTo.FAKE,
                   SPOOF_REASON["en"], SPOOF_REASON["hi"])]
