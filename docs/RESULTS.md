# Trinetra: measured accuracy (test split)

| | |
| --- | --- |
| Split | test |
| Run at (UTC) | 2026-10-24 17:38 |
| Code | commit fd251fb (with uncommitted changes) |
| Engine | 0.2.0 |
| OCR | tesseract 5.5.3 (eng+hin) |
| Modes | LLM_MODE=off, STT_MODE=off, AUDIO_MODEL=off |

Every item in the split was run once through the full pipeline (OCR, extraction, checks, rule table), offline. Expected verdicts come from `data/test-set/labels.csv`; no rules, signals or thresholds were changed after seeing these results.

## Totals

- **33** items checked; **33** got the expected verdict (**100%**).
- For cropped notices whose payee or contact may be cut off, the label accepts either *likely fake* or *suspicious*; both count as expected.
- Time per check: median **0.81 s**, slowest **1.03 s** (95th percentile 1.01 s). The target is a reply within 60 s.

## Never-allowed errors (must_never_be)

The most important number: a fake notice must never be called *verified official*, and a genuine one must never be called *likely fake*.

**0 violations** (0 fakes called verified official, 0 genuine items called likely fake).

Genuine items flagged at all (likely fake or suspicious): 0 of 15. Fakes caught (likely fake or suspicious): 18 of 18 (100%; target 90%).

## Expected verdict vs what Trinetra gave

| Expected ↓ / Given → | likely_fake | suspicious | cannot_verify | verified_official | could_not_check | Total |
| --- | --- | --- | --- | --- | --- | --- |
| **cannot_verify** | · | · | 15 | · | · | 15 |
| **likely_fake** | 10 | · | · | · | · | 10 |
| **likely_fake_or_suspicious** | 5 | 1 | · | · | · | 6 |
| **suspicious** | · | 2 | · | · | · | 2 |

## By format

The originals (clean PNG and PDF) are all in the dev split, used to build the rules, so they are not measured here. This split holds the same notices as WhatsApp-compressed copies, phone photos and crops.

| | Items | Expected verdict | Accuracy | Never-allowed errors |
| --- | --- | --- | --- | --- |
| Cropped (top part only) | 11 | 11 | 100% | 0 |
| Phone photo of a printout | 11 | 11 | 100% | 0 |
| WhatsApp-compressed | 11 | 11 | 100% | 0 |

## By language

| | Items | Expected verdict | Accuracy | Never-allowed errors |
| --- | --- | --- | --- | --- |
| Hindi + English (bilingual) | 3 | 3 | 100% | 0 |
| English | 21 | 21 | 100% | 0 |
| Hindi | 9 | 9 | 100% | 0 |

## Every miss, with why

None.

## Not included yet

- The voice checks (V1, V2, V3, V4) are not included yet: the recordings do not exist yet. Voice notes and videos are transcribed (faster-whisper) and checked by the same rules as text, with no fake-voice detector.

## Bugs fixed during this run

None. Nothing crashed and no item failed because of a bug.

## How to read these numbers

- The test set is synthetic and small, so one miss moves accuracy by about 3 points.
- The test split shows the same notices as the dev split, transformed. It measures how well checks survive compression, photos and crops, not how they do on scam notices never seen before.

## How to rerun

From the repository root:

```sh
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --split test --markdown docs/RESULTS.md
```

## Per item

| Item | Format | Language | Truth | Expected | Given | Rule | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `variants/F1_sc_warrant_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.99 s |
| `variants/F1_sc_warrant_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 1.01 s |
| `variants/F1_sc_warrant_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.83 s |
| `variants/F2_cbi_deposit_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | forged | likely_fake | likely_fake | R3 | 1.03 s |
| `variants/F2_cbi_deposit_hi__photo.jpg` | Phone photo of a printout | Hindi | forged | likely_fake | likely_fake | R3 | 1.02 s |
| `variants/F2_cbi_deposit_hi__crop.jpg` | Cropped (top part only) | Hindi | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.89 s |
| `variants/F3_ed_freeze_bi__whatsapp.jpg` | WhatsApp-compressed | Hindi + English (bilingual) | forged | likely_fake | likely_fake | R3 | 0.99 s |
| `variants/F3_ed_freeze_bi__photo.jpg` | Phone photo of a printout | Hindi + English (bilingual) | forged | likely_fake | likely_fake | R3 | 0.96 s |
| `variants/F3_ed_freeze_bi__crop.jpg` | Cropped (top part only) | Hindi + English (bilingual) | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.86 s |
| `variants/F4_dpb_surety_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.83 s |
| `variants/F4_dpb_surety_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 0.92 s |
| `variants/F4_dpb_surety_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.75 s |
| `variants/F5_rbi_penalty_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.81 s |
| `variants/F5_rbi_penalty_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 0.71 s |
| `variants/F5_rbi_penalty_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.66 s |
| `variants/H2_sim_police_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | suspicious | suspicious | R4 | 0.73 s |
| `variants/H2_sim_police_en__photo.jpg` | Phone photo of a printout | English | forged | suspicious | suspicious | R4 | 0.74 s |
| `variants/H2_sim_police_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | suspicious | R4 | 0.63 s |
| `variants/G1_court_summons_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.78 s |
| `variants/G1_court_summons_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/G1_court_summons_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.65 s |
| `variants/G2_bank_rekyc_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.81 s |
| `variants/G2_bank_rekyc_hi__photo.jpg` | Phone photo of a printout | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.76 s |
| `variants/G2_bank_rekyc_hi__crop.jpg` | Cropped (top part only) | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.72 s |
| `variants/G3_tax_intimation_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.76 s |
| `variants/G3_tax_intimation_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.81 s |
| `variants/G3_tax_intimation_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.65 s |
| `variants/H1_mf_sip_valid_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.73 s |
| `variants/H1_mf_sip_valid_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/H1_mf_sip_valid_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.63 s |
| `variants/H3_police_awareness_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.85 s |
| `variants/H3_police_awareness_hi__photo.jpg` | Phone photo of a printout | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/H3_police_awareness_hi__crop.jpg` | Cropped (top part only) | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.71 s |

