"""Voice notes, audio and video: ffmpeg -> transcript -> mask -> the same text checks. Speech-to-text is faked.

ffmpeg runs for real (it is local, not the network); the whisper model is never loaded.
"""

import shutil
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, verdict
from app.extract import speech
from app.intake import MESSAGES, Inbound, Intake
from app.models import Kind, Signal, Strength, PointsTo, Verdict
from app.pipeline import check_audio
from app.reply import UNCLEAR_SPEECH, VIDEO_SCOPE, VOICE_SCOPE
from app.store import Store
from tests.test_judge_mode import TEXTS

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

HINDI_DEMAND = "बेटा, मैं अस्पताल में हूँ। तुरंत 20,000 रुपये भेज दो, किसी को मत बताना।"
LISTENING = MESSAGES["en"]["listening"]


def ffmpeg(tmp_path, name: str, *args: str) -> bytes:
    out = tmp_path / name
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args, str(out)], check=True, timeout=60)
    return out.read_bytes()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    tone = ["-f", "lavfi", "-i", "sine=frequency=300:duration=2"]
    return {
        "ogg": ffmpeg(d, "v.ogg", *tone, "-c:a", "libopus"),
        "m4a": ffmpeg(d, "v.m4a", *tone, "-c:a", "aac"),
        "mp4": ffmpeg(d, "v.mp4", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=5", *tone,
                      "-c:v", "mpeg4", "-c:a", "aac", "-shortest"),
        "silent_mp4": ffmpeg(d, "s.mp4", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5", "-c:v", "mpeg4"),
        "long": ffmpeg(d, "long.ogg", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "200", "-c:a", "libopus",
                       "-b:a", "6k"),
    }


@pytest.fixture
def heard(monkeypatch):
    """Fake speech-to-text: whatever the test sets is "heard"; the PCM must have come from ffmpeg."""
    said = {"text": HINDI_DEMAND, "lang": "hi", "pcm_bytes": 0}

    def fake(pcm: bytes):
        said["pcm_bytes"] = len(pcm)
        return said["text"], said["lang"]

    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "transcribe_pcm", fake)
    monkeypatch.setattr(speech, "_model", lambda: pytest.fail("the real model must not load in tests"))
    return said


@pytest.fixture
def intake(tmp_path):
    return Intake(Store(tmp_path / "t.db"), salt="s", data_dir=tmp_path)


def say(intake, text=None, chat="1", **kw) -> list:
    out: list = []
    intake.handle(Inbound("telegram", chat, text, **kw), out.append)
    return out


def voice(intake, data: bytes, media="voice", mime=None, chat="1") -> list:
    return say(intake, media=media, mime=mime, file_size=len(data), fetch=lambda: data, chat=chat)


# --- the pipeline ---------------------------------------------------------------------------------


def test_ffmpeg_gives_16k_mono(media):
    assert abs(len(speech.to_pcm(media["ogg"])) - 2 * 16000 * 2) < 16000  # 2 s of 16-bit samples, give or take


def test_a_hindi_voice_note_with_a_money_demand_is_flagged(heard, media):
    r = check_audio(media["ogg"])
    assert r.kind is Kind.VOICE and r.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_FAKE)
    assert r.lang == "hi" and heard["pcm_bytes"] > 0
    assert "हमने सुना" in r.reply and VOICE_SCOPE["hi"] in r.reply


def test_a_voice_note_is_never_verified(heard, media):
    assert verdict.decide(Kind.VOICE, [], False, True, True)[0] is not Verdict.VERIFIED_OFFICIAL
    for text in TEXTS.values():
        heard["text"], heard["lang"] = text, "en"
        assert check_audio(media["ogg"]).verdict is not Verdict.VERIFIED_OFFICIAL


def test_an_empty_transcript_says_we_could_not_make_out_the_words(heard, media):
    heard["text"] = ""
    r = check_audio(media["ogg"])
    assert (r.verdict, r.rule_row) == (Verdict.COULD_NOT_CHECK, "R0")
    assert r.reply.startswith(UNCLEAR_SPEECH["en"]) and r.heard == ""


def test_a_video_is_transcribed_and_a_silent_video_is_unclear(heard, media):
    assert check_audio(media["mp4"]).verdict is Verdict.SUSPICIOUS
    assert check_audio(media["m4a"]).verdict is Verdict.SUSPICIOUS
    assert check_audio(media["silent_mp4"]).verdict is Verdict.COULD_NOT_CHECK


