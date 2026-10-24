"""Hindi and English keyword patterns used by the field extractor and the checks.

All matching runs on text passed through `normalize`, which folds the Devanagari
spelling variants that OCR and different keyboards produce (nukta, chandrabindu).
Patterns are normalized the same way when compiled, so they can be written naturally.
"""

from __future__ import annotations

import re
import unicodedata

DEV = "ऀ-ॿ"
# Python's \b breaks inside Devanagari words (vowel signs are not \w), so use explicit edges.
L = rf"(?<![\w{DEV}])"
R = rf"(?![\w{DEV}])"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return text.replace("़", "").replace("ँ", "ं")


def _c(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{normalize(p)})" for p in patterns), re.IGNORECASE)


# --- sentences and negation -------------------------------------------------

_ABBREV = re.compile(r"(?:\b(?:rs|no|mr|mrs|ms|dr|sh|smt|a/c|ref|st|vs)\.)$", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """Split on sentence punctuation followed by space, keeping abbreviations like "Rs." together."""
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text)
    out: list[str] = []
    for p in parts:
        if out and _ABBREV.search(out[-1]):
            out[-1] = f"{out[-1]} {p}"
        elif p.strip():
            out.append(p)
    return out


NEGATION = _c(
    r"\b(?:not|never|nor|don't|dont|doesn't|cannot|can't|won't)\b",
    r"\bno\b(?!\.)",  # but not "A/c No."
    rf"{L}(?:नहीं|न|मत|ना){R}",
)

# --- the ask -----------------------------------------------------------------

PAYMENT_VERB = _c(
    r"\b(?:pay|transfer|deposit|send|remit|wire)\b",
    r"\b(?:must|should|to) be (?:paid|deposited|transferred)\b",
    rf"{L}भेज(?:ें|ो|िए|ना|\s*दो|\s*दें|\s*दीजिए)",
    rf"{L}जमा\s*कर(?:ें|ो|िए|नी होगी|ना होगा|ना है|नी है)",
    rf"{L}ट्रांसफ़र\s*कर(?:ें|ो|िए|नी होगी|ना होगा|ना है|नी है)",
    rf"{L}भुगतान\s*कर(?:ें|ो|िए|ना होगा|ना है)",
)

SECRECY = _c(
    r"\b(?:do not|don't|dont|never)\s+(?:tell|disclose|inform|discuss|reveal|share|mention)\b"
    r"[^.]{0,40}?\b(?:anyone|anybody|any person|any one|family|friends|relatives|bank|home)\b",
    r"\bconfidential\b",
    r"\bsecrecy\b",
    r"\bkeep (?:this|it) (?:a )?(?:secret|confidential)\b",
    r"\b(?:do not|don't|dont)\s+(?:visit|contact|call)\s+(?:your|the)\s+(?:bank|branch)\b",
    rf"{L}किसी\s+को\s+(?:भी\s+)?(?:न|मत|नहीं)\s+बता",
    rf"{L}को\s+मत\s+बता",
    rf"{L}गोपनीय",
    rf"{L}बताने\s+पर",
    rf"{L}बताना\s+दंडनीय",
)
# A warning not to share an OTP or PIN is good advice, not secrecy.
SECRECY_EXCLUDE = _c(r"\b(?:otp|pin|password|cvv)\b", rf"{L}(?:पासवर्ड|पिन){R}")

ARREST = _c(
    r"\barrest(?:ed)?\b",
    r"\bwarrant\b",
    r"\bdigital (?:custody|arrest)\b",
    rf"{L}गिरफ़्तार",
    rf"{L}वारंट",
    rf"{L}हिरासत",
    rf"{L}डिजिटल\s+अरेस्ट",
)

AWARENESS = _c(r"\b(?:beware|awareness|be alert|be careful|scam|fraud)\b", rf"{L}(?:सावधान|सतर्क|जागरूक|ठगी|धोखाधड़ी)")

REFUND = _c(
    r"\b(?:will|shall|would) be (?:fully |entirely )?(?:returned|refunded|released)\b",
    r"\brefundable\b",
    r"\bpay (?:you )?back\b",
    r"\breturned to you\b",
    rf"{L}वापस\s+कर\s+द",
    rf"{L}लौटा\s+द",
)

URGENT = _c(
    r"\b(?:today|tonight|immediately|immediate|urgent|urgently|right now|at once)\b",
    rf"{L}(?:आज|तुरंत|फ़ौरन){R}",
)
HOURS = _c(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", rf"(\d+(?:\.\d+)?)\s*घंट")
MINUTES = _c(r"(\d+)\s*(?:minutes?|mins?)\b", rf"(\d+)\s*मिनट")
# "Today" or "immediately" means a deadline within the day.
URGENT_HOURS = 24.0


def asks_secrecy(text: str) -> bool:
    return any(not SECRECY_EXCLUDE.search(m.group(0)) for m in SECRECY.finditer(text))


def threatens_arrest(text: str, asks_for_money: bool) -> bool:
    """`asks_for_money`: the item demands payment or names a payee.

    "Beware of digital arrest" in an awareness notice warns about the scam, so awareness words
    cancel an arrest mention, but only when the item asks for no money. A forged notice
    that says "Beware: a warrant has been issued" and demands payment is still a threat.
    """
    return any(
        ARREST.search(s) and not NEGATION.search(s) and (asks_for_money or not AWARENESS.search(s))
        for s in sentences(text)
    )


def promises_refund(text: str) -> bool:
    return bool(REFUND.search(text))


def deadline_hours(text: str) -> float | None:
    found = [float(m.group(1) or m.group(2)) for m in HOURS.finditer(text)]
    found += [float(m.group(1) or m.group(2)) / 60 for m in MINUTES.finditer(text)]
    if URGENT.search(text):
        found.append(URGENT_HOURS)
    return min(found) if found else None


def payment_sentences(text: str) -> list[str]:
    """Sentences that tell the reader to pay. Warnings ("do not transfer money") are excluded."""
    return [s for s in sentences(text) if PAYMENT_VERB.search(s) and not NEGATION.search(s)]
