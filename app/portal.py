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
from app import ui
from app.auth import LoginLimiter, Sessions, client_of
from app.config import settings
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


def page(title: str, body: str, staff: Staff | None = None, narrow: bool = False) -> HTMLResponse:
    """Issuer-zone page. Signed-in pages always show which organisation the staff member acts for."""
    return ui.layout(
        title, body, zone="issuer",
        nav=[("/issuer", "Notices")] if staff else [],
        current="/issuer" if staff else "",
        user=staff.username if staff else None,
        logout="/issuer/logout" if staff else None,
        org=staff.issuer["name"] if staff else None,
        narrow=narrow,
    )


def login_page(error: str = "") -> HTMLResponse:
    return page(
        "Issuer log in",
        '<div class="card login-card"><p class="eyebrow">Issuer portal</p><h1>Log in</h1>'
        "<p class=muted>For courts, banks and other issuers sending notices with a Trinetra verification code.</p>"
        f'{ui.problem(error) if error else ""}'
        '<form method="post" action="/issuer/login">'
        '<label class="field">Username<input name="username" autocomplete="username" required></label>'
        '<label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>'
        '<button class="btn btn-lg" type="submit" style="width:100%">Log in</button></form></div>',
        narrow=True,
    )


# --- issuer pages -------------------------------------------------------------------


@router.get("/issuer", response_class=HTMLResponse)
def dashboard(request: Request, staff: Staff | None = Depends(current_staff), p: Portal = Depends(portal_deps)) -> HTMLResponse:
    if staff is None:
        return login_page()
    return _dashboard(request, staff, p)


def _dashboard(request: Request, staff: Staff, p: Portal, error: IssueError | str | None = None, text: str = "") -> HTMLResponse:
    token = p.sessions.csrf(request)
    rows = "".join(
        f'<tr><td><a class="mono" href="/issuer/notices/{n["id"]}">{n["code"]}</a></td>'
        f'<td>{escape(n["notice_type"])}</td>'
        f'<td>{escape(n["issued_at"][:16].replace("T", " "))}</td>'
        f'<td>{ui.badge(n["status"])}</td><td>{escape(n["issued_by"])}</td><td>'
        + (
            f'<form method="post" action="/issuer/notices/{n["id"]}/revoke" '
            f'data-confirm="Revoke {n["code"]}? Anyone who checks it will be told it is cancelled.">'
            f'<input type="hidden" name="csrf" value="{token}">'
            '<button class="btn btn-ghost btn-sm" type="submit">Revoke</button></form>'
            if n["status"] == "valid" else '<span class="muted small">Revoked</span>'
        )
        + "</td></tr>"
        for n in p.store.issuer_notices(staff.issuer["id"])
    )
    if isinstance(error, IssueError):
        alert = ui.problem(error.headline, error.summary, error.reasons, error.after,
                           tone="warn" if error.reason == "suspicious_unconfirmed" else "danger")
    elif error:
        alert = ui.problem("This notice can't be issued", error)
    else:
        alert = ""
    options = "".join(f"<option>{escape(t)}</option>" for t in codes.NOTICE_TYPES)
    body = f"""
<div class="page-head"><div><p class="eyebrow">Notices</p><h1>Issue a notice</h1></div></div>
<div class="layout-2">
  <form class="card" method="post" action="/issuer/notices" enctype="multipart/form-data">
    <input type="hidden" name="csrf" value="{token}">
    {alert}
    <label class="field">Notice type<select name="notice_type">{options}</select></label>
    <label class="dropzone" for="notice-file">
      <span class="icon" aria-hidden="true">⇪</span><span class="big">Upload the notice</span>
      <span class="muted">PDF, PNG or JPEG. Upload the original, without a Trinetra code.</span>
      <input id="notice-file" type="file" name="file" accept="application/pdf,image/png,image/jpeg">
      <span class="chosen" aria-live="polite"></span>
    </label>
    <div class="or">or</div>
    <label class="field">Paste its text<textarea name="text" rows="6">{escape(text)}</textarea></label>
    <label class="check"><input type="checkbox" name="confirm_genuine" value="yes"> <span>{CONFIRM_LABEL}</span></label>
    <p class="muted small" style="margin:6px 0 16px 30px">Needed only if Trinetra rates the notice suspicious.</p>
    <button class="btn btn-gold btn-lg" type="submit">Issue with verification code</button>
  </form>
  <aside class="card">
    <h2>How it works</h2>
    <ol class="muted" style="padding-left:1.2em;margin:0">
      <li>Trinetra checks the notice first and refuses anything it would call fake, or that names another body.</li>
      <li>It adds a QR code and a code like <span class="mono">TRN-7K2Q-9XFD</span>, and keeps a fingerprint of the text.</li>
      <li>Anyone can forward the notice to Trinetra or open the code's page. Edited copies are caught.</li>
      <li>Revoke a notice at any time; its code then shows as cancelled.</li>
    </ol>
  </aside>
</div>
<h2>Issued notices</h2>
<div class="table-wrap"><table class="data"><thead><tr><th>Code</th><th>Type</th><th>Issued (UTC)</th><th>Status</th>
<th>By</th><th></th></tr></thead><tbody>{rows or '<tr><td colspan="6" class="empty">None yet.</td></tr>'}</tbody></table></div>
"""
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
        return _dashboard(request, staff, p, e, text)
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
    if n["stamped_path"].endswith(".png"):
        preview = f'<img class="stamped" src="/issuer/notices/{notice_id}/stamped" alt="The stamped notice with its QR code">'
    else:
        kind = "PDF" if n["stamped_path"].endswith(".pdf") else "text"
        preview = (f'<div class="card empty"><p>The stamped notice is a {kind} file.</p>'
                   f'<a class="btn" href="/issuer/notices/{notice_id}/stamped?download=1">Download it</a></div>')
    if n["status"] == "valid":
        revoke = (
            f'<form class="card" method="post" action="/issuer/notices/{notice_id}/revoke" '
            f'data-confirm="Revoke {n["code"]}? Anyone who checks it will be told it is cancelled.">'
            f'<input type="hidden" name="csrf" value="{p.sessions.csrf(request)}">'
            "<h3>Revoke</h3><p class=muted>The code will show as cancelled. Anyone who forwards this notice is told "
            "its code is fake or cancelled.</p>"
            '<button class="btn btn-danger" type="submit">Revoke this notice</button></form>'
        )
    else:
        revoke = ui.problem(
            "This notice is revoked",
            f'Revoked {(n["revoked_at"] or "")[:16].replace("T", " ")} UTC by {n["revoked_by"] or ""}.',
        )
    body = f"""
<p><a href="/issuer">← All notices</a></p>
<div class="layout-2 wide-left">
  <div>{preview}</div>
  <div>
    <section class="card">
      <p class="eyebrow">Verification code</p>
      <p class="code-hero">{n["code"]} {ui.copy_button(n["code"], "Copy code")}</p>
      <table class="kv">
        <tr><th>Status</th><td>{ui.badge(n["status"])}</td></tr>
        <tr><th>Notice type</th><td>{escape(n["notice_type"])}</td></tr>
        <tr><th>Issued</th><td>{escape(n["issued_at"][:16].replace("T", " "))} UTC</td></tr>
        <tr><th>Issued by</th><td>{escape(n["issued_by"])}</td></tr>
        <tr><th>Public page</th><td><a class="mono small" href="{escape(url)}">{escape(url)}</a> {ui.copy_button(url, "Copy link")}</td></tr>
      </table>
      <div class="btn-row" style="margin-top:16px">
        <a class="btn btn-gold" href="/issuer/notices/{notice_id}/stamped?download=1">Download stamped notice</a>
        <a class="btn btn-ghost" href="/issuer/notices/{notice_id}/qr.png">Download QR code</a>
      </div>
    </section>
    {revoke}
  </div>
</div>
"""
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
    "valid": ("This code was issued with this notice and has not been cancelled. Check that the notice you hold "
              "matches, or forward it to Trinetra to compare it automatically.",
              "यह कोड इसी नोटिस के साथ जारी हुआ था और रद्द नहीं हुआ है। जाँच के लिए नोटिस Trinetra को फ़ॉरवर्ड करें।"),
    "revoked": ("The issuer has cancelled this notice. Do not act on it or pay anything because of it.",
                "जारी करने वाले ने यह नोटिस रद्द कर दिया है। इस पर कोई कदम न उठाएँ और इसके कारण कोई भुगतान न करें।"),
}
NEVER_SHOWN = "This page never shows names, amounts or other details from the notice."


