"""FastAPI app: web upload page, the Telegram webhook and the review console."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from html import escape
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import qrcode
from qrcode.image.svg import SvgPathImage

from app import pipeline, reply, samples, ui
from app.channels.telegram import TelegramAPI, TelegramSender, parse_update
from app.config import settings
from app.console import router as console_router
from app.portal import router as portal_router
from app.evidence import EXTENSIONS, EvidenceStore, MediaDir
from app.extract import ocr
from app.auth import client_of
from app.intake import (DISPUTE_ASK, DISPUTE_THANKS, LABELS, MAX_BYTES, MESSAGES, NOTE_MAX_CHARS, NOTE_THANKS,
                        VERDICT_CODES, Intake, sniff_av)
from app.models import Kind, Verdict
from app.store import NewReport, report_payees
from app.web_hold import WebHold
from app.extract.mask import mask_text
from app.jobs import start_purger
from app.store import Store
import logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = threading.Event()
    start_purger(Store(settings.data_dir / "trinetra.db"), MediaDir(settings.data_dir / "media"), stop)
    yield
    stop.set()


app = FastAPI(title="Trinetra", lifespan=lifespan)
# Fonts, CSS and JS for every page, served locally so nothing depends on a CDN.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(console_router)
app.include_router(portal_router)


@dataclass(frozen=True)
class TelegramDeps:
    api: TelegramAPI
    intake: Intake
    secret: str


@lru_cache
def telegram_deps() -> TelegramDeps | None:
    """None until a token, a webhook secret and a salt are all set; the webhook is then switched off."""
    if not (settings.telegram_bot_token and settings.telegram_webhook_secret and settings.user_id_salt):
        return None
    store = Store(settings.data_dir / "trinetra.db")
    intake = Intake(store, settings.user_id_salt, data_dir=settings.data_dir, media_ttl_hours=settings.media_ttl_hours)
    return TelegramDeps(TelegramAPI(settings.telegram_bot_token), intake, settings.telegram_webhook_secret)


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    deps: TelegramDeps | None = Depends(telegram_deps),
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict[str, bool]:
    if deps is None:
        raise HTTPException(404)
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", deps.secret):
        raise HTTPException(403)
    msg = parse_update(await request.json(), deps.api)
    if msg is not None:
        # Answer Telegram at once; the "Checking…" message and the verdict are sent from the background task.
        sender = TelegramSender(deps.api, msg)
        if sender.acks_on_arrival():
            sender.ack()
        background.add_task(deps.intake.handle, msg, sender)
    return {"ok": True}


MAX_TEXT_CHARS = 5000

TELEGRAM_BOT = "Trinetra_check_bot"
FEEDBACK_BUTTONS = {"en": ("Helpful", "Not helpful"), "hi": ("मददगार", "मददगार नहीं")}
PUBLIC_NAV = [("/", "Check"), ("/try", "Try the bot")]


def t(en: str, hi: str, tag: str = "span", attrs: str = "") -> str:
    """Text that the page's Hindi/English toggle can swap."""
    return f'<{tag}{attrs} data-en="{escape(en)}" data-hi="{escape(hi)}">{escape(en)}</{tag}>'


