"""The shared design: static files are served locally, zones are marked, badges always carry a word."""

from fastapi.testclient import TestClient

from app import ui
from app.main import app

client = TestClient(app)


def test_css_js_and_fonts_are_served_locally():
    page = client.get("/").text
    assert '/static/trinetra.css' in page and '/static/trinetra.js' in page
    assert "fonts.googleapis" not in page and "cdn." not in page
    assert client.get("/static/trinetra.css").status_code == 200
    font = client.get("/static/fonts/domine-latin-700-normal.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"


def test_every_zone_has_the_shared_header_and_footer():
    for path in ("/", "/issuer", "/console", "/v/TRN-7K2Q-9XFD"):
        page = client.get(path).text
        assert 'class="wordmark"' in page and 'class="site-footer"' in page, path


def test_zone_markers():
    assert "Restricted: reviewers only" in client.get("/console").text
    assert "Issuer portal" in client.get("/issuer").text
    assert "Restricted" not in client.get("/").text


def test_public_page_has_hero_toggle_and_safety_line():
    page = client.get("/").text
    assert "check before you pay" in page and 'data-set-lang="hi"' in page
    assert "@Trinetra_check_bot" in page and "Trinetra never asks for OTP, PIN or money." in page


def test_badges_always_carry_their_word():
    for kind, word in (("likely_fake", "Likely fake"), ("suspicious", "Suspicious"), ("cannot_verify", "Cannot verify"),
                       ("verified_official", "Verified"), ("revoked", "Cancelled"), ("valid", "Valid")):
        assert f">{word}</span>" in ui.badge(kind)
    assert "संदिग्ध" in ui.badge("suspicious", "hi")


def test_reply_card_keeps_the_reply_text():
    reply = "Likely fake. Do not pay.\n\n• Reason one.\n• Reason two.\n\nDo not pay. Reply 1.\n\nAutomated checks."
    card = ui.reply_card(reply, "likely_fake")
    for part in ("Likely fake. Do not pay.", "<li>Reason one.</li>", "Do not pay. Reply 1.", "Automated checks."):
        assert part in card
    assert "<script>" not in ui.reply_card("<script>x</script>", "suspicious")
