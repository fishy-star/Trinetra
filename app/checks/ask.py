"""Ask check: would a real authority ask for this?"""

from __future__ import annotations

import re
from typing import Any

from app.checks import lexicon
from app.models import Fields, PointsTo, Signal, Strength
from app.registry import is_official_payee, normalize_payee

# SEBI validated handles: name@valid or name@valid<bank>.
VALID_HANDLE = re.compile(r"@valid[a-z]*$", re.IGNORECASE)


def _weak(code: str, en: str, hi: str) -> Signal:
    return Signal("ask", f"ask.{code}", Strength.WEAK, PointsTo.FAKE, en, hi)


def _payee_official(fields: Fields, issuer: dict[str, Any] | None = None) -> bool:
    """Official means a SEBI @valid handle, an entry in official_payees.json, or a payee the issuer registered.

    With no payee named, nothing is proven.
    """
    own = {normalize_payee(p) for p in (issuer or {}).get("payees", [])}
    return bool(fields.payees) and all(
        VALID_HANDLE.search(p) or is_official_payee(p) or normalize_payee(p) in own for p in fields.payees
    )


def run(fields: Fields, text: str, issuer: dict[str, Any] | None = None) -> list[Signal]:
    text = lexicon.normalize(text)
    urgent = fields.deadline_hours is not None and fields.deadline_hours <= 24
    refund = lexicon.promises_refund(text)
    reward = fields.offers_reward and fields.asks_for_money

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
    # What makes a request for money a scam signal and not a friend splitting a bill: something pushing you.
    lure_en, lure_hi = list(pressure_en), list(pressure_hi)
    for on, en, hi in (
        (fields.threatens_account, "a threat to your account", "आपके खाते को लेकर धमकी"),
        (reward, "a promised reward", "इनाम का लालच"),
        (bool(fields.claimed_identity), "a claimed identity you cannot check", "ऐसी पहचान जिसकी आप जाँच नहीं कर सकते"),
    ):
        if on:
            lure_en.append(en)
            lure_hi.append(hi)

    signals: list[Signal] = []
    if fields.demands_payment and not _payee_official(fields, issuer) and pressure_en:
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
    elif fields.asks_for_money and not _payee_official(fields, issuer) and lure_en:
        signals.append(
            Signal(
                "ask",
                "ask.money_request",
                Strength.STRONG,
                PointsTo.FAKE,
                f"It asks you to send money, with pressure: {', '.join(lure_en)}. Scammers ask for money this way.",
                f"यह आपसे पैसे भेजने को कहता है, दबाव के साथ: {', '.join(lure_hi)}। ठग इसी तरह पैसे माँगते हैं।",
            )
        )
    if fields.asks_for_credentials:
        signals.append(
            Signal(
                "ask",
                "ask.credential_request",
                Strength.STRONG,
                PointsTo.FAKE,
                "It asks you to verify your identity or share details such as an OTP, PIN, KYC or card number "
                "through a message. Banks and officials never ask for this by message.",
                "यह आपसे संदेश के ज़रिए पहचान की पुष्टि करने या OTP, PIN, KYC या कार्ड जैसी जानकारी देने को कहता है। "
                "बैंक और अधिकारी संदेश से यह कभी नहीं माँगते।",
            )
        )
    if reward:
        signals.append(
            _weak(
                "reward_for_payment",
                "It promises a prize, a big return or a gift in exchange for money. That is how these scams work.",
                "यह पैसों के बदले इनाम, बड़ा मुनाफ़ा या तोहफ़ा देने का वादा करता है। ठगी इसी तरह होती है।",
            )
        )
    if fields.threatens_account:
        signals.append(
            _weak(
                "account_threat",
                "It threatens that your account will be blocked or a service cut off. Scammers use this to scare you.",
                "यह आपका खाता बंद करने या सेवा काटने की धमकी देता है। ठग डराने के लिए ऐसा करते हैं।",
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
                "It rushes you to act now. Scammers do this so you cannot stop and check.",
                "यह आपसे तुरंत कदम उठाने को कहता है। ठग ऐसा इसलिए करते हैं ताकि आप रुककर जाँच न कर सकें।",
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