WEB_TEXT = {
    "en": {"report": "Report", "report_ask": "Report this to the Trinetra team? Your file will be kept as evidence.",
           "consent": "I agree that Trinetra keeps this file as evidence.", "report_yes": "Yes, report it",
           "paid": "I already paid", "call": "Call 1930", "bank": "Call your bank now, on the number on your card or "
           "passbook.", "file": "File a complaint at cybercrime.gov.in", "note": "What was wrong? (optional, one line)",
           "send": "Send", "report_done": "Reported. Your report is case {case}. A reviewer will look at it.",
           "already": "This was already reported from the web as case {case}.",
           "expired": "This check has expired. Check it again, then report it.",
           "consent_needed": "Tick the box to agree before reporting.", "busy": "Too many requests. Try again later."},
    "hi": {"report": "रिपोर्ट करें", "report_ask": "इसे Trinetra टीम को रिपोर्ट करें? आपकी फ़ाइल सबूत के रूप में रखी जाएगी।",
           "consent": "मैं सहमत हूँ कि Trinetra इस फ़ाइल को सबूत के रूप में रखे।", "report_yes": "हाँ, रिपोर्ट करें",
           "paid": "मैंने भुगतान कर दिया", "call": "1930 पर कॉल करें", "bank": "अभी अपने बैंक को कॉल करें, कार्ड या पासबुक "
           "पर दिए नंबर पर।", "file": "cybercrime.gov.in पर शिकायत दर्ज करें", "note": "क्या गलत था? (ज़रूरी नहीं, एक लाइन)",
           "send": "भेजें", "report_done": "रिपोर्ट हो गई। आपकी रिपोर्ट केस {case} है। एक समीक्षक इसे देखेगा।",
           "already": "यह पहले ही वेब से केस {case} के रूप में रिपोर्ट हो चुका है।",
           "expired": "यह जाँच पुरानी हो गई है। फिर से जाँचें, फिर रिपोर्ट करें।",
           "consent_needed": "रिपोर्ट करने से पहले सहमति वाला बॉक्स चुनें।", "busy": "बहुत ज़्यादा अनुरोध। बाद में कोशिश करें।"},
}


def _hidden(**fields: str) -> str:
    return "".join(f'<input type="hidden" name="{k}" value="{escape(v)}">' for k, v in fields.items())


def _actions(token: str, verdict: str, lang: str) -> str:
    """Report and I already paid, as on the bot. Both open in place and work without JavaScript."""
    if verdict == Verdict.COULD_NOT_CHECK.value:
        return ""
    w = WEB_TEXT[lang]
    paid = reply.ALREADY_PAID[lang]
    return (
        '<div class="web-actions">'
        f'<details class="act"><summary class="btn btn-ghost btn-sm">{w["report"]}</summary>'
        f'<form method="post" action="/report">{_hidden(token=token, lang=lang)}<p>{escape(w["report_ask"])}</p>'
        f'<label class="check"><input type="checkbox" name="consent" value="yes" required> {escape(w["consent"])}</label>'
        f'<button class="btn btn-sm" type="submit">{w["report_yes"]}</button></form></details>'
        f'<details class="act"><summary class="btn btn-ghost btn-sm">{w["paid"]}</summary>'
        f'<div class="paid"><p><strong>{escape(paid)}</strong></p>'
        f'<p><a class="btn btn-gold" href="tel:1930">{w["call"]}</a></p><p>{escape(w["bank"])}</p>'
        f'<p><a href="https://cybercrime.gov.in" rel="noopener">{w["file"]}</a></p></div></details></div>'
    )


def _feedback_form(sha256: str, verdict: str, lang: str, token: str = "") -> str:
    up, down = FEEDBACK_BUTTONS[lang]
    return (
        '<form class="feedback" method="post" action="/feedback">'
        f'{_hidden(sha=sha256, verdict=verdict, token=token, lang=lang)}'
        f'<span class="muted small">{"क्या यह मददगार था?" if lang == "hi" else "Was this helpful?"}</span>'
        f'<button class="btn btn-ghost btn-sm" name="vote" value="helpful" type="submit">{up}</button>'
        f'<button class="btn btn-ghost btn-sm" name="vote" value="not_helpful" type="submit">{down}</button></form>'
    )


