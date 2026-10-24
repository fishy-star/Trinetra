"""Evidence packet for an approved case: a PDF for people and a JSON file for systems.

The packet supports a certificate under Section 63(4) of the Bharatiya Sakshya Adhiniyam.
It is not itself a certificate, and it never claims to be court-admissible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

import pytesseract
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import ENGINE_VERSION, settings

FOOTER = (
    "Prepared to support a certificate under Section 63(4), Bharatiya Sakshya Adhiniyam. "
    "This packet is not itself a certificate."
)
FIELD_LABELS = (
    ("claimed_sender", "Claimed sender"),
    ("agencies", "Bodies named"),
    ("case_numbers", "Case numbers"),
    ("amounts", "Amounts"),
    ("upi_ids", "UPI IDs"),
    ("accounts", "Bank accounts"),
    ("urls", "Web addresses"),
    ("emails", "Email addresses"),
    ("phones", "Phone numbers"),
    ("demands_payment", "Demands payment"),
    ("threatens_user", "Threatens arrest"),
    ("asks_secrecy", "Asks for secrecy"),
    ("deadline_hours", "Deadline (hours)"),
)


def model_versions() -> dict[str, str]:
    try:
        tesseract = f"tesseract {pytesseract.get_tesseract_version()} (eng+hin)"
    except (pytesseract.TesseractNotFoundError, OSError):
        tesseract = "tesseract not found"
    return {
        "engine": ENGINE_VERSION,
        "ocr": tesseract,
        "llm_extractor": settings.llm_mode,
        "speech_to_text": settings.stt_mode,
        "audio_model": settings.audio_model,
    }


def build_json(case: dict[str, Any], reports: list[dict[str, Any]], review: dict[str, Any]) -> dict[str, Any]:
    """`reports` rows carry their parsed `fields` and `signals`. Reporters appear only as counts, never by ID."""
    return {
        "case_id": case["id"],
        "status": case["status"],
        "generated_at": datetime.now(UTC).isoformat(),
        "reviewer": {"name": review["reviewer"], "decision": review["action"], "at": review["at"], "note": review["note"]},
        "versions": model_versions(),
        "payees": case["payees"],
        "timeline": [
            {"reported_at": r["reported_at"], "sha256": r["sha256"], "kind": r["kind"]} for r in reports
        ],
        "files": [
            {
                "sha256": r["sha256"],
                "size_bytes": r["size"],
                "kind": r["kind"],
                "first_reported_at": r["reported_at"],
                "verdict": r["verdict"],
                "rule_row": r["rule_row"],
                "engine_version": r["engine_version"],
                "fields": r["fields"],
                "signals": [{"code": s["code"], "strength": s["strength"], "reason": s["reason_en"]} for s in r["signals"]],
            }
            for r in _unique_files(reports)
        ],
        "footer": FOOTER,
    }


def _unique_files(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for r in reports:
        seen.setdefault(r["sha256"], r)
    return list(seen.values())


def _latin(value: Any) -> str:
    """The PDF fonts cannot shape Devanagari, so Hindi text is pointed to the JSON, which has it in full."""
    text = ", ".join(_fmt(v) for v in value) if isinstance(value, list) else _fmt(value)
    if any(ord(c) > 0x24F for c in text):
        return "(Hindi text: see the JSON packet)"
    return escape(text) if text else "—"


def _fmt(value: Any) -> str:
    if isinstance(value, dict):  # bank account
        return value["number"] + (f" (IFSC {value['ifsc']})" if value.get("ifsc") else "")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value)


def build_pdf(packet: dict[str, Any], json_sha256: str) -> bytes:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10)
    mono = ParagraphStyle("mono", parent=small, fontName="Courier", wordWrap="CJK")
    h1, h2 = styles["Heading1"], styles["Heading2"]

    def table(rows: list[list[Any]], widths: list[float]) -> Table:
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ]
            )
        )
        return t

    def p(text: str, style=small) -> Paragraph:
        return Paragraph(text, style)

    r = packet["reviewer"]
    story: list[Any] = [
        Paragraph(f"Trinetra evidence packet: case {packet['case_id']}", h1),
        table(
            [
                [p("Decision"), p("Approved as fake by a named reviewer")],
                [p("Reviewer"), p(escape(r["name"]))],
                [p("Reviewed at (UTC)"), p(escape(r["at"]))],
                [p("Reviewer note"), p(escape(r["note"] or "—"))],
                [p("Reports"), p(str(len(packet["timeline"])))],
                [p("Distinct files"), p(str(len(packet["files"])))],
                [p("Payees"), p(_latin(packet["payees"]))],
                [p("Generated at (UTC)"), p(escape(packet["generated_at"]))],
                [p("Versions"), p(escape("; ".join(f"{k}: {v}" for k, v in packet["versions"].items())))],
                [p("SHA-256 of the JSON packet"), p(json_sha256, mono)],
            ],
            [45 * mm, 125 * mm],
        ),
        Paragraph("Timeline of reports", h2),
        table(
            [[p("<b>Reported at (UTC)</b>"), p("<b>Kind</b>"), p("<b>File SHA-256</b>")]]
            + [[p(escape(t["reported_at"][:19].replace("T", " "))), p(t["kind"]), p(t["sha256"], mono)] for t in packet["timeline"]],
            [45 * mm, 20 * mm, 105 * mm],
        ),
    ]
    for i, f in enumerate(packet["files"], 1):
        story += [
            Paragraph(f"File {i}", h2),
            table(
                [
                    [p("SHA-256"), p(f["sha256"], mono)],
                    [p("Size"), p(f"{f['size_bytes']:,} bytes")],
                    [p("Kind"), p(f["kind"])],
                    [p("Verdict"), p(f"{f['verdict']} (rule {f['rule_row']}, engine {escape(f['engine_version'])})")],
                ]
                + [[p(label), p(_latin(f["fields"].get(key)))] for key, label in FIELD_LABELS]
                + [[p("Signals"), p("<br/>".join(escape(f"{s['code']} ({s['strength']}): {s['reason']}") for s in f["signals"]) or "—")]],
                [45 * mm, 125 * mm],
            ),
            Spacer(1, 4 * mm),
        ]

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        first, second = FOOTER.split(". ", 1)
        canvas.drawString(20 * mm, 14 * mm, first + ".")
        canvas.drawString(20 * mm, 10.5 * mm, second)
        canvas.drawRightString(190 * mm, 6 * mm, f"Case {packet['case_id']} · page {doc.page}")
        canvas.restoreState()

    out = BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=22 * mm,
        title=f"Trinetra evidence packet, case {packet['case_id']}", author="Trinetra",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def build(case: dict[str, Any], reports: list[dict[str, Any]], review: dict[str, Any]) -> tuple[bytes, bytes]:
    """Returns (pdf, json) bytes. The PDF records the JSON's SHA-256, tying the two together."""
    packet = build_json(case, reports, review)
    data = json.dumps(packet, ensure_ascii=False, indent=2).encode("utf-8")
    return build_pdf(packet, hashlib.sha256(data).hexdigest()), data
