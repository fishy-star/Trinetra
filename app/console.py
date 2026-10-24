"""Review console at /console: cases, evidence, decisions and packets.

Behind a login: a username from REVIEWERS plus the shared REVIEWER_PASSWORD. Every review,
evidence view and packet download is written to the audit log. Everything shown is escaped,
because notice text and extracted fields come from scammers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import packet, registry
from app.config import settings
from app.evidence import CONTENT_TYPES, EvidenceStore, IntegrityError, write_once
from app.store import Store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/console")

COOKIE = "trinetra_console"
SESSION_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60
ACTIONS = {"approve_fake": "Approve as fake", "not_fake": "Mark not fake", "needs_info": "Needs more info"}
STATUS_LABELS = {"open": "Open", "approved_fake": "Approved as fake", "not_fake": "Not fake", "needs_info": "Needs more info"}


@dataclass(frozen=True)
class Console:
    store: Store
    evidence: EvidenceStore
    packets_dir: Path
    reviewers: tuple[str, ...]
    password: str | None
    secret: bytes

    @property
    def enabled(self) -> bool:
        return bool(self.reviewers and self.password)


@lru_cache
def console_deps() -> Console:
    store = Store(settings.data_dir / "trinetra.db")
    secret = hashlib.sha256(f"console:{settings.user_id_salt}:{settings.reviewer_password}".encode()).digest()
    return Console(
        store, EvidenceStore(settings.data_dir / "evidence"), settings.data_dir / "packets",
        settings.reviewers, settings.reviewer_password, secret,
    )


# --- sessions -----------------------------------------------------------------

_failed: dict[str, list[float]] = defaultdict(list)


def _sign(c: Console, value: str) -> str:
    return hmac.new(c.secret, value.encode(), hashlib.sha256).hexdigest()


def _session_cookie(c: Console, user: str) -> str:
    body = f"{user}|{int(time.time()) + SESSION_SECONDS}"
    return f"{body}|{_sign(c, body)}"


def current_user(request: Request, c: Console = Depends(console_deps)) -> str | None:
    raw = request.cookies.get(COOKIE, "")
    try:
        user, expiry, sig = raw.rsplit("|", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(c, f"{user}|{expiry}")) or int(expiry) < time.time():
        return None
    return user if user in c.reviewers else None


def csrf_token(c: Console, request: Request) -> str:
    return _sign(c, "csrf|" + request.cookies.get(COOKIE, ""))[:32]


def require_user(request: Request, c: Console = Depends(console_deps)) -> str:
    user = current_user(request, c)
    if user is None:
        raise HTTPException(303, headers={"Location": "/console"})
    return user


def check_csrf(c: Console, request: Request, token: str) -> None:
    if not hmac.compare_digest(token, csrf_token(c, request)):
        raise HTTPException(403, "Invalid form token. Reload the page and try again.")


# --- pages ------------------------------------------------------------------

STYLE = """
:root { --bg:#f7f7f4; --fg:#1d1d1b; --muted:#5f5f5a; --line:#d9d8d0; --card:#fff; --accent:#1f4e79;
        --bad:#9b1c1c; --ok:#1e6b3a; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecebe6; --muted:#a3a29b; --line:#3a3a36;
        --card:#1f1f1d; --accent:#8ab4e0; --bad:#f09595; --ok:#8fd19e; --warn:#e8c070; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
header { display:flex; gap:16px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
header a { color:var(--accent); } header .who { margin-left:auto; color:var(--muted); }
main { max-width:1100px; margin:0 auto; padding:16px; }
table { width:100%; border-collapse:collapse; background:var(--card); }
th, td { text-align:left; vertical-align:top; padding:6px 8px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; }
th { font-weight:600; color:var(--muted); font-size:.85rem; }
.wrap { overflow-x:auto; }
code, .mono { font-family:ui-monospace, Menlo, monospace; font-size:.85rem; overflow-wrap:anywhere; }
.status-open, .status-needs_info { color:var(--warn); } .status-approved_fake { color:var(--bad); } .status-not_fake { color:var(--ok); }
.card { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:12px; margin:12px 0; }
.actions { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end; }
button { font:inherit; font-weight:600; padding:8px 14px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
button.primary { background:var(--bad); border-color:var(--bad); color:#fff; }
input, textarea { font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); }
img.evidence { max-width:100%; max-height:520px; border:1px solid var(--line); }
pre { white-space:pre-wrap; background:var(--card); border:1px solid var(--line); padding:8px; }
.muted { color:var(--muted); } .error { color:var(--bad); }
"""


def page(title: str, body: str, user: str | None = None) -> HTMLResponse:
    nav = (
        f'<a href="/console">Cases</a><a href="/console/audit">Audit log</a>'
        f'<span class="who">{escape(user)} · <form method="post" action="/console/logout" style="display:inline">'
        f'<button type="submit">Log out</button></form></span>'
        if user else ""
    )
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · Trinetra console</title><style>{STYLE}</style></head>"
        f"<body><header><strong>Trinetra review console</strong>{nav}</header><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"},
    )


def login_page(c: Console, error: str = "") -> HTMLResponse:
    if not c.enabled:
        return page("Log in", '<p class="error">The console is not set up. Set REVIEWERS and REVIEWER_PASSWORD in .env '
                    "and restart the server.</p>")
    return page(
        "Log in",
        f'<h1>Log in</h1>{f"<p class=error>{escape(error)}</p>" if error else ""}'
        '<form method="post" action="/console/login" class="card" style="max-width:360px">'
        '<p><label>Reviewer name<br><input name="username" autocomplete="username" required></label></p>'
        '<p><label>Password<br><input name="password" type="password" autocomplete="current-password" required></label></p>'
        '<button class="primary" type="submit">Log in</button></form>',
    )


@router.get("", response_class=HTMLResponse)
def cases_page(request: Request, c: Console = Depends(console_deps)) -> HTMLResponse:
    user = current_user(request, c)
    if user is None:
        return login_page(c)
    rows = "".join(
        f'<tr><td><a href="/console/case/{r["id"]}">Case {r["id"]}</a></td><td>{r["reports"]}</td>'
        f'<td class="status-{r["status"]}">{STATUS_LABELS[r["status"]]}</td>'
        f'<td class="mono">{escape(r["payees"] or "—")}</td><td>{escape(r["first_report"][:16])}</td>'
        f'<td>{escape(r["last_report"][:16])}</td></tr>'
        for r in c.store.cases()
    )
    body = (
        "<h1>Cases</h1><p class=muted>Most-reported first. Reports with the same file hash or the same payee share a case.</p>"
        '<div class="wrap"><table><tr><th>Case</th><th>Reports</th><th>Status</th><th>Payees</th>'
        f"<th>First report (UTC)</th><th>Last report (UTC)</th></tr>{rows or '<tr><td colspan=6>No reports yet.</td></tr>'}"
        "</table></div>"
    )
    return page("Cases", body, user)


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), c: Console = Depends(console_deps)) -> Response:
    client = request.client.host if request.client else "unknown"
    recent = [t for t in _failed[client] if t > time.time() - LOCKOUT_SECONDS]
    _failed[client] = recent
    if len(recent) >= MAX_FAILED_LOGINS:
        return login_page(c, "Too many failed attempts. Try again in 15 minutes.")
    ok = (
        c.enabled
        and username in c.reviewers
        and hmac.compare_digest(password.encode(), (c.password or "").encode())
    )
    if not ok:
        _failed[client].append(time.time())
        c.store.audit(username[:40] or "-", "login_failed")
        return login_page(c, "Wrong reviewer name or password.")
    c.store.audit(username, "login")
    resp = RedirectResponse("/console", status_code=303)
    resp.set_cookie(COOKIE, _session_cookie(c, username), max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https", path="/console")
    return resp


@router.post("/logout")
def logout() -> Response:
    resp = RedirectResponse("/console", status_code=303)
    resp.delete_cookie(COOKIE, path="/console")
    return resp


def _case_or_404(c: Console, case_id: int):
    case = c.store.case(case_id)
    if case is None:
        raise HTTPException(404, "No such case")
    return case


def _fields_table(fields: dict[str, Any]) -> str:
    rows = []
    for key, label in packet.FIELD_LABELS:
        value = fields.get(key)
        if isinstance(value, list):
            shown = ", ".join(packet._fmt(v) for v in value)
        else:
            shown = packet._fmt(value)
        rows.append(f"<tr><th>{label}</th><td class=mono>{escape(shown or '—')}</td></tr>")
    return f"<table>{''.join(rows)}</table>"


def _evidence_preview(r) -> str:
    url = f"/console/report/{r['id']}/file"
    suffix = Path(r["evidence_path"]).suffix
    if suffix in (".png", ".jpg"):
        return f'<a href="{url}"><img class="evidence" src="{url}" alt="Reported file"></a>'
    return f'<a href="{url}">Open the reported {"PDF" if suffix == ".pdf" else "text"}</a>'


@router.get("/case/{case_id}", response_class=HTMLResponse)
def case_page(case_id: int, request: Request, user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    case = _case_or_404(c, case_id)
    reports = c.store.case_reports(case_id)
    token = csrf_token(c, request)

    files: dict[str, Any] = {}
    for r in reports:
        files.setdefault(r["sha256"], r)
    items = []
    for sha, r in files.items():
        signals = json.loads(r["signals_json"])
        sig_rows = "".join(
            f"<tr><td class=mono>{escape(s['code'])}</td><td>{escape(s['strength'])}</td><td>{escape(s['reason_en'])}</td></tr>"
            for s in signals
        ) or "<tr><td colspan=3>No signals.</td></tr>"
        count = sum(1 for x in reports if x["sha256"] == sha)
        items.append(
            f'<div class="card"><h3>File reported {count} time{"s" if count != 1 else ""}</h3>'
            f'<p><span class=muted>SHA-256</span> <code>{sha}</code><br>'
            f'<span class=muted>Size</span> {r["size"]:,} bytes · <span class=muted>Kind</span> {escape(r["kind"])} · '
            f'<span class=muted>Verdict</span> {escape(r["verdict"])} · <span class=muted>Rule row</span> {escape(r["rule_row"])} · '
            f'<span class=muted>Engine</span> {escape(r["engine_version"])}</p>'
            f"{_evidence_preview(r)}<h4>Extracted fields</h4>{_fields_table(json.loads(r['fields_json']))}"
            f'<h4>Signals</h4><div class="wrap"><table><tr><th>Code</th><th>Strength</th><th>Reason</th></tr>{sig_rows}</table></div></div>'
        )
    timeline = "".join(
        f"<tr><td>{escape(r['reported_at'][:19])}</td><td>{escape(r['kind'])}</td><td class=mono>{r['sha256'][:16]}…</td></tr>"
        for r in reports
    )
    reviews = "".join(
        f"<li>{escape(v['at'][:19])}: <strong>{escape(v['reviewer'])}</strong> chose {escape(ACTIONS[v['action']])}"
        f"{' (' + escape(v['note']) + ')' if v['note'] else ''}</li>"
        for v in c.store.case_reviews(case_id)
    )
    buttons = "".join(
        f'<button name="action" value="{a}" class="{"primary" if a == "approve_fake" else ""}" type="submit">{label}</button>'
        for a, label in ACTIONS.items()
    )
    has_packet = _latest_packet(c, case_id, ".pdf") is not None
    packet_links = (
        f'<p><a href="/console/case/{case_id}/packet.pdf">Download packet (PDF)</a> · '
        f'<a href="/console/case/{case_id}/packet.json">Download packet (JSON)</a></p>' if has_packet else ""
    )
    body = (
        f'<h1>Case {case_id} <span class="status-{case["status"]}">· {STATUS_LABELS[case["status"]]}</span></h1>'
        f"{packet_links}"
        f'<form method="post" action="/console/case/{case_id}/review" class="card"><input type="hidden" name="csrf" value="{token}">'
        '<div class="actions"><label>Note (optional)<br><input name="note" maxlength="300" size="40"></label>'
        f"{buttons}</div></form>"
        f'<p><span class=muted>Payees</span> <span class=mono>{escape(", ".join(c.store.case_payees(case_id)) or "—")}</span></p>'
        f"<h2>Timeline</h2><div class=wrap><table><tr><th>Reported at (UTC)</th><th>Kind</th><th>File</th></tr>{timeline}</table></div>"
        f"<h2>Files</h2>{''.join(items)}"
        f"<h2>Review history</h2>{f'<ul>{reviews}</ul>' if reviews else '<p class=muted>No decisions yet.</p>'}"
    )
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
    hashes = sorted({r["sha256"] for r in c.store.case_reports(case_id)})
    payees = c.store.case_payees(case_id)

    c.store.review(case_id, user, action, note)
    c.store.audit(user, f"review_{action}", f"case={case_id}")
    if action == "approve_fake":
        registry.update_known_fakes(add_hashes=hashes, add_payees=payees)
        _make_packet(c, case_id)
    elif case["status"] == "approved_fake":
        # Reversing an approval takes the case's files and payees out of the known fakes again.
        registry.update_known_fakes(remove_hashes=hashes, remove_payees=payees)
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


@router.get("/audit", response_class=HTMLResponse)
def audit_page(user: str = Depends(require_user), c: Console = Depends(console_deps)) -> HTMLResponse:
    rows = "".join(
        f"<tr><td>{escape(a['at'][:19])}</td><td>{escape(a['actor'])}</td><td>{escape(a['action'])}</td>"
        f"<td class=mono>{escape(a['detail'] or '')}</td></tr>"
        for a in c.store.audit_entries()
    )
    return page("Audit log", f"<h1>Audit log</h1><div class=wrap><table><tr><th>At (UTC)</th><th>Who</th><th>Action</th>"
                f"<th>Detail</th></tr>{rows}</table></div>", user)
