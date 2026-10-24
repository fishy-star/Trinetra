"""Shared page templates: layout, header and footer, zones, badges and the reply card.

Three zones, each with its own feel: public (/ and /v/<code>), issuer (/issuer) and reviewer
(/console). All text is escaped here or by the caller; nothing from a notice is ever trusted.
"""

from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse

SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"}
ASSET_VERSION = "1"  # bump to make browsers fetch new CSS/JS

# Verdicts and statuses: each has its own colour AND its word, never colour alone.
BADGES = {
    "likely_fake": ("fake", "Likely fake", "संभवतः नकली"),
    "suspicious": ("suspicious", "Suspicious", "संदिग्ध"),
    "cannot_verify": ("unverified", "Cannot verify", "पुष्टि नहीं हो सकी"),
    "verified_official": ("verified", "Verified", "पुष्टि हुई"),
    "could_not_check": ("unread", "Could not read", "पढ़ा नहीं जा सका"),
    "valid": ("verified", "Valid", "मान्य"),
    "revoked": ("cancelled", "Cancelled", "रद्द"),
    "cancelled": ("cancelled", "Cancelled", "रद्द"),
    "open": ("open", "Open", "खुला"),
    "needs_info": ("suspicious", "Needs more info", "और जानकारी चाहिए"),
    "approved_fake": ("fake", "Confirmed fake", "नकली पुष्टि"),
    "not_fake": ("verified", "Not fake", "नकली नहीं"),
}
TONES = {"likely_fake": "fake", "suspicious": "suspicious", "cannot_verify": "unverified", "verified_official": "verified"}


def badge(kind: str, lang: str = "en", large: bool = False) -> str:
    cls, en, hi = BADGES.get(kind, ("open", kind.replace("_", " ").capitalize(), kind))
    return f'<span class="badge badge-{cls}{" badge-lg" if large else ""}">{escape(hi if lang == "hi" else en)}</span>'


def copy_code(value: str, label: str = "Copy") -> str:
    return (
        f'<span class="copy"><code>{escape(value)}</code>'
        f'<button type="button" data-copy="{escape(value)}" aria-label="Copy {escape(value)}">{label}</button></span>'
    )


def copy_button(value: str, label: str = "Copy") -> str:
    return (f'<span class="copy"><button type="button" data-copy="{escape(value)}" '
            f'aria-label="{escape(label)}: {escape(value)}">{escape(label)}</button></span>')


def wordmark(href: str) -> str:
    return f'<a class="wordmark" href="{href}"><span class="eyes" aria-hidden="true"><i></i><i></i><i></i></span>Trinetra</a>'


def layout(
    title: str,
    body: str,
    *,
    zone: str,
    nav: list[tuple[str, str]] = (),
    current: str = "",
    user: str | None = None,
    logout: str | None = None,
    org: str | None = None,
    narrow: bool = False,
    status: int = 200,
    lang: str = "en",
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """`zone` is "public", "issuer" or "reviewer". `nav` is (href, label) pairs; `current` marks one."""
    home = {"public": "/", "issuer": "/issuer", "reviewer": "/console"}[zone]
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == current else ""}>{escape(label)}</a>' for href, label in nav
    )
    tag = {
        "reviewer": '<span class="zone-tag restricted">Restricted: reviewers only</span>',
        "issuer": '<span class="zone-tag issuer">Issuer portal</span>',
        "public": "",
    }[zone]
    who = (
        f'<span>{escape(user)}</span><form method="post" action="{logout}">'
        '<button class="btn btn-quiet" type="submit">Log out</button></form>'
        if user and logout else ""
    )
    org_bar = (
        f'<div class="org-bar"><div class="bar"><span>Issuer portal:</span><strong>{escape(org)}</strong>'
        '<span class="muted small">Everything you do here is recorded under this organisation.</span></div></div>'
        if org else ""
    )
    footer_note = {
        "public": "Trinetra never asks for OTP, PIN or money.",
        "issuer": "Issue only notices from your own organisation. Every action is recorded in the audit log.",
        "reviewer": "Restricted area. Every view, decision and download is recorded in the audit log.",
    }[zone]
    html = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · Trinetra</title>"
        '<meta name="color-scheme" content="light">'
        f'<link rel="stylesheet" href="/static/trinetra.css?v={ASSET_VERSION}">'
        f'<script src="/static/trinetra.js?v={ASSET_VERSION}" defer></script>'
        f'</head><body class="zone-{zone}">'
        f'<header class="site-header"><div class="bar">{wordmark(home)}<nav>{links}</nav>'
        f'<div class="right">{tag}{who}</div></div></header>{org_bar}'
        f'<main class="page{" narrow" if narrow else ""}" id="main">{body}</main>'
        f'<footer class="site-footer"><div class="bar">{wordmark("/")}'
        f'<span>{footer_note}</span></div></footer></body></html>'
    )
    return HTMLResponse(html, status_code=status, headers={**SECURITY_HEADERS, **(headers or {})})


def reply_card(reply: str, verdict: str | None, lang: str = "en", extra: str = "") -> str:
    """The bot's reply as a card: badge, verdict line, reasons, action lines, review status."""
    blocks = [b for b in reply.split("\n\n") if b.strip()]
    if not blocks:
        return ""
    head, rest = blocks[0], blocks[1:]
    footer = rest.pop() if len(rest) >= 1 and verdict else None
    parts = []
    for block in rest:
        lines = block.splitlines()
        if all(ln.startswith("• ") for ln in lines):
            parts.append("<ul>" + "".join(f"<li>{escape(ln[2:])}</li>" for ln in lines) + "</ul>")
        else:
            parts.append(f'<p class="action">{escape(block)}</p>')
    tone = TONES.get(verdict or "", "")
    return (
        f'<section class="card reply-card{" tone-" + tone if tone else ""}" role="status" aria-live="polite">'
        f'{badge(verdict, lang, large=True) if verdict else ""}'
        f'<p class="verdict">{escape(head)}</p>{"".join(parts)}'
        f'{f"<p class=foot>{escape(footer)}</p>" if footer else ""}{extra}</section>'
    )


def notice(message: str, tone: str = "info") -> str:
    return f'<div class="alert alert-{tone}" role="status">{escape(message)}</div>'


def problem(headline: str, summary: str = "", reasons: list[str] = (), after: str = "", tone: str = "danger") -> str:
    """A refusal or error: a headline, a short summary, then each reason as its own bullet."""
    items = "".join(f"<li>{escape(r)}</li>" for r in reasons)
    return (
        f'<div class="alert alert-{tone}" role="alert"><h3>{escape(headline)}</h3>'
        f'{f"<p>{escape(summary)}</p>" if summary else ""}{f"<ul>{items}</ul>" if items else ""}'
        f'{f"<p>{escape(after)}</p>" if after else ""}</div>'
    )
