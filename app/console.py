"""Review console at /console: cases, evidence, decisions and packets.

Behind a login: a username from REVIEWERS plus the shared REVIEWER_PASSWORD. Every review,
evidence view and packet download is written to the audit log. Everything shown is escaped,
because notice text and extracted fields come from scammers.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import packet, registry, ui
from app.auth import LoginLimiter, Sessions, client_of
from app.config import settings
from app.evidence import CONTENT_TYPES, EvidenceStore, IntegrityError, write_once
from app.extract.mask import mask_account, mask_email, mask_phone, mask_upi
from app.store import Store, now

log = logging.getLogger(__name__)
router = APIRouter(prefix="/console")

COOKIE = "trinetra_console"
ACTIONS = {"approve_fake": "Approve as fake", "not_fake": "Mark not fake", "needs_info": "Needs more info"}


@dataclass(frozen=True)
class Console:
    store: Store
    evidence: EvidenceStore
    packets_dir: Path
    reviewers: tuple[str, ...]
    password: str | None
    secret: bytes
    # Sends a dispute outcome to the person: (channel, chat_id, text). None when no bot is set up.
    notify: Callable[[str, str, str], None] | None = None
    disputed_csv: Path = Path(__file__).resolve().parent.parent / "data/adversarial/disputed.csv"

    @property
    def enabled(self) -> bool:
        return bool(self.reviewers and self.password)

    @property
    def sessions(self) -> Sessions:
        return Sessions(COOKIE, "/console", self.secret)


@lru_cache
def console_deps() -> Console:
    store = Store(settings.data_dir / "trinetra.db")
    secret = hashlib.sha256(f"console:{settings.user_id_salt}:{settings.reviewer_password}".encode()).digest()
    notify = None
    if settings.telegram_bot_token:
        from app.channels.telegram import TelegramAPI

        api = TelegramAPI(settings.telegram_bot_token)

        def notify(channel: str, chat_id: str, text: str) -> None:
            if channel == "telegram":
                api.send_message(chat_id, text)

    return Console(
        store, EvidenceStore(settings.data_dir / "evidence"), settings.data_dir / "packets",
        settings.reviewers, settings.reviewer_password, secret, notify,
    )


# --- sessions -----------------------------------------------------------------

limiter = LoginLimiter()


def current_user(request: Request, c: Console = Depends(console_deps)) -> str | None:
    user = c.sessions.user(request)
    return user if user in c.reviewers else None


def csrf_token(c: Console, request: Request) -> str:
    return c.sessions.csrf(request)


def require_user(request: Request, c: Console = Depends(console_deps)) -> str:
    user = current_user(request, c)
    if user is None:
        raise HTTPException(303, headers={"Location": "/console"})
    return user


def check_csrf(c: Console, request: Request, token: str) -> None:
    c.sessions.check_csrf(request, token)


# --- pages ------------------------------------------------------------------

def page(title: str, body: str, user: str | None = None) -> HTMLResponse:
    """Reviewer-zone page, marked "Restricted: reviewers only"."""
    nav = [("/console", "Cases"), ("/console/disputes", "Disputes"), ("/console/stats", "Stats"),
           ("/console/audit", "Audit log")] if user else []
    current = {"Audit log": "/console/audit", "Usage": "/console/stats", "Disputes": "/console/disputes"}.get(
        title, "/console")
    return ui.layout(title, body, zone="reviewer", nav=nav, current=current,
                     user=user, logout="/console/logout" if user else None)


def login_page(c: Console, error: str = "") -> HTMLResponse:
    if not c.enabled:
        return page("Log in", '<div class="login-card">' + ui.problem(
            "The console is not set up.", "Set REVIEWERS and REVIEWER_PASSWORD in .env and restart the server.") + "</div>")
    return page(
        "Log in",
        '<div class="card login-card"><p class="eyebrow">Review console</p><h1>Reviewer log in</h1>'
        "<p class=muted>For the Trinetra team reviewing reported cases. Everything you do here is recorded.</p>"
        f'{ui.problem(error) if error else ""}'
        '<form method="post" action="/console/login">'
        '<label class="field">Reviewer name<input name="username" autocomplete="username" required></label>'
        '<label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>'
        '<button class="btn btn-lg" type="submit" style="width:100%">Log in</button></form></div>',
    )


# Most severe first: a case shows the worst verdict any of its reports got.
VERDICT_ORDER = ("likely_fake", "suspicious", "cannot_verify", "could_not_check", "verified_official")


def _worst(reports) -> str:
    seen = {r["verdict"] for r in reports}
    return next((v for v in VERDICT_ORDER if v in seen), "cannot_verify")


def _masked_payee(payee: str) -> str:
    return mask_upi(payee) if "@" in payee else mask_account(payee)


def _age(iso: str) -> str:
    seconds = max(0, int((now() - datetime.fromisoformat(iso)).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit} ago"
    return "just now"


def _claimed(reports) -> str:
    for r in reports:
        sender = json.loads(r["fields_json"]).get("claimed_sender")
        if sender:
            return sender
    return "—"


@router.get("", response_class=HTMLResponse)
def cases_page(request: Request, c: Console = Depends(console_deps)) -> HTMLResponse:
    user = current_user(request, c)
    if user is None:
        return login_page(c)
    cases = c.store.cases()
    today = now().date().isoformat()
    rows, reports_today = [], 0
    for case in cases:
        reports = c.store.case_reports(case["id"])
        reports_today += sum(1 for r in reports if r["reported_at"].startswith(today))
        payees = ", ".join(_masked_payee(p) for p in c.store.case_payees(case["id"])) or "—"
        rows.append(
            f'<tr><td><a href="/console/case/{case["id"]}"><strong>Case {case["id"]}</strong></a></td>'
            f"<td>{ui.badge(_worst(reports))}</td><td>{escape(_claimed(reports))}</td>"
            f'<td><strong>{case["reports"]}</strong></td><td class="mono">{escape(payees)}</td>'
            f'<td>{escape(_age(case["first_report"]))}</td><td>{ui.badge(case["status"])}</td></tr>'
        )
    open_count = sum(1 for k in cases if k["status"] in ("open", "needs_info"))
    approved = sum(1 for k in cases if k["status"] == "approved_fake")
    body = f"""
