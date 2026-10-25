"""The web page's voice recorder: the markup, and the server accepting what browsers record (STT faked)."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import main
from app.extract import speech
from app.intake import sniff_av

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
DEMAND = "Hi Mom, it's me. I'm in hospital. Please send 15000 rupees today. Don't tell Dad."


def record(tmp_path, name: str, *codec: str) -> bytes:
    """What MediaRecorder produces: WebM/Opus (Chrome, Android), MP4/AAC (iPhone Safari), Ogg/Opus (Firefox)."""
    out = tmp_path / name
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=2", *codec, str(out)], check=True, timeout=60)
    return out.read_bytes()


@pytest.fixture
def heard(monkeypatch):
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "transcribe_pcm", lambda pcm: (DEMAND, "en"))
    monkeypatch.setattr(speech, "_model", lambda: pytest.fail("the real model must not load in tests"))


@pytest.mark.parametrize("name,codec,content_type,kind", [
    ("rec.webm", ("-c:a", "libopus", "-f", "webm"), "audio/webm;codecs=opus", "webm"),
    ("rec.webm", ("-c:a", "libopus", "-f", "webm"), "audio/webm", "webm"),
    ("rec.m4a", ("-c:a", "aac", "-f", "mp4"), "audio/mp4", "mp4"),
    ("rec.ogg", ("-c:a", "libopus", "-f", "ogg"), "audio/ogg;codecs=opus", "ogg"),
])
def test_browser_recordings_go_through_the_same_pipeline(heard, tmp_path, name, codec, content_type, kind):
    data = record(tmp_path, name, *codec)
    assert sniff_av(data) == kind
    page = TestClient(main.app).post("/check", files={"file": (f"voice-note.{name.split('.')[1]}", data, content_type)},
                                     data={"text": ""}).text
    assert "Suspicious" in page and "We heard: &quot;Hi Mom" in page
    assert "Report</summary>" in page and "I already paid</summary>" in page


def test_the_check_page_has_the_recorder_next_to_upload():
    page = TestClient(main.app).get("/").text
    upload = page.index('id="file"')
    recorder = page.index("data-recorder")
    assert upload < recorder < page.index('name="text"')
    assert re.search(r'<div class="recorder" data-recorder hidden>', page)  # shown only by the page script
    assert "Record voice note" in page and "वॉइस नोट रिकॉर्ड करें" in page
    assert "Your browser can&#x27;t record here. Upload an audio file or use our Telegram bot." in page
    assert 'accept="image/png,image/jpeg,application/pdf,audio/*,video/*"' in page


def test_the_try_page_has_a_recorder_that_posts_to_the_check_endpoint():
    page = TestClient(main.app).get("/try").text
    form = page[page.index('<section class="card try-record">'):]
    assert '<form method="post" action="/check" enctype="multipart/form-data">' in form
    assert 'type="file" name="file"' in form and "data-recorder" in form and "Check this" in form


def test_labels_are_plain_words_without_emoji():
    page = TestClient(main.app).get("/").text
    block = page[page.index("data-recorder"):page.index("</div>", page.index("data-label-checking"))]
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", block)
