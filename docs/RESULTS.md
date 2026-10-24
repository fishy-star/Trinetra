# Trinetra results: test split

| | |
| --- | --- |
| Split | test |
| Run at (UTC) | 2026-10-24 16:38 |
| Code | commit a1fea16 (with uncommitted changes) |
| Engine | 0.2.0 |
| OCR | tesseract 5.5.3 (eng+hin) |
| Modes | LLM_MODE=off, STT_MODE=off, AUDIO_MODEL=off |

33 items run, 4 skipped. Expected verdicts come from `data/test-set/labels.csv`. No code, rules or thresholds were changed after this run.

## Headline

| Measure | Result | Target |
| --- | --- | --- |
| Correct verdicts | 33/33 (100%) | |
| Forged rated verified official | 0 | 0 |
| Forged rated likely fake or suspicious | 18/18 (100%) | 90% |
| Genuine rated likely fake | 0 | 0 |
| Genuine flagged at all (false positives) | 0/15 | |
| Hard failures | 0 | 0 |
| Median time per item | 0.82 s | ≤ 60 s |
| 95th-percentile time | 1.02 s | |

## Accuracy per expected verdict

| | Items | Correct | Accuracy | Hard failures |
| --- | --- | --- | --- | --- |
| cannot_verify | 15 | 15 | 100% | 0 |
| likely_fake | 10 | 10 | 100% | 0 |
| likely_fake_or_suspicious | 6 | 6 | 100% | 0 |
| suspicious | 2 | 2 | 100% | 0 |

## By language

| | Items | Correct | Accuracy | Hard failures |
| --- | --- | --- | --- | --- |
| bi | 3 | 3 | 100% | 0 |
| en | 21 | 21 | 100% | 0 |
| hi | 9 | 9 | 100% | 0 |

## By format

| | Items | Correct | Accuracy | Hard failures |
| --- | --- | --- | --- | --- |
| crop_jpg | 11 | 11 | 100% | 0 |
| photo_jpg | 11 | 11 | 100% | 0 |
| whatsapp_jpg | 11 | 11 | 100% | 0 |

## False positives (genuine items flagged)

None.

## Hard failures

None.

## Every wrong verdict, with why

None.
## Skipped

- `voice/V1_clone_son_hi.ogg (record it)`: voice notes are checked from milestone 5 (not built yet)
- `voice/V2_clone_uncle_en.ogg (record it)`: voice notes are checked from milestone 5 (not built yet)
- `voice/V3_real_son_hi.ogg (record it)`: voice notes are checked from milestone 5 (not built yet)
- `voice/V4_real_uncle_en.ogg (record it)`: voice notes are checked from milestone 5 (not built yet)

## Per item

| Item | Format | Lang | Truth | Expected | Got | Rule | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `variants/F1_sc_warrant_en__whatsapp.jpg` | whatsapp_jpg | en | forged | likely_fake | likely_fake | R3 | 0.97 s |
| `variants/F1_sc_warrant_en__photo.jpg` | photo_jpg | en | forged | likely_fake | likely_fake | R3 | 1.01 s |
| `variants/F1_sc_warrant_en__crop.jpg` | crop_jpg | en | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.83 s |
| `variants/F2_cbi_deposit_hi__whatsapp.jpg` | whatsapp_jpg | hi | forged | likely_fake | likely_fake | R3 | 1.03 s |
| `variants/F2_cbi_deposit_hi__photo.jpg` | photo_jpg | hi | forged | likely_fake | likely_fake | R3 | 1.02 s |
| `variants/F2_cbi_deposit_hi__crop.jpg` | crop_jpg | hi | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.88 s |
| `variants/F3_ed_freeze_bi__whatsapp.jpg` | whatsapp_jpg | bi | forged | likely_fake | likely_fake | R3 | 0.99 s |
| `variants/F3_ed_freeze_bi__photo.jpg` | photo_jpg | bi | forged | likely_fake | likely_fake | R3 | 0.99 s |
| `variants/F3_ed_freeze_bi__crop.jpg` | crop_jpg | bi | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.86 s |
| `variants/F4_dpb_surety_en__whatsapp.jpg` | whatsapp_jpg | en | forged | likely_fake | likely_fake | R3 | 0.82 s |
| `variants/F4_dpb_surety_en__photo.jpg` | photo_jpg | en | forged | likely_fake | likely_fake | R3 | 0.91 s |
| `variants/F4_dpb_surety_en__crop.jpg` | crop_jpg | en | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.75 s |
| `variants/F5_rbi_penalty_en__whatsapp.jpg` | whatsapp_jpg | en | forged | likely_fake | likely_fake | R3 | 0.81 s |
| `variants/F5_rbi_penalty_en__photo.jpg` | photo_jpg | en | forged | likely_fake | likely_fake | R3 | 0.71 s |
| `variants/F5_rbi_penalty_en__crop.jpg` | crop_jpg | en | forged | likely_fake_or_suspicious | likely_fake | R3 | 0.66 s |
| `variants/H2_sim_police_en__whatsapp.jpg` | whatsapp_jpg | en | forged | suspicious | suspicious | R4 | 0.73 s |
| `variants/H2_sim_police_en__photo.jpg` | photo_jpg | en | forged | suspicious | suspicious | R4 | 0.76 s |
| `variants/H2_sim_police_en__crop.jpg` | crop_jpg | en | forged | likely_fake_or_suspicious | suspicious | R4 | 0.66 s |
| `variants/G1_court_summons_en__whatsapp.jpg` | whatsapp_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.88 s |
| `variants/G1_court_summons_en__photo.jpg` | photo_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 1.06 s |
| `variants/G1_court_summons_en__crop.jpg` | crop_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.66 s |
| `variants/G2_bank_rekyc_hi__whatsapp.jpg` | whatsapp_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.82 s |
| `variants/G2_bank_rekyc_hi__photo.jpg` | photo_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.77 s |
| `variants/G2_bank_rekyc_hi__crop.jpg` | crop_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.73 s |
| `variants/G3_tax_intimation_en__whatsapp.jpg` | whatsapp_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.76 s |
| `variants/G3_tax_intimation_en__photo.jpg` | photo_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.79 s |
| `variants/G3_tax_intimation_en__crop.jpg` | crop_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.65 s |
| `variants/H1_mf_sip_valid_en__whatsapp.jpg` | whatsapp_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.73 s |
| `variants/H1_mf_sip_valid_en__photo.jpg` | photo_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.84 s |
| `variants/H1_mf_sip_valid_en__crop.jpg` | crop_jpg | en | genuine | cannot_verify | cannot_verify | R5 | 0.63 s |
| `variants/H3_police_awareness_hi__whatsapp.jpg` | whatsapp_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.84 s |
| `variants/H3_police_awareness_hi__photo.jpg` | photo_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.82 s |
| `variants/H3_police_awareness_hi__crop.jpg` | crop_jpg | hi | genuine | cannot_verify | cannot_verify | R5 | 0.71 s |

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

Latency  median 0.82 s   95th percentile 1.02 s  (target median <= 60 s)

Hard failures: 0
False positives: 0
Wrong verdicts: 0
Skipped voice/V1_clone_son_hi.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V2_clone_uncle_en.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V3_real_son_hi.ogg (record it): voice notes are checked from milestone 5 (not built yet)
Skipped voice/V4_real_uncle_en.ogg (record it): voice notes are checked from milestone 5 (not built yet)
```
