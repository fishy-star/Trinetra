"""Builds one HTML page per notice. Run: python3 -I build_html.py <out_dir> <font_dir>"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from notices import NOTICES  # noqa: E402

OUT = Path(sys.argv[1])
FONTS = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

# A generic seal: no real emblem, no real institution's mark.
SEAL = """<svg width="120" height="120" viewBox="0 0 120 120"><circle cx="60" cy="60" r="56" fill="none" stroke="#2b3a8c" stroke-width="3"/>
<circle cx="60" cy="60" r="44" fill="none" stroke="#2b3a8c" stroke-width="1.5"/><path d="M60 30 L68 52 L91 52 L72 66 L79 88 L60 75 L41 88 L48 66 L29 52 L52 52 Z" fill="none" stroke="#2b3a8c" stroke-width="1.5"/></svg>"""


def page(n: dict) -> str:
    e = html.escape
    hi = n["lang"] in ("hi", "bi")
    header = "".join(f'<div class="h{i}">{e(h)}</div>' for i, h in enumerate(n["header"]))
    body = "".join(f"<p>{e(p)}</p>" for p in n["body"])
    box = ("<div class='box'>" + "".join(f"<div>{e(b)}</div>" for b in n["box"]) + "</div>") if n["box"] else ""
    warn = f"<p class='warn'>{e(n['warning'])}</p>" if n["warning"] else ""
    contact = f"<p class='contact'>{e(n['contact'])}</p>" if n["contact"] else ""
    sign = "<br>".join(e(s) for s in n["sign"])
    seal = SEAL if n["label"] == "forged" or n["id"].startswith(("G1", "G3")) else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'NotoSerifDeva'; src: url('{(FONTS / 'noto-serif-devanagari-devanagari-400-normal.woff2').as_uri()}'); font-weight: 400; }}
@font-face {{ font-family: 'NotoSerifDeva'; src: url('{(FONTS / 'noto-serif-devanagari-devanagari-700-normal.woff2').as_uri()}'); font-weight: 700; }}
body {{ margin:0; background:#fff; }}
.sheet {{ width:794px; height:1123px; box-sizing:border-box; padding:64px 72px 48px; position:relative;
  font-family: 'DejaVu Serif', 'NotoSerifDeva', serif; color:#111; font-size:{'17' if hi else '16'}px; line-height:1.6; }}
.head {{ text-align:center; border-bottom:3px double #222; padding-bottom:14px; margin-bottom:18px; }}
.h0 {{ font-size:22px; font-weight:700; letter-spacing:0.5px; }}
.h1, .h2 {{ font-size:15px; }}
.title {{ text-align:center; font-size:20px; font-weight:700; text-decoration:underline; margin:14px 0 6px; }}
.ref {{ text-align:center; font-size:14px; margin-bottom:18px; }}
.to {{ font-weight:700; margin-bottom:10px; }}
p {{ margin:0 0 12px; text-align:justify; }}
.box {{ border:1.5px solid #333; padding:10px 16px; margin:14px 0; font-weight:700; font-family:'DejaVu Sans Mono', 'NotoSerifDeva', monospace; font-size:15px; }}
.warn {{ font-weight:700; }}
.contact {{ font-size:15px; }}
.sign {{ position:absolute; right:72px; bottom:120px; text-align:center; font-size:15px; }}
.seal {{ position:absolute; left:84px; bottom:110px; opacity:0.85; }}
.foot {{ position:absolute; left:0; right:0; bottom:18px; text-align:center; font-family:'DejaVu Sans', sans-serif; font-size:11px; color:#888; }}
</style></head><body><div class="sheet">
<div class="head">{header}</div>
<div class="title">{e(n['title'])}</div>
<div class="ref">{e(n['ref'])}</div>
<div class="to">{e(n['to'])}</div>
{body}{box}{warn}{contact}
<div class="seal">{seal}</div>
<div class="sign">{sign}</div>
<div class="foot">SYNTHETIC TEST SAMPLE · Trinetra team · not a real document · {n['id']}</div>
</div></body></html>"""


for n in NOTICES:
    (OUT / f"{n['id']}.html").write_text(page(n), encoding="utf-8")
print(f"wrote {len(NOTICES)} pages to {OUT}")