def _page(text: str = "", reply: str | None = None, lang: str = "", verdict: str | None = None,
          reply_lang: str = "en", sha256: str | None = None, token: str = "", result_html: str = "",
          tone: str = "warn") -> HTMLResponse:
    if result_html:
        result = result_html
    elif reply and verdict:
        extra = (_actions(token, verdict, reply_lang) if token else "") + (
            _feedback_form(sha256, verdict, reply_lang, token) if sha256 else "")
        result = ui.reply_card(reply, verdict, reply_lang, extra)
    elif reply:
        result = ui.notice(reply, tone)
    else:
        result = ""
    body = f"""
<div class="center"><div class="lang-toggle" data-lang-toggle data-initial="{lang}" role="group" aria-label="Language">
  <button type="button" data-set-lang="en" aria-pressed="{'false' if lang == 'hi' else 'true'}">English</button>
  <button type="button" data-set-lang="hi" lang="hi" aria-pressed="{'true' if lang == 'hi' else 'false'}">हिन्दी</button>
</div></div>
<section class="hero">
  <h1>{t("Trinetra:", "Trinetra:")} <span class="accent">{t("check before you pay", "भुगतान से पहले जाँचें")}</span></h1>
  {t("Got a notice, voice note or message asking for money? Upload it or paste it here. We tell you in plain words "
     "whether it looks fake.",
     "पैसे माँगने वाला कोई नोटिस या संदेश मिला? उसे यहाँ अपलोड या पेस्ट करें। हम साफ़ शब्दों में बताएँगे कि वह नकली लगता है या नहीं।",
     "p")}
</section>
{result}
<form class="card" method="post" action="/check" enctype="multipart/form-data">
  <input type="hidden" name="lang" value="{lang}">
  <label class="dropzone" for="file">
    <span class="icon" aria-hidden="true">⇪</span>
    {t("Upload a photo or PDF", "फ़ोटो या PDF अपलोड करें", "span", ' class="big"')}
    {t("Tap to choose, or drag it here. JPG, PNG or PDF.", "चुनने के लिए टैप करें, या यहाँ खींचकर लाएँ। JPG, PNG या PDF।",
       "span", ' class="muted"')}
    <input id="file" name="file" type="file" accept="image/png,image/jpeg,application/pdf">
    <span class="chosen" aria-live="polite"></span>
  </label>
  <div class="or">{t("or", "या")}</div>
  <label class="field" for="text">{t("Paste the message", "संदेश पेस्ट करें")}
    <textarea id="text" name="text" maxlength="{MAX_TEXT_CHARS}"
      placeholder="Paste the SMS, WhatsApp or email text here"
      data-en="Paste the SMS, WhatsApp or email text here"
      data-hi="SMS, WhatsApp या ईमेल का टेक्स्ट यहाँ पेस्ट करें">{escape(text)}</textarea></label>
  <button class="btn btn-gold btn-lg" type="submit">{t("Check it", "जाँचें")}</button>
  <p class="muted small center" style="margin:12px 0 0">{t("We do not keep what you paste or upload here, unless you report it.",
     "आप यहाँ जो पेस्ट या अपलोड करते हैं, हम उसे नहीं रखते, जब तक आप उसे रिपोर्ट न करें।")}</p>
</form>
<h2>{t("Check from any app", "किसी भी ऐप से जाँचें")}</h2>
<div class="strip">
  <div class="tile"><span class="icon" aria-hidden="true">✆</span>{t("WhatsApp", "WhatsApp", "strong")}
    {t("Save the photo or PDF and upload it here, or forward it to our Telegram bot.",
       "फ़ोटो या PDF सेव करके यहाँ अपलोड करें, या हमारे Telegram बॉट को फ़ॉरवर्ड करें।")}</div>
  <div class="tile"><span class="icon" aria-hidden="true">▣</span>{t("Instagram or SMS", "Instagram या SMS", "strong")}
    {t("Take a screenshot and upload it, or copy the text and paste it.",
       "स्क्रीनशॉट लेकर अपलोड करें, या टेक्स्ट कॉपी करके पेस्ट करें।")}</div>
  <div class="tile"><span class="icon" aria-hidden="true">⎙</span>{t("PDF notice", "PDF नोटिस", "strong")}
    {t("Share the PDF to Trinetra on Telegram, or upload it here.",
       "PDF को Telegram पर Trinetra के साथ शेयर करें, या यहाँ अपलोड करें।")}</div>
</div>
<div class="card telegram">
  <div class="qr qr-sm" role="img" aria-label="QR code for t.me/{TELEGRAM_BOT}">{_bot_qr_svg()}</div>
  <div>{t("Use Trinetra in Telegram", "Telegram में Trinetra इस्तेमाल करें", "strong")}<br>
    <span class="mono">@{TELEGRAM_BOT}</span><br>
    {t("Scan the code with your phone, or tap the button.", "फ़ोन से कोड स्कैन करें, या बटन दबाएँ।", "span", ' class="muted small"')}</div>
  <a class="btn" href="https://t.me/{TELEGRAM_BOT}" rel="noopener">{t("Open the Telegram bot", "Telegram बॉट खोलें")}</a>
</div>
<p class="never"><span class="shield" aria-hidden="true">⛨</span>
  {t("Trinetra never asks for OTP, PIN or money.", "Trinetra कभी OTP, PIN या पैसे नहीं माँगता।")}</p>
"""
    return ui.layout("Check before you pay", body, zone="public", lang=lang or "en", nav=PUBLIC_NAV, current="/")


