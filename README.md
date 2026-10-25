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

Requires Python 3.11+, Tesseract with the Hindi language pack and ffmpeg (`brew install tesseract tesseract-lang ffmpeg` on macOS, or `apt install tesseract-ocr tesseract-ocr-hin ffmpeg` on Ubuntu).

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

**Issuer portal.** Courts, banks and other issuers sign in at `http://localhost:8000/issuer` and issue notices with a verification code and QR code. Anyone can check a code at `/v/<code>`, or forward the notice to the bot. To create the two demo issuers and print their passwords once:

```bash
python tools/seed_demo_issuers.py
python tools/make_demo_summons.py     # a demo summons to issue, in data/runtime/demo/
```

**Judge mode.** `/try` shows a QR code for the bot and the three samples. In the bot, "Try a sample" (or `/samples`) sends them. Point `DEMO_SUMMONS_PATH` and `DEMO_ALTERED_PATH` in `.env` at a stamped demo summons from the issuer portal and its altered copy (`tools/demo_alter_amount.py`).

Polling and a webhook cannot run at the same time. The deployed service uses the webhook at `/telegram/webhook` instead, which is off until `TELEGRAM_WEBHOOK_SECRET` is set.

**Voice notes and videos.** Download the speech-to-text model once (faster-whisper small, about 500 MB, into `models/`), then set `STT_MODE=whisper` in `.env`:

```bash
python tools/download_models.py
```

Recordings up to 3 minutes are converted with ffmpeg, transcribed on the CPU, masked, and checked by the same rules as a text message. A voice note is never "verified", and there is no fake-voice detector: the reply says so. If `AUDIO_SERVICE_URL` is set, that service's `spoof_likely` answer is used as one weak signal; if it is unset or down, it is skipped.

**Offline mode.** With `LLM_MODE=off STT_MODE=off AUDIO_MODEL=off`, everything runs without AWS or internet. That is also how the tests run.

```bash
pytest                      # all tests
python tools/evaluate.py    # metrics on the test set
```

## Tech stack

Python, FastAPI, SQLite, Tesseract OCR, pyHanko (PDF signatures), faster-whisper and ffmpeg (speech to text for Hindi and English, on the server), Amazon Bedrock (field extraction), ReportLab (evidence packets). Deployed on AWS in the Mumbai region (`ap-south-1`).

## Acknowledgements

- **Test data:** `data/test-set/` is synthetic. All names, account numbers, UPI IDs and phone numbers in it are invented.
- **Evaluation datasets** (not for commercial use): AIForge-Doc v2 (CC BY-NC-SA 4.0), RealText-V2 (CC BY-NC 4.0), IndicSynth (CC BY-NC 4.0), IndieFake (research use).
- **Real-world SMS check:** the [SMS Spam Collection](https://doi.org/10.24432/C5CC84) by Tiago A. Almeida and José María Gómez Hidalgo (2011), from the UCI Machine Learning Repository, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Used unmodified, downloaded into `data/external/sms_spam/` (not committed), to measure false alarms on ordinary messages.
- **Models:** XLS-R + SLS audio deepfake detector (MIT licence), Amazon Transcribe, Amazon Bedrock.
- **AI-assisted development:** parts of this project were built with help from Claude and Claude Code (Anthropic).