def test_a_corrupt_file_and_a_long_one_raise_a_polite_error(heard, media):
    with pytest.raises(speech.AudioUnreadable):
        check_audio(b"OggS" + b"\x13\x37" * 500)
    with pytest.raises(speech.AudioTooLong):
        check_audio(media["long"])


def test_transcription_times_out(heard, media, monkeypatch):
    monkeypatch.setattr(speech, "transcribe_pcm", lambda pcm: time.sleep(0.6) or ("", "en"))
    with pytest.raises(speech.SpeechTimeout):
        speech.transcribe(media["ogg"], timeout=0.1)
    time.sleep(0.6)  # let the one worker finish before the next test


def test_what_we_heard_is_masked_and_trimmed(heard, media):
    heard["text"], heard["lang"] = "My Aadhaar is 1234 5678 9012. " + "Please send money today. " * 20, "en"
    r = check_audio(media["ogg"])
    assert "1234 5678" not in r.reply and "XXXX XXXX 9012" in r.heard
    assert len(r.heard) <= 121 and r.heard.endswith("…")


def test_the_spoof_service_is_weak_only_and_skipped_when_down(heard, media, monkeypatch):
    def answer(spoof):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"spoof_likely": spoof})))

    [s] = speech.spoof_signal(b"\x00" * 100, "http://audio.test/check", answer(True))
    assert (s.code, s.strength, s.check) == ("media.voice_spoof_likely", Strength.WEAK, "media")
    assert speech.spoof_signal(b"\x00" * 100, "http://audio.test/check", answer(False)) == []
    down = httpx.Client(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    assert speech.spoof_signal(b"\x00" * 100, "http://audio.test/check", down) == []
    assert speech.spoof_signal(b"\x00" * 100, "") == []  # unset: skipped
    # On its own it never moves a harmless message past "cannot verify", and never past "suspicious".
    weak = Signal("media", "media.voice_spoof_likely", Strength.WEAK, PointsTo.FAKE, "x", "x")
    monkeypatch.setattr(speech, "spoof_signal", lambda pcm: [weak])
    heard["text"], heard["lang"] = "Hi, it's me. Are we still meeting for lunch on Sunday?", "en"
    assert check_audio(media["ogg"]).verdict is Verdict.CANNOT_VERIFY
    heard["text"] = HINDI_DEMAND
    assert check_audio(media["ogg"]).verdict is Verdict.SUSPICIOUS


# --- the bot -------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind,media_type,mime", [("ogg", "voice", None), ("m4a", "audio", "audio/mp4"),
                                                  ("mp4", "video", "video/mp4"), ("mp4", "video_note", None),
                                                  ("ogg", "document", "audio/ogg"), ("mp4", "document", "video/mp4")])
def test_the_bot_listens_then_answers_with_what_it_heard(heard, media, intake, kind, media_type, mime):
    say(intake, "YES")
    out = voice(intake, media[kind], media_type, mime)
    assert str(out[0]) == LISTENING
    text = str(out[-1])
    scope = VIDEO_SCOPE if kind == "mp4" else VOICE_SCOPE  # a video gets "voice or the face"
    assert text.startswith("संदिग्ध") and "हमने सुना: \"बेटा" in text and scope["hi"] in text
    assert any(d.startswith("why:") for _, d in out[-1].buttons)
    assert intake.store.stats()["by_kind"] == {"voice": 1}


def test_a_repeat_forward_is_answered_from_the_cache_with_what_we_heard(heard, media, intake, monkeypatch):
    say(intake, "YES")
    first = voice(intake, media["ogg"])[-1]
    monkeypatch.setattr(speech, "transcribe_pcm", lambda pcm: pytest.fail("cached: not transcribed again"))
    again = voice(intake, media["ogg"])
    assert [str(o) for o in again] == [str(first)]