<div class="page-head"><div><p class="eyebrow">Review console</p><h1>Cases</h1></div>
<p class="muted small">Most-reported first. Reports with the same file hash or the same payee share a case.</p></div>
<div class="kpis">
  <div class="kpi attn"><span class="n">{open_count}</span><span class="l">Open, awaiting a decision</span></div>
  <div class="kpi"><span class="n">{approved}</span><span class="l">Approved as fake</span></div>
  <div class="kpi"><span class="n">{reports_today}</span><span class="l">Reports today (UTC)</span></div>
</div>
<div class="table-wrap"><table class="data"><thead><tr><th>Case</th><th>Verdict</th><th>Claims to be</th><th>Reports</th>
<th>Payee</th><th>Age</th><th>Status</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="7" class="empty">No reports yet.</td></tr>'}</tbody></table></div>
"""
    return page("Cases", body, user)


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), c: Console = Depends(console_deps)) -> Response:
    client = client_of(request)
    if limiter.locked(client):
        return login_page(c, "Too many failed attempts. Try again in 15 minutes.")
    ok = (
        c.enabled
        and username in c.reviewers
        and hmac.compare_digest(password.encode(), (c.password or "").encode())
    )
    if not ok:
        limiter.fail(client)
        c.store.audit(username[:40] or "-", "login_failed")
        return login_page(c, "Wrong reviewer name or password.")
    c.store.audit(username, "login")
    resp = RedirectResponse("/console", status_code=303)
    c.sessions.start(resp, request, username)
    return resp


@router.post("/logout")
def logout(c: Console = Depends(console_deps)) -> Response:
    resp = RedirectResponse("/console", status_code=303)
    c.sessions.end(resp)
    return resp


def _case_or_404(c: Console, case_id: int):
    case = c.store.case(case_id)
    if case is None:
        raise HTTPException(404, "No such case")
    return case


MASKERS = {"upi_ids": mask_upi, "phones": mask_phone, "emails": mask_email}
REVEAL_REASON_MIN = 8


def _fields_table(fields: dict[str, Any], full: bool = False) -> str:
    """Extracted fields. Payees, phones and emails are masked unless `full`, which only the logged reveal route sets."""
    rows = []
    for key, label in packet.FIELD_LABELS:
        value = fields.get(key)
        if not full and key in MASKERS:
            value = [MASKERS[key](v) for v in value or []]
        elif not full and key == "accounts":
            value = [{**a, "number": mask_account(a["number"])} for a in value or []]
        shown = ", ".join(packet._fmt(v) for v in value) if isinstance(value, list) else packet._fmt(value)
        if shown:
            rows.append(f"<tr><th>{label}</th><td>{escape(shown)}</td></tr>")
    return f'<table class="kv">{"".join(rows)}</table>' if rows else '<p class="muted">No fields extracted.</p>'


def _evidence(r, count: int) -> str:
    url = f"/console/report/{r['id']}/file"
    suffix = Path(r["evidence_path"]).suffix
    if suffix in (".png", ".jpg"):
        view = f'<a href="{url}"><img class="evidence-img" src="{url}" alt="Reported file"></a>'
    else:
        view = (f'<div class="empty"><p>The reported file is a {"PDF" if suffix == ".pdf" else "text message"}.</p>'
                f'<a class="btn btn-ghost" href="{url}">Open the reported {"PDF" if suffix == ".pdf" else "text"}</a></div>')
    return (
        f'<section class="card evidence"><div class="card-title"><h2>Evidence</h2>'
        f'<span class="muted small">reported {count} time{"s" if count != 1 else ""} · {escape(r["kind"])} · '
        f'{r["size"]:,} bytes</span></div>{view}'
        f'<p style="margin-top:12px"><span class="muted small">SHA-256</span><br>{ui.copy_code(r["sha256"])}</p></section>'
    )


def _reasoning(r) -> str:
    signals = json.loads(r["signals_json"])
    groups = []
    for check, title in (("source", "Source"), ("media", "Media"), ("ask", "Ask")):
        mine = [s for s in signals if s["check"] == check]
        items = "".join(
            f'<div class="signal"><span class="strength strength-{escape(s["strength"])}">{escape(s["strength"])}</span>'
            f'<span>{escape(s["reason_en"])}</span><span class="code">{escape(s["code"])}</span></div>'
            for s in mine
        ) or '<p class="muted small">Nothing found.</p>'
        groups.append(f'<div class="signal-group"><h3>{title}</h3>{items}</div>')
    return (
        f'<section class="card"><div class="card-title"><h2>Reasoning</h2>{ui.badge(r["verdict"])}</div>'
        f'<p class="small muted">Rule row <strong class="mono">{escape(r["rule_row"])}</strong> · engine '
        f'<span class="mono">{escape(r["engine_version"])}</span></p>'
        f'{"".join(groups)}<h3>Extracted fields</h3>{_fields_table(json.loads(r["fields_json"]))}</section>'
    )


@router.get("/case/{case_id}", response_class=HTMLResponse)
def case_page(case_id: int, request: Request, user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    return _case_view(c, case_id, request, user)


@router.post("/case/{case_id}/reveal", response_class=HTMLResponse)
def reveal(
    case_id: int,
    request: Request,
    reason: str = Form(""),
    csrf: str = Form(""),
    user: str = Depends(require_user),
    c: Console = Depends(console_deps),
) -> HTMLResponse:
    """Zero trust: full payees and fields leave the server only here, with a reason, after an audit entry."""
    check_csrf(c, request, csrf)
    _case_or_404(c, case_id)
    reason = " ".join(reason.split())[:200]
    if len(reason) < REVEAL_REASON_MIN:
        return _case_view(c, case_id, request, user, error="Give a reason of at least a few words to reveal full details.")
    c.store.audit(user, "evidence_revealed", f"case={case_id} reason={reason}")
    return _case_view(c, case_id, request, user, revealed=reason)


def _case_view(c: Console, case_id: int, request: Request, user: str, revealed: str | None = None,
               error: str = "") -> HTMLResponse:
    case = _case_or_404(c, case_id)
    reports = c.store.case_reports(case_id)
    token = csrf_token(c, request)

    files: dict[str, Any] = {}
    for r in reports:
        files.setdefault(r["sha256"], r)
    sections = "".join(
        f'<div class="layout-2">{_evidence(r, sum(1 for x in reports if x["sha256"] == sha))}{_reasoning(r)}</div>'
        for sha, r in files.items()
    )
    timeline = "".join(
        f"<li><strong>{escape(r['reported_at'][:19].replace('T', ' '))}</strong> UTC · {escape(r['kind'])} · "
        f"<span class=mono>{r['sha256'][:16]}…</span></li>"
        for r in reports
    )
    reviews = "".join(
        f"<li><strong>{escape(v['reviewer'])}</strong> chose {escape(ACTIONS[v['action']])} · "
        f"{escape(v['at'][:19].replace('T', ' '))} UTC{' · ' + escape(v['note']) if v['note'] else ''}</li>"
        for v in c.store.case_reviews(case_id)
    )
    styles = {"approve_fake": "btn-danger", "not_fake": "btn-ok", "needs_info": "btn-ghost"}
    buttons = "".join(
        f'<button name="action" value="{a}" class="btn {styles[a]}" type="submit">{label}</button>'
        for a, label in ACTIONS.items()
    )
    packet_links = (
        f'<a class="btn btn-gold btn-sm" href="/console/case/{case_id}/packet.pdf">Download packet (PDF)</a>'
        f'<a class="btn btn-ghost btn-sm" href="/console/case/{case_id}/packet.json">Download packet (JSON)</a>'
        if _latest_packet(c, case_id, ".pdf") else ""
    )
    payees = c.store.case_payees(case_id)
    if revealed:
        full_fields = "".join(
            f'<h3>File <span class="mono">{sha[:12]}…</span></h3>{_fields_table(json.loads(r["fields_json"]), full=True)}'
            for sha, r in files.items()
        )
        disclosure = (
            '<section class="card reveal-open"><div class="card-title"><h2>Full details</h2>'
            '<span class="badge badge-suspicious">Revealed and logged</span></div>'
            f'<p class="small muted">Revealed to {escape(user)} for: “{escape(revealed)}”. This is in the audit log.</p>'
            f'<h3>Payees</h3><p>{"<br>".join(ui.copy_code(p) for p in payees) or "—"}</p>{full_fields}</section>'
        )
    else:
        disclosure = (
            f'<form class="card" method="post" action="/console/case/{case_id}/reveal">'
            f'<input type="hidden" name="csrf" value="{token}"><h2>Full details</h2>'
            '<p class="muted small">Payees, phone numbers and emails are masked. Revealing them is recorded in the '
            "audit log with your name and reason.</p>"
            f'{ui.problem(error) if error else ""}'
            '<label class="field">Reason<input name="reason" required minlength="8" maxlength="200" '
            'placeholder="e.g. Preparing the complaint for the cyber cell"></label>'
            '<button class="btn btn-ghost" type="submit">Reveal full details</button></form>'
        )
    body = f"""