@lru_cache
def web_store() -> Store | None:
    """The web page answers from the same approved fakes and issued notices as the bot."""
    return Store(settings.data_dir / "trinetra.db")


@lru_cache
def web_evidence() -> EvidenceStore:
    return EvidenceStore(settings.data_dir / "evidence")


held = WebHold()


def lookups(store: Store | None) -> dict:
    return {"known": store.is_known_fake, "issuers": store} if store else {}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return _page()


@app.post("/check", response_class=HTMLResponse)
async def check_form(
    text: str = Form(""),
    lang: str = Form(""),
    file: UploadFile | None = File(None),
    store: Store | None = Depends(web_store),
) -> HTMLResponse:
    lang = lang if lang in ("en", "hi") else ""
    if file is not None and file.filename:
        say = MESSAGES[lang or "en"]
        data = await file.read(MAX_BYTES["pdf"] + 1)
        kind = ocr.sniff(data)  # by magic bytes, never by file name
        if kind is None:
            if sniff_av(data):  # voice and video checks are not built yet: say so, never crash
                if store:
                    store.record_check(Kind.VOICE.value, "web", Verdict.COULD_NOT_CHECK.value)
                return _page(text, say["media_soon"], lang)
            return _page(text, say["unsupported"], lang)
        if len(data) > MAX_BYTES[kind]:
            return _page(text, say["too_large"], lang)
        # OCR blocks for about a second, so keep it off the event loop.
        result = await run_in_threadpool(partial(pipeline.check_file, data, lang=lang or None, **lookups(store)))
        _count(store, result, "web")
        token = held.put(data, Kind.NOTICE, EXTENSIONS[kind], result)
        return _page(text, result.reply, lang, result.verdict.value, result.lang, result.sha256, token)
    if not text.strip():
        return _page(text, "Paste a message to check.", lang)
    text = text[:MAX_TEXT_CHARS]
    result = pipeline.check_text(text, lang=lang or None, **lookups(store))
    _count(store, result, "web")
    token = held.put(text.encode("utf-8"), Kind.TEXT, ".txt", result)
    return _page(text, result.reply, lang, result.verdict.value, result.lang, result.sha256, token)


def _count(store: Store | None, result, channel: str) -> None:
    if store:
        store.record_check(result.kind.value, channel, result.verdict.value)


_votes: dict[str, list[float]] = defaultdict(list)
VOTES_PER_WINDOW = 30
VOTE_WINDOW_SECONDS = 600


def _allow(request: Request, bucket: str) -> bool:
    """Per-client limit on web feedback, disputes and reports: 30 in 10 minutes per kind."""
    key, now_ = f"{bucket}:{client_of(request)}", time.time()
    recent = [t for t in _votes[key] if t > now_ - VOTE_WINDOW_SECONDS]
    ok = len(recent) < VOTES_PER_WINDOW
    if ok:
        recent.append(now_)
    _votes[key] = recent
    return ok


def _is_sha(sha: str) -> bool:
    return len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha)


def _lang(lang: str) -> str:
    return lang if lang in ("en", "hi") else "en"


def _dispute_card(sha: str, verdict: str, token: str, lang: str) -> str:
    """After "Not helpful": was the message actually genuine?"""
    t = LABELS[lang]
    fields = _hidden(sha=sha, verdict=verdict, token=token, lang=lang)
    return (
        f'<section class="card dispute">{ui.notice(FEEDBACK_THANKS_WEB[lang], "info")}'
        f'<h2>{escape(DISPUTE_ASK[lang])}</h2>'
        f'<form method="post" action="/dispute">{fields}<input type="hidden" name="answer" value="genuine">'
        f'<button class="btn" type="submit">{escape(t["genuine_yes"])}</button></form>'
        f'<form method="post" action="/dispute">{fields}<input type="hidden" name="answer" value="other">'
        f'<label class="field">{escape(WEB_TEXT[lang]["note"])}<input name="note" maxlength="{NOTE_MAX_CHARS}"></label>'
        f'<button class="btn btn-ghost" type="submit">{escape(t["genuine_no"])}</button></form></section>'
    )


