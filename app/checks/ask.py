"""Ask check: would a real authority ask for this?"""

from __future__ import annotations

import re

from app.checks import lexicon
from app.models import Fields, PointsTo, Signal, Strength
from app.registry import is_official_payee

# SEBI validated handles: name@valid or name@valid<bank>.
VALID_HANDLE = re.compile(r"@valid[a-z]*$", re.IGNORECASE)


def _weak(code: str, en: str, hi: str) -> Signal:
    return Signal("ask", f"ask.{code}", Strength.WEAK, PointsTo.FAKE, en, hi)


def _payee_official(fields: Fields) -> bool:
    # Official means a SEBI @valid handle or an entry in official_payees.json. With no payee named, nothing is proven.
    return bool(fields.payees) and all(VALID_HANDLE.search(p) or is_official_payee(p) for p in fields.payees)


def run(fields: Fields, text: str) -> list[Signal]:
    text = lexicon.normalize(text)
    urgent = fields.deadline_hours is not None and fields.deadline_hours <= 24
    refund = lexicon.promises_refund(text)

    pressure_en: list[str] = []
    pressure_hi: list[str] = []
    for on, en, hi in (
        (urgent, "a short deadline", "कम समय की मोहलत"),
        (fields.asks_secrecy, "secrecy", "गोपनीयता"),
        (refund, "a promised refund", "पैसे लौटाने का वादा"),
        (fields.threatens_user, "an arrest threat", "गिरफ़्तारी की धमकी"),
    ):
        if on:
            pressure_en.append(en)
            pressure_hi.append(hi)

    signals: list[Signal] = []
    if fields.demands_payment and not _payee_official(fields) and pressure_en:
        signals.append(
            Signal(
                "ask",
                "ask.payment_to_non_official_payee",
                Strength.STRONG,
                PointsTo.FAKE,
                f"It asks you to send money to a non-official account, with pressure: {', '.join(pressure_en)}. "
                "Real authorities do not ask for money this way.",
                f"यह आपसे किसी गैर-आधिकारिक खाते में पैसे भेजने को कहता है, दबाव के साथ: {', '.join(pressure_hi)}। "
                "असली अधिकारी इस तरह पैसे नहीं माँगते।",
            )
        )
    if fields.asks_secrecy:
        signals.append(
            _weak(
                "secrecy",
                "It tells you to keep this secret from your family, friends or bank. Real authorities never ask this.",
                "यह आपसे यह बात परिवार, दोस्तों या बैंक से छिपाने को कहता है। असली अधिकारी ऐसा कभी नहीं कहते।",
            )
        )
    if urgent:
        signals.append(
            _weak(
                "urgent_deadline",
                "It gives you very little time to act. Scammers rush you so you cannot check.",
                "यह आपको बहुत कम समय देता है। ठग जल्दबाज़ी करवाते हैं ताकि आप जाँच न कर सकें।",
            )
        )
    if fields.threatens_user:
        signals.append(
            _weak(
                "arrest_threat",
                "It threatens you with arrest. No police, CBI, ED or court arrests anyone on a phone or video call.",
                "यह आपको गिरफ़्तारी की धमकी देता है। कोई पुलिस, CBI, ED या न्यायालय फ़ोन या वीडियो कॉल पर गिरफ़्तारी नहीं करता।",
            )
        )
    if refund:
        signals.append(
            _weak(
                "refund_promise",
                "It promises to return your money after 'verification'. This is a common scam trick.",
                "यह 'जाँच' के बाद पैसे लौटाने का वादा करता है। यह ठगी का आम तरीका है।",
            )
        )
    return signals
