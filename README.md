# Trinetra: check before you pay

Trinetra checks the messages scammers use to take money: forged court, police and bank notices, "digital arrest" threats, and voice notes from a "relative" in trouble. Forward a notice, message, voice note or video to the Telegram bot, or upload it on the website, and within a minute you get a plain answer in Hindi or English: is it likely fake, suspicious, verified, or something we cannot confirm, and why. It is built for ordinary people, many of them older and not technical, who have a few minutes to decide whether to pay. Trinetra never asks for an OTP, PIN or payment.

## Try it

- **Telegram:** open [@Trinetra_check_bot](https://t.me/Trinetra_check_bot), agree to the short privacy notice, and forward anything you want checked. "Try a sample" sends three sample notices to forward back.
- **Website:** upload a photo, PDF, voice note or video, paste a message, or record a voice note in the browser. The `/try` page has a QR code for the bot and the samples. When Trinetra is self-hosted with `./start.sh`, the public link is a temporary tunnel address that changes on every run.

## What it checks

- **Notices:** photos (JPG, PNG) and PDFs, read with OCR in Hindi and English.
- **Text:** SMS, WhatsApp or email text, in English, Hindi (Devanagari) and Hinglish.
- **Links, UPI IDs and phone numbers** sent on their own: Trinetra says what it found (an official government or bank domain or not, a SEBI-validated UPI handle or not, an owner we cannot confirm, or the 1930 helpline). These notes never move a verdict.
- **Voice notes and audio** (OGG, MP3, M4A, WAV and others, up to 3 minutes): transcribed on the server with faster-whisper, then checked like text. The reply shows what we heard.
- **Videos and round video messages:** the speech is transcribed and about six frames are read for on-screen text. Both are checked together, and the reply shows what we heard and saw.

## How it works

Every item goes through the same pipeline: read it (OCR, transcript or the text itself), mask ID numbers, extract the claimed sender, payees, amounts, links and deadlines, run the checks, apply a fixed rule table, reply.

| Check | Question | Examples of what it looks for |
| --- | --- | --- |
| **Source** | Did it come from who it claims? | A "court" asking for money into a private UPI ID; an official body using Gmail, a mobile number or a non-government website; a famous name or "your bank" without saying which; a relative in trouble in a voice note; a Trinetra issuer code that is valid, altered, revoked or missing |
| **Ask** | Would a real authority ask for this? | Pay or send money with pressure (a deadline, secrecy, an arrest threat, a refund promise, a reward); share an OTP, PIN or KYC details by message; your account will be blocked |
| **Media** | Was it forged or cloned? | Only an optional external voice-spoof service, as one weak signal. It is off unless configured. There is no fake-voice or fake-face detector (see Limitations) |

The rule table, first match wins:

| Row | When | Verdict |
| --- | --- | --- |
| R0 | Nothing readable | Could not check |
| R1 | Matches a reviewer-confirmed fake (same file, payee or link) | Likely fake |
| R1A | Carries a valid issuer code, but the notice was altered | Likely fake |
| R2 | Not a voice note or video, source proven by a valid issuer code, and no strong warning | Verified official |
| R3 | Strong warnings from two different checks | Likely fake |
| R4 | A strong warning from one check, or two or more weak ones | Suspicious |
| R5 | Anything else | Cannot verify |

Rules that always hold:

- **No scores.** Replies give a verdict, the reasons in plain words, what to do, and whether a person has reviewed it.
- **Voice notes and videos are never verified.** The reply says we check what the message asks for, not whether the voice or face is real.
- **"Cannot verify" never reads as safe.** It says so: "That does not mean it is safe."
- **AI never decides.** Speech-to-text and OCR turn media into text. The verdict always comes from the rule table in `app/verdict.py`, a pure function with no model calls.

## Verified with proof

A notice is called verified only when its source is proven. Trinetra does this through the issuer portal:

- Courts, banks, police and government offices sign in at `/issuer` with their own accounts, which only ever see their own organisation's notices.
- Before issuing, the portal runs the normal check on the notice. It refuses one that would be rated likely fake, or that names a different body as the sender. One rated suspicious is issued only after the issuer confirms that it is genuine and that its payment details are official.
- Each issued notice gets a random code (`TRN-XXXX-XXXX`) and a QR code linking to `/v/<code>`. That public page shows only the issuer, the issue date, the notice type and whether it is still valid, and it is rate-limited so codes cannot be guessed.
- Trinetra keeps a fingerprint of the notice's text and key fields (case numbers, amounts, payees, dates), never the notice itself.
- When someone forwards the notice, Trinetra reads the code from the QR code or the printed text:
  - text and key fields match what was issued: **verified official**;
  - a key field was changed, for example the amount or the payee: **likely fake** (R1A);
  - the code does not exist, or the issuer has revoked (cancelled) the notice: a strong warning;
  - the notice names an issuer that uses codes, but carries none: a strong warning.
- Every portal action is written to the audit log.

## Human review and evidence

- **Reports.** After a verdict, a person can tap Report (or reply `1`; on the website, tick the consent box). They are asked to confirm, then the file is stored as evidence under its SHA-256 hash, which is checked again on every read.
- **Campaigns.** Reports of the same file, or with the same payee, are grouped into one case. The review console at `/console` lists cases by number of reports.
- **Logged reveal.** Case pages mask payees, phone numbers and emails. "Reveal full details" asks the reviewer for a reason and writes it to the audit log before showing anything.
- **Approval.** A named reviewer chooses Approve as fake, Mark not fake, or Needs more info. Approving as fake:
  - adds the case's files, payees and non-official link domains to the known fakes, so later forwards are answered at once (R1);
  - makes its evidence files read-only;
  - produces an evidence packet as PDF and JSON: the timeline of reports, each file's SHA-256 hash, the extracted fields, the signals, rule row and verdict, the engine version, and the reviewer and time. The packet says it is "Prepared to support a certificate under Section 63(4), Bharatiya Sakshya Adhiniyam. This packet is not itself a certificate."
  - Packet downloads are logged.
- **Disputes.** After "Not helpful", the bot and the website ask whether the message was actually genuine. A dispute keeps the verdict and the signals, not the message. A reviewer confirms it genuine or keeps the verdict, with a required note, and the bot tells the person the outcome in their language. A confirmed dispute is added to a list for the team; it never changes the rules by itself.
- **Stats.** `/console/stats` shows usage as counts only: checks per hour, by type, channel and verdict, feedback, and open and resolved disputes.
- **Three zones:**
  - **public:** the check page, `/try` and `/v/<code>`;
  - **issuer portal:** `/issuer`;
  - **reviewer console:** `/console`, marked "Restricted: reviewers only".

  The issuer portal and the console each have their own login, and every review action is in the audit log.

## Privacy and safety

- **Consent first.** The bot shows a short privacy notice in Hindi or English. Anything sent before agreeing is held in memory only, for up to 30 minutes, and files are not even downloaded until the person agrees.
- **Masked before storage.** Aadhaar, PAN and long account numbers are masked. Chat IDs are stored only as salted hashes. The "We heard" and "On screen" lines are shown once and never stored.
- **Short retention.** Files are deleted after 24 hours unless reported (`MEDIA_TTL_HOURS`), by a job that runs every 10 minutes. The website does not keep what you upload or paste unless you report it.
- **`DELETE`** erases a person's data at once, except files already in an approved evidence packet, and the bot says so.
- **Rate limits.** 10 items per person per hour and 50 per day on the bot. Limits per client on the website's report, feedback and dispute forms, on `/v/<code>` lookups, and on login attempts.
- **Strict file checks.** Files are checked by their content, not their name. Size caps are 5 MB for images, 16 MB for recordings and 100 MB for PDFs; PDFs are read up to 10 pages and recordings up to 3 minutes.
- **What we don't collect:** contacts, chat history, location, voiceprints or face data. Feedback stores only the item's hash, the verdict and the button, plus an optional masked note.

## Measured results

Full details, per-item tables and rerun commands are in [docs/RESULTS.md](docs/RESULTS.md). All runs are offline.

- **Synthetic test split:**
  - 33 of 33 items got the expected verdict;
  - no forged notice was called verified and no genuine one was called likely fake;
  - 18 of 18 fakes were flagged and 0 of 15 genuine items were flagged;
  - median time per check 0.81 s.
- **Public SMS sample:** 500 messages from the UCI SMS Spam Collection.
  - 0 of 450 normal messages were flagged.
  - 1 of 50 spam messages was flagged. That is expected: most of this spam is adverts and prize draws, not someone posing as an official body or bank and asking for money.
- **Adversarial set:** 30 short messages a tester might try, 20 scams and 10 normal.

  | | Before the adversarial signals | After |
  | --- | --- | --- |
  | As expected | 12 of 30 | 30 of 30 |
  | Scams flagged | 4 of 20 | 20 of 20 |
  | Normal messages flagged | 1 of 10 | 0 of 10 |

All three were rerun after the later voice, video and negation changes, with the same totals.

Caveats:

- **The test set is small and synthetic,** so one miss moves accuracy by about 3 points.
- **The test split holds the same notices as the dev split**, as WhatsApp-compressed copies, phone photos and crops. It measures robustness, not unseen scams.
- **The adversarial "after" column was built after seeing the "before" results** on the same cases, so it shows the fixes work, not performance on new scams.
- **Voice notes and videos have no measured results yet.** The test set's voice recordings have not been made.

## Limitations

- **No fake-voice or fake-face detector.**
  - Detectors trained on known voice and face generators tend to miss new ones, and they are unreliable on compressed phone audio and video.
  - A wrong "this voice is real" would do more harm than saying nothing.
  - So Trinetra checks what the message asks for and tells the person to call back on a number they already have. An external spoof service can be plugged in as one weak signal only.
- **Telegram and the website only.** There is no WhatsApp channel yet.
- **Self-hosted today:** one Python service with SQLite, local evidence files and a temporary tunnel link. The AWS design below is the production plan, not what runs now.
- **Small synthetic test set** (see above). No document-forgery or PDF-signature check yet; "verified" relies on issuer codes alone.
- **Speech-to-text and OCR make mistakes.** Common whisper mistakes in Hindi are corrected before the checks, but an unclear recording or photo can still be misread.
- **The review console uses one shared password** for all reviewers.

## Architecture

```
app/
  channels/    Telegram: long polling, webhook, parallel chats in order within each chat
  intake.py    consent, rate limits, type and size checks, hash cache, buttons, reports, disputes
  extract/     OCR (Tesseract), speech (ffmpeg + faster-whisper), video frames, transcript fixes,
               field extraction, masking
  checks/      source, ask, issuer codes, lone links and payees, Hindi + English keyword lists
  verdict.py   the rule table: a pure function, no I/O, no models
  reply.py     reply templates in Hindi and English
  pipeline.py  read -> extract -> checks -> verdict -> reply
  portal.py    issuer portal and the public /v/<code> page
  console.py   review console: cases, reveal, approval, packets, disputes, stats, audit log
  store.py     SQLite: users, items, results, cases, reviews, disputes, audit log
  evidence.py  evidence files under their SHA-256, read-only once approved
  packet.py    PDF + JSON evidence packets
  main.py      the website and the HTTP routes
tools/         evaluation, model download, demo issuers and samples
data/          test set, adversarial cases, registry of official domains, payees and agencies
```

Production design on AWS (Mumbai, `ap-south-1`). **Planned, not used yet:**

| Today | On AWS |
| --- | --- |
| One FastAPI service | AWS Lambda functions behind the webhooks |
| SQLite | Amazon DynamoDB |
| Local evidence folder, read-only on approval | Amazon S3 with Object Lock |
| Secret salt and passwords in `.env` | AWS KMS for keys and encryption |
| Reviewer and issuer password logins | Amazon Cognito |
| faster-whisper on the server | Amazon Transcribe |
| Regex field extraction | Amazon Bedrock to extract fields and word replies, never to choose the verdict |

## Run it yourself

Requirements:

- Python 3.11;
- Tesseract with English and Hindi;
- ffmpeg;
- for `./start.sh`, which is written for macOS: `cloudflared`.

```bash
brew install tesseract tesseract-lang ffmpeg cloudflared   # Ubuntu: apt install tesseract-ocr tesseract-ocr-hin ffmpeg
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tools/download_models.py      # faster-whisper small, about 500 MB, into models/
cp .env.example .env                 # then fill it in
```

Keys in `.env`:

| Key | What it is |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `USER_ID_SALT` | A long random secret for hashing chat IDs: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `REVIEWERS`, `REVIEWER_PASSWORD` | Reviewer names (comma-separated) and the console password |
| `STT_MODE` | `whisper` to check voice notes and videos; `off` to turn that off |
| `DATA_DIR`, `MEDIA_TTL_HOURS` | Where the database and files live; how long unreported files are kept (24) |
| `PUBLIC_BASE_URL` | The public address for `/v/<code>` links in QR codes. `./start.sh` sets it |
| `DEMO_SUMMONS_PATH`, `DEMO_ALTERED_PATH` | Sample notices for "Try a sample" |
| `AUDIO_SERVICE_URL` | Optional voice-spoof service, used as one weak signal |
| `TELEGRAM_WEBHOOK_SECRET` | Only when Telegram should call a webhook instead of polling |

The WhatsApp, S3 and Bedrock keys in `.env.example` are placeholders for the production design and are not used yet.

Start everything:

```bash
./start.sh       # tunnel, website, bot; prints the public link
./restart.sh     # restart the website and bot after a change; the link stays the same
```

Press Ctrl+C twice within 3 seconds to stop. To create the two demo issuers and a demo notice:

```bash
python tools/seed_demo_issuers.py
python tools/make_demo_summons.py
```

Tests and evaluations (all offline; no test uses the network):

```bash
pytest
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off python tools/evaluate.py --split test
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off python tools/evaluate.py --sms data/external/sms_spam/SMSSpamCollection --sample 500 --seed 20261024
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off python tools/evaluate.py --adversarial data/adversarial/cases.csv --compare data/adversarial/before.json
```

The SMS check needs the dataset downloaded into `data/external/sms_spam/` first (see Acknowledgements).

## Acknowledgements

- **Test data:** `data/test-set/` and `data/adversarial/` are synthetic. All names, account numbers, UPI IDs and phone numbers in them are invented.
- **Real-world SMS check:** the [SMS Spam Collection](https://doi.org/10.24432/C5CC84) by Tiago A. Almeida and José María Gómez Hidalgo (2011), from the UCI Machine Learning Repository, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Used unmodified, downloaded into `data/external/sms_spam/` (not committed), to measure false alarms on ordinary messages.
- **Speech to text:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT licence) with the Whisper small model by OpenAI (MIT licence), as converted by Systran (`Systran/faster-whisper-small`).
- **OCR and media:** Tesseract OCR, ffmpeg.
- **Fonts:** Domine, IBM Plex Sans, IBM Plex Sans Devanagari and IBM Plex Mono, under the SIL Open Font License 1.1, self-hosted from the @fontsource packages (licences in `app/static/fonts/`).
- **AI-assisted development:** parts of this project were built with help from Claude and Claude Code (Anthropic).
