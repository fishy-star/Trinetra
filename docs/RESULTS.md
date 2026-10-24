# Trinetra: measured accuracy (test split)

| | |
| --- | --- |
| Split | test |
| Run at (UTC) | 2026-10-24 17:01 |
| Code | commit dc68711 (with uncommitted changes) |
| Engine | 0.2.0 |
| OCR | tesseract 5.5.3 (eng+hin) |
| Modes | LLM_MODE=off, STT_MODE=off, AUDIO_MODEL=off |

Every item in the split was run once through the full pipeline (OCR, extraction, checks, rule table), offline. Expected verdicts come from `data/test-set/labels.csv`; no rules, signals or thresholds were changed after seeing these results.

## Totals

- **33** items checked; **33** got the expected verdict (**100%**).
- For cropped notices whose payee or contact may be cut off, the label accepts either *likely fake* or *suspicious*; both count as expected.
- Time per check: median **0.81 s**, slowest **1.04 s** (95th percentile 0.99 s). The target is a reply within 60 s.

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

- The voice checks (V1, V2, V3, V4) are not included yet: speech-to-text and the audio model are not wired up, and the recordings do not exist yet.

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
| `variants/F1_sc_warrant_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.96 s |
| `variants/F1_sc_warrant_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 0.99 s |
| `variants/F1_sc_warrant_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.82 s |
| `variants/F2_cbi_deposit_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | forged | likely_fake | likely_fake | R3 | 1.04 s |
| `variants/F2_cbi_deposit_hi__photo.jpg` | Phone photo of a printout | Hindi | forged | likely_fake | likely_fake | R3 | 1.01 s |
| `variants/F2_cbi_deposit_hi__crop.jpg` | Cropped (top part only) | Hindi | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.88 s |
| `variants/F3_ed_freeze_bi__whatsapp.jpg` | WhatsApp-compressed | Hindi + English (bilingual) | forged | likely_fake | likely_fake | R3 | 0.97 s |
| `variants/F3_ed_freeze_bi__photo.jpg` | Phone photo of a printout | Hindi + English (bilingual) | forged | likely_fake | likely_fake | R3 | 0.94 s |
| `variants/F3_ed_freeze_bi__crop.jpg` | Cropped (top part only) | Hindi + English (bilingual) | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.85 s |
| `variants/F4_dpb_surety_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.82 s |
| `variants/F4_dpb_surety_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 0.90 s |
| `variants/F4_dpb_surety_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.74 s |
| `variants/F5_rbi_penalty_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | likely_fake | likely_fake | R3 | 0.82 s |
| `variants/F5_rbi_penalty_en__photo.jpg` | Phone photo of a printout | English | forged | likely_fake | likely_fake | R3 | 0.71 s |
| `variants/F5_rbi_penalty_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.67 s |
| `variants/H2_sim_police_en__whatsapp.jpg` | WhatsApp-compressed | English | forged | suspicious | suspicious | R4 | 0.72 s |
| `variants/H2_sim_police_en__photo.jpg` | Phone photo of a printout | English | forged | suspicious | suspicious | R4 | 0.73 s |
| `variants/H2_sim_police_en__crop.jpg` | Cropped (top part only) | English | forged | likely_fake_or_suspicious | suspicious | R4 | 0.62 s |
| `variants/G1_court_summons_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.78 s |
| `variants/G1_court_summons_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/G1_court_summons_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.65 s |
| `variants/G2_bank_rekyc_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/G2_bank_rekyc_hi__photo.jpg` | Phone photo of a printout | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.76 s |
| `variants/G2_bank_rekyc_hi__crop.jpg` | Cropped (top part only) | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.71 s |
| `variants/G3_tax_intimation_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.74 s |
| `variants/G3_tax_intimation_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.78 s |
| `variants/G3_tax_intimation_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.64 s |
| `variants/H1_mf_sip_valid_en__whatsapp.jpg` | WhatsApp-compressed | English | genuine | cannot_verify | cannot_verify | R5 | 0.71 s |
| `variants/H1_mf_sip_valid_en__photo.jpg` | Phone photo of a printout | English | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/H1_mf_sip_valid_en__crop.jpg` | Cropped (top part only) | English | genuine | cannot_verify | cannot_verify | R5 | 0.63 s |
| `variants/H3_police_awareness_hi__whatsapp.jpg` | WhatsApp-compressed | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.83 s |
| `variants/H3_police_awareness_hi__photo.jpg` | Phone photo of a printout | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.81 s |
| `variants/H3_police_awareness_hi__crop.jpg` | Cropped (top part only) | Hindi | genuine | cannot_verify | cannot_verify | R5 | 0.70 s |

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

Latency  median 0.81 s   95th percentile 0.99 s  (target median <= 60 s)

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

Run at 2026-10-24 17:09 UTC, engine 0.2.0. Median time per message 0.000 s, slowest 0.001 s.

| | Messages | Flagged (likely fake or suspicious) | likely_fake | suspicious | cannot_verify | verified_official | could_not_check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Normal (ham) | 450 | **0 (0.0%)**, false alarms | 0 | 0 | 446 | 0 | 4 |
| Spam | 50 | **0 (0.0%)** | 0 | 0 | 50 | 0 | 0 |

**Spam is not the same as financial scams.** Most spam in this dataset is prize draws, ringtone offers, dating lines and adverts. Trinetra looks for one thing: a message that claims to be an official body or a bank and pushes you to pay. So most spam getting *cannot verify* is expected, and the spam number is not a detection rate. The number that matters here is the false alarms on normal messages.

### Normal messages flagged (0)

None.

### How to rerun

Download the zip from the UCI page into `data/external/sms_spam/` (git-ignored), unzip it, then:

```sh
LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --sms data/external/sms_spam/SMSSpamCollection --sample 500 --seed 20261024 --results docs/RESULTS.md
```
