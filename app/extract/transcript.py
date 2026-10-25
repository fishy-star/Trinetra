"""Speech-to-text output only: fold common whisper mistakes before the checks read it.

Whisper writes Hindi by sound, so the same word comes out several ways (भेज/बेज, रुपये/रूपै), voiced and
unvoiced consonants swap (दो/तो, मत/मद), and amounts come out as words. These general sound-alike rules
put the words back into the spelling the lexicon knows. They never run on typed text or OCR.
The reply still shows what was actually heard; only the checks see the normalised copy.
"""

from __future__ import annotations

import re

from app.checks.lexicon import DEV, L, R, normalize

# Each rule: a pattern for the sound-alike forms -> the spelling the lexicon uses.
_DEV_WORDS = [
    (r"(?:भेज|बेज|भेझ|बेझ|भैज|बैज)", "भेज"),
    (r"(?:हज़ार|हजार|हजा|हाजार|हज़ा|हज्जार)", "हज़ार"),
    (r"(?:रुपये|रुपए|रूपये|रूपए|रूपै|रुपै|रूपे|रुपे|रुपया|रूपया|रुपय|रूपय)", "रुपये"),
    (r"(?:पैसे|पेसे|पैसा|पेसा|पइसे)", "पैसे"),
    # Words the family-emergency pattern looks for: ट/त and short/long vowels swap.
    (r"(?:बेटा|बेता|बेट्टा)", "बेटा"), (r"(?:बेटी|बेती)", "बेटी"), (r"(?:मम्मी|ममी|मम्मि)", "मम्मी"),
    (r"(?:पापा|पप्पा|पाप्पा)", "पापा"), (r"(?:अस्पताल|आस्पताल|हस्पताल|असपताल|अस्पतल)", "अस्पताल"),
    (r"(?:एक्सीडेंट|एक्सिडेंट|ऐक्सीडेंट|एक्सीडैंट)", "एक्सीडेंट"),
]
# Context rules: a sound-alike that is also a common word is fixed only next to the word it belongs to.
_DEV_CONTEXT = [
    (rf"{L}भेज(\s*)(?:तो|ते|थो){R}", r"भेज\1दो"),  # "भेज तो" -> "भेज दो"; "तो" alone means "then"
    (rf"{L}(?:मद|मध|मात){R}(\s*)(?=बता)", r"मत\1"),  # "मद बताना" -> "मत बताना"; "मदद" is untouched
]
_ROMAN_WORDS = [
    (r"\b(?:bhej|bej|bhejh|bhaij)\s*(?:do|doo|tho|to|de)\b", "bhej do"),
    (r"\b(?:bhejo|bejo|bhejoo)\b", "bhejo"),
    (r"\b(?:paise|paisay|paisey|pese|paisa)\b", "paise"),
    (r"\b(?:rupaye|rupay|rupaiye|rupiye|rupye|ruppe)\b", "rupaye"),
    (r"\b(?:hazaar|hazar|hajar|hajaar|hazzar|hajjar)\b", "hazaar"),
    (r"\b(?:mat|mad|math)\s+(?=bata)", "mat "),
]

# Whisper often leaves Hindi unpunctuated, so a later "मत बताना" would read as negating "पैसे भेज दो".
# A Hindi imperative ends its clause: mark the break after one when no punctuation follows.
# A comma after it ("भेज दो, किसी को मत बताना") is a clause break too.
_IMPERATIVE_END = [
    re.compile(normalize(rf"{L}((?:भेज|दे|कर|बता|ला)\s*(?:दो|दीजिए|दीजिये)|भेजो|करो|भेजिए|भेजिये){R}\s*,?(?!\s*[।.!?])")),
    re.compile(r"\b((?:bhej|de|kar)\s*do|bhejo|karo|bhejiye)\b\s*,?(?!\s*[.!?])", re.IGNORECASE),
]

# --- spoken amounts ------------------------------------------------------------------------------------

_SMALL = {
    # English
    "one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # Hindi in Devanagari (after lexicon.normalize: no nukta, chandrabindu folded)
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9,
    "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "भीस": 20, "पच्चीस": 25, "तीस": 30, "चालीस": 40,
    "पचास": 50, "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90, "डेढ़": 1.5, "डेढ": 1.5, "ढाई": 2.5,
    # Hindi in Roman script ("saath" is left out: it also means "with")
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7,
    "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20, "bis": 20,
    "pachees": 25, "pachchees": 25, "tees": 30, "chalees": 40, "chalis": 40, "pachaas": 50, "pachas": 50,
    "sattar": 70, "assi": 80, "nabbe": 90, "dedh": 1.5, "dhai": 2.5,
}
_HUNDRED = {"hundred", "सौ", "sau", "so"}
_BIG = {"thousand": 1000, "हज़ार": 1000, "हजार": 1000, "hazaar": 1000, "lakh": 100000, "lac": 100000,
        "लाख": 100000, "crore": 10000000, "करोड़": 10000000, "करोड": 10000000, "karod": 10000000}
_SMALL = {normalize(k): v for k, v in _SMALL.items()}
_HUNDRED = {normalize(k) for k in _HUNDRED}
_BIG = {normalize(k): v for k, v in _BIG.items()}
_NUMBER_WORDS = sorted({*_SMALL, *_HUNDRED, *_BIG, "and"}, key=len, reverse=True)
_WORD = rf"(?<![\w{DEV}])(?:{'|'.join(re.escape(w) for w in _NUMBER_WORDS)}|\d+)(?![\w{DEV}])"
_RUN = re.compile(rf"{_WORD}(?:[\s-]+{_WORD})*", re.IGNORECASE)


def _value(words: list[str]) -> int | None:
    """"twenty thousand" -> 20000, "बीस हज़ार" -> 20000, "one lakh fifty thousand" -> 150000.

    Only a run with hundred, thousand, lakh or crore in it is an amount: "do" alone means "give", "ek" means "a".
    """
    if not any(w in _HUNDRED or w in _BIG for w in words):
        return None
    total, current = 0.0, 0.0
    for w in words:
        if w == "and":
            continue
        if w.isdigit():
            current += int(w)
        elif w in _SMALL:
            current += _SMALL[w]
        elif w in _HUNDRED:
            current = (current or 1) * 100
        elif w in _BIG:
            total += (current or 1) * _BIG[w]
            current = 0
    total += current
    return int(total) if total >= 100 else None


def _amounts(text: str) -> str:
    def swap(m: re.Match[str]) -> str:
        words = [w.lower() for w in re.split(r"[\s-]+", m.group(0)) if w]
        value = _value(words)
        return str(value) if value is not None else m.group(0)

    return _RUN.sub(swap, text)


def normalise(text: str) -> str:
    """The transcript in the spellings the checks know, with spoken amounts as digits."""
    text = normalize(text)
    for pattern, word in _DEV_WORDS:
        text = re.sub(rf"{L}{normalize(pattern)}{R}", normalize(word), text)
    for pattern, repl in _DEV_CONTEXT:
        text = re.sub(normalize(pattern), normalize(repl), text)
    for pattern, word in _ROMAN_WORDS:
        text = re.sub(pattern, word, text, flags=re.IGNORECASE)
    text = _IMPERATIVE_END[0].sub(r"\1। ", text)
    text = _IMPERATIVE_END[1].sub(r"\1. ", text)
    return _amounts(text)