FEEDBACK_THANKS_WEB = {"en": "Thank you for your feedback.", "hi": "आपकी राय के लिए धन्यवाद।"}


@app.post("/feedback", response_class=HTMLResponse)
def feedback(
    request: Request,
    sha: str = Form(""),
    verdict: str = Form(""),
    vote: str = Form(""),
    token: str = Form(""),
    lang: str = Form(""),
    store: Store | None = Depends(web_store),
) -> HTMLResponse:
    """Web feedback on a verdict: stores only the item hash, the verdict and the vote."""
    ok = vote in ("helpful", "not_helpful") and verdict in VERDICT_CODES and _is_sha(sha)
    if ok and not _allow(request, "vote"):
        ok = False
    if ok and store:
        store.add_feedback(sha, verdict, vote)
    if not ok:
        return _page(reply="We could not record that feedback.")
    if vote == "not_helpful":
        return _page(lang=_lang(lang), result_html=_dispute_card(sha, verdict, token[:64], _lang(lang)))
    return _page(reply=FEEDBACK_THANKS_WEB[_lang(lang)], tone="info")


@app.post("/dispute", response_class=HTMLResponse)
def dispute(
    request: Request,
    sha: str = Form(""),
    verdict: str = Form(""),
    answer: str = Form(""),
    token: str = Form(""),
    note: str = Form(""),
    lang: str = Form(""),
    store: Store | None = Depends(web_store),
) -> HTMLResponse:
    """"Yes, it's genuine" opens a dispute with the verdict and signals; "No" keeps an optional one-line note."""
    lang = _lang(lang)
    if answer not in ("genuine", "other") or verdict not in VERDICT_CODES or not _is_sha(sha):
        return _page(reply="We could not record that.", lang=lang)
    if not _allow(request, "dispute"):
        return _page(reply=WEB_TEXT[lang]["busy"], lang=lang)
    if answer == "other":
        line = " ".join(note.split())[:NOTE_MAX_CHARS]
        if line and store:
            store.add_feedback_note(sha, mask_text(line))
        return _page(reply=NOTE_THANKS[lang], lang=lang, tone="info")
    h = held.get(token[:64])
    result = h.result if h and h.result.sha256 == sha else None
    if store:
        dispute_id, _ = store.add_dispute(
            channel="web", chat_id=None, user_hash=None, lang=lang, sha256=sha,
            kind=result.kind.value if result else "text", verdict=result.verdict.value if result else verdict,
            rule_row=result.rule_row if result else "",
            signals=[[s.code, s.strength.value, s.reason_en] for s in (result.signals if result else [])],
        )
        store.audit("web", "dispute_opened", f"dispute={dispute_id} verdict={verdict}")
    return _page(reply=DISPUTE_THANKS[lang], lang=lang, tone="info")


@app.post("/report", response_class=HTMLResponse)
def report(
    request: Request,
    token: str = Form(""),
    consent: str = Form(""),
    lang: str = Form(""),
    store: Store | None = Depends(web_store),
    evidence: EvidenceStore = Depends(web_evidence),
) -> HTMLResponse:
    """Report from the web page. The held item is copied to the evidence store only after the consent tick."""
    lang = _lang(lang)
    w = WEB_TEXT[lang]
    if consent != "yes":
        return _page(reply=w["consent_needed"], lang=lang)
    if not _allow(request, "report"):
        return _page(reply=w["busy"], lang=lang)
    h = held.get(token[:64])
    if h is None or store is None:
        return _page(reply=w["expired"], lang=lang)
    sha = h.result.sha256
    case = store.already_reported("web", sha)
    if case is not None:
        return _page(reply=w["already"].format(case=case), lang=lang, tone="info")
    try:
        path = evidence.put(h.data, sha, h.ext)
        if h.kind is Kind.TEXT:
            result, fields = pipeline.analyse_text(h.data.decode("utf-8"), sha256=sha, lang=h.result.lang, **lookups(store))
        else:
            result, fields = pipeline.analyse_file(h.data, sha256=sha, lang=h.result.lang, **lookups(store))
        case = store.add_report(NewReport(sha, "web", h.kind, len(h.data), str(path), result, fields, report_payees(fields)))
    except Exception:
        log.exception("web report failed sha256=%s", sha[:12])
        return _page(reply=MESSAGES[lang]["error"], lang=lang)
    store.audit("web", "report", f"case={case} sha256={sha}")
    return _page(reply=w["report_done"].format(case=case), lang=lang, tone="info")


