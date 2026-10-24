# Trinetra

Trinetra is a Telegram and WhatsApp bot that checks suspicious financial messages. Forward it a **notice** (image or PDF), a **voice note** or a **text message**, and within 60 seconds it replies in Hindi or English with what it found and what to do next.

It targets two common scams:

- **"Digital arrest" notices:** forged Supreme Court, CBI, ED or RBI notices that demand money "for verification".
- **Cloned voices:** a voice note that sounds like a relative asking for urgent UPI money.

## How it works

Every item goes through three checks:

1. **Source:** did it come from who it claims? For example, a "court notice" that asks you to pay a private UPI ID, or gives a Gmail address as the contact, fails this check.
2. **Ask:** would a real authority ask for this? Payment demands combined with secrecy, a short deadline, an arrest threat or a promised refund are red flags.
3. **Media:** was the file edited or the voice cloned? Detector results are supporting evidence only and are never enough to call something fake on their own.

A fixed rule table in code turns the checks into one of five results:

| Result | Meaning |
| --- | --- |
| Likely fake | Strong evidence from two different checks, or it matches a confirmed fake |
| Suspicious | One strong warning sign, or several weaker ones |
| Cannot verify | Nothing proves it is fake, but nothing proves it is real either. This never means "safe" |
| Verified official | Positive proof, such as a valid digital signature or a match with a known official notice. Voice notes are never verified |
| Could not check | The file could not be read |

A language model may help extract details and phrase replies, but it never chooses the result. Replies never show scores or model names. Each reply names its evidence and says whether a person has reviewed it.

After a reply, the user can send:

- `1` to report the item. Reported items are grouped into campaigns (same file or same payee) and reviewed by a person, who can approve a PDF + JSON evidence packet that records each file's SHA-256 hash.
- `2` if they already paid. Trinetra tells them to call 1930 and their bank right away.

Repeat forwards of the same file are answered instantly from a cache. Once a campaign is confirmed fake, every later forward of it is answered at once.

## Privacy

- Users see a consent notice in Hindi and English before their first check.
- Files are deleted after 24 hours unless the user reports them.
- Aadhaar, PAN and account numbers are masked before anything is stored.
- No contacts, chats, location, voiceprints or face data are collected.
- Sending `DELETE` erases the user's data at once, except items already in an approved evidence packet.
- Trinetra never asks for an OTP, PIN, password or payment.

## Status

Under development.

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

Offline, with no AWS services and no language model (this is also how the tests and the evaluation script run):

```sh
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off uvicorn app.main:app
```

This serves the webhooks, a web upload page and the review console at `/console`.

With AWS, set `AWS_REGION=ap-south-1`, `S3_BUCKET` and `BEDROCK_MODEL_ID`, then turn on `LLM_MODE=bedrock` and `STT_MODE=aws` as needed.

### Configuration

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` | WhatsApp Cloud API credentials |
| `AWS_REGION` | AWS region, `ap-south-1` |
| `S3_BUCKET` | Write-once evidence bucket (S3 Object Lock) |
| `BEDROCK_MODEL_ID` | Model used for field extraction when `LLM_MODE=bedrock` |
| `LLM_MODE` | `bedrock` or `off` |
| `STT_MODE` | Speech-to-text: `aws`, `whisper` or `off` |
| `AUDIO_MODEL` | Voice-spoof model: `xlsr` or `off` |
| `REVIEWERS` | Comma-separated usernames allowed into the review console |
| `DATA_DIR` | Where runtime data is stored |
| `MEDIA_TTL_HOURS` | How long unreported media is kept, default `24` |

## Evaluation

`python tools/evaluate.py` runs the synthetic test set in `data/test-set/` and prints accuracy per result, any hard failures, results split by language and format, and latency.

## AI tools used

Claude, Claude Code.
