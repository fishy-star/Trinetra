"""Change an amount printed on a stamped demo notice, the way a scammer would, keeping its QR code.

    python tools/demo_alter_amount.py stamped.png altered.png [--old 2,40,000] [--new 9,40,000]

Finds the amount with OCR, paints over it and writes the new one in a similar size.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytesseract
from PIL import Image, ImageDraw, ImageFont


def alter(image: Image.Image, old: str, new: str) -> Image.Image:
    image = image.convert("RGB")
    data = pytesseract.image_to_data(image, lang="eng", output_type=pytesseract.Output.DICT)
    hits = [i for i, word in enumerate(data["text"]) if old in word]
    if not hits:
        raise SystemExit(f"Could not find {old!r} on the notice.")
    pen = ImageDraw.Draw(image)
    for i in hits:
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        pen.rectangle([x - 2, y - 4, x + w + 6, y + h + 6], fill="white")
        pen.text((x, y - 2), data["text"][i].replace(old, new), fill="black", font=ImageFont.load_default(size=int(h * 1.3)))
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stamped", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--old", default="2,40,000")
    parser.add_argument("--new", default="9,40,000")
    args = parser.parse_args()
    alter(Image.open(args.stamped), args.old, args.new).save(args.out, "PNG")
    print(f"{args.out}: {args.old} -> {args.new}")


if __name__ == "__main__":
    main()
