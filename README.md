# Trinetra

**Forward a suspicious notice or voice note. Learn in a minute whether it's real.**

Trinetra (Sanskrit for "three eyes") is a Telegram and WhatsApp bot that checks forwarded financial communications: court and police notices, regulator letters, voice notes from "relatives", and text messages. It replies in Hindi or English with a plain verdict and the reasons behind it, and gives a one-tap path to report the scam.

## The problem

"Digital arrest" scams send forged Supreme Court, CBI, ED or RBI notices and demand money "for verification". Cloned voices of relatives ask for urgent UPI transfers. The victim has minutes to decide, and nothing helps them check before the money moves.

## How it works

Every item goes through three checks:

| Check | Question | Example |
| --- | --- | --- |
| **Source** | Did it come from who it claims? | A "court" using a Gmail address, or asking for money into a private account |
| **Media** | Was it forged or cloned? | Audio spoof detection, document edit traces. Supporting evidence only |
| **Ask** | Would a real authority ask for this? | A payment to a private UPI ID, a 2-hour deadline, "tell no one" |

A fixed rule table, not a model, turns the signals into one of four verdicts:

| Verdict | When |
| --- | --- |
| Verified official | Only with positive proof, such as a valid digital signature. A voice note is never verified |
| Likely fake | Strong signals from two different checks, or a match to a confirmed fake |
| Suspicious | One strong signal, or several weak ones |
| Cannot verify | Nothing conclusive. Never shown as safe |

Design rules:
- **No scores are ever shown.** Every reply names its evidence and says whether a human has reviewed it.
- **A detector alone never decides.** It can push a verdict to "suspicious" at most.
- **The language model only extracts fields and helps word replies.** It never chooses the verdict.

## User flow

1. The user forwards a notice, voice note or message to the bot.
2. The bot replies "Checking…" at once, then sends the verdict within 60 seconds.
3. Reply **1** to report it. The file goes to write-once evidence storage, and similar reports are grouped into one campaign.
4. Reply **2** if already paid: the bot says to call **1930** and the bank immediately.
5. A human reviewer approves each campaign in the review console. Approval produces an evidence packet (PDF and JSON) with each file's SHA-256 hash.

## Privacy and safety

- A consent notice in Hindi and English on first use. Send `DELETE` at any time to erase your data.
- Media is deleted after 24 hours unless reported.
- Aadhaar, PAN and account numbers are masked before storage.
- No contacts, chat history, location, voiceprints or face data are collected.
- Rate limits per user. The bot never asks for an OTP, PIN or payment.
- Trinetra blocks nothing and accuses no one. Every outward action passes a human reviewer.

## Project structure

```
app/              the service: intake, extraction, checks, verdict, replies, console
  extract/        OCR, PDF text and signatures, speech-to-text, field extraction
  checks/         source, ask and media checks
  channels/       Telegram and WhatsApp
tools/            evaluation script
tests/            unit and regression tests
data/test-set/    synthetic test notices, texts and voice scripts with expected verdicts
data/registry/    official domains, known fakes, agencies
```

## Running it

Requires Python 3.11+ and Tesseract with the Hindi language pack (`brew install tesseract tesseract-lang` on macOS, or `apt install tesseract-ocr tesseract-ocr-hin` on Ubuntu).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your tokens
uvicorn app.main:app --reload
```

Open `http://localhost:8000` for the web upload page, and `http://localhost:8000/console` for the review console.

**Telegram bot (local).** Set `TELEGRAM_BOT_TOKEN` and `USER_ID_SALT` in `.env`, then run the long-polling bot in its own terminal:

```bash
set -a; source .env; set +a
python -m app.channels.telegram_poll
```

**Review console.** Set `REVIEWERS` (comma-separated names) and `REVIEWER_PASSWORD` in `.env`, then run the web service in a second terminal and open `http://localhost:8000/console`:

```bash
set -a; source .env; set +a
uvicorn app.main:app --port 8000
```

The bot and the web service share the database in `DATA_DIR`. Approving a case as fake records its file hashes and payees as known fakes in the runtime database, locks its evidence files, and writes a PDF + JSON evidence packet. Until a case is approved, a user's `DELETE` still erases their report. `data/registry/known_fakes.json` is a read-only seed list that the app never writes.

Polling and a webhook cannot run at the same time. The deployed service uses the webhook at `/telegram/webhook` instead, which is off until `TELEGRAM_WEBHOOK_SECRET` is set.

**Offline mode.** With `LLM_MODE=off STT_MODE=off AUDIO_MODEL=off`, everything runs without AWS or internet. That is also how the tests run.

```bash
pytest                      # all tests
python tools/evaluate.py    # metrics on the test set
```

## Tech stack

Python, FastAPI, SQLite, Tesseract OCR, pyHanko (PDF signatures), Amazon Transcribe (Hindi and Indian English), Amazon Bedrock (field extraction), an XLS-R + SLS audio anti-spoofing model, ReportLab (evidence packets). Deployed on AWS in the Mumbai region (`ap-south-1`).

## Acknowledgements

- **Test data:** `data/test-set/` is synthetic. All names, account numbers, UPI IDs and phone numbers in it are invented.
- **Evaluation datasets** (not for commercial use): AIForge-Doc v2 (CC BY-NC-SA 4.0), RealText-V2 (CC BY-NC 4.0), IndicSynth (CC BY-NC 4.0), IndieFake (research use).
- **Models:** XLS-R + SLS audio deepfake detector (MIT licence), Amazon Transcribe, Amazon Bedrock.
- **AI-assisted development:** parts of this project were built with help from Claude and Claude Code (Anthropic).
