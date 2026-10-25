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
    text = re.sub(r"[ \t ]+", " ", text)
    return text.replace("़", "").replace("ँ", "ं")


def _c(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{normalize(p)})" for p in patterns), re.IGNORECASE)


# --- sentences and negation -------------------------------------------------

_ABBREV = re.compile(r"(?:\b(?:rs|no|mr|mrs|ms|dr|sh|smt|a/c|ref|st|vs)\.)$", re.IGNORECASE)


_SENTENCE_END = re.compile(r"[.!?।:]\s*$")


def unwrap(text: str) -> str:
    """Rejoin lines that OCR or PDF extraction wrapped inside a paragraph.

    A wrapped line runs close to the full text width and does not end a sentence. Short lines
    (headers, signatures, "A/c No." boxes) keep their line break.
    """
    lines = text.splitlines()
    width = max((len(ln.strip()) for ln in lines), default=0)
    out: list[str] = []
    for ln in lines:
        prev = out[-1] if out else ""
        if prev.strip() and ln.strip() and len(prev.strip()) >= 0.6 * width and not _SENTENCE_END.search(prev):
            out[-1] = f"{prev.rstrip()} {ln.strip()}"
        else:
            out.append(ln)
    return "\n".join(out)


def sentences(text: str) -> list[str]:
    """Split on sentence punctuation followed by space, keeping abbreviations like "Rs." together."""
    parts = re.split(r"(?<=[.!?।])\s+|\n+", unwrap(text))
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
    # The legal threats every digital-arrest script uses.
    r"\bclear your name\b",
    r"\blegal action\b",
    r"\b(?:fir|case|complaint)\s+(?:has been\s+|is\s+)?(?:registered|filed|lodged)\s+against you\b",
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
    r"\b(?:today|tonight|immediately|immediate|urgent|urgently|right now|at once|asap|right away|fast)\b",
    r"\btrust me\b",
    r"\b(?:jaldi|turant|foran|fauran)\b",
    r"\babhi\b(?!\s*(?:tak|bhi))",  # "abhi tak" means "so far", not "now"
    rf"{L}(?:आज|तुरंत|फ़ौरन){R}",
)
# "Valid for 5 minutes" in an OTP message is how long a code works, not a deadline to act.
VALIDITY = _c(r"\bvalid(?:\s+(?:for|till|until|upto|up to))?\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\b",
              rf"\d+\s*(?:मिनट|घंटे)\s*(?:के लिए\s*)?मान्य")
