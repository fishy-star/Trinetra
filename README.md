# Trinetra

A WhatsApp/Telegram bot built for the Amazon AI Cyber Security Hackathon, Track 3 (Deepfake Detection for Financial Communications).

Trinetra lets a person forward a suspicious **notice** (image or PDF), **voice note**, or **text message**, and replies within 60 seconds in Hindi or English with three checks:

1. **Source** — did it come from who it claims?
2. **Media** — was it forged or cloned? (supporting evidence only)
3. **Ask** — would a real authority ask for this?

The verdict (`verified_official`, `cannot_verify`, `suspicious`, `likely_fake`, or `could_not_check`) is always decided by a fixed rule table in code — never by a language model. No scores are ever shown to users. Suspicious or fake notices can be reported; reported cases are clustered into campaigns and reviewed by a human, who approves an evidence packet (PDF + JSON with the file's SHA-256 hash).

The threat this targets: "digital arrest" scams (forged Supreme Court / CBI / ED / RBI notices demanding money "for verification") and cloned-voice relatives asking for urgent UPI money.

Full build spec, architecture, verdict rules, reply templates, privacy rules, and build order: see [CLAUDE.md](CLAUDE.md).

## Status

Not yet built. See the Build order table in [CLAUDE.md](CLAUDE.md) for the milestone plan.

## Setup

Requires Python 3.11 and Tesseract with English and Hindi language data (`brew install tesseract tesseract-lang` on macOS, `apt install tesseract-ocr tesseract-ocr-hin` on Debian/Ubuntu).

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Secrets come only from environment variables. Keep them in a local `.env` (git-ignored) and never commit them. Runtime data (SQLite, media, local evidence store, packets) lives in `DATA_DIR`, which defaults to `data/runtime/` and is git-ignored. `data/test-set/` and `data/registry/` are tracked.

## Running

- **Offline (no AWS, no LLM):** `LLM_MODE=off STT_MODE=off AUDIO_MODEL=off uvicorn app.main:app` — this is also how tests and the eval script run.
- **With AWS:** set `AWS_REGION=ap-south-1`, `S3_BUCKET`, `BEDROCK_MODEL_ID`, and set `LLM_MODE=bedrock`, `STT_MODE=aws` as needed. See the Environment variables section in [CLAUDE.md](CLAUDE.md) for the full list.

(This section will be kept current as the implementation progresses — see "Working rules for Claude Code" in CLAUDE.md.)

## What was built before vs. during the event

_To be filled in._

## AI tools used

Claude, Claude Code.
