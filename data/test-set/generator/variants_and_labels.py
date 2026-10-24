"""Makes robustness variants of each notice and writes the label sheets.
Usage: python3 -I variants_and_labels.py <test_set_dir>"""
import csv
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

sys.path.insert(0, str(Path(__file__).parent))
from notices import NOTICES, TEXTS, VOICES  # noqa: E402

ROOT = Path(sys.argv[1])
PNG, VAR = ROOT / "png", ROOT / "variants"
VAR.mkdir(exist_ok=True)
random.seed(7)


def whatsapp(img):
    """WhatsApp-style recompression: downscale and low JPEG quality."""
    w = 900
    return img.convert("RGB").resize((w, int(img.height * w / img.width)), Image.LANCZOS)


def phone_photo(img):
    """A photo of a printed notice: tilted, on a table, uneven light, slight blur."""
    img = img.convert("RGB")
    bg = Image.new("RGB", (int(img.width * 1.18), int(img.height * 1.12)), (122, 98, 74))
    rot = img.rotate(random.uniform(-4, 4), resample=Image.BICUBIC, expand=True, fillcolor=(122, 98, 74))
    bg.paste(rot, ((bg.width - rot.width) // 2, (bg.height - rot.height) // 2))
    shade = Image.new("L", bg.size, 0)
    d = ImageDraw.Draw(shade)
    for i in range(bg.width):
        d.line([(i, 0), (i, bg.height)], fill=int(70 * i / bg.width))
    bg = Image.composite(Image.new("RGB", bg.size, (0, 0, 0)), bg, shade.point(lambda v: v // 2))
    bg = ImageEnhance.Contrast(bg).enhance(0.9).filter(ImageFilter.GaussianBlur(0.8))
    return bg.resize((1080, int(bg.height * 1080 / bg.width)), Image.LANCZOS)


def cropped(img):
    """Only the top part forwarded, as when someone screenshots part of a page."""
    return img.convert("RGB").crop((0, 0, img.width, int(img.height * 0.62)))


rows = []
for n in NOTICES:
    src = Image.open(PNG / f"{n['id']}.png")
    whatsapp(src).save(VAR / f"{n['id']}__whatsapp.jpg", quality=35)
    phone_photo(src).save(VAR / f"{n['id']}__photo.jpg", quality=55)
    cropped(src).save(VAR / f"{n['id']}__crop.jpg", quality=70)
    for fmt, path in [("png", f"png/{n['id']}.png"), ("pdf", f"pdf/{n['id']}.pdf"),
                      ("whatsapp_jpg", f"variants/{n['id']}__whatsapp.jpg"),
                      ("photo_jpg", f"variants/{n['id']}__photo.jpg"),
                      ("crop_jpg", f"variants/{n['id']}__crop.jpg")]:
        # A crop may cut off the account box; for forged notices it may drop to suspicious.
        exp = n["expected"]
        note = n["signals"]
        if fmt == "crop_jpg" and n["label"] == "forged":
            exp = "likely_fake_or_suspicious"
            note = "lower part cut off: payee or contact may be missing; must never be verified_official"
        rows.append({"file": path, "kind": "notice", "format": fmt, "lang": n["lang"], "truth": n["label"],
                     "expected_verdict": exp, "must_never_be": "verified_official" if n["label"] == "forged" else "likely_fake",
                     "split": "dev" if fmt in ("png", "pdf") else "test", "what_it_tests": note})

for tid, truth, lang, exp, text in TEXTS:
    rows.append({"file": f"texts.csv#{tid}", "kind": "text", "format": "text", "lang": lang, "truth": truth,
                 "expected_verdict": exp, "must_never_be": "verified_official" if truth == "forged" else "likely_fake",
                 "split": "dev", "what_it_tests": "forwarded text message"})

for vid, truth, lang, exp, how, script in VOICES:
    rows.append({"file": f"voice/{vid}.ogg (record it)", "kind": "voice", "format": "audio", "lang": lang,
                 "truth": truth, "expected_verdict": exp,
                 "must_never_be": "verified_official" if truth == "cloned" else "likely_fake",
                 "split": "test", "what_it_tests": "a voice note can never be verified; scam ask without proof of source gives suspicious"})

with open(ROOT / "labels.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

with open(ROOT / "texts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "truth", "lang", "expected_verdict", "text"])
    w.writerows(TEXTS)

lines = ["# Voice-note scripts", "",
         "Consent first: every speaker signs a one-line note saying they agree to their own voice being recorded and cloned for Trinetra testing only.",
         "Clone only team members' own voices. Never clone anyone outside the team.", "",
         "Record on a phone as a WhatsApp voice note (.ogg/opus), in a quiet room, then also once with background noise.", ""]
for vid, truth, lang, exp, how, script in VOICES:
    lines += [f"## {vid}  ({truth}, {lang}, expected: {exp})", "", how, "", f"> {script}", ""]
(ROOT / "voice_scripts.md").write_text("\n".join(lines), encoding="utf-8")
print(len(rows), "label rows")