def _verify_allowed(client: str) -> bool:
    now = time.time()
    recent = [t for t in _lookups[client] if t > now - VERIFY_WINDOW_SECONDS]
    _lookups[client] = recent
    if len(recent) >= VERIFY_LIMIT:
        return False
    recent.append(now)
    return True


def public_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return ui.layout(title, body, zone="public", narrow=True, status=status, headers={"Referrer-Policy": "no-referrer"})


def _status_card(mark: str, tone: str, word: str, word_hi: str, inner: str) -> str:
    return (
        f'<section class="card status-card"><div class="status-mark {tone}" aria-hidden="true">{mark}</div>'
        f'<p class="status-word {tone}">{escape(word)}</p><p class="muted" lang="hi">{escape(word_hi)}</p>{inner}</section>'
    )


@router.get("/v/{code}", response_class=HTMLResponse)
def verify(code: str, request: Request, p: Portal = Depends(portal_deps)) -> HTMLResponse:
    if not _verify_allowed(client_of(request)):
        return public_page("Too many checks", ui.problem(
            "Too many checks", "Please wait a few minutes and try again.", tone="warn"), 429)
    found = codes.find_codes(code)
    notice = p.store.notice_by_code(found[0]) if found else None
    if notice is None:
        inner = (
            '<div class="alert alert-danger"><strong>Do not trust a notice that shows this code.</strong>'
            "<p>Trinetra has no notice with this verification code. Do not pay anything because of it.</p></div>"
            f'<p class="muted small">{NEVER_SHOWN}</p>'
        )
        return public_page("Unknown code", _status_card("✕", "bad", "No notice has this code", "इस कोड का कोई नोटिस नहीं है",
                                                       inner), 404)
    v = codes.public_view(notice)
    explain_en, explain_hi = STATUS_TEXT[v["status"]]
    details = (
        f'<table class="kv"><tr><th>Code</th><td>{ui.copy_code(v["code"])}</td></tr>'
        f'<tr><th>Issued by</th><td><strong>{escape(v["issuer"])}</strong></td></tr>'
        f'<tr><th>Notice type</th><td>{escape(v["notice_type"])}</td></tr>'
        f'<tr><th>Issued on</th><td>{escape(v["issued_on"])}</td></tr></table>'
    )
    if v["status"] == "valid":
        inner = f'{details}<p style="margin-top:18px">{explain_en}</p><p lang="hi">{explain_hi}</p>'
        card = _status_card("✓", "ok", "Valid", "मान्य", inner)
    else:
        warning = (
            '<div class="alert alert-danger"><strong>Do not act on it or pay anything.</strong>'
            f"<p>{explain_en}</p><p lang=\"hi\">{explain_hi}</p></div>"
        )
        card = _status_card("⊘", "bad", "Cancelled", "रद्द", warning + details)
    body = f'{card}<p class="muted small center">{NEVER_SHOWN}</p>'
    return public_page(v["code"], body)
