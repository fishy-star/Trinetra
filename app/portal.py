"""Issuer portal at /issuer, and the public verification page at /v/<code>.

Issuer staff have their own accounts, separate from reviewers, and see and act on their own
organisation's notices only: every query is scoped to the signed-in user's issuer. Every
action is written to the audit log. The public page shows no personal data and is rate-limited
so codes cannot be guessed.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import issuers as codes
from app.auth import LoginLimiter, Sessions, client_of
from app.config import settings
from app.console import STYLE
from app.extract.ocr import sniff
from app.intake import MAX_BYTES
from app.issuing import CONFIRM_LABEL, MEDIA_TYPES, IssueError, issue_notice
from app.store import Store

router = APIRouter()

VERIFY_LIMIT = 30  # lookups per client per window
VERIFY_WINDOW_SECONDS = 600


@dataclass(frozen=True)
class Portal:
    store: Store
    issued_dir: Path
    secret: bytes
    base_url: str

    @property
    def sessions(self) -> Sessions:
        return Sessions("trinetra_issuer", "/issuer", self.secret)


@lru_cache
def portal_deps() -> Portal:
    salt = settings.user_id_salt or secrets.token_hex(16)  # without a salt, sessions last until restart
    secret = hashlib.sha256(f"issuer:{salt}".encode()).digest()
    return Portal(Store(settings.data_dir / "trinetra.db"), settings.data_dir / "issued", secret, settings.public_base_url)


limiter = LoginLimiter()


@dataclass(frozen=True)
class Staff:
    username: str
    issuer: dict


def current_staff(request: Request, p: Portal = Depends(portal_deps)) -> Staff | None:
    username = p.sessions.user(request)
    account = p.store.issuer_user(username) if username else None
    if account is None:
        return None
    issuer = p.store.issuer(account["issuer_id"])
    return Staff(username, issuer) if issuer else None


def require_staff(staff: Staff | None = Depends(current_staff)) -> Staff:
    if staff is None:
        raise HTTPException(303, headers={"Location": "/issuer"})
    return staff


def actor(staff: Staff) -> str:
    return f"issuer:{staff.username}"


def page(title: str, body: str, staff: Staff | None = None) -> HTMLResponse:
    nav = (
        f'<a href="/issuer">Notices</a><span class="who">{escape(staff.username)} · {escape(staff.issuer["name"])} · '
        '<form method="post" action="/issuer/logout" style="display:inline"><button type="submit">Log out</button></form></span>'
        if staff else ""
    )
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · Trinetra</title><style>{STYLE}</style></head>"
        f"<body><header><strong>Trinetra issuer portal</strong>{nav}</header><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"},
    )


def login_page(error: str = "") -> HTMLResponse:
    return page(
        "Log in",
        f'<h1>Issuer log in</h1>{f"<p class=error>{escape(error)}</p>" if error else ""}'
        '<form method="post" action="/issuer/login" class="card" style="max-width:360px">'
        '<p><label>Username<br><input name="username" autocomplete="username" required></label></p>'
        '<p><label>Password<br><input name="password" type="password" autocomplete="current-password" required></label></p>'
        '<button class="primary" type="submit">Log in</button></form>',
    )


# --- issuer pages -------------------------------------------------------------------


@router.get("/issuer", response_class=HTMLResponse)
def dashboard(request: Request, staff: Staff | None = Depends(current_staff), p: Portal = Depends(portal_deps)) -> HTMLResponse:
    if staff is None:
        return login_page()
    return _dashboard(request, staff, p)


def _dashboard(request: Request, staff: Staff, p: Portal, error: str = "", text: str = "") -> HTMLResponse:
    rows = "".join(
        f'<tr><td><a href="/issuer/notices/{n["id"]}" class="mono">{n["code"]}</a></td><td>{escape(n["notice_type"])}</td>'
        f'<td>{escape(n["issued_at"][:16].replace("T", " "))}</td><td>{escape(n["issued_by"])}</td>'
        f'<td class="status-{"approved_fake" if n["status"] == "revoked" else "not_fake"}">{n["status"].title()}</td></tr>'
        for n in p.store.issuer_notices(staff.issuer["id"])
    )
    options = "".join(f"<option>{t}</option>" for t in codes.NOTICE_TYPES)
    body = (
        f'<h1>{escape(staff.issuer["name"])}</h1>'
        f'{f"<p class=error>{escape(error)}</p>" if error else ""}'
        '<form method="post" action="/issuer/notices" enctype="multipart/form-data" class="card">'
        f'<input type="hidden" name="csrf" value="{p.sessions.csrf(request)}">'
        "<h2 style='margin-top:0'>Issue a notice</h2>"
        f'<p><label>Notice type<br><select name="notice_type">{options}</select></label></p>'
        '<p><label>Upload the notice (PDF, PNG or JPEG)<br>'
        '<input type="file" name="file" accept="application/pdf,image/png,image/jpeg"></label></p>'
        f'<p><label>Or paste its text<br><textarea name="text" rows="5" style="width:100%">{escape(text)}</textarea></label></p>'
        f'<p><label><input type="checkbox" name="confirm_genuine" value="yes"> {CONFIRM_LABEL}</label><br>'
        '<span class="muted">Needed only if Trinetra rates the notice suspicious.</span></p>'
        '<button class="primary" type="submit">Issue with verification code</button></form>'
        '<h2>Issued notices</h2><div class="wrap"><table><tr><th>Code</th><th>Type</th><th>Issued (UTC)</th>'
        f"<th>By</th><th>Status</th></tr>{rows or '<tr><td colspan=5>None yet.</td></tr>'}</table></div>"
    )
    return page("Notices", body, staff)


@router.post("/issuer/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), p: Portal = Depends(portal_deps)) -> Response:
    client = client_of(request)
    if limiter.locked(client):
        return login_page("Too many failed attempts. Try again in 15 minutes.")
    account = p.store.issuer_user(username)
    if account is None or not codes.check_password(password, account["password_hash"]):
        limiter.fail(client)
        p.store.audit(f"issuer:{username[:40] or '-'}", "issuer_login_failed")
        return login_page("Wrong username or password.")
    p.store.audit(f"issuer:{username}", "issuer_login", f"issuer={account['issuer_id']}")
    resp = RedirectResponse("/issuer", status_code=303)
    p.sessions.start(resp, request, username)
    return resp


@router.post("/issuer/logout")
def logout(p: Portal = Depends(portal_deps)) -> Response:
    resp = RedirectResponse("/issuer", status_code=303)
    p.sessions.end(resp)
    return resp


@router.post("/issuer/notices")
async def issue(
    request: Request,
    notice_type: str = Form(""),
    text: str = Form(""),
    csrf: str = Form(""),
    confirm_genuine: str = Form(""),
    file: UploadFile | None = File(None),
    staff: Staff = Depends(require_staff),
    p: Portal = Depends(portal_deps),
) -> Response:
    p.sessions.check_csrf(request, csrf)
    data = None
    if file is not None and file.filename:
        data = await file.read(MAX_BYTES["pdf"] + 1)
        kind = sniff(data)
        if kind and len(data) > MAX_BYTES[kind]:
            p.store.audit(actor(staff), "issue_refused", "reason=too_large")
            return _dashboard(request, staff, p, "This file is too large.", text)
    confirmed = confirm_genuine == "yes"
    try:
        issued = await run_in_threadpool(
            issue_notice, p.store, staff.issuer, staff.username, notice_type, p.issued_dir, p.base_url, data, text,
            confirmed,
        )
    except IssueError as e:
        # Every refusal is logged, so repeated attempts from a misused issuer account show up.
        p.store.audit(actor(staff), "issue_refused", f"issuer={staff.issuer['id']} reason={e.reason} {e.detail}".strip())
        return _dashboard(request, staff, p, str(e), text)
    if issued.confirmed_signals:
        p.store.audit(
            actor(staff), "issue_confirmed_suspicious",
            f"issuer={staff.issuer['id']} code={issued.code} signals={','.join(issued.confirmed_signals)}",
        )
    p.store.audit(actor(staff), "issue_notice", f"issuer={staff.issuer['id']} code={issued.code}")
    return RedirectResponse(f"/issuer/notices/{issued.notice_id}", status_code=303)


def _own_notice(p: Portal, staff: Staff, notice_id: int):
    notice = p.store.issuer_notice(staff.issuer["id"], notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice")  # also for another issuer's notice: no hint it exists
    return notice


@router.get("/issuer/notices/{notice_id}", response_class=HTMLResponse)
def notice_page(notice_id: int, request: Request, staff: Staff = Depends(require_staff), p: Portal = Depends(portal_deps)) -> HTMLResponse:
    n = _own_notice(p, staff, notice_id)
    url = codes.verify_url(p.base_url, n["code"])
    preview = (
        f'<p><img class="evidence" src="/issuer/notices/{notice_id}/stamped" alt="Stamped notice"></p>'
        if n["stamped_path"].endswith(".png") else ""
    )
    revoke = (
        f'<form method="post" action="/issuer/notices/{notice_id}/revoke" class="card">'
        f'<input type="hidden" name="csrf" value="{p.sessions.csrf(request)}">'
        "<p>Revoking makes this code show as cancelled. Anyone who forwards the notice is told the code is fake or cancelled.</p>"
        '<button class="primary" type="submit">Revoke this notice</button></form>'
        if n["status"] == "valid" else
        f'<p class="error">Revoked {escape((n["revoked_at"] or "")[:16].replace("T", " "))} by {escape(n["revoked_by"] or "")}.</p>'
    )
    body = (
        f'<h1>Notice <span class="mono">{n["code"]}</span></h1>'
        f'<p>{escape(n["notice_type"])} · issued {escape(n["issued_at"][:16].replace("T", " "))} UTC by {escape(n["issued_by"])} · '
        f'status <strong>{n["status"]}</strong></p>'
        f'<p>Public page: <a href="{escape(url)}">{escape(url)}</a></p>'
        f'<p><a href="/issuer/notices/{notice_id}/stamped?download=1">Download the stamped notice</a> · '
        f'<a href="/issuer/notices/{notice_id}/qr.png">Download the QR code</a></p>'
        f"{preview}{revoke}"
    )
    return page(n["code"], body, staff)


@router.get("/issuer/notices/{notice_id}/stamped")
def stamped(notice_id: int, download: int = 0, staff: Staff = Depends(require_staff), p: Portal = Depends(portal_deps)) -> Response:
    n = _own_notice(p, staff, notice_id)
    path = Path(n["stamped_path"])
    kind = {".png": "png", ".pdf": "pdf", ".txt": "text"}[path.suffix]
    if download:
        p.store.audit(actor(staff), "download_stamped", f"code={n['code']}")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if download or kind != "png":
        headers["Content-Disposition"] = f'attachment; filename="{n["code"]}{path.suffix}"'
    return Response(path.read_bytes(), media_type=MEDIA_TYPES[kind], headers=headers)


@router.get("/issuer/notices/{notice_id}/qr.png")
def qr(notice_id: int, staff: Staff = Depends(require_staff), p: Portal = Depends(portal_deps)) -> Response:
    n = _own_notice(p, staff, notice_id)
    p.store.audit(actor(staff), "download_qr", f"code={n['code']}")
    return Response(
        codes.qr_png(codes.verify_url(p.base_url, n["code"])), media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{n["code"]}-qr.png"', "Cache-Control": "no-store"},
    )


@router.post("/issuer/notices/{notice_id}/revoke")
def revoke(notice_id: int, request: Request, csrf: str = Form(""), staff: Staff = Depends(require_staff),
           p: Portal = Depends(portal_deps)) -> Response:
    p.sessions.check_csrf(request, csrf)
    n = _own_notice(p, staff, notice_id)
    if p.store.revoke_notice(staff.issuer["id"], notice_id, staff.username):
        p.store.audit(actor(staff), "revoke_notice", f"code={n['code']}")
        p.store.clear_results()  # a cached "verified" for this notice must not be served again
    return RedirectResponse(f"/issuer/notices/{notice_id}", status_code=303)


# --- public verification page ---------------------------------------------------------

_lookups: dict[str, list[float]] = defaultdict(list)

STATUS_TEXT = {
    "valid": ("Valid", "This code was issued with this notice and has not been cancelled. Check that the notice you "
              "hold matches, or forward it to Trinetra to compare it automatically."),
    "revoked": ("Cancelled", "The issuer has cancelled this notice. Do not act on it or pay anything because of it."),
}


def _verify_allowed(client: str) -> bool:
    now = time.time()
    recent = [t for t in _lookups[client] if t > now - VERIFY_WINDOW_SECONDS]
    _lookups[client] = recent
    if len(recent) >= VERIFY_LIMIT:
        return False
    recent.append(now)
    return True


def public_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    resp = page(title, body)
    resp.status_code = status
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@router.get("/v/{code}", response_class=HTMLResponse)
def verify(code: str, request: Request, p: Portal = Depends(portal_deps)) -> HTMLResponse:
    if not _verify_allowed(client_of(request)):
        return public_page("Too many checks", "<h1>Too many checks</h1><p>Please wait a few minutes and try again.</p>", 429)
    found = codes.find_codes(code)
    notice = p.store.notice_by_code(found[0]) if found else None
    if notice is None:
        return public_page(
            "Unknown code",
            "<h1>No notice has this code</h1><p class=error>Trinetra has no notice with this verification code. "
            "Do not trust a notice that shows it, and do not pay anything because of it.</p>",
            404,
        )
    v = codes.public_view(notice)
    label, explain = STATUS_TEXT[v["status"]]
    body = (
        f'<h1>Verification code <span class="mono">{v["code"]}</span></h1><div class="card"><table>'
        f'<tr><th>Status</th><td class="status-{"not_fake" if v["status"] == "valid" else "approved_fake"}"><strong>{label}</strong></td></tr>'
        f'<tr><th>Issued by</th><td>{escape(v["issuer"])}</td></tr>'
        f'<tr><th>Notice type</th><td>{escape(v["notice_type"])}</td></tr>'
        f'<tr><th>Issued on</th><td>{escape(v["issued_on"])}</td></tr>'
        f"</table></div><p>{explain}</p>"
        "<p class=muted>This page never shows names, amounts or other details from the notice.</p>"
    )
    return public_page(v["code"], body)
