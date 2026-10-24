# Trinetra test set (synthetic)

Made for testing only. Every person, account number, UPI handle, phone number, bank and fund name is invented. Each notice has a grey "SYNTHETIC TEST SAMPLE" footer. Do not use these anywhere except testing Trinetra, and never let a detection rule look at that footer.

## What is here

| Folder or file | What it holds |
| --- | --- |
| `png/` | 11 notices as clean images |
| `pdf/` | The same 11 notices as PDFs (no digital signatures) |
| `variants/` | 3 versions of each notice: `__whatsapp` (recompressed), `__photo` (tilted phone photo of a printout), `__crop` (top part only) |
| `texts.csv` | 4 forwarded text messages |
| `voice_scripts.md` | 4 voice-note scripts for the team to record (2 real, 2 cloned from your own voices) |
| `labels.csv` | One row per item: what it is, the true label, the expected verdict, the verdict it must never get, and the dev/test split |

## The notices

| ID | Truth | Language | Expected verdict | What it tests |
| --- | --- | --- | --- | --- |
| F1 | Forged | English | Likely fake | Fake Supreme Court warrant: move savings to an account, 2-hour deadline, secrecy, Gmail contact |
| F2 | Forged | Hindi | Likely fake | Fake CBI notice: UPI "security deposit", same-day deadline, secrecy, personal mobile |
| F3 | Forged | Hindi + English | Likely fake | Fake ED freeze order: move funds to a trading firm, link on a non-government site |
| F4 | Forged | English | Likely fake | Fake Data Protection Board and police notice: RTGS "surety" with refund promise |
| F5 | Forged | English | Likely fake | Fake RBI penalty by UPI, told not to visit the branch |
| H2 | Forged | English | Suspicious | Fake telecom and police notice with a WhatsApp contact and secrecy, but no payment yet |
| G1 | Genuine-style | English | Cannot verify | Civil court summons: appear in person, no money asked |
| G2 | Genuine-style | Hindi | Cannot verify | Bank re-KYC reminder that mentions OTP only to warn |
| G3 | Genuine-style | English | Cannot verify | Tax intimation that mentions payment and UPI only to warn |
| H1 | Genuine-style | English | Cannot verify | A real payment request, but to a SEBI-style @valid handle with a normal due date |
| H3 | Genuine-style | Hindi | Cannot verify | Police awareness notice full of scam words but with no demand |

No genuine-style item can reach "Verified official", because none has a valid digital signature from a real authority. That is correct behaviour, not a bug.

## How to score

- Use `dev` rows to tune rules and thresholds. Run `test` rows once, at the end, and report those numbers.
- A hard failure is any item that gets its `must_never_be` verdict: a forged item called verified, or a genuine item called likely fake.
- Report results split by `lang` and by `format`, as slide 7 promises.

## Regenerate or add more

The generator scripts are in `generator/`. Add a notice to `notices.py`, then run:

```
python3 build_html.py <html_out> <devanagari_font_dir>
node render.mjs <html_out> <test_set_dir>      # needs: npm install playwright
python3 variants_and_labels.py <test_set_dir>
```

The Hindi text uses Noto Serif Devanagari from the `@fontsource/noto-serif-devanagari` npm package.