<div class="page-head"><div><p class="eyebrow"><a href="/console">Cases</a> / Case {case_id}</p>
<h1>Case {case_id}</h1></div><div class="btn-row">{ui.badge(case["status"], large=True)}{packet_links}</div></div>
<div class="action-bar"><span class="label">Decision</span>
<form method="post" action="/console/case/{case_id}/review"><input type="hidden" name="csrf" value="{token}">
<input name="note" maxlength="300" placeholder="Note (optional), e.g. matches the SC warrant campaign" aria-label="Note">
{buttons}</form></div>
{sections}
<div class="layout-2">
  <div><section class="card"><h2>Payees</h2><p class="mono">{escape(", ".join(_masked_payee(p) for p in payees) or "—")}</p></section>
  {disclosure}</div>
  <section class="card"><h2>Timeline of reports</h2><ul class="timeline">{timeline}</ul></section>
</div>
<section class="card"><h2>Review history</h2>{f'<ul class="timeline">{reviews}</ul>' if reviews else '<p class="muted">No decisions yet.</p>'}</section>
"""
    return page(f"Case {case_id}", body, user)


@router.post("/case/{case_id}/review")
def review(
    case_id: int,
    request: Request,
    action: str = Form(...),
    note: str = Form(""),
    csrf: str = Form(""),
    user: str = Depends(require_user),
    c: Console = Depends(console_deps),
) -> Response:
    check_csrf(c, request, csrf)
    if action not in ACTIONS:
        raise HTTPException(400, "Unknown action")
    case = _case_or_404(c, case_id)
    note = note.strip()[:300]
    reports = c.store.case_reports(case_id)

    c.store.review(case_id, user, action, note)
    c.store.audit(user, f"review_{action}", f"case={case_id}")
    if action == "approve_fake":
        # Links in an approved fake case that are not official become known fakes too, so a lone forward of
        # the same link is caught at once.
        domains = sorted({
            u.split("/", 1)[0].lower().removeprefix("www.")
            for r in reports for u in json.loads(r["fields_json"]).get("urls", [])
            if not registry.is_official_domain(u.split("/", 1)[0])
        })
        c.store.add_known_fakes(case_id, user, sorted({r["sha256"] for r in reports}), c.store.case_payees(case_id),
                                domains)
        # Evidence is locked only now. Before approval a user's DELETE could still erase it.
        locked = sum(c.evidence.lock(Path(r["evidence_path"])) for r in reports if Path(r["evidence_path"]).exists())
        c.store.audit(user, "evidence_locked", f"case={case_id} files={locked}")
        _make_packet(c, case_id)
    elif case["status"] == "approved_fake":
        # Reversing an approval takes the case's files and payees out of the known fakes again.
        # Evidence stays locked: in AWS an Object Lock retention cannot be shortened.
        c.store.remove_known_fakes(case_id)
        c.store.audit(user, "known_fakes_removed", f"case={case_id}")
    # Cached answers were given before this decision; drop them so the next forward is answered afresh.
    c.store.clear_results()
    return RedirectResponse(f"/console/case/{case_id}", status_code=303)


def _make_packet(c: Console, case_id: int) -> None:
    case = c.store.case(case_id)
    latest = c.store.case_reviews(case_id)[-1]
    reports = [
        {**dict(r), "fields": json.loads(r["fields_json"]), "signals": json.loads(r["signals_json"])}
        for r in c.store.case_reports(case_id)
    ]
    pdf, data = packet.build(
        {"id": case_id, "status": case["status"], "payees": c.store.case_payees(case_id)}, reports, dict(latest)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    for suffix, content in ((".pdf", pdf), (".json", data)):
        write_once(c.packets_dir / f"case-{case_id}-{stamp}{suffix}", content)
    c.store.audit(latest["reviewer"], "packet_generated", f"case={case_id} json_sha256={hashlib.sha256(data).hexdigest()}")


def _latest_packet(c: Console, case_id: int, suffix: str) -> Path | None:
    found = sorted(c.packets_dir.glob(f"case-{case_id}-*{suffix}"))
    return found[-1] if found else None


@router.get("/case/{case_id}/packet.{fmt}")
def download_packet(case_id: int, fmt: str, user: str = Depends(require_user), c: Console = Depends(console_deps)) -> Response:
    if fmt not in ("pdf", "json"):
        raise HTTPException(404)
    path = _latest_packet(c, case_id, f".{fmt}")
    if path is None:
        raise HTTPException(404, "No packet yet: approve the case first.")
    c.store.audit(user, "packet_download", f"case={case_id} file={path.name}")
    media = "application/pdf" if fmt == "pdf" else "application/json"
    return Response(
        path.read_bytes(), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="trinetra-case-{case_id}.{fmt}"', "Cache-Control": "no-store"},
    )


@router.get("/report/{report_id}/file")
def evidence_file(report_id: int, user: str = Depends(require_user), c: Console = Depends(console_deps)) -> Response:
    r = c.store.report(report_id)
    if r is None:
        raise HTTPException(404)
    path = Path(r["evidence_path"])
    try:
        data = c.evidence.get(path)
    except FileNotFoundError:
        raise HTTPException(404, "Evidence file missing") from None
    except IntegrityError:
        c.store.audit(user, "evidence_integrity_failure", f"report={report_id}")
        raise HTTPException(500, "Evidence file does not match its hash") from None
    c.store.audit(user, "evidence_view", f"case={r['case_id']} report={report_id}")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"}
    if path.suffix == ".pdf":
        # A scammer's PDF is downloaded, never rendered inside the console.
        headers["Content-Disposition"] = f'attachment; filename="case-{r["case_id"]}-{r["sha256"][:12]}.pdf"'
    return Response(data, media_type=CONTENT_TYPES[path.suffix], headers=headers)


# --- disputes -------------------------------------------------------------------

DISPUTE_ACTIONS = {"confirmed_genuine": "Confirm genuine", "kept": "Keep verdict"}
DISPUTE_STATUS = {"open": "Open", "confirmed_genuine": "Confirmed genuine", "kept": "Verdict kept"}
DISPUTED_COLUMNS = ["dispute_id", "added_at", "kind", "verdict", "rule_row", "signal_codes", "sha256", "reviewer", "note"]


@router.get("/disputes", response_class=HTMLResponse)
def disputes_page(request: Request, user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    """People who said a verdict was wrong. A reviewer decides; rules never change on their own."""
    token = csrf_token(c, request)
    items = []
    for d in c.store.disputes():
        signals = "".join(f'<li><span class="mono">{escape(code)}</span> · {escape(strength)}: {escape(reason)}</li>'
                          for code, strength, reason in json.loads(d["signals_json"]))
        head = (f'<div class="card-title"><h2>Dispute {d["id"]}</h2><span class="muted small">'
                f'{escape(DISPUTE_STATUS.get(d["status"], d["status"]))} · {escape(d["channel"])} · {escape(d["kind"])} · '
                f'opened {_age(d["created_at"])}</span></div>'
                f'<p>Our verdict: {ui.badge(d["verdict"])} <span class="mono small">{escape(d["rule_row"] or "")}</span>'
                f' · <span class="mono small">{escape(d["sha256"][:16])}</span></p>'
                f'<ul class="small">{signals or "<li>No signals fired.</li>"}</ul>')
        if d["status"] == "open":
            buttons = "".join(f'<button class="btn{"" if k == "kept" else " btn-ghost"}" name="action" value="{k}">{label}'
                              "</button>" for k, label in DISPUTE_ACTIONS.items())
            tail = (f'<form method="post" action="/console/dispute/{d["id"]}/resolve">'
                    f'<input type="hidden" name="csrf" value="{token}">'
                    '<label class="field">Note (required)<input name="note" maxlength="300" required></label>'
                    f'<div class="actions">{buttons}</div></form>')
        else:
            tail = (f'<p class="small">Decided by <strong>{escape(d["reviewer"] or "")}</strong> '
                    f'{_age(d["resolved_at"])}: {escape(d["note"] or "")}</p>')
        items.append(f'<section class="card">{head}{tail}</section>')
    open_count = sum(1 for d in c.store.disputes() if d["status"] == "open")
    body = (
        '<div class="page-head"><div><p class="eyebrow">Review console</p><h1>Disputes</h1></div>'
        f'<p class="muted small">{open_count} open. People who said a message was genuine after we flagged it. '
        "We keep the verdict and signals, not the message. Confirm genuine adds the case to "
        "data/adversarial/disputed.csv for the team; it never changes the rules by itself.</p></div>"
        + ("".join(items) or '<section class="card"><p class="empty">No disputes yet.</p></section>')
    )
    return page("Disputes", body, user)


@router.post("/dispute/{dispute_id}/resolve")
def resolve_dispute(
    dispute_id: int,
    request: Request,
    action: str = Form(...),
    note: str = Form(""),
    csrf: str = Form(""),
    user: str = Depends(require_user),
    c: Console = Depends(console_deps),
) -> Response:
    check_csrf(c, request, csrf)
    note = note.strip()[:300]
    if action not in DISPUTE_ACTIONS:
        raise HTTPException(400, "Unknown action")
    if not note:
        raise HTTPException(400, "A note is required")
    d = c.store.dispute(dispute_id)
    if d is None:
        raise HTTPException(404)
    if not c.store.resolve_dispute(dispute_id, action, user, note):
        return RedirectResponse("/console/disputes", status_code=303)  # already decided
    c.store.audit(user, f"dispute_{action}", f"dispute={dispute_id}")
    if action == "confirmed_genuine":
        _add_disputed_row(c, d, user, note)
    if d["chat_id"] and c.notify:
        from app.intake import DISPUTE_OUTCOME

        try:
            c.notify(d["channel"], d["chat_id"], DISPUTE_OUTCOME[action][d["lang"] if d["lang"] == "hi" else "en"])
        except Exception as exc:  # noqa: BLE001 - the decision stands; the team can follow up
            log.warning("dispute %s: could not message the user (%s)", dispute_id, type(exc).__name__)
            c.store.audit(user, "dispute_notify_failed", f"dispute={dispute_id}")
        else:
            c.store.audit(user, "dispute_user_notified", f"dispute={dispute_id}")
            c.store.forget_dispute_chat(dispute_id)
    return RedirectResponse("/console/disputes", status_code=303)


def _add_disputed_row(c: Console, d, reviewer: str, note: str) -> None:
    """Append to the team's list of confirmed-genuine items. Hash, verdict and signal codes only, never the message."""
    path = c.disputed_csv
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(DISPUTED_COLUMNS)
        w.writerow([d["id"], now().isoformat(timespec="seconds"), d["kind"], d["verdict"], d["rule_row"] or "",
                    " ".join(code for code, _, _ in json.loads(d["signals_json"])), d["sha256"], reviewer, note])


