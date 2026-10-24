"""The evaluator's report, with made-up rows. It must never crash on misses: the test split runs only once."""

from tools.evaluate import Row, markdown, report, summary


def rows() -> list[Row]:
    def row(item, truth, expected, never, got, rule="R5", signals=(), **kw):
        r = Row(item, f"png/{item}.png", "notice", "photo_jpg", "en", truth, expected, never, got=got, seconds=1.0,
                rule_row=rule, signals=list(signals), **kw)
        return r

    return [
        row("A", "forged", "likely_fake", "verified_official", "likely_fake", "R3",
            [("source.non_official_contact", "strong", "x."), ("ask.payment_to_non_official_payee", "strong", "y.")]),
        row("B", "forged", "likely_fake", "verified_official", "suspicious", "R4",
            [("source.non_official_contact", "strong", "x.")], claimed_sender="RBI"),
        row("C", "genuine", "cannot_verify", "likely_fake", "likely_fake", "R3",
            [("source.non_official_contact", "strong", "x."), ("ask.payment_to_non_official_payee", "strong", "y.")]),
        row("D", "genuine", "cannot_verify", "likely_fake", "suspicious", "R4", [("ask.secrecy", "weak", "s."),
                                                                               ("ask.urgent_deadline", "weak", "u.")]),
        row("E", "forged", "likely_fake_or_suspicious", "verified_official", "could_not_check", "R0"),
        Row("V1", "voice/V1.ogg", "voice", "audio", "hi", "cloned", "suspicious", "verified_official",
            skipped="not built yet"),
        row("F", "forged", "likely_fake", "verified_official", "error", "", error="RuntimeError: boom"),
    ]


def test_summary_counts():
    s = summary(rows())
    assert s["correct"] == 1 and len(s["hard"]) == 1 and len(s["false_positives"]) == 2
    assert s["forged_caught"] == 2 and s["genuine_likely_fake"] == 1 and len(s["skipped"]) == 1


def test_why_explains_each_rule_row():
    by = {r.item: r for r in rows()}
    assert "R4: one check had a STRONG fake signal (source)" in by["B"].why() and "'RBI'" in by["B"].why()
    assert "R4: no STRONG signal, but 2 WEAK fake signals" in by["D"].why()
    assert "R0: too little readable text" in by["E"].why()
    assert "crashed" in by["F"].why()


def test_reports_render_misses_without_crashing():
    console = report(rows(), "test")
    assert any("Wrong verdicts: 5" in line for line in console)
    md = markdown(rows(), "test", console)
    for heading in ("## Totals", "## Never-allowed errors (must_never_be)", "## Expected verdict vs what Trinetra gave",
                    "## By format", "## By language", "## Every miss, with why", "## Not included yet",
                    "## Bugs fixed during this run", "## How to rerun", "## Full console output"):
        assert heading in md
    assert md.index("## Never-allowed errors") < md.index("## Expected verdict vs")
    assert "**1 violations** (0 fakes called verified official, 1 genuine items called likely fake)" in md
    assert "- `png/C.png`: got **likely_fake**" in md
    assert "| **cannot_verify** | 1 | 1 | · | · | · | 2 |" in md  # confusion table row
    assert "The voice checks (V1) are not included yet" in md
    assert "tools/evaluate.py --split test --markdown docs/RESULTS.md" in md
    assert "Phone photo of a printout" in md and "originals (clean PNG and PDF) are all in the dev split" in md


def test_sms_mode_reports_and_appends_once(tmp_path):
    from tools.evaluate import SMS_SECTION, run_sms, sms_report, write_sms_section

    data = tmp_path / "SMSSpamCollection"
    data.write_text(
        "ham\tSee you at 6, bring the keys\n"
        "spam\tYou WON a prize! Call 09061701461 to claim\n"
        "ham\tYour SBI account is blocked today. Pay Rs 499 to kyc.update@ybl immediately | fee\n"
        "not-a-label\tignored\n", encoding="utf-8")
    rows, total = run_sms(data, sample=10, seed=1)
    assert total == 3 and len(rows) == 3
    console, section = sms_report(rows, total, data, 10, 1)
    assert section.startswith(SMS_SECTION) and "Spam is not the same as financial scams" in section
    assert "### Normal messages flagged (1)" in section and "\\| fee" in section  # listed, with | escaped
    assert "--sms" in section and "--seed 1" in section
    results = tmp_path / "RESULTS.md"
    results.write_text("# Results\n", encoding="utf-8")
    write_sms_section(results, section)
    write_sms_section(results, section)
    assert results.read_text(encoding="utf-8").count(SMS_SECTION) == 1


def test_sms_sample_is_reproducible(tmp_path):
    from tools.evaluate import run_sms

    data = tmp_path / "s"
    data.write_text("".join(f"ham\tmessage number {i}\n" for i in range(50)), encoding="utf-8")
    first = [r.text for r in run_sms(data, 10, 7)[0]]
    assert first == [r.text for r in run_sms(data, 10, 7)[0]] and len(set(first)) == 10
