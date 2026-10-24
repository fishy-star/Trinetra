"""Text from notices: Tesseract OCR (eng+hin) for images, pypdf text for PDFs."""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from io import BytesIO

import pytesseract
import zxingcpp
from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

log = logging.getLogger(__name__)

LANGS = "eng+hin"
MAX_PDF_PAGES = 10
MAX_PIXELS = 40_000_000  # refuse decompression bombs
MIN_OCR_WIDTH = 1400  # small phone screenshots are upscaled to this width before OCR

# Homebrew installs outside the default PATH of some shells and IDEs.
if not shutil.which("tesseract") and shutil.which("tesseract", path="/opt/homebrew/bin"):
    pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"


class UnreadableFile(Exception):
    """The file is the type it claims, but no text could be got from it."""


def sniff(data: bytes) -> str | None:
    """File type from magic bytes, never from the name or the declared type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def clean_ocr(text: str) -> str:
    """Undo common OCR slips in the parts the checks rely on."""
    # "cbi.suraksha. jama@ybl" -> "cbi.suraksha.jama@ybl"; "name @ ybl" -> "name@ybl"
    text = re.sub(r"(?<=\w)\.\s+(?=[\w.\-]+@\w)", ".", text)
    text = re.sub(r"(?<=\w)\s*@\s*(?=\w)", "@", text)
    # "₹" is often read as "%" in front of Indian digit grouping: %4,85,000
    text = re.sub(r"%(?=\d{1,2}(?:,\d{2})*,\d{3}\b)", "₹", text)
    return text


def _upright(image: Image.Image) -> Image.Image:
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return image  # too little text to tell the orientation
    angle = int(osd.get("rotate", 0))
    return image.rotate(-angle, expand=True) if angle else image


def _row_contrast(image: Image.Image, angle: float) -> float:
    """How sharply text rows line up at `angle`: variance of the per-row ink density."""
    rotated = image.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
    rows = list(rotated.resize((1, rotated.height), Image.BOX).getdata())  # mean of each row
    mean = sum(rows) / len(rows)
    return sum((r - mean) ** 2 for r in rows) / len(rows)


def skew_angle(image: Image.Image, limit: float = 15.0) -> float:
    """Angle that makes text lines horizontal, by projection profile: coarse 1° steps, then 0.2°."""
    small = image.copy()
    small.thumbnail((600, 600))
    # Ink is what is much darker than its surroundings, so a table or shadow behind the paper is not ink.
    # Text ends up white on black, so the padding a rotation adds carries no ink either.
    local = small.filter(ImageFilter.BoxBlur(8))
    ink = ImageChops.subtract(local, small).point(lambda p: 255 if p > 25 else 0)
    best = max(range(-int(limit), int(limit) + 1), key=lambda a: _row_contrast(ink, a))
    fine = [best + step / 5 for step in range(-5, 6)]
    return max(fine, key=lambda a: _row_contrast(ink, a))


def _straighten(image: Image.Image) -> Image.Image:
    angle = skew_angle(image)
    return image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255) if abs(angle) >= 0.5 else image


def image_text(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise UnreadableFile("image too large")
            image = ImageOps.exif_transpose(im).convert("L")
    except (UnidentifiedImageError, OSError) as e:
        raise UnreadableFile("not a readable image") from e
    if image.width < MIN_OCR_WIDTH:
        scale = MIN_OCR_WIDTH / image.width
        image = image.resize((MIN_OCR_WIDTH, round(image.height * scale)), Image.LANCZOS)
    image = _straighten(_upright(image))
    return clean_ocr(pytesseract.image_to_string(image, lang=LANGS))


def pdf_text(data: bytes) -> str:
    """Text layer of the first MAX_PDF_PAGES pages; scanned pages fall back to OCR of their images."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnreadableFile("encrypted PDF")
        pages = reader.pages[:MAX_PDF_PAGES]
        chunks = []
        for page in pages:
            text = page.extract_text() or ""
            if not text.strip():
                text = "\n".join(image_text(img.data) for img in page.images)
            chunks.append(text)
    except (PdfReadError, ValueError, KeyError) as e:
        raise UnreadableFile("not a readable PDF") from e
    # NFKC folds the ligatures PDF fonts use ("ﬁ" -> "fi").
    return unicodedata.normalize("NFKC", "\n".join(chunks))


def qr_texts(data: bytes) -> list[str]:
    """Text of every QR code in an image, or in the images embedded in a PDF's first pages."""
    images: list[Image.Image] = []
    try:
        if sniff(data) == "pdf":
            for page in PdfReader(BytesIO(data)).pages[:MAX_PDF_PAGES]:
                images += [img.image for img in page.images]
        elif sniff(data) in ("png", "jpeg"):
            with Image.open(BytesIO(data)) as im:
                if im.width * im.height <= MAX_PIXELS:
                    images.append(ImageOps.exif_transpose(im).convert("RGB"))
    except (PdfReadError, ValueError, KeyError, OSError, UnidentifiedImageError):
        return []
    found: list[str] = []
    for image in images:
        try:
            found += [r.text for r in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)]
        except (ValueError, RuntimeError):
            continue
    return found


def file_text(data: bytes) -> str:
    kind = sniff(data)
    if kind == "pdf":
        return pdf_text(data)
    if kind in ("png", "jpeg"):
        return image_text(data)
    raise UnreadableFile("unsupported file type")
