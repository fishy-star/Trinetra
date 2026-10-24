"""Run the synthetic test set through the pipeline and print the metrics table.

    python tools/evaluate.py                 # dev split (default)
    python tools/evaluate.py --split test    # once, at the end

Runs offline. Expected verdicts come from data/test-set/labels.csv and are never changed here.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.models import Kind  # noqa: E402

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

    @property
    def correct(self) -> bool:
        return self.got in expected_verdicts(self.expected)

    @property
    def hard_failure(self) -> bool:
        return self.got == self.must_never_be


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
        start = time.perf_counter()
        if r["kind"] == "text":
            result = pipeline.check_text(texts[row.item], kind=Kind.TEXT)
        elif r["kind"] == "notice":
            result = pipeline.check_file((TEST_SET / r["file"]).read_bytes())
        else:
            row.skipped = "voice notes are checked from milestone 5"
            rows.append(row)
            continue
        row.seconds = time.perf_counter() - start
        row.got = result.verdict.value
        rows.append(row)
    return rows


def table(title: str, groups: dict[str, list[Row]]) -> None:
    print(f"\n{title}")
    print(f"  {'':<26}{'items':>6}{'correct':>9}{'accuracy':>10}{'hard fails':>12}")
    for key in sorted(groups):
        g = groups[key]
        correct = sum(r.correct for r in g)
        print(f"  {key:<26}{len(g):>6}{correct:>9}{correct / len(g):>10.0%}{sum(r.hard_failure for r in g):>12}")


def report(rows: list[Row], split: str) -> int:
    done = [r for r in rows if r.got is not None]
    skipped = [r for r in rows if r.skipped]
    print(f"Trinetra evaluation, split={split}: {len(done)} items run, {len(skipped)} skipped")

    by = defaultdict(list)
    for r in done:
        by[r.expected].append(r)
    table("Accuracy per expected verdict", by)
    for name, attr in (("By language", "lang"), ("By format", "format")):
        groups = defaultdict(list)
        for r in done:
            groups[getattr(r, attr)].append(r)
        table(name, groups)

    forged = [r for r in done if r.truth in FORGED]
    caught = [r for r in forged if r.got in ("likely_fake", "suspicious")]
    genuine = [r for r in done if r.truth not in FORGED]
    print("\nTargets")
    print(f"  forged rated verified_official   {sum(r.got == 'verified_official' for r in forged)}  (target 0)")
    if forged:
        print(f"  forged rated fake or suspicious  {len(caught)}/{len(forged)} = {len(caught) / len(forged):.0%}  (target 90%)")
    print(f"  genuine rated likely_fake        {sum(r.got == 'likely_fake' for r in genuine)}  (target 0)")

    seconds = sorted(r.seconds for r in done)
    if seconds:
        p95 = seconds[min(len(seconds) - 1, round(0.95 * (len(seconds) - 1)))]
        print(f"\nLatency  median {statistics.median(seconds):.2f} s   95th percentile {p95:.2f} s  (target median <= 60 s)")

    wrong = [r for r in done if not r.correct]
    hard = [r for r in done if r.hard_failure]
    print(f"\nHard failures: {len(hard)}")
    for r in hard:
        print(f"  {r.file}: got {r.got}, must never be {r.must_never_be}")
    print(f"Other misses: {len(wrong) - len(hard)}")
    for r in wrong:
        if not r.hard_failure:
            print(f"  {r.file}: expected {r.expected}, got {r.got}")
    for r in skipped:
        print(f"Skipped {r.file}: {r.skipped}")
    return 1 if hard else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    args = parser.parse_args()
    sys.exit(report(run(args.split), args.split))


if __name__ == "__main__":
    main()
