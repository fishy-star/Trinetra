"""Issuer verification codes: issue, stamp and compare notices.

An issuer (a court, bank, police unit…) issues a notice through the portal. Trinetra keeps a
fingerprint of its text and its key fields (case numbers, amounts, payees, dates), and gives it a
random code such as TRN-7K2Q-9XFD with a QR code linking to /v/<code>. When someone forwards the
notice, the code is read from the QR (or the printed text) and the notice is compared with what
was issued.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from app.checks import lexicon
from app.models import Fields
from app.registry import normalize_payee

# No 0/O, 1/I/L or U, so a code read aloud or by OCR is not ambiguous. 30^8 ≈ 6.5 × 10^11 codes.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_RE = re.compile(rf"\bTRN[-\s]?([{ALPHABET}]{{4}})[-\s]?([{ALPHABET}]{{4}})\b")
# The parts of Trinetra's stamp: removed before checking, so the stamp's own link and code are never
# read as the notice's contact details. Only these phrases go; the rest of a line stays.
STAMP_PARTS = re.compile(
    r"verify this notice:?\s*(?:forward it to trinetra,?\s*or visit)?"
    r"|\S*/v/TRN[-\s]?\w{4}[-\s]?\w{4}"
    r"|\(?code:?\s+TRN[-\s]?\w{4}[-\s]?\w{4}\)?(?:\s*·\s*issued by[^\n]*)?"
    r"|\bTRN[-\s]?[A-Z0-9]{4}[-\s]?[A-Z0-9]{4}\b",
    re.IGNORECASE,
)
KEY_FIELDS = ("amounts", "payees", "case_numbers", "dates")
ISSUER_TYPES = ("court", "bank", "police", "government", "other")
NOTICE_TYPES = ("Summons", "Notice", "Order", "Demand letter", "Statement", "Other")

# Comparison thresholds: share of the issued notice's words found in the forwarded copy.
MATCH_RECALL = 0.85  # at least this, with all key fields present and none changed: verified
DIFFERENT_RECALL = 0.5  # below this, with the copy readable: a different notice wearing a real code
READABLE_SHARE = 0.5  # the copy must have at least half as many words as the original to judge


def new_code() -> str:
    chars = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"TRN-{chars[:4]}-{chars[4:]}"


def find_codes(text: str) -> list[str]:
    """Codes printed in text or carried in a /v/<code> link."""
    return list(dict.fromkeys(f"TRN-{a}-{b}" for a, b in CODE_RE.findall(text.upper())))


def verify_url(base_url: str, code: str) -> str:
    return f"{base_url.rstrip('/')}/v/{code}"


def strip_stamp(text: str) -> str:
    """Remove what Trinetra's stamp adds, so its link and code are not read as the notice's own."""
    return STAMP_PARTS.sub(" ", text)


# --- fingerprints and key fields ------------------------------------------------------


def tokens(text: str) -> set[str]:
    text = lexicon.normalize(strip_stamp(text)).lower()
    return {t for t in re.findall(r"[\wऀ-ॿ]+", text) if len(t) >= 2}


def fingerprint(text: str) -> str:
    return " ".join(sorted(tokens(text)))


_MULTIPLIER = {"crore": 10_000_000, "करोड": 10_000_000, "lakh": 100_000, "lakhs": 100_000, "लाख": 100_000}


def normalize_amount(raw: str) -> str:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    if not m:
        return raw.strip().lower()
    value = float(m.group(0).replace(",", ""))
    for word, mult in _MULTIPLIER.items():
        if word in raw.lower():
            value *= mult
    return str(round(value))


def normalize_case_number(raw: str) -> str:
    return re.sub(r"\s+", "", raw.upper()).replace("O", "0")


def key_fields(fields: Fields) -> dict[str, list[str]]:
    """The fields a change to which makes an issued notice "altered"."""
    return {
        "amounts": sorted({normalize_amount(a) for a in fields.amounts}),
        "payees": sorted({normalize_payee(p) for p in fields.payees if normalize_payee(p)}),
        "case_numbers": sorted({normalize_case_number(c) for c in fields.case_numbers}),
        "dates": sorted(set(fields.dates)),
    }


@dataclass(frozen=True)
class Comparison:
    outcome: str  # "match" | "altered" | "unclear"
    changed: tuple[str, ...]  # key fields with a value the issued notice did not have
    recall: float


def compare(issued_fingerprint: str, issued_keys: dict[str, list[str]], text: str, keys: dict[str, list[str]]) -> Comparison:
    issued = set(issued_fingerprint.split())
    seen = tokens(text)
    recall = len(issued & seen) / len(issued) if issued else 0.0
    readable = len(seen) >= READABLE_SHARE * len(issued)
    changed = tuple(k for k in KEY_FIELDS if set(keys.get(k, [])) - set(issued_keys.get(k, [])))
    missing = any(set(issued_keys.get(k, [])) - set(keys.get(k, [])) for k in KEY_FIELDS)

    if changed and readable:
        return Comparison("altered", changed, recall)
    if readable and recall < DIFFERENT_RECALL:
        return Comparison("altered", (), recall)  # a real code on someone else's notice
    if not changed and not missing and recall >= MATCH_RECALL:
        return Comparison("match", (), recall)
    return Comparison("unclear", changed, recall)


# --- passwords -------------------------------------------------------------------------

PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


# --- QR codes and stamping ----------------------------------------------------------------


def qr_image(url: str, box: int = 10) -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def qr_png(url: str) -> bytes:
    out = BytesIO()
    qr_image(url).save(out, "PNG")
    return out.getvalue()


def stamp_lines(code: str, url: str, issuer_name: str) -> list[str]:
    return [
        "Verify this notice: forward it to Trinetra, or visit",
        url,
        f"Code {code} · issued by {issuer_name}",
    ]


def stamp_image(data: bytes, code: str, url: str, issuer_name: str) -> bytes:
    """The notice with a band underneath carrying the QR code and the verify line. Returns PNG."""
    with Image.open(BytesIO(data)) as im:
        page = im.convert("RGB")
    band = max(240, page.width // 4)
    out = Image.new("RGB", (page.width, page.height + band), "white")
    out.paste(page, (0, 0))
    draw = ImageDraw.Draw(out)
    draw.line([(0, page.height + 2), (page.width, page.height + 2)], fill=(120, 120, 120), width=2)

    qr = qr_image(url).resize((band - 30, band - 30), Image.NEAREST)
    out.paste(qr, (20, page.height + 15))
    size = max(16, page.width // 42)
    font = ImageFont.load_default(size=size)
    x, y = band + 10, page.height + 25
    for line in stamp_lines(code, url, issuer_name):
        draw.text((x, y), line, fill="black", font=font)
        y += int(size * 1.6)
    buf = BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def stamp_pdf(data: bytes, code: str, url: str, issuer_name: str) -> bytes:
    """The PDF with a band added under its first page carrying the QR code and the verify line."""
    band = 110.0
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    first = writer.pages[0]
    x0, y0, x1, _ = (float(v) for v in first.mediabox)
    width = x1 - x0

    overlay_buf = BytesIO()
    c = pdf_canvas.Canvas(overlay_buf, pagesize=(width, band))
    c.setStrokeGray(0.5)
    c.line(0, band - 2, width, band - 2)
    c.drawImage(ImageReader(qr_image(url, box=6)), 15, 10, width=band - 25, height=band - 25)
    c.setFont("Helvetica", 9.5)
    y = band - 30
    for line in stamp_lines(code, url, issuer_name):
        c.drawString(band, y, line)
        y -= 16
    c.showPage()
    c.save()

    overlay = PdfReader(BytesIO(overlay_buf.getvalue())).pages[0]
    first.mediabox.lower_left = (x0, y0 - band)
    first.cropbox.lower_left = (x0, y0 - band)
    first.merge_transformed_page(overlay, Transformation().translate(tx=x0, ty=y0 - band))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def stamp_text(text: str, code: str, url: str) -> str:
    return f"{text.rstrip()}\n\nVerify this notice: forward it to Trinetra, or visit {url}\n(Code {code})"


def public_view(notice: dict[str, Any]) -> dict[str, Any]:
    """What /v/<code> may show: no personal data, no fields, no text."""
    return {
        "code": notice["code"],
        "issuer": notice["issuer_name"],
        "issuer_type": notice["issuer_type"],
        "notice_type": notice["notice_type"],
        "issued_on": notice["issued_at"][:10],
        "status": notice["status"],
    }
