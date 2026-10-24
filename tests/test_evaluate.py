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
    for heading in ("## Headline", "## False positives (genuine items flagged)", "## Hard failures",
                    "## Every wrong verdict, with why", "## By language", "## By format", "## Full console output"):
        assert heading in md
    assert "### `png/C.png`" in md and "Signal `ask.secrecy` (weak)" in md