HOURS = _c(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", rf"(\d+(?:\.\d+)?)\s*घंट")
MINUTES = _c(r"(\d+)\s*(?:minutes?|mins?)\b", rf"(\d+)\s*मिनट")
# "Today" or "immediately" means a deadline within the day.
URGENT_HOURS = 24.0


_SHARE = re.compile(r"\b(?:share|reveal)\b", re.IGNORECASE)


def asks_secrecy(text: str) -> bool:
    """Being told to hide something. "Do not share it with anyone" about an OTP or PIN is advice, not secrecy."""
    about_codes = bool(SECRECY_EXCLUDE.search(text))
    return any(
        not SECRECY_EXCLUDE.search(m.group(0)) and not (about_codes and _SHARE.search(m.group(0)))
        for m in SECRECY.finditer(text)
    )


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
    text = VALIDITY.sub(" ", text)
    found = [float(m.group(1) or m.group(2)) for m in HOURS.finditer(text)]
    found += [float(m.group(1) or m.group(2)) / 60 for m in MINUTES.finditer(text)]
    if URGENT.search(text):
        found.append(URGENT_HOURS)
    return min(found) if found else None


def payment_sentences(text: str) -> list[str]:
    """Sentences that tell the reader to pay. Warnings ("do not transfer money") are excluded."""
    return [s for s in sentences(text) if PAYMENT_VERB.search(s) and not NEGATION.search(s)]


# --- money, credentials, rewards and account threats (English, Hindi and Hinglish) -----------------------

_MONEY = (r"(?:(?<![a-z])(?:money|cash|paise|paisa|rupaye|rupees?|rs\.?|inr|fees?|amount)(?![a-z])"
          r"|₹\s*\d|(?<![\d.])\d[\d,]{2,}(?![\d])|\d+\s*(?:k|lakh|lakhs|crore|thousand)\b)")
MONEY_REQUEST = _c(
    rf"\b(?:send|give|transfer|pay|lend|deposit)\b(?:\s+(?:me|us|him|her|it))?[^.!?\n]{{0,30}}?{_MONEY}",
    r"\b(?:i|we)\s+(?:really\s+|urgently\s+)?need\s+(?:some\s+)?(?:money|cash|rs\.?\s*\d|₹\s*\d|\d[\d,]{2,})",
    rf"{_MONEY}[^.!?\n]{{0,30}}?\b(?:bhejo|bhej\s*do|bhejiye|bhej\s*dijiye|bhejna|transfer\s*karo|jama\s*karo|de\s*do|dedo)\b",
    rf"\b(?:bhejo|bhej\s*do|bhejiye)\b[^.!?\n]{{0,30}}?{_MONEY}",
    rf"{L}(?:पैसे|रुपये|रुपए|₹\s*\d|\d[\d,]{{2,}})[^।!?\n]{{0,30}}भेज",
)
CREDENTIALS = _c(
    r"\bverify\s+(?:your\s+)?(?:identity|account|kyc|pan|aadhaar|card|debit card|credit card|details|"
    r"bank details|bank account details|account details)\b",
    r"\b(?:update|complete|link)\s+(?:your\s+)?(?:kyc|e-?kyc|pan(?:\s+card)?|aadhaar)\b",
    r"\b(?:share|send|tell|give|enter|provide|confirm|reply with)\b[^.!?\n]{0,25}?"
    r"\b(?:otp|pin|cvv|password|card number|card details|expiry date)\b",
    r"\bre-?activate\s+(?:your\s+)?(?:account|card|sim)\b",
    r"\bkyc\s*update\s*kar(?:o|en|ein|iye|na|wa\w*)\b",
    r"\botp\s*(?:batao|bata\s*do|bhejo|bhej\s*do|share\s*karo|de\s*do)\b",
    rf"{L}KYC\s*अपडेट\s*कर(?:ें|ो|िए|ाएं|ाएँ)",
    rf"{L}OTP\s*(?:बताएं|बताएँ|बताइए|बताओ|भेजें|शेयर\s*करें)",
    rf"{L}पहचान\s*(?:की\s*)?(?:पुष्टि|सत्यापन)\s*कर",
)
# Telling you to go to the branch is how a real bank asks; it is not a credential request over a message.
CREDENTIALS_EXCLUDE = _c(r"\bbranch\b", rf"{L}शाखा")
REWARD = _c(
    r"\b(?:won|winner|prize|lottery|lucky draw|jackpot|reward|bonus|cashback|guaranteed|double your money)\b",
    r"\bi\s*(?:will|'ll)\s+give\s+(?:you|u)\b",
    r"\bfree\s+(?:gift|tesla|iphone|phone|car|bike|laptop|recharge)\b",
    r"\bget\s+(?:rs\.?\s*|₹\s*)?\d[\d,]*\s*(?:back|in \d+ days)\b",
    r"\b(?:inaam|inam|lottery|jeeta|jeete|jeeti|wapas milega|double ho)\b",
    rf"{L}(?:लॉटरी|इनाम|पुरस्कार|जीती|जीता|जीते)",
)
ACCOUNT_THREAT = _c(
    r"\b(?:account|a/c|card|debit card|credit card|sim|number|connection|service|power|electricity|wallet|upi)\b"
    r"[^.!?\n]{0,40}?\b(?:locked|blocked|suspended|frozen|deactivated|disconnected|cut off|closed|terminated)\b",
    r"\b(?:suspicious|unusual|unauthori[sz]ed)\s+(?:activity|transaction|login)\b",
    r"\b(?:band|block|suspend|cut)\s*ho\s*ja(?:yega|ayega|egi|ayegi)\b",
    rf"{L}(?:बंद\s*(?:हो|कर\s*दिया)\s*जाएगा|ब्लॉक\s*(?:हो|कर\s*दिया)\s*जाएगा|काट\s*(?:दिया|दी)\s*जाएगा|"
    rf"काट\s*दी\s*जाएगी|निलंबित)",
)
# A negation right next to the threat ("will not be blocked", "बंद नहीं होगा") cancels it.
_NEAR_NEGATION = _c(r"\b(?:not|never|won't|no longer)\b", rf"{L}(?:नहीं|न){R}")
FAMOUS = _c(
    r"\b(?:elon musk|mukesh ambani|nita ambani|anil ambani|ratan tata|gautam adani|bill gates|mark zuckerberg|"
    r"sundar pichai|narendra modi|pm modi|shah ?rukh khan|salman khan|amitabh bachchan|akshay kumar|virat kohli|"
    r"sachin tendulkar|ms dhoni|mahendra singh dhoni)\b",
    rf"{L}(?:मुकेश\s*अंबानी|रतन\s*टाटा|नरेंद्र\s*मोदी|अमिताभ\s*बच्चन|शाहरुख)",
)
UNNAMED_BANK = _c(r"\bbank\b", r"\b(?:debit|credit|atm)\s+card\b", rf"{L}बैंक")


def asks_for_money(text: str) -> bool:
    return any(MONEY_REQUEST.search(s) and not NEGATION.search(s) for s in sentences(text))


def asks_for_credentials(text: str) -> bool:
    return any(
        CREDENTIALS.search(s) and not NEGATION.search(s) and not CREDENTIALS_EXCLUDE.search(s)
        for s in sentences(text)
    )


def offers_reward(text: str) -> bool:
    return any(REWARD.search(s) and not NEGATION.search(s) for s in sentences(text))


def threatens_account(text: str) -> bool:
    for m in ACCOUNT_THREAT.finditer(text):
        around = text[max(0, m.start() - 15): m.end() + 15]
        if not _NEAR_NEGATION.search(around):
            return True
    return False


def claimed_identity(text: str, named_sender: str | None) -> str | None:
    """A famous person, or "your bank" without saying which: claims a reader cannot check."""
    famous = FAMOUS.search(text)
    if famous:
        return famous.group(0)
    if named_sender is None and UNNAMED_BANK.search(text):
        return "your bank"
    return None


# --- a relative in trouble asking for money (voice notes only: a voice cannot prove who is speaking) -------

RELATIVE = _c(
    r"\b(?:beta|beti|papa|mummy|mumma|mom|mum|dad|daddy|bhaiya|bhai|didi|chacha|mama|nana|nani|dadi)\b",
    r"\b(?:your|ur)\s+(?:son|daughter|brother|sister|nephew|niece|grandson|granddaughter|husband|wife)\b",
    rf"{L}(?:बेटा|बेटी|पापा|मम्मी|माँ|मां|भैया|भाई|दीदी|चाचा|मामा|नाना|नानी|दादी|दादा){R}",
)
TROUBLE = _c(
    r"\b(?:hospital|accident|police|stuck|jail|arrested|emergency|kidnapped|thana|musibat|phas\s*gaya|phans\s*gaya)\b",
    r"\blost my (?:phone|wallet|bag|passport)\b",
    rf"{L}(?:अस्पताल|हॉस्पिटल|एक्सीडेंट|दुर्घटना|पुलिस|थाने|थाना|मुसीबत|जेल|इमरजेंसी)",
    rf"{L}फं?स\s*(?:गया|गई|गयी)",
)
RELATIVE_IDENTITY = "a relative"


def family_emergency(text: str) -> bool:
    """A claimed relative, some trouble, and a request for money."""
    return bool(RELATIVE.search(text) and TROUBLE.search(text)
                and (asks_for_money(text) or payment_sentences(text)))
