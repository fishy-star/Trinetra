"""Run the synthetic test set through the pipeline and print the metrics table.

    python tools/evaluate.py                                    # dev split (default)
    python tools/evaluate.py --split test --markdown docs/RESULTS.md   # once, at the end

Runs offline. Expected verdicts come from data/test-set/labels.csv and are never changed here.
"""

from __future__ import annotations

import argparse
import csv
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


def markdown(rows: list[Row], split: str, console: list[str]) -> str:
    s = summary(rows)
    done = s["done"]
    pct = lambda a, b: f"{a / b:.0%}" if b else "n/a"  # noqa: E731
    md = [f"# Trinetra results: {split} split", ""]
    md += ["| | |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in run_info(split).items()]
    md += ["", f"{len(done)} items run, {len(s['skipped'])} skipped. Expected verdicts come from "
           "`data/test-set/labels.csv`. No code, rules or thresholds were changed after this run.", ""]
    md += ["## Headline", "", "| Measure | Result | Target |", "| --- | --- | --- |",
           f"| Correct verdicts | {s['correct']}/{len(done)} ({pct(s['correct'], len(done))}) | |",
           f"| Forged rated verified official | {s['forged_verified']} | 0 |",
           f"| Forged rated likely fake or suspicious | {s['forged_caught']}/{len(s['forged'])} "
           f"({pct(s['forged_caught'], len(s['forged']))}) | 90% |",
           f"| Genuine rated likely fake | {s['genuine_likely_fake']} | 0 |",
           f"| Genuine flagged at all (false positives) | {len(s['false_positives'])}/{len(s['genuine'])} | |",
           f"| Hard failures | {len(s['hard'])} | 0 |",
           f"| Median time per item | {s['median']:.2f} s | ≤ 60 s |",
           f"| 95th-percentile time | {s['p95']:.2f} s | |", ""]

    def grouped(title: str, groups: dict[str, list[Row]]) -> list[str]:
        lines = [f"## {title}", "", "| | Items | Correct | Accuracy | Hard failures |", "| --- | --- | --- | --- | --- |"]
        for key, g in groups.items():
            c = sum(r.correct for r in g)
            lines.append(f"| {key} | {len(g)} | {c} | {pct(c, len(g))} | {sum(r.hard_failure for r in g)} |")
        return lines + [""]

    md += grouped("Accuracy per expected verdict", groups_by(done, "expected"))
    md += grouped("By language", groups_by(done, "lang"))
    md += grouped("By format", groups_by(done, "format"))
    md += ["## False positives (genuine items flagged)", ""]
    md += [f"- `{r.file}`: got **{r.got}** ({r.rule_row})" for r in s["false_positives"]] or ["None."]
    md += ["", "## Hard failures", ""]
    md += [f"- `{r.file}`: got **{r.got}**, must never be {r.must_never_be}" for r in s["hard"]] or ["None."]
    md += ["", "## Every wrong verdict, with why", ""]
    if not s["wrong"]:
        md.append("None.")
    for r in s["wrong"]:
        md += [f"### `{r.file}`", "",
               f"- Truth: {r.truth}. Expected **{r.expected}**, got **{r.got}** (rule row {r.rule_row or 'none'}).",
               f"- Why: {r.why()}"]
        md += [f"- Signal `{code}` ({strength}): {reason}" for code, strength, reason in r.signals] or ["- No signals fired."]
        md.append("")
    md += ["## Skipped", ""]
    md += [f"- `{r.file}`: {r.skipped}" for r in s["skipped"]] or ["None."]
    md += ["", "## Per item", "", "| Item | Format | Lang | Truth | Expected | Got | Rule | Time |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    md += [f"| `{r.file}` | {r.format} | {r.lang} | {r.truth} | {r.expected} | {r.got}{'' if r.correct else ' ✗'} | "
           f"{r.rule_row} | {r.seconds:.2f} s |" for r in done]
    md += ["", "## Full console output", "", "```", *console, "```", ""]
    return "\n".join(md)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--markdown", type=Path, help="also write the full results as Markdown to this file")
    args = parser.parse_args()
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
