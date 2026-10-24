"""Draw a demo summons from "Pune District Court (demo)" for the issuer-portal demo.

    python tools/make_demo_summons.py [out.png]     # default: $DATA_DIR/demo/summons_pune_demo.png

Clearly marked as a demo. Every name and number in it is invented.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WIDTH, MARGIN = 1240, 90

LINES = [
    ("title", "PUNE DISTRICT COURT (DEMO)"),
    ("title2", "IN THE COURT OF THE CIVIL JUDGE (SENIOR DIVISION), PUNE"),
    ("gap", ""),
    ("heading", "SUMMONS TO DEFENDANT"),
    ("ref", "Civil Suit No. CS/PUNE/2026/0412 · Dated 20-10-2026"),
    ("gap", ""),
    ("body", "To: Mr. Rohan Kulkarni, Kothrud, Pune (Defendant)"),
    ("gap", ""),
    ("body", "Whereas the plaintiff has instituted a suit against you for recovery of Rs. 2,40,000, being the "
             "security deposit of a rented flat, you are hereby summoned to appear in this Court in person or by a "
             "pleader on 14-12-2026 at 11:00 a.m. to answer the claim."),
    ("gap", ""),
    ("body", "You may file your written statement on or before the date of hearing. Take notice that if you fail to "
             "appear, the suit may be heard and decided in your absence."),
    ("gap", ""),
    ("body", "The status of this suit can be checked at the court's filing counter or on ecourts.gov.in."),
    ("gap", ""),
    ("body", "By order of the Court"),
    ("body", "Assistant Superintendent, Pune District Court (demo)"),
    ("gap", ""),
    ("small", "DEMO NOTICE FOR TRINETRA. Not a real court document. All names and numbers are invented."),
]
SIZES = {"title": 40, "title2": 26, "heading": 34, "ref": 26, "body": 27, "small": 20, "gap": 27}


def draw(out: Path) -> None:
    image = Image.new("RGB", (WIDTH, 1500), "white")
    pen = ImageDraw.Draw(image)
    y = 90
    for style, text in LINES:
        size = SIZES[style]
        font = ImageFont.load_default(size=size)
        if style == "gap":
            y += size
            continue
        wrapped = textwrap.wrap(text, width=int((WIDTH - 2 * MARGIN) / (size * 0.52))) or [""]
        for line in wrapped:
            width = pen.textlength(line, font=font)
            x = (WIDTH - width) / 2 if style in ("title", "title2", "heading") else MARGIN
            pen.text((x, y), line, fill="black", font=font)
            y += int(size * 1.45)
    image = image.crop((0, 0, WIDTH, y + 70))
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "PNG")
    print(out)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from app.config import settings

    draw(Path(sys.argv[1]) if len(sys.argv) > 1 else settings.data_dir / "demo" / "summons_pune_demo.png")
