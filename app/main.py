"""FastAPI app: web upload page and the Telegram webhook (more routes arrive with later milestones)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from functools import lru_cache, partial
from html import escape

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import pipeline
from app.channels.telegram import TelegramAPI, parse_update
from app.config import settings
from app.intake import Intake
from app.store import Store

app = FastAPI(title="Trinetra")


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
    return TelegramDeps(TelegramAPI(settings.telegram_bot_token), Intake(store, settings.user_id_salt), settings.telegram_webhook_secret)


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
    msg = parse_update(await request.json())
    if msg is not None:
        # Answer Telegram at once; the "Checking…" message and the verdict are sent from the background task.
        background.add_task(deps.intake.handle, msg, partial(deps.api.send_message, msg.chat_id))
    return {"ok": True}

MAX_TEXT_CHARS = 5000

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trinetra</title>
<style>
  :root {{ --bg: #fafaf7; --fg: #1d1d1b; --muted: #5f5f5a; --line: #d9d8d0; --accent: #1f4e79; --card: #fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #161615; --fg: #ecebe6; --muted: #a3a29b; --line: #3a3a36; --accent: #8ab4e0; --card: #1f1f1d; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; }}
  main {{ max-width: 640px; margin: 0 auto; padding: 24px 16px 48px; }}
  h1 {{ margin: 0 0 4px; font-size: 1.6rem; }}
  p.lead {{ margin: 0 0 20px; color: var(--muted); }}
  label {{ display: block; font-weight: 600; margin: 16px 0 6px; }}
  textarea {{ width: 100%; min-height: 160px; padding: 10px; font: inherit; color: inherit;
             background: var(--card); border: 1px solid var(--line); border-radius: 6px; }}
  input[type=file], select {{ font: inherit; color: inherit; }}
  button {{ margin-top: 16px; padding: 10px 18px; font: inherit; font-weight: 600; color: #fff;
           background: var(--accent); border: 0; border-radius: 6px; cursor: pointer; }}
  .reply {{ margin-top: 24px; padding: 16px; white-space: pre-wrap; background: var(--card);
           border: 1px solid var(--line); border-radius: 6px; }}
  .note {{ margin-top: 24px; font-size: .9rem; color: var(--muted); }}
</style>
</head>
<body>
<main>
  <h1>Trinetra</h1>
  <p class="lead">Paste a suspicious message to check it before you pay.</p>
  <form method="post" action="/check" enctype="multipart/form-data">
    <label for="text">Message text</label>
    <textarea id="text" name="text" maxlength="{max_chars}">{text}</textarea>
    <label for="file">Or upload a notice (image or PDF)</label>
    <input id="file" name="file" type="file" accept="image/png,image/jpeg,application/pdf">
    <label for="lang">Reply language</label>
    <select id="lang" name="lang">
      <option value="">Same as the message</option>
      <option value="en"{en_sel}>English</option>
      <option value="hi"{hi_sel}>हिन्दी</option>
    </select>
    <div><button type="submit">Check</button></div>
  </form>
  {reply}
  <p class="note">Trinetra never asks for an OTP, PIN, password or payment. We do not keep what you paste here.</p>
</main>
</body>
</html>
"""


def _page(text: str = "", reply: str | None = None, lang: str = "") -> HTMLResponse:
    return HTMLResponse(
        PAGE.format(
            max_chars=MAX_TEXT_CHARS,
            text=escape(text),
            en_sel=" selected" if lang == "en" else "",
            hi_sel=" selected" if lang == "hi" else "",
            reply=f'<div class="reply" role="status">{escape(reply)}</div>' if reply else "",
        )
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return _page()


@app.post("/check", response_class=HTMLResponse)
async def check_form(
    text: str = Form(""), lang: str = Form(""), file: UploadFile | None = File(None)
) -> HTMLResponse:
    lang = lang if lang in ("en", "hi") else ""
    if file is not None and file.filename:
        # Image and PDF reading arrives with OCR in milestone 3.
        return _page(text, "Image and PDF checks are not available yet. Paste the message text instead.", lang)
    if not text.strip():
        return _page(text, "Paste a message to check.", lang)
    result = pipeline.check_text(text[:MAX_TEXT_CHARS], lang=lang or None)
    return _page(text, result.reply, lang)


class CheckRequest(BaseModel):
    text: str
    lang: str | None = None


class CheckResponse(BaseModel):
    verdict: str
    reply: str


@app.post("/api/check")
def check_api(req: CheckRequest) -> CheckResponse:
    # Verdict and reply only: rule rows and signal codes stay internal so probing teaches nothing.
    result = pipeline.check_text(req.text[:MAX_TEXT_CHARS], lang=req.lang if req.lang in ("en", "hi") else None)
    return CheckResponse(verdict=result.verdict.value, reply=result.reply)