class CheckRequest(BaseModel):
    text: str
    lang: str | None = None


class CheckResponse(BaseModel):
    verdict: str
    reply: str


@app.post("/api/check")
def check_api(req: CheckRequest, store: Store | None = Depends(web_store)) -> CheckResponse:
    # Verdict and reply only: rule rows and signal codes stay internal so probing teaches nothing.
    lang = req.lang if req.lang in ("en", "hi") else None
    result = pipeline.check_text(req.text[:MAX_TEXT_CHARS], lang=lang, **lookups(store))
    _count(store, result, "api")
    return CheckResponse(verdict=result.verdict.value, reply=result.reply)


# --- judge mode: /try -------------------------------------------------------------------------


@lru_cache
def _bot_qr_svg() -> str:
    image = qrcode.make(f"https://t.me/{TELEGRAM_BOT}", image_factory=SvgPathImage, box_size=10, border=2)
    svg = image.to_string(encoding="unicode")
    return svg[svg.index("<svg"):]  # drop the XML declaration so it can sit inside HTML


@app.get("/try", response_class=HTMLResponse)
def try_page() -> HTMLResponse:
    tiles = "".join(
        f'<div class="card sample"><img src="/try/samples/{s.slug}.png" alt="{escape(s.title)}" loading="lazy">'
        f'<h3>{escape(s.title)}</h3><p class="muted small">{escape(s.hint)}</p>'
        f'<a class="btn btn-ghost" href="/try/samples/{s.slug}.png" download="trinetra-{s.slug}.png">Download</a></div>'
        for s in samples.available()
    )
    body = f"""
<section class="hero"><p class="eyebrow">Judge mode</p><h1>Try Trinetra in <span class="accent">one minute</span></h1>
<p>Open the bot on your phone, ask for the samples, and forward them back. You will see a forged notice caught,
a genuine one verified, and an edited copy exposed.</p></section>
<div class="try-grid">
  <section class="card qr-card"><div class="qr" role="img" aria-label="QR code for t.me/{TELEGRAM_BOT}">{_bot_qr_svg()}</div>
    <p class="mono">@{TELEGRAM_BOT}</p>
    <a class="btn" href="https://t.me/{TELEGRAM_BOT}" rel="noopener">Open in Telegram</a></section>
  <ol class="steps">
    <li><strong>Scan the code</strong><span>Your phone opens Trinetra in Telegram. Press Start.</span></li>
    <li><strong>Send YES, then tap “Try a sample”</strong><span>The bot sends three sample notices.</span></li>
    <li><strong>Forward a sample back</strong><span>You get the verdict and the reasons in seconds. Try all three.</span></li>
  </ol>
</div>
<p class="alert alert-info">No Telegram app on this computer? Open it on your phone, or use
<a href="https://web.telegram.org" rel="noopener">web.telegram.org</a>.</p>
<h2>The samples</h2>
<p class="muted">Or download them here and send them to the bot yourself, or upload them on the <a href="/">check page</a>.</p>
<div class="strip samples">{tiles or '<p class="muted">The samples are not set up on this server yet.</p>'}</div>
<p class="never"><span class="shield" aria-hidden="true">⛨</span> Trinetra never asks for OTP, PIN or money.</p>
"""
    return ui.layout("Try the bot", body, zone="public", nav=PUBLIC_NAV, current="/try")


@app.get("/try/samples/{slug}.png")
def sample_file(slug: str) -> Response:
    match = next((s for s in samples.available() if s.slug == slug), None)
    if match is None:
        raise HTTPException(404)
    data = match.path.read_bytes()
    media = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return Response(data, media_type=media, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