@router.get("/audit", response_class=HTMLResponse)
def audit_page(user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    entries = c.store.audit_entries()
    counts: dict[str, int] = {}
    for a in entries:
        counts[a["action"]] = counts.get(a["action"], 0) + 1
    chips = '<button type="button" class="chip" data-filter="*" aria-pressed="true">All<span class="count">' \
        f'{len(entries)}</span></button>' + "".join(
            f'<button type="button" class="chip" data-filter="{escape(k)}" aria-pressed="false">{escape(k.replace("_", " "))}'
            f'<span class="count">{n}</span></button>'
            for k, n in sorted(counts.items())
        )
    rows = "".join(
        f'<tr data-action="{escape(a["action"])}"><td class="mono nowrap">{escape(a["at"][:19].replace("T", " "))}</td>'
        f'<td class="nowrap">{escape(a["actor"])}</td><td class="nowrap"><strong>{escape(a["action"])}</strong></td>'
        f'<td class="mono">{escape(a["detail"] or "")}</td></tr>'
        for a in entries
    )
    body = (
        '<div class="page-head"><div><p class="eyebrow">Review console</p><h1>Audit log</h1></div>'
        f'<p class="muted small">The latest {len(entries)} entries, newest first. Times are UTC.</p></div>'
        f'<div class="chips" data-filter-for="audit" role="group" aria-label="Filter by action">{chips}</div>'
        '<div class="table-wrap"><table class="data" id="audit"><thead><tr><th>At</th><th>Who</th><th>Action</th>'
        f'<th>Detail</th></tr></thead><tbody>{rows or "<tr><td colspan=4 class=empty>Nothing yet.</td></tr>"}</tbody></table></div>'
    )
    return page("Audit log", body, user)


KIND_LABELS = {"notice": "Notices (photos and PDFs)", "text": "Text messages", "voice": "Voice notes"}
VOTE_LABELS = {"helpful": "Helpful", "not_helpful": "Not helpful"}


def _count_rows(counts: dict, labels: dict | None = None, badge: bool = False) -> str:
    total = sum(counts.values()) or 1
    rows = "".join(
        f'<tr><td>{ui.badge(k) if badge else escape((labels or {}).get(k, k))}</td><td class="num">{n}</td>'
        f'<td class="bar-cell"><span class="meter" style="width:{round(100 * n / total)}%"></span></td></tr>'
        for k, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return f'<table class="data counts"><tbody>{rows or "<tr><td class=empty>None yet.</td></tr>"}</tbody></table>'


@router.get("/stats", response_class=HTMLResponse)
def stats_page(user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    """Usage as counts only: no message content, no hashes, no user identifiers."""
    s = c.store.stats()
    helpful, unhelpful = s["feedback"].get("helpful", 0), s["feedback"].get("not_helpful", 0)
    votes = helpful + unhelpful
    hours = [(now() - timedelta(hours=h)).strftime("%Y-%m-%dT%H") for h in range(23, -1, -1)]
    peak = max([s["per_hour"].get(h, 0) for h in hours] + [1])
    bars = "".join(
        f'<div class="hour" title="{h[11:13]}:00 UTC: {s["per_hour"].get(h, 0)} checks">'
        f'<span class="col" style="height:{round(100 * s["per_hour"].get(h, 0) / peak)}%"></span>'
        f'<span class="lbl">{h[11:13]}</span></div>'
        for h in hours
    )
    fb_rows = "".join(
        f'<tr><td>{ui.badge(v)}</td><td class="num">{s["feedback_by_verdict"].get((v, "helpful"), 0)}</td>'
        f'<td class="num">{s["feedback_by_verdict"].get((v, "not_helpful"), 0)}</td></tr>'
        for v in ("likely_fake", "suspicious", "cannot_verify", "verified_official", "could_not_check")
        if (v, "helpful") in s["feedback_by_verdict"] or (v, "not_helpful") in s["feedback_by_verdict"]
    )
    body = f"""
<div class="page-head"><div><p class="eyebrow">Review console</p><h1>Usage</h1></div>
<p class="muted small">Counts only. No message content, hashes or user identifiers are shown or used here.</p></div>
<div class="kpis">
  <div class="kpi attn"><span class="n">{s["users"]}</span><span class="l">Unique users (consented)</span></div>
  <div class="kpi"><span class="n">{s["checks"]}</span><span class="l">Checks answered</span></div>
  <div class="kpi"><span class="n">{helpful}</span><span class="l">Helpful</span></div>
  <div class="kpi"><span class="n">{unhelpful}</span><span class="l">Not helpful</span></div>
  <div class="kpi"><span class="n">{f"{round(100 * helpful / votes)}%" if votes else "—"}</span><span class="l">Found it helpful</span></div>
  <div class="kpi{" attn" if s["disputes"].get("open") else ""}"><span class="n">{s["disputes"].get("open", 0)}</span><span class="l"><a href="/console/disputes">Open disputes</a></span></div>
  <div class="kpi"><span class="n">{s["disputes"].get("confirmed_genuine", 0) + s["disputes"].get("kept", 0)}</span><span class="l">Resolved disputes</span></div>
</div>
<section class="card"><h2>Checks per hour, last 24 hours (UTC)</h2><div class="hours">{bars}</div></section>
<div class="layout-2">
  <section class="card"><h2>Checks by type</h2>{_count_rows(s["by_kind"], KIND_LABELS)}
    <h3 style="margin-top:16px">By channel</h3>{_count_rows(s["by_channel"], {"telegram": "Telegram", "web": "Web page", "api": "API"})}</section>
  <section class="card"><h2>Verdicts</h2>{_count_rows(s["by_verdict"], badge=True)}</section>
</div>
<section class="card"><h2>Feedback by verdict</h2>
<div class="table-wrap"><table class="data"><thead><tr><th>Verdict</th><th>Helpful</th><th>Not helpful</th></tr></thead>
<tbody>{fb_rows or '<tr><td colspan="3" class="empty">No feedback yet.</td></tr>'}</tbody></table></div></section>
"""
    return page("Usage", body, user)
