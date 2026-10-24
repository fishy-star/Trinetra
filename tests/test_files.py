"""Notices as files: OCR, PDF text, type and size checks, Telegram downloads, the web upload.

These run the real local Tesseract on dev-split items only. No network.
"""

import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import intake as intake_mod
from app.channels.telegram import TelegramAPI, parse_update
from app.extract import ocr
from app.intake import MESSAGES, Inbound, Intake
from app.main import app
from app.pipeline import check_file
from app.store import Store
from tests.test_testset import TEST_SET

F1_PNG = (TEST_SET / "png/F1_sc_warrant_en.png").read_bytes()
F1_PDF = (TEST_SET / "pdf/F1_sc_warrant_en.pdf").read_bytes()
G2_PNG = (TEST_SET / "png/G2_bank_rekyc_hi.png").read_bytes()


def blank_png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(b, "PNG")
    return b.getvalue()


# --- extraction --------------------------------------------------------------


def test_sniff_uses_magic_bytes():
    assert ocr.sniff(F1_PNG) == "png"
    assert ocr.sniff(F1_PDF) == "pdf"
    assert ocr.sniff(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert ocr.sniff(b"PK\x03\x04 a zip renamed to .pdf") is None


def test_clean_ocr_repairs_upi_and_rupee():
    assert ocr.clean_ocr("UPI: cbi.suraksha. jama@ybl") == "UPI: cbi.suraksha.jama@ybl"
    assert ocr.clean_ocr("pay name @ ybl") == "pay name@ybl"
    assert ocr.clean_ocr("राशि: %4,85,000") == "राशि: ₹4,85,000"
    assert ocr.clean_ocr("interest of 5% a year") == "interest of 5% a year"


@pytest.mark.parametrize("angle", [-9, -4, 6, 11])
def test_skew_angle_is_recovered(angle):
    page = Image.open(io.BytesIO(F1_PNG)).convert("L")
    tilted = page.rotate(angle, expand=True, fillcolor=110)  # grey table behind the paper
    assert ocr.skew_angle(tilted) == pytest.approx(-angle, abs=0.6)


def test_tilted_photo_of_dev_notice_is_still_likely_fake():
    page = Image.open(io.BytesIO(F1_PNG)).rotate(-8, expand=True, fillcolor=(120, 110, 100))
    b = io.BytesIO()
    page.convert("RGB").save(b, "JPEG", quality=60)
    result = check_file(b.getvalue())
    assert result.verdict.value == "likely_fake"
    assert "gmail" not in result.reply  # reasons are plain sentences, not extracted values
    assert result.reply.count("• ") >= 2


def test_blank_image_could_not_be_checked():
    result = check_file(blank_png(), fallback_lang="hi")
    assert result.rule_row == "R0"
    assert result.reply.startswith("हम यह फ़ाइल नहीं पढ़ सके")


def test_garbage_pdf_could_not_be_checked():
    assert check_file(b"%PDF-1.7 this is not really a pdf").rule_row == "R0"


# --- intake ------------------------------------------------------------------


class Files:
    def __init__(self, tmp_path):
        self.fetches = 0
        self.intake = Intake(Store(tmp_path / "t.db"), salt="s")
        self.say(text="YES")

    def say(self, text=None, media=None, data=None, mime=None, size=None, hint="en"):
        def fetch():
            self.fetches += 1
            return data

        out: list[str] = []
        msg = Inbound("telegram", "9", text, media, hint, mime, size, fetch if data is not None else None)
        self.intake.handle(msg, out.append)
        return out


@pytest.fixture
def files(tmp_path):
    return Files(tmp_path)


def test_photo_gets_checking_then_verdict(files):
    out = files.say(media="photo", data=F1_PNG)
    assert out[0] == MESSAGES["en"]["checking"]
    assert out[1].startswith("Likely fake. Do not pay.")


def test_pdf_document_is_checked(files):
    out = files.say(media="document", data=F1_PDF, mime="application/pdf")
    assert out[1].startswith("Likely fake")


def test_same_file_again_comes_from_cache_in_its_own_language(files):
    first = files.say(media="photo", data=G2_PNG)
    again = files.say(media="photo", data=G2_PNG)
    assert first[1].startswith("हम इसकी पुष्टि नहीं कर सके")
    assert again == [first[1]]


def test_declared_pdf_that_is_really_an_image_is_refused(files):
    assert files.say(media="document", data=F1_PNG, mime="application/pdf") == [MESSAGES["en"]["unsupported"]]


def test_unsupported_document_type_is_not_downloaded(files):
    assert files.say(media="document", data=b"hello", mime="text/plain") == [MESSAGES["en"]["unsupported"]]
    assert files.fetches == 0


def test_declared_size_over_limit_is_not_downloaded(files):
    out = files.say(media="photo", data=F1_PNG, size=6 * 1024 * 1024)
    assert out == [MESSAGES["en"]["too_large"]]
    assert files.fetches == 0


def test_actual_size_over_limit_is_refused(files, monkeypatch):
    monkeypatch.setitem(intake_mod.MAX_BYTES, "png", 1000)
    assert files.say(media="photo", data=F1_PNG) == [MESSAGES["en"]["too_large"]]


def test_nothing_is_downloaded_before_consent(tmp_path):
    fresh = Intake(Store(tmp_path / "t.db"), salt="s")
    fetched = []
    out: list[str] = []
    fresh.handle(Inbound("telegram", "5", media="photo", fetch=lambda: fetched.append(1) or F1_PNG), out.append)
    assert out == [intake_mod.CONSENT] and fetched == []


def test_files_count_toward_rate_limit(files, monkeypatch):
    monkeypatch.setattr(files.intake.store, "items_since", lambda uid, since: 10)
    assert files.say(media="photo", data=F1_PNG) == [MESSAGES["en"]["limit_hour"]]
    assert files.fetches == 0


# --- Telegram ------------------------------------------------------------------


def test_telegram_photo_downloads_largest_size():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            assert json.loads(request.content) == {"file_id": "big"}
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/f.jpg"}})
        return httpx.Response(200, content=F1_PNG)

    api = TelegramAPI("123:TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler)))
    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 7, "type": "private"},
            "photo": [{"file_id": "small", "file_size": 900}, {"file_id": "big", "file_size": 90000}],
        },
    }
    msg = parse_update(update, api)
    assert msg.media == "photo" and msg.file_size == 90000
    assert requests == []  # nothing fetched until intake asks
    assert msg.fetch() == F1_PNG
    assert requests[-1].endswith("/file/bot123:TOKEN/photos/f.jpg")


def test_telegram_document_carries_mime_and_size():
    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 7, "type": "private"},
            "document": {"file_id": "d", "mime_type": "application/pdf", "file_size": 1234},
        },
    }
    msg = parse_update(update)
    assert (msg.media, msg.mime, msg.file_size, msg.fetch) == ("document", "application/pdf", 1234, None)


# --- web page --------------------------------------------------------------------


def test_web_upload_checks_a_notice():
    resp = TestClient(app).post("/check", files={"file": ("notice.png", F1_PNG, "image/png")})
    assert resp.status_code == 200
    assert "Likely fake. Do not pay." in resp.text


def test_web_upload_refuses_other_types():
    resp = TestClient(app).post("/check", files={"file": ("notice.pdf", b"not a pdf at all", "application/pdf")})
    assert MESSAGES["en"]["unsupported"] in resp.text.replace("&#x27;", "'")
