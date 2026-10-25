"""Video: on-screen text from a few frames (real ffmpeg and Tesseract), added to the transcript. STT is faked."""

import io
import shutil
import subprocess
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.extract import speech, video
from app.intake import MESSAGES, Inbound, Intake
from app.models import Verdict
from app.pipeline import check_audio
from app.reply import UNCLEAR_SPEECH, VIDEO_SCOPE, VOICE_SCOPE
from app.store import Store

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("tesseract") is None,
                                reason="ffmpeg and tesseract are needed")


def slide(lines: list[str]) -> bytes:
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((60, 140 + i * 90), line, fill="black", font=ImageFont.load_default(size=56))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def make_video(tmp_path, name: str, picture: bytes | None, seconds: int = 3, sound: bool = False) -> bytes:
    src = tmp_path / f"{name}.png"
    if picture is not None:
        src.write_bytes(picture)
        inputs = ["-loop", "1", "-i", str(src)]
    else:
        inputs = ["-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=5"]
    if sound:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=300"]
    out = tmp_path / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs, "-t", str(seconds), "-r", "5",
                    "-c:v", "mpeg4", *(["-c:a", "aac"] if sound else []), str(out)], check=True, timeout=60)
    return out.read_bytes()


@pytest.fixture
def silent(monkeypatch):
    """Nothing is said: the fake speech-to-text hears no words."""
    said = {"text": ""}
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "transcribe_pcm", lambda pcm: (said["text"], "en"))
    monkeypatch.setattr(speech, "_model", lambda: pytest.fail("the real model must not load in tests"))
    return said


def test_a_silent_video_with_a_payment_demand_on_screen_is_flagged(silent, tmp_path):
    r = check_audio(make_video(tmp_path, "pay", slide(["Pay to xyz@ybl today"])))
    assert r.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_FAKE)
    assert "ask.payment_to_non_official_payee" in {s.code for s in r.signals}
    assert r.video and "xyz@ybl" in r.seen and r.heard == ""
    assert 'On screen: "' in r.reply and VIDEO_SCOPE["en"] in r.reply


def test_a_bare_payee_on_screen_is_read_and_checked_like_typed_text(silent, tmp_path):
    # "pay to xyz@ybl" with no pressure is "cannot verify" as typed text too: a friend's UPI ID is not a scam.
    r = check_audio(make_video(tmp_path, "bare", slide(["pay to xyz@ybl"])))
    assert "xyz@ybl" in r.seen and r.verdict is Verdict.CANNOT_VERIFY
    assert "source.payee_unconfirmed" in {s.code for s in r.signals}


def test_a_video_with_no_text_and_no_speech_says_we_could_not_make_out_the_words(silent, tmp_path):
    r = check_audio(make_video(tmp_path, "blank", None, sound=True))
    assert (r.verdict, r.rule_row) == (Verdict.COULD_NOT_CHECK, "R0")
    assert r.reply.startswith(UNCLEAR_SPEECH["en"])


def test_speech_and_screen_text_are_checked_together(silent, tmp_path):
    silent["text"] = "Hello, please do this quickly."
    r = check_audio(make_video(tmp_path, "both", slide(["Send Rs 20,000 to help@ybl", "Do not tell anyone"]),
                               sound=True))
    assert r.verdict is Verdict.SUSPICIOUS and r.heard.startswith("Hello") and "help@ybl" in r.seen


def test_a_video_is_never_verified_and_never_claims_to_spot_fake_faces(silent, tmp_path):
    r = check_audio(make_video(tmp_path, "notice", slide(["Supreme Court of India", "Case 123/2026"])))
    assert r.verdict is not Verdict.VERIFIED_OFFICIAL
    for lang in ("en", "hi"):
        assert "deepfake" not in VIDEO_SCOPE[lang].lower()
    assert "don't judge whether the voice or the face is real" in VIDEO_SCOPE["en"]


def test_audio_only_files_are_not_read_for_frames(silent, tmp_path):
    reader = video.ScreenReader(make_video(tmp_path, "a", slide(["Pay to xyz@ybl today"]))[:0] or b"OggS")
    assert reader.result() == (False, "")


def test_reading_stops_at_the_budget_and_keeps_what_it_has(tmp_path):
    data = make_video(tmp_path, "slow", slide(["Pay to xyz@ybl today"]))
    calls = []

    def slow_ocr(png: bytes) -> str:
        calls.append(1)
        time.sleep(0.7)  # the second frame would finish after the 1-second budget
        return "line one read" if len(calls) == 1 else "read too late"

    start = time.monotonic()
    reader = video.ScreenReader(data, budget=1.0, read=slow_ocr)
    is_video, text = reader.result()
    assert time.monotonic() - start < 1.6
    assert is_video and "line one read" in text and "read too late" not in text
    time.sleep(0.8)  # let the worker finish its last frame before the next test


def test_unique_lines_merge_repeated_frames():
    assert video.unique_lines(["Pay now\nPay  now\n|", "pay now\nCall 1930"]) == ["Pay now", "Call 1930"]


def test_the_bot_reads_a_round_video_message(silent, tmp_path):
    intake = Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path)
    data = make_video(tmp_path, "round", slide(["Pay to xyz@ybl today"]))
    out: list = []
    intake.handle(Inbound("telegram", "1", "YES"), out.append)
    intake.handle(Inbound("telegram", "1", media="video_note", file_size=len(data), fetch=lambda: data), out.append)
    assert str(out[-2]) == MESSAGES["en"]["listening"]
    text = str(out[-1])
    assert text.startswith("Suspicious") and 'On screen: "' in text and VIDEO_SCOPE["en"] in text
    assert VOICE_SCOPE["en"] not in text