## Full console output

```
Trinetra evaluation, split=test: 33 items run, 4 skipped

Accuracy per expected verdict
                             items  correct  accuracy  hard fails
  cannot_verify                 15       15      100%           0
  likely_fake                   10       10      100%           0
  likely_fake_or_suspicious      6        6      100%           0
  suspicious                     2        2      100%           0

By language
                             items  correct  accuracy  hard fails
  bi                             3        3      100%           0
  en                            21       21      100%           0
  hi                             9        9      100%           0

By format
                             items  correct  accuracy  hard fails
  crop_jpg                      11       11      100%           0
  photo_jpg                     11       11      100%           0
  whatsapp_jpg                  11       11      100%           0

Targets
  forged rated verified_official   0  (target 0)
  forged rated fake or suspicious  18/18 = 100%  (target 90%)
  genuine rated likely_fake        0  (target 0)
  genuine flagged (false positives) 0/15

Latency  median 0.81 s   95th percentile 1.01 s  (target median <= 60 s)

Hard failures: 0
False positives: 0
Wrong verdicts: 0
Skipped voice/V1_clone_son_hi.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V2_clone_uncle_en.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V3_real_son_hi.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V4_real_uncle_en.ogg (record it): voice notes are checked from milestone 5 (not built yet)
```

## Real-world check: public SMS dataset

