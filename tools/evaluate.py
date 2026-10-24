"""Run the synthetic test set through the pipeline and print the metrics table.

    python tools/evaluate.py                                    # dev split (default)
    python tools/evaluate.py --split test --markdown docs/RESULTS.md   # once, at the end
    python tools/evaluate.py --sms data/external/sms_spam/SMSSpamCollection --results docs/RESULTS.md
                                                                # real-world check on public SMS
    python tools/evaluate.py --adversarial data/adversarial/cases.csv --snapshot data/adversarial/before.json
    python tools/evaluate.py --adversarial data/adversarial/cases.csv --compare data/adversarial/before.json \
        --results docs/RESULTS.md                               # adversarial check, before and after

Runs offline. Expected verdicts come from data/test-set/labels.csv and are never changed here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import statistics
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.config import ENGINE_VERSION, settings  # noqa: E402
from app.models import Kind, Result  # noqa: E402

TEST_SET = Path(__file__).resolve().parent.parent / "data" / "test-set"
FORGED = ("forged", "cloned")


@dataclass
class Row:
    item: str
    file: str
    kind: str
    format: str
    lang: str
    truth: str
    expected: str
    must_never_be: str
    got: str | None = None
    seconds: float = 0.0
    skipped: str | None = None
    rule_row: str = ""
    signals: list[tuple[str, str, str]] = field(default_factory=list)  # (code, strength, reason)
    claimed_sender: str | None = None
    demands_payment: bool = False
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.got in expected_verdicts(self.expected)

    @property
    def hard_failure(self) -> bool:
        return self.got == self.must_never_be

    @property
    def false_positive(self) -> bool:
        """A genuine item flagged as likely fake or suspicious."""
        return self.truth not in FORGED and self.got in ("likely_fake", "suspicious")

    def why(self) -> str:
        """How the rule table reached this verdict, in plain words."""
        if self.error:
            return f"The check crashed: {self.error}"
        strong = sorted({c.split(".")[0] for c, strength, _ in self.signals if strength == "strong"})
        weak = sum(1 for _, strength, _ in self.signals if strength == "weak")
        sender = f"claimed sender read as {self.claimed_sender!r}" if self.claimed_sender else "no claimed sender found"
        demand = "a payment demand was found" if self.demands_payment else "no payment demand was found"
        rows = {
            "R0": "R0: too little readable text was extracted (OCR or PDF text), so nothing could be checked.",
            "R1": "R1: matched a reviewer-confirmed fake.",
            "R1A": "R1A: carries a valid issuer code but no longer matches what was issued.",
            "R2": "R2: the source was proven and no STRONG fake signal fired.",
            "R3": f"R3: STRONG fake signals from two checks ({', '.join(strong)}).",
            "R4": (f"R4: one check had a STRONG fake signal ({', '.join(strong)})" if strong
                   else f"R4: no STRONG signal, but {weak} WEAK fake signals") + ".",
            "R5": f"R5: no STRONG fake signal and {weak} WEAK fake signal{'s' if weak != 1 else ''} (two are needed).",
        }
        return f"{rows.get(self.rule_row, self.rule_row)} Extraction: {sender}; {demand}."


def item_id(file: str) -> str:
    return file.split("#", 1)[1] if "#" in file else Path(file).name.split("_", 1)[0]


def expected_verdicts(value: str) -> set[str]:
    # "likely_fake_or_suspicious" accepts either verdict.
    return {"likely_fake", "suspicious"} if value == "likely_fake_or_suspicious" else {value}


def run(split: str) -> list[Row]:
    with open(TEST_SET / "texts.csv", encoding="utf-8") as f:
        texts = {r["id"]: r["text"] for r in csv.DictReader(f)}
    with open(TEST_SET / "labels.csv", encoding="utf-8") as f:
        labels = [r for r in csv.DictReader(f) if r["split"] == split]

    rows = []
    for r in labels:
        row = Row(item_id(r["file"]), r["file"], r["kind"], r["format"], r["lang"], r["truth"],
                  r["expected_verdict"], r["must_never_be"])
        if r["kind"] not in ("text", "notice"):
            row.skipped = "voice notes are checked from milestone 5 (not built yet)"
            rows.append(row)
            continue
        start = time.perf_counter()
        try:
            if r["kind"] == "text":
                result = pipeline.check_text(texts[row.item], kind=Kind.TEXT)
            else:
                result = pipeline.check_file((TEST_SET / r["file"]).read_bytes())
        except Exception as e:  # one broken item must not stop a run that can only happen once
            row.seconds = time.perf_counter() - start
            row.got, row.error = "error", f"{type(e).__name__}: {e}"
            traceback.print_exc()
            rows.append(row)
            continue
        row.seconds = time.perf_counter() - start
        record(row, result)
        rows.append(row)
    return rows


def record(row: Row, result: Result) -> None:
    row.got = result.verdict.value
    row.rule_row = result.rule_row
    row.signals = [(s.code, s.strength.value, s.reason_en) for s in result.signals]
    row.claimed_sender = result.fields.claimed_sender
    row.demands_payment = result.fields.demands_payment


def groups_by(rows: list[Row], attr: str) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        groups[getattr(r, attr)].append(r)
    return dict(sorted(groups.items()))


def p95(seconds: list[float]) -> float:
    ordered = sorted(seconds)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def summary(rows: list[Row]) -> dict:
    done = [r for r in rows if r.got is not None]
    forged = [r for r in done if r.truth in FORGED]
    genuine = [r for r in done if r.truth not in FORGED]
    seconds = [r.seconds for r in done]
    return {
        "done": done,
        "skipped": [r for r in rows if r.skipped],
        "forged": forged,
        "genuine": genuine,
        "correct": sum(r.correct for r in done),
        "forged_verified": sum(r.got == "verified_official" for r in forged),
        "forged_caught": sum(r.got in ("likely_fake", "suspicious") for r in forged),
        "genuine_likely_fake": sum(r.got == "likely_fake" for r in genuine),
        "false_positives": [r for r in done if r.false_positive],
        "hard": [r for r in done if r.hard_failure],
        "wrong": [r for r in done if not r.correct],
        "median": statistics.median(seconds) if seconds else 0.0,
        "p95": p95(seconds) if seconds else 0.0,
    }


def table(title: str, groups: dict[str, list[Row]]) -> list[str]:
    lines = [f"\n{title}", f"  {'':<26}{'items':>6}{'correct':>9}{'accuracy':>10}{'hard fails':>12}"]
    for key, g in groups.items():
        correct = sum(r.correct for r in g)
        lines.append(f"  {key:<26}{len(g):>6}{correct:>9}{correct / len(g):>10.0%}{sum(r.hard_failure for r in g):>12}")
    return lines


def report(rows: list[Row], split: str) -> list[str]:
    s = summary(rows)
    done = s["done"]
    out = [f"Trinetra evaluation, split={split}: {len(done)} items run, {len(s['skipped'])} skipped"]
    out += table("Accuracy per expected verdict", groups_by(done, "expected"))
    out += table("By language", groups_by(done, "lang"))
    out += table("By format", groups_by(done, "format"))
    out.append("\nTargets")
    out.append(f"  forged rated verified_official   {s['forged_verified']}  (target 0)")
    if s["forged"]:
        out.append(f"  forged rated fake or suspicious  {s['forged_caught']}/{len(s['forged'])} = "
                   f"{s['forged_caught'] / len(s['forged']):.0%}  (target 90%)")
    out.append(f"  genuine rated likely_fake        {s['genuine_likely_fake']}  (target 0)")
    out.append(f"  genuine flagged (false positives) {len(s['false_positives'])}/{len(s['genuine'])}")
    if done:
        out.append(f"\nLatency  median {s['median']:.2f} s   95th percentile {s['p95']:.2f} s  (target median <= 60 s)")
    out.append(f"\nHard failures: {len(s['hard'])}")
    out += [f"  {r.file}: got {r.got}, must never be {r.must_never_be}" for r in s["hard"]]
    out.append(f"False positives: {len(s['false_positives'])}")
    out += [f"  {r.file}: genuine, got {r.got}" for r in s["false_positives"]]
    out.append(f"Wrong verdicts: {len(s['wrong'])}")
    for r in s["wrong"]:
        out.append(f"  {r.file}: expected {r.expected}, got {r.got} ({r.rule_row or 'no rule row'})")
        out.append(f"      why: {r.why()}")
        out += [f"      signal {code} ({strength}): {reason}" for code, strength, reason in r.signals]
    out += [f"Skipped {r.file}: {r.skipped}" for r in s["skipped"]]
    return out


def run_info(split: str) -> dict[str, str]:
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, cwd=TEST_SET.parent.parent,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    try:
        import pytesseract

        ocr = f"tesseract {pytesseract.get_tesseract_version()} (eng+hin)"
    except Exception:
        ocr = "tesseract not found"
    dirty = " (with uncommitted changes)" if git("status", "--porcelain") else ""
    return {
        "Split": split,
        "Run at (UTC)": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "Code": f"commit {git('rev-parse', '--short', 'HEAD')}{dirty}",
        "Engine": ENGINE_VERSION,
        "OCR": ocr,
        "Modes": f"LLM_MODE={settings.llm_mode}, STT_MODE={settings.stt_mode}, AUDIO_MODEL={settings.audio_model}",
    }


VERDICTS = ("likely_fake", "suspicious", "cannot_verify", "verified_official", "could_not_check")
FORMAT_NAMES = {"png": "Original (clean image)", "pdf": "Original (PDF)", "whatsapp_jpg": "WhatsApp-compressed",
                "photo_jpg": "Phone photo of a printout", "crop_jpg": "Cropped (top part only)", "text": "Text message",
                "audio": "Voice note"}
LANG_NAMES = {"en": "English", "hi": "Hindi", "bi": "Hindi + English (bilingual)"}
RERUN = "LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --split {split} --markdown docs/RESULTS.md"


def markdown(rows: list[Row], split: str, console: list[str], fixes: list[str] = ()) -> str:
    """The results written for judges: totals, never-allowed errors first, then the breakdowns."""
    s = summary(rows)
    done = s["done"]
    pct = lambda a, b: f"{a / b:.0%}" if b else "n/a"  # noqa: E731
    errors = [r for r in done if r.error]
    md = [f"# Trinetra: measured accuracy ({split} split)", ""]
    md += ["| | |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in run_info(split).items()]
    md += ["", "Every item in the split was run once through the full pipeline (OCR, extraction, checks, rule table), "
           "offline. Expected verdicts come from `data/test-set/labels.csv`; no rules, signals or thresholds were "
           "changed after seeing these results.", ""]

    md += ["## Totals", "",
           f"- **{len(done)}** items checked; **{s['correct']}** got the expected verdict "
           f"(**{pct(s['correct'], len(done))}**).",
           "- For cropped notices whose payee or contact may be cut off, the label accepts either *likely fake* or "
           "*suspicious*; both count as expected.",
           f"- Time per check: median **{s['median']:.2f} s**, slowest **{max((r.seconds for r in done), default=0):.2f} s** "
           f"(95th percentile {s['p95']:.2f} s). The target is a reply within 60 s.", ""]

    md += ["## Never-allowed errors (must_never_be)", "",
           "The most important number: a fake notice must never be called *verified official*, and a genuine one must "
           "never be called *likely fake*.", "",
           f"**{len(s['hard'])} violations** "
           f"({s['forged_verified']} fakes called verified official, {s['genuine_likely_fake']} genuine items called likely fake).", ""]
    md += [f"- `{r.file}`: got **{r.got}**, which must never happen for this item" for r in s["hard"]]
    md += [*([""] if s["hard"] else []), f"Genuine items flagged at all (likely fake or suspicious): "
           f"{len(s['false_positives'])} of {len(s['genuine'])}. Fakes caught (likely fake or suspicious): "
           f"{s['forged_caught']} of {len(s['forged'])} ({pct(s['forged_caught'], len(s['forged']))}; target 90%).", ""]

    md += ["## Expected verdict vs what Trinetra gave", "",
           "| Expected ↓ / Given → | " + " | ".join(VERDICTS) + " | Total |",
           "| --- | " + " | ".join("---" for _ in VERDICTS) + " | --- |"]
    for expected, group in groups_by(done, "expected").items():
        counts = [sum(r.got == v for r in group) for v in VERDICTS]
        md.append(f"| **{expected}** | " + " | ".join(str(c) if c else "·" for c in counts) + f" | {len(group)} |")
    if errors:
        md.append(f"\n{len(errors)} item(s) crashed and are counted as wrong.")
    md.append("")

    def grouped(title: str, groups: dict[str, list[Row]], names: dict[str, str], note: str = "") -> list[str]:
        lines = [f"## {title}", ""] + ([note, ""] if note else [])
        lines += ["| | Items | Expected verdict | Accuracy | Never-allowed errors |", "| --- | --- | --- | --- | --- |"]
        for key, g in groups.items():
            c = sum(r.correct for r in g)
            lines.append(f"| {names.get(key, key)} | {len(g)} | {c} | {pct(c, len(g))} | {sum(r.hard_failure for r in g)} |")
        return lines + [""]

    formats = groups_by(done, "format")
    note = ("" if {"png", "pdf"} & formats.keys() else
            "The originals (clean PNG and PDF) are all in the dev split, used to build the rules, so they are not "
            "measured here. This split holds the same notices as WhatsApp-compressed copies, phone photos and crops.")
    md += grouped("By format", formats, FORMAT_NAMES, note)
    md += grouped("By language", groups_by(done, "lang"), LANG_NAMES)

    md += ["## Every miss, with why", ""]
    md += [f"- `{r.file}`: expected {r.expected}, got **{r.got}**. {r.why()}" for r in s["wrong"]] or ["None."]
    md.append("")

    md += ["## Not included yet", ""]
    voice = [r for r in s["skipped"] if r.kind == "voice"]
    if voice:
        md.append(f"- The voice checks ({', '.join(r.item for r in voice)}) are not included yet: speech-to-text "
                  "and the audio model are not wired up, and the recordings do not exist yet.")
    md += [f"- `{r.file}`: {r.skipped}" for r in s["skipped"] if r.kind != "voice"]
    if not s["skipped"]:
        md.append("Nothing; every item in the split was run.")
    md.append("")

    md += ["## Bugs fixed during this run", ""]
    md += [f"- {f}" for f in fixes] or ["None. Nothing crashed and no item failed because of a bug."]
    md += ["", "## How to read these numbers", "",
           "- The test set is synthetic and small, so one miss moves accuracy by about 3 points.",
           "- The test split shows the same notices as the dev split, transformed. It measures how well checks survive "
           "compression, photos and crops, not how they do on scam notices never seen before.", ""]

    md += ["## How to rerun", "", "From the repository root:", "", "```sh", RERUN.format(split=split), "```", "",
           "## Per item", "", "| Item | Format | Language | Truth | Expected | Given | Rule | Time |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    md += [f"| `{r.file}` | {FORMAT_NAMES.get(r.format, r.format)} | {LANG_NAMES.get(r.lang, r.lang)} | {r.truth} | "
           f"{r.expected} | {r.got}{'' if r.correct else ' (miss)'} | {r.rule_row} | {r.seconds:.2f} s |" for r in done]
    md += ["", "## Full console output", "", "```", *console, "```", ""]
    return "\n".join(md)


# --- real-world check: the UCI SMS Spam Collection ---------------------------------------------------

SMS_SECTION = "## Real-world check: public SMS dataset"
SMS_SAMPLE = 500
SMS_SEED = 20261024
FLAGGED = ("likely_fake", "suspicious")


@dataclass
class SmsRow:
    label: str  # "ham" or "spam"
    text: str
    got: str = ""
    rule_row: str = ""
    signals: list[tuple[str, str, str]] = field(default_factory=list)
    seconds: float = 0.0


def run_sms(path: Path, sample: int, seed: int) -> tuple[list[SmsRow], int]:
    """A fixed-seed random sample, run through the text pipeline with the rules exactly as they are."""
    messages = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        label, _, text = line.partition("\t")
        if label in ("ham", "spam") and text.strip():
            messages.append(SmsRow(label, text))
    picked = random.Random(seed).sample(messages, min(sample, len(messages)))
    for row in picked:
        start = time.perf_counter()
        result = pipeline.check_text(row.text, kind=Kind.TEXT)
        row.seconds = time.perf_counter() - start
        row.got, row.rule_row = result.verdict.value, result.rule_row
        row.signals = [(s.code, s.strength.value, s.reason_en) for s in result.signals]
    return picked, len(messages)


def _md_text(text: str) -> str:
    return re.sub(r"([\\`*_|<>\[\]#])", r"\\\1", " ".join(text.split()))


def sms_report(rows: list[SmsRow], total: int, path: Path, sample: int, seed: int) -> tuple[list[str], str]:
    ham = [r for r in rows if r.label == "ham"]
    spam = [r for r in rows if r.label == "spam"]
    alarms = [r for r in ham if r.got in FLAGGED]
    caught = [r for r in spam if r.got in FLAGGED]
    pct = lambda a, b: f"{a / b:.1%}" if b else "n/a"  # noqa: E731
    seconds = [r.seconds for r in rows]

    console = [f"SMS check: {len(rows)} of {total} messages (seed {seed}): {len(ham)} ham, {len(spam)} spam",
               f"  ham flagged (false alarms): {len(alarms)}/{len(ham)} = {pct(len(alarms), len(ham))}",
               f"  spam flagged:               {len(caught)}/{len(spam)} = {pct(len(caught), len(spam))}"]
    for r in alarms:
        console.append(f"  FALSE ALARM {r.got} ({r.rule_row}): {' '.join(r.text.split())}")
        console += [f"      {code} ({strength}): {reason}" for code, strength, reason in r.signals]

    def counts(group: list[SmsRow]) -> str:
        return " | ".join(str(sum(r.got == v for r in group)) for v in VERDICTS)

    command = (f"LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --sms {path} "
               f"--sample {sample} --seed {seed} --results docs/RESULTS.md")
    md = [SMS_SECTION, "",
          "A check on real messages Trinetra was never built on: the "
          "[UCI SMS Spam Collection](https://doi.org/10.24432/C5CC84) (Almeida and Hidalgo, 2011; CC BY 4.0), "
          f"5,574 English SMS labelled *ham* (normal) or *spam*. A fixed-seed random sample of **{len(rows)}** messages "
          f"(seed {seed}) was run through the same text pipeline and rule table as everything else. No rules were "
          "changed for, or after, this check.", "",
          f"Run at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC, engine {ENGINE_VERSION}. Median time per message "
          f"{statistics.median(seconds):.3f} s, slowest {max(seconds):.3f} s.", "",
          "| | Messages | Flagged (likely fake or suspicious) | " + " | ".join(VERDICTS) + " |",
          "| --- | --- | --- | " + " | ".join("---" for _ in VERDICTS) + " |",
          f"| Normal (ham) | {len(ham)} | **{len(alarms)} ({pct(len(alarms), len(ham))})**, false alarms | {counts(ham)} |",
          f"| Spam | {len(spam)} | **{len(caught)} ({pct(len(caught), len(spam))})** | {counts(spam)} |", "",
          "**Spam is not the same as financial scams.** Most spam in this dataset is prize draws, ringtone offers, "
          "dating lines and adverts. Trinetra looks for one thing: a message that claims to be an official body or a "
          "bank and pushes you to pay. So most spam getting *cannot verify* is expected, and the spam number is not a "
          "detection rate. The number that matters here is the false alarms on normal messages.", "",
          f"### Normal messages flagged ({len(alarms)})", ""]
    if alarms:
        for r in alarms:
            reason = r.signals[0][2] if r.signals else ""
            md.append(f"- **{r.got}** ({r.rule_row}): “{_md_text(r.text)}”  ")
            md.append(f"  Signals: {', '.join(f'`{c}` ({s})' for c, s, _ in r.signals)}. First reason: {reason}")
    else:
        md.append("None.")
    md += ["", "### How to rerun", "",
           "Download the zip from the UCI page into `data/external/sms_spam/` (git-ignored), unzip it, then:", "",
           "```sh", command, "```", ""]
    return console, "\n".join(md)


def write_sms_section(results: Path, section: str) -> None:
    """Append the section to RESULTS.md, replacing an earlier SMS section so a rerun does not duplicate it."""
    existing = results.read_text(encoding="utf-8") if results.exists() else ""
    if SMS_SECTION in existing:
        existing = existing[: existing.index(SMS_SECTION)]
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text(existing.rstrip("\n") + "\n\n" + section, encoding="utf-8")


# --- adversarial check: messages judges might try ---------------------------------------------------

ADV_SECTION = "## Adversarial check (before and after)"
# What changed between the before and after runs, for the record. The rule table (R0 to R5) is unchanged:
# new signals feed into it, and "likely fake" still needs STRONG signals from two different checks.
ADV_CHANGES = [
    "New STRONG `ask.money_request`: someone asks you to send, give or transfer money (any payee, English, Hindi or "
    "Hinglish such as \"paise bhejo\"), together with something pushing you: urgency, secrecy, a threat, a reward or "
    "a claimed identity. Without that push it does not fire, so a friend splitting a bill is not flagged.",
    "New STRONG `ask.credential_request`: verify your identity, update KYC or PAN, share an OTP, PIN or CVV, confirm "
    "card details, re-activate your account (including \"KYC update karo\", \"OTP batao\"). Not when the sentence "
    "says \"never\" or sends you to the branch.",
    "New WEAK `ask.reward_for_payment`: a prize, lottery, big return or \"I will give you X\" alongside a request for money.",
    "New WEAK `source.claimed_identity_unverifiable`: claims to be a famous person, or says \"your bank\" without naming it.",
    "New WEAK `ask.account_threat`: account locked, blocked or suspended, a service cut off, \"suspicious activity\" "
    "(English, Hindi, Hinglish).",
    "Pressure words added to the urgency signal: \"fast\", \"trust me\", \"asap\", \"abhi\", \"jaldi\", \"turant\".",
    "Legal-threat words added to the arrest-threat signal: \"clear your name\", \"legal action\", \"a case has been "
    "filed against you\", and \"cyber crime\" units as a police sender. Added after seeing case A13 missed.",
    "Common bank names (SBI, HDFC, ICICI, Axis, PNB, Kotak and others) and \"Income Tax\" recognised as claimed senders.",
    "False-alarm fixes: \"Do not share it with anyone\" in a message about an OTP or PIN is advice, not secrecy; "
    "\"Valid for 5 minutes\" is how long a code works, not a deadline. Seen on case N01.",
    "Bug fix (not tuning): Python does not count Devanagari vowel signs as letters, so short Hindi messages such as "
    "N05 were wrongly rated \"could not check\". Readability now counts Devanagari properly.",
    "Lone inputs: a bare link, UPI ID or phone number now gets a note saying what we found (official domain or not, "
    "validated handle or not, owner unknown, 1930 is the helpline) instead of \"could not check\"; a link or payee "
    "from a reviewer-confirmed fake is answered as a known fake. These notes never count toward a verdict.",
    "Replies: a credential request gets \"Banks never ask you to verify through a message. Call the number on the back "
    "of your card.\" \"We found no warning signs\" now appears only when nothing at all was found.",
]


def run_adversarial(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        cases = list(csv.DictReader(f))
    out = []
    for c in cases:
        result = pipeline.check_text(c["text"], kind=Kind.TEXT)
        out.append({
            "id": c["id"], "text": c["text"], "expected": c["expected_verdict"], "what": c["what_it_tests"],
            "scam": c["expected_verdict"] != "cannot_verify", "got": result.verdict.value, "rule_row": result.rule_row,
            "signals": [[s.code, s.strength.value, s.reason_en] for s in result.signals],
            "correct": result.verdict.value in expected_verdicts(c["expected_verdict"]),
        })
    return out


def adversarial_counts(rows: list[dict]) -> dict:
    scams = [r for r in rows if r["scam"]]
    normal = [r for r in rows if not r["scam"]]
    return {
        "correct": sum(r["correct"] for r in rows), "total": len(rows),
        "scams": len(scams), "scams_flagged": sum(r["got"] in FLAGGED for r in scams),
        "normal": len(normal), "normal_flagged": sum(r["got"] in FLAGGED for r in normal),
    }


def adversarial_console(rows: list[dict]) -> list[str]:
    c = adversarial_counts(rows)
    lines = [f"Adversarial: {c['correct']}/{c['total']} as expected; scams flagged {c['scams_flagged']}/{c['scams']}; "
             f"normal messages flagged {c['normal_flagged']}/{c['normal']}"]
    for r in rows:
        mark = "ok  " if r["correct"] else "MISS"
        lines.append(f"  {mark} {r['id']} expected {r['expected']:<26} got {r['got']:<16} {r['rule_row']:<3} "
                     f"{', '.join(code for code, _, _ in r['signals']) or '-'}")
    return lines


def adversarial_section(before: dict, after: list[dict], cases: Path) -> str:
    b, a = adversarial_counts(before["rows"]), adversarial_counts(after)
    by_id = {r["id"]: r for r in before["rows"]}
    digest = hashlib.sha256(cases.read_bytes()).hexdigest()
    md = [ADV_SECTION, "",
          f"Thirty short messages a judge might try: {a['scams']} scams written from common real-world patterns "
          "(relatives and celebrities asking for money, bank and KYC phishing, lotteries, job and task scams, parcels "
          "at customs, electricity cut-offs, fake police texts, \"pay a little, get a lot\") and "
          f"{a['normal']} normal messages that must not be flagged, in English, Hindi and Hinglish. The cases are in "
          f"`{cases}` (SHA-256 `{digest[:16]}…`) and were written, and the \"before\" run recorded "
          f"({before['run_at']} UTC, engine {before['engine']}), before any signal was added.", "",
          "| | Before | After |", "| --- | --- | --- |",
          f"| As expected | {b['correct']}/{b['total']} | {a['correct']}/{a['total']} |",
          f"| Scams flagged (likely fake or suspicious) | {b['scams_flagged']}/{b['scams']} | "
          f"{a['scams_flagged']}/{a['scams']} |",
          f"| Normal messages flagged (false alarms) | {b['normal_flagged']}/{b['normal']} | "
          f"{a['normal_flagged']}/{a['normal']} |", "",
          "**How to read the after column.** The new signals were designed after seeing the before results on these "
          "same cases, so the after column shows the fixes work, not how Trinetra does on unseen scams. The independent "
          "checks are the unchanged test split and the public SMS sample, both rerun after the change (sections above).",
          "", "### What changed", "", *[f"- {c}" for c in ADV_CHANGES], "",
          "### Every case", "",
          "| Case | What it tests | Expected | Before | After |", "| --- | --- | --- | --- | --- |"]
    for r in after:
        old = by_id.get(r["id"], {})
        mark = lambda row: f"{row.get('got', '?')} ({row.get('rule_row', '')})" + ("" if row.get("correct") else " ✗")  # noqa: E731
        md.append(f"| {r['id']} | {_md_text(r['what'])} | {r['expected']} | {mark(old)} | {mark(r)} |")
    wrong = [r for r in after if not r["correct"]]
    md += ["", "### Still wrong after the change", ""]
    for r in wrong:
        signals = ", ".join(f"`{code}` ({strength})" for code, strength, _ in r["signals"]) or "none"
        md.append(f"- **{r['id']}** ({_md_text(r['what'])}): expected {r['expected']}, got **{r['got']}** "
                  f"(rule {r['rule_row']}; signals: {signals}).")
    if not wrong:
        md.append("None.")
    md += ["", "### How to rerun", "", "```sh",
           f"LLM_MODE=off STT_MODE=off AUDIO_MODEL=off .venv/bin/python tools/evaluate.py --adversarial {cases} "
           f"--compare data/adversarial/before.json --results docs/RESULTS.md", "```", ""]
    return "\n".join(md)


def write_section(results: Path, heading: str, section: str) -> None:
    """Add a section to RESULTS.md, replacing an earlier copy of it (up to the next top-level section)."""
    text = results.read_text(encoding="utf-8") if results.exists() else ""
    if heading in text:
        start = text.index(heading)
        nxt = text.find("\n## ", start + len(heading))
        text = text[:start].rstrip("\n") + ("\n\n" + text[nxt + 1:] if nxt != -1 else "")
    results.write_text(text.rstrip("\n") + "\n\n" + section, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--markdown", type=Path, help="also write the full results as Markdown to this file")
    parser.add_argument("--sms", type=Path, help="instead, run a sample of the UCI SMS Spam Collection file")
    parser.add_argument("--sample", type=int, default=SMS_SAMPLE, help="how many SMS to sample (default 500)")
    parser.add_argument("--seed", type=int, default=SMS_SEED, help="random seed for the SMS sample")
    parser.add_argument("--results", type=Path, help="with --sms or --compare: add the section to this RESULTS.md")
    parser.add_argument("--adversarial", type=Path, help="instead, run the adversarial cases CSV")
    parser.add_argument("--snapshot", type=Path, help="with --adversarial: save this run as the 'before' JSON")
    parser.add_argument("--compare", type=Path, help="with --adversarial: the 'before' JSON to compare against")
    args = parser.parse_args()
    if args.adversarial:
        rows = run_adversarial(args.adversarial)
        print("\n".join(adversarial_console(rows)))
        if args.snapshot:
            args.snapshot.write_text(json.dumps({"run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
                                                 "engine": ENGINE_VERSION, "rows": rows}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
            print(f"\nSaved {args.snapshot}")
        if args.compare and args.results:
            before = json.loads(args.compare.read_text(encoding="utf-8"))
            write_section(args.results, ADV_SECTION, adversarial_section(before, rows, args.adversarial))
            print(f"\nWrote the before/after section to {args.results}")
        return
    if args.sms:
        rows, total = run_sms(args.sms, args.sample, args.seed)
        console, section = sms_report(rows, total, args.sms, args.sample, args.seed)
        print("\n".join(console))
        if args.results:
            write_sms_section(args.results, section)
            print(f"\nAppended to {args.results}")
        return
    rows = run(args.split)
    console = report(rows, args.split)
    print("\n".join(console))
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown(rows, args.split, console), encoding="utf-8")
        print(f"\nWrote {args.markdown}")
    sys.exit(1 if summary(rows)["hard"] else 0)


if __name__ == "__main__":
    main()