def test_the_bot_handles_unclear_corrupt_long_and_off(heard, media, intake, monkeypatch):
    say(intake, "YES")
    heard["text"] = ""
    assert str(voice(intake, media["ogg"])[-1]).startswith(UNCLEAR_SPEECH["en"])
    assert [str(o) for o in voice(intake, b"OggS" + b"\x01" * 300)] == [LISTENING, MESSAGES["en"]["audio_unreadable"]]
    assert [str(o) for o in voice(intake, b"not audio at all")] == [MESSAGES["en"]["audio_unreadable"]]
    assert [str(o) for o in voice(intake, media["long"])] == [LISTENING, MESSAGES["en"]["audio_too_long"]]
    monkeypatch.setattr(speech, "available", lambda: False)
    assert [str(o) for o in voice(intake, media["m4a"])] == [LISTENING, MESSAGES["en"]["audio_off"]]
    s = intake.store.stats()
    assert s["by_verdict"] == {"could_not_check": 5} and s["by_kind"] == {"voice": 5}


def test_a_too_large_recording_is_refused_before_download(heard, intake):
    say(intake, "YES")
    out = say(intake, media="voice", file_size=17 * 1024 * 1024, fetch=lambda: pytest.fail("not downloaded"))
    assert [str(o) for o in out] == [MESSAGES["en"]["too_large"]]


def test_messages_about_recordings_follow_the_users_language(heard, intake):
    c = Intake(intake.store, salt="s", data_dir=intake.store.path.parent)
    out: list = []
    for msg in (Inbound("telegram", "9", "YES", lang_hint="hi"),
                Inbound("telegram", "9", media="voice", lang_hint="hi", file_size=10, fetch=lambda: b"nope")):
        c.handle(msg, out.append)
    assert str(out[-1]) == MESSAGES["hi"]["audio_unreadable"]


def test_a_reported_voice_note_becomes_a_case(heard, media, intake):
    say(intake, "YES")
    note = voice(intake, media["ogg"])[-1]
    say(intake, next(d for _, d in note.buttons if d.startswith("rep:")), callback_id="cb")
    out = say(intake, next(d for _, d in note.buttons if d.startswith("rep:")).replace("rep:", "repy:"), callback_id="cb")
    assert "case number is 1" in str(out[-1])
    [r] = intake.store.case_reports(1)
    assert r["kind"] == "voice" and r["evidence_path"].endswith(".ogg")


# --- the web page -----------------------------------------------------------------------------------


def test_the_web_page_checks_a_voice_note(heard, media):
    page = TestClient(main.app).post("/check", files={"file": ("note.ogg", media["ogg"], "audio/ogg")}).text
    assert "Listening" not in page and "संदिग्ध" in page and "हमने सुना" in page
    assert "I already paid</summary>" in page or "मैंने भुगतान कर दिया</summary>" in page


def test_the_web_page_is_polite_about_a_corrupt_recording(heard):
    resp = TestClient(main.app).post("/check", files={"file": ("x.mp4", b"\x00\x00\x00\x20ftypisom" + b"\x00" * 50,
                                                                "video/mp4")})
    assert resp.status_code == 200 and "We couldn&#x27;t open this recording" in resp.text


# --- through the real Telegram parser ---------------------------------------------------------------


@pytest.mark.parametrize("key,extra", [("voice", {"mime_type": "audio/ogg", "duration": 2}),
                                       ("audio", {"mime_type": "audio/mp4"}), ("video", {"mime_type": "video/mp4"}),
                                       ("video_note", {"length": 240}),
                                       ("voice", {"mime_type": "audio/ogg", "forward_origin_note": True})])
def test_a_telegram_voice_note_reaches_transcription(heard, media, intake, key, extra):
    """A real Telegram update (also a forwarded one) carries a download, so it is never "unsupported"."""
    from app.channels.telegram import parse_update

    class Api:
        def download(self, file_id):
            assert file_id == "f1"
            return media["ogg"] if key in ("voice", "audio") else media["mp4"]

    forwarded = {"forward_origin": {"type": "user"}} if extra.pop("forward_origin_note", False) else {}
    upd = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 42, "type": "private"},
                                       "from": {"language_code": "en"}, **forwarded,
                                       key: {"file_id": "f1", "file_size": 9000, **extra}}}
    msg = parse_update(upd, Api())
    assert msg.media == key and msg.fetch is not None and msg.file_size == 9000
    say(intake, "YES", chat="42")
    out: list = []
    intake.handle(msg, out.append)
    assert str(out[0]) == LISTENING and str(out[-1]).startswith("संदिग्ध")
    assert MESSAGES["en"]["unsupported"] not in [str(o) for o in out]