A check on real messages Trinetra was never built on: the [UCI SMS Spam Collection](https://doi.org/10.24432/C5CC84) (Almeida and Hidalgo, 2011; CC BY 4.0), 5,574 English SMS labelled *ham* (normal) or *spam*. A fixed-seed random sample of **500** messages (seed 20261024) was run through the same text pipeline and rule table as everything else. No rules were changed for, or after, this check.

Run at 2026-10-24 17:38 UTC, engine 0.2.0. Median time per message 0.000 s, slowest 0.001 s.

| | Messages | Flagged (likely fake or suspicious) | likely_fake | suspicious | cannot_verify | verified_official | could_not_check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Normal (ham) | 450 | **0 (0.0%)**, false alarms | 0 | 0 | 446 | 0 | 4 |
| Spam | 50 | **1 (2.0%)** | 0 | 1 | 49 | 0 | 0 |

**Spam is not the same as financial scams.** Most spam in this dataset is prize draws, ringtone offers, dating lines and adverts. Trinetra looks for one thing: a message that claims to be an official body or a bank and pushes you to pay. So most spam getting *cannot verify* is expected, and the spam number is not a detection rate. The number that matters here is the false alarms on normal messages.

### Normal messages flagged (0)

None.

### How to rerun

Download the zip from the UCI page into `data/external/sms_spam/` (git-ignored), unzip it, then:

```sh
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --sms data/external/sms_spam/SMSSpamCollection --sample 500 --seed 20261024 --results docs/RESULTS.md
```

## Adversarial check (before and after)

Thirty short messages a judge might try: 20 scams written from common real-world patterns (relatives and celebrities asking for money, bank and KYC phishing, lotteries, job and task scams, parcels at customs, electricity cut-offs, fake police texts, "pay a little, get a lot") and 10 normal messages that must not be flagged, in English, Hindi and Hinglish. The cases are in `data/adversarial/cases.csv` (SHA-256 `0c8c55c003d2cb20…`) and were written, and the "before" run recorded (2026-10-24 17:27 UTC, engine 0.2.0), before any signal was added.

| | Before | After |
| --- | --- | --- |
| As expected | 12/30 | 30/30 |
| Scams flagged (likely fake or suspicious) | 4/20 | 20/20 |
| Normal messages flagged (false alarms) | 1/10 | 0/10 |

**How to read the after column.** The new signals were designed after seeing the before results on these same cases, so the after column shows the fixes work, not how Trinetra does on unseen scams. The independent checks are the unchanged test split and the public SMS sample, both rerun after the change (sections above).

### What changed

- New STRONG `ask.money_request`: someone asks you to send, give or transfer money (any payee, English, Hindi or Hinglish such as "paise bhejo"), together with something pushing you: urgency, secrecy, a threat, a reward or a claimed identity. Without that push it does not fire, so a friend splitting a bill is not flagged.
- New STRONG `ask.credential_request`: verify your identity, update KYC or PAN, share an OTP, PIN or CVV, confirm card details, re-activate your account (including "KYC update karo", "OTP batao"). Not when the sentence says "never" or sends you to the branch.
- New WEAK `ask.reward_for_payment`: a prize, lottery, big return or "I will give you X" alongside a request for money.
- New WEAK `source.claimed_identity_unverifiable`: claims to be a famous person, or says "your bank" without naming it.
- New WEAK `ask.account_threat`: account locked, blocked or suspended, a service cut off, "suspicious activity" (English, Hindi, Hinglish).
- Pressure words added to the urgency signal: "fast", "trust me", "asap", "abhi", "jaldi", "turant".
- Legal-threat words added to the arrest-threat signal: "clear your name", "legal action", "a case has been filed against you", and "cyber crime" units as a police sender. Added after seeing case A13 missed.
- Common bank names (SBI, HDFC, ICICI, Axis, PNB, Kotak and others) and "Income Tax" recognised as claimed senders.
- False-alarm fixes: "Do not share it with anyone" in a message about an OTP or PIN is advice, not secrecy; "Valid for 5 minutes" is how long a code works, not a deadline. Seen on case N01.
- Bug fix (not tuning): Python does not count Devanagari vowel signs as letters, so short Hindi messages such as N05 were wrongly rated "could not check". Readability now counts Devanagari properly.
- Lone inputs: a bare link, UPI ID or phone number now gets a note saying what we found (official domain or not, validated handle or not, owner unknown, 1930 is the helpline) instead of "could not check"; a link or payee from a reviewer-confirmed fake is answered as a known fake. These notes never count toward a verdict.
- Replies: a credential request gets "Banks never ask you to verify through a message. Call the number on the back of your card." "We found no warning signs" now appears only when nothing at all was found.

### Every case

| Case | What it tests | Expected | Before | After |
| --- | --- | --- | --- | --- |
| A01 | celebrity asking for money with a huge reward (given exactly) | suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A02 | bank phishing: suspicious activity, locked account, verify identity (given exactly) | suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A03 | relative on a new number asking for money | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A04 | Hinglish relative emergency, UPI payee, secrecy | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A05 | named-bank KYC phishing with a lookalike link | likely_fake_or_suspicious | cannot_verify (R5) ✗ | likely_fake (R3) |
| A06 | Hindi KYC phishing with a short link | likely_fake_or_suspicious | suspicious (R4) | likely_fake (R3) |
| A07 | OTP request disguised as a delivery problem | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A08 | lottery prize with a fee to a UPI ID | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A09 | Hindi lottery with a tax fee | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A10 | task scam: pay a deposit to earn more | likely_fake_or_suspicious | suspicious (R4) | suspicious (R4) |
| A11 | Hinglish job scam with a registration fee | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A12 | parcel stuck at customs, pay duty today | likely_fake_or_suspicious | suspicious (R4) | suspicious (R4) |
| A13 | FedEx-to-police digital arrest opener | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A14 | electricity cut-off threat with a mobile number | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A15 | Hindi electricity cut-off threat | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A16 | Hinglish fake CBI text with arrest threat and secrecy | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A17 | pay a little, get a lot | likely_fake_or_suspicious | suspicious (R4) | suspicious (R4) |
| A18 | Hinglish money doubling | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| A19 | tax refund phishing with a lookalike domain | likely_fake_or_suspicious | cannot_verify (R5) ✗ | likely_fake (R3) |
| A20 | Hinglish celebrity prize with a fee | likely_fake_or_suspicious | cannot_verify (R5) ✗ | suspicious (R4) |
| N01 | a real OTP SMS | cannot_verify | suspicious (R4) ✗ | cannot_verify (R5) |
| N02 | a delivery update | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N03 | a friend asking to split a bill | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N04 | a bank's genuine never-share warning | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N05 | Hindi family message | cannot_verify | could_not_check (R0) ✗ | cannot_verify (R5) |
| N06 | a genuine debit alert | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N07 | Hinglish friend, pay me back later | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N08 | a genuine electricity bill | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N09 | an appointment reminder | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |
| N10 | a genuine account statement notice | cannot_verify | cannot_verify (R5) | cannot_verify (R5) |

### Still wrong after the change

None.

### How to rerun

```sh
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --adversarial data/adversarial/cases.csv --compare data/adversarial/before.json --results docs/RESULTS.md
```
