"""Reply templates, Hindi and English. No scores and no model names, ever."""

from __future__ import annotations

import re

from app.models import Kind, PointsTo, Signal, Strength, Verdict
from app.registry import verify_route

VERDICT_LINE = {
    "en": {
        Verdict.LIKELY_FAKE: "Likely fake. Do not pay.",
        Verdict.SUSPICIOUS: "Suspicious. Check before you pay.",
        Verdict.CANNOT_VERIFY: "We could not verify this. That does not mean it is safe.",
        Verdict.VERIFIED_OFFICIAL: "Verified: this matches an official source.",
        Verdict.COULD_NOT_CHECK: "We could not read this file. Send a clearer photo, or check with the sender "
        "through an official number.",
    },
    "hi": {
        Verdict.LIKELY_FAKE: "संभवतः नकली। कोई भुगतान न करें।",
        Verdict.SUSPICIOUS: "संदिग्ध। भुगतान से पहले जाँच करें।",
        Verdict.CANNOT_VERIFY: "हम इसकी पुष्टि नहीं कर सके। इसका मतलब यह नहीं कि यह सुरक्षित है।",
        Verdict.VERIFIED_OFFICIAL: "पुष्टि हुई: यह आधिकारिक स्रोत से मेल खाता है।",
        Verdict.COULD_NOT_CHECK: "हम यह फ़ाइल नहीं पढ़ सके। साफ़ फ़ोटो भेजें, या आधिकारिक नंबर पर भेजने वाले से पुष्टि करें।",
    },
}

# The action line after a fake or suspicious verdict, tailored to who the message claims to be from.
ACTION_FAKE = {
    "authority": {
        "en": "Do not pay. No police, CBI, ED or court arrests anyone on a video call or asks for money to clear your "
        "name. Reply 1 to report this. Reply 2 if you already paid.",
        "hi": "भुगतान न करें। पुलिस, CBI, ED या कोई न्यायालय वीडियो कॉल पर गिरफ़्तारी नहीं करता और नाम साफ़ करने के लिए "
        "पैसे नहीं माँगता। रिपोर्ट करने के लिए 1 भेजें। अगर आप भुगतान कर चुके हैं तो 2 भेजें।",
    },
    "finance": {
        "en": "Do not pay. Your bank will never ask you to pay a fee to a UPI ID or update KYC through a link. Contact "
        "your bank through its official app or branch. Reply 1 to report this. Reply 2 if you already paid.",
        "hi": "भुगतान न करें। आपका बैंक कभी भी किसी UPI ID पर शुल्क भेजने या किसी लिंक से KYC अपडेट करने को नहीं कहेगा। "
        "अपने बैंक से उसके आधिकारिक ऐप या शाखा के ज़रिए संपर्क करें। रिपोर्ट करने के लिए 1 भेजें। अगर आप भुगतान कर चुके "
        "हैं तो 2 भेजें।",
    },
    "general": {
        "en": "Do not pay. Check with the sender through a number or website you already trust, not one given in "
        "this message. Reply 1 to report this. Reply 2 if you already paid.",
        "hi": "भुगतान न करें। भेजने वाले से ऐसे नंबर या वेबसाइट के ज़रिए पुष्टि करें जिस पर आपको पहले से भरोसा है, इस संदेश "
        "में दिए गए नंबर से नहीं। रिपोर्ट करने के लिए 1 भेजें। अगर आप भुगतान कर चुके हैं तो 2 भेजें।",
    },
}
# RBI is a regulator, but people meet it as "my bank" scams, so it gets the bank line.
FINANCE_IDS = frozenset({"rbi", "sebi", "npci"})
FINANCE_TYPES = frozenset({"bank", "financial"})
AUTHORITY_TYPES = frozenset({"government", "court", "regulator", "police"})
ACTION_VOICE = {
    "en": "Call the person back on the number you already have for them before you send anything.",
    "hi": "कुछ भी भेजने से पहले उस व्यक्ति को उस नंबर पर वापस कॉल करें जो आपके पास पहले से है।",
}
FOOTER_UNREVIEWED = {
    "en": "Automated checks. A person has not yet reviewed this result.",
    "hi": "स्वचालित जाँच। किसी व्यक्ति ने अभी इसकी समीक्षा नहीं की है।",
}
FOOTER_REVIEWED = {
    "en": "Reviewed by the Trinetra team.",
    "hi": "Trinetra टीम द्वारा समीक्षा की गई।",
}
ALREADY_PAID = {
    "en": "Call 1930 now and tell your bank. Speed matters, because money can be held if it is reported quickly. "
    "You can also file at cybercrime.gov.in.",
    "hi": "अभी 1930 पर कॉल करें और अपने बैंक को बताएँ। जल्दी रिपोर्ट करने पर पैसा रोका जा सकता है। "
    "आप cybercrime.gov.in पर भी शिकायत कर सकते हैं।",
}
# R0 for a text message: nothing to read because it is too short, not because a file was unreadable.
TOO_SHORT = {
    "en": ("This message is too short to check.", "Forward the full message, or a screenshot of it."),
    "hi": ("यह संदेश जाँच के लिए बहुत छोटा है।", "पूरा संदेश या उसका स्क्रीनशॉट फ़ॉरवर्ड करें।"),
}

# Every reply names its evidence. When no check fired, say so without implying safety.
NO_SIGNALS = {
    "en": "We found no warning signs, but we could not confirm who sent it. If it asks for money, confirm "
    "through an official number first.",
    "hi": "हमें कोई चेतावनी संकेत नहीं मिला, लेकिन हम यह पुष्टि नहीं कर सके कि इसे किसने भेजा। अगर इसमें पैसे माँगे "
    "गए हैं, तो पहले किसी आधिकारिक नंबर से पुष्टि करें।",
}

# R1: the item matches a fake a reviewer has confirmed. Always shown with the reviewed footer.
KNOWN_FAKE = {
    "en": "It matches a notice or payee our team has already confirmed as fake.",
    "hi": "यह किसी ऐसे नोटिस या भुगतान खाते से मेल खाता है जिसे हमारी टीम पहले ही नकली के रूप में पुष्टि कर चुकी है।",
}

CHECK_ORDER = ("source", "ask", "media")
_RANK = {Strength.PROOF: 0, Strength.STRONG: 1, Strength.WEAK: 2}


def detect_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    dev = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    return "hi" if letters and dev / len(letters) > 0.5 else "en"


def reasons(verdict: Verdict, signals: list[Signal], lang: str) -> list[str]:
    """Up to three reasons, one per check that fired, strongest signal first."""
    wanted = PointsTo.GENUINE if verdict is Verdict.VERIFIED_OFFICIAL else PointsTo.FAKE
    out = []
    for check in CHECK_ORDER:
        mine = sorted((s for s in signals if s.check == check and s.points_to is wanted), key=lambda s: _RANK[s.strength])
        if mine:
            out.append(mine[0].reason_hi if lang == "hi" else mine[0].reason_en)
    return out[:3]


def sender_group(sender_type: str | None, sender_id: str | None) -> str:
    if sender_id in FINANCE_IDS or sender_type in FINANCE_TYPES:
        return "finance"
    if sender_type in AUTHORITY_TYPES:
        return "authority"
    return "general"


def verify_line(sender_type: str | None, sender_id: str | None, lang: str) -> str | None:
    route = verify_route(sender_type, sender_id)
    return route[lang] if route else None


def build(
    verdict: Verdict,
    signals: list[Signal],
    kind: Kind,
    lang: str,
    sender: str = "general",
    reviewed: bool = False,
    verify: str | None = None,
) -> str:
    """`sender` is the group from `sender_group`: "authority", "finance" or "general".

    `reviewed` is set for R1, a match to a reviewer-confirmed fake. `verify` is a line telling the
    person where to check the notice at its source; it is shown only when we could not settle it.
    """
    if verdict is Verdict.COULD_NOT_CHECK and kind is Kind.TEXT:
        parts = [" ".join(TOO_SHORT[lang])]
    else:
        parts = [VERDICT_LINE[lang][verdict]]
    if verdict is not Verdict.COULD_NOT_CHECK:
        lines = reasons(verdict, signals, lang)
        if reviewed and verdict is Verdict.LIKELY_FAKE:
            lines = [KNOWN_FAKE[lang], *lines][:3]
        parts.append("\n".join(f"• {r}" for r in lines) if lines else NO_SIGNALS[lang])
    if verify and verdict in (Verdict.SUSPICIOUS, Verdict.CANNOT_VERIFY):
        parts.append(verify)
    if verdict in (Verdict.LIKELY_FAKE, Verdict.SUSPICIOUS):
        parts.append(ACTION_FAKE[sender][lang])
    if kind is Kind.VOICE:
        parts.append(ACTION_VOICE[lang])
    parts.append((FOOTER_REVIEWED if reviewed else FOOTER_UNREVIEWED)[lang])
    return "\n\n".join(parts)



# --- the bot's short reply and its "Why?" ------------------------------------------------------
# The bot answers older, non-technical users: a plain verdict line, the single strongest reason,
# one action line and a one-line review status. Everything else is one tap away, behind "Why?".
# The web page and the API keep the full reply from build().

SHORT_VERDICT = {
    "en": {
        Verdict.LIKELY_FAKE: "Likely fake. Do not pay.",
        Verdict.SUSPICIOUS: "Suspicious. Check before you pay.",
        Verdict.CANNOT_VERIFY: "We couldn't verify this.",
        Verdict.VERIFIED_OFFICIAL: "Verified official.",
        Verdict.COULD_NOT_CHECK: "We couldn't read this file.",
    },
    "hi": {
        Verdict.LIKELY_FAKE: "संभवतः नकली। कोई भुगतान न करें।",
        Verdict.SUSPICIOUS: "संदिग्ध। भुगतान से पहले जाँच करें।",
        Verdict.CANNOT_VERIFY: "हम इसकी पुष्टि नहीं कर सके।",
        Verdict.VERIFIED_OFFICIAL: "आधिकारिक, पुष्टि हुई।",
        Verdict.COULD_NOT_CHECK: "हम यह फ़ाइल नहीं पढ़ सके।",
    },
}
# One action line. Fake and suspicious replies get the line for who the message claims to be from.
SHORT_ACTION_FAKE = {
    "authority": {
        "en": "No police, CBI, ED or court arrests anyone on a video call or asks for money to clear your name.",
        "hi": "पुलिस, CBI, ED या कोई न्यायालय वीडियो कॉल पर गिरफ़्तारी नहीं करता और नाम साफ़ करने के लिए पैसे नहीं माँगता।",
    },
    "finance": {
        "en": "Your bank will never ask you to pay a fee to a UPI ID or update KYC through a link.",
        "hi": "आपका बैंक कभी भी किसी UPI ID पर शुल्क भेजने या किसी लिंक से KYC अपडेट करने को नहीं कहेगा।",
    },
    "general": {
        "en": "Check with the sender on a number you already trust, not one given in this message.",
        "hi": "भेजने वाले से ऐसे नंबर पर पुष्टि करें जिस पर आपको पहले से भरोसा है, इस संदेश में दिए नंबर पर नहीं।",
    },
}
SHORT_ACTION = {
    # "Cannot verify" must never read as safe, so its action line says so.
    Verdict.CANNOT_VERIFY: {
        "en": "That does not mean it is safe. If it asks for money, confirm through an official number first.",
        "hi": "इसका मतलब यह नहीं कि यह सुरक्षित है। अगर इसमें पैसे माँगे गए हैं, तो पहले किसी आधिकारिक नंबर से पुष्टि करें।",
    },
    Verdict.VERIFIED_OFFICIAL: {
        "en": "Act on it only through the official office or website it names.",
        "hi": "इस पर केवल उसमें बताए गए आधिकारिक कार्यालय या वेबसाइट के ज़रिए ही कदम उठाएँ।",
    },
    Verdict.COULD_NOT_CHECK: {
        "en": "Send a clearer photo, or check with the sender through an official number.",
        "hi": "साफ़ फ़ोटो भेजें, या आधिकारिक नंबर पर भेजने वाले से पुष्टि करें।",
    },
}
SHORT_ACTION_VOICE = {
    "en": "Call the person back on the number you already have for them before you send anything.",
    "hi": "कुछ भी भेजने से पहले उस व्यक्ति को उस नंबर पर वापस कॉल करें जो आपके पास पहले से है।",
}
FOOTER_SHORT = {
    False: {"en": "Automated check · not yet reviewed by a person", "hi": "स्वचालित जाँच · अभी किसी व्यक्ति ने समीक्षा नहीं की"},
    True: {"en": "Reviewed by the Trinetra team", "hi": "Trinetra टीम द्वारा समीक्षा की गई"},
}
WHY_HEADING = {"en": "Why we said this:", "hi": "हमने ऐसा क्यों कहा:"}
_ORDER = {check: i for i, check in enumerate(CHECK_ORDER)}


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.।!?])\s+", text.strip(), maxsplit=1)
    return parts[0]


def _pointing(verdict: Verdict, signals: list[Signal]) -> list[Signal]:
    """Signals that support the verdict, strongest first, source before ask before media."""
    wanted = PointsTo.GENUINE if verdict is Verdict.VERIFIED_OFFICIAL else PointsTo.FAKE
    mine = [s for s in signals if s.points_to is wanted]
    return sorted(mine, key=lambda s: (_RANK[s.strength], _ORDER.get(s.check, 9)))


def strongest_reason(verdict: Verdict, signals: list[Signal], lang: str, reviewed: bool = False) -> str | None:
    if verdict is Verdict.COULD_NOT_CHECK:
        return None
    if reviewed and verdict is Verdict.LIKELY_FAKE:
        return KNOWN_FAKE[lang]
    mine = _pointing(verdict, signals)
    if mine:
        return first_sentence(mine[0].reason_hi if lang == "hi" else mine[0].reason_en)
    return first_sentence(NO_SIGNALS[lang])


def short(verdict: Verdict, signals: list[Signal], kind: Kind, lang: str, sender: str = "general",
          reviewed: bool = False) -> str:
    """The bot's reply: verdict line, the strongest reason, one action line, one line of review status."""
    if verdict is Verdict.COULD_NOT_CHECK and kind is Kind.TEXT:
        line, action = TOO_SHORT[lang]
        return "\n\n".join([line, action, FOOTER_SHORT[reviewed][lang]])
    if kind is Kind.VOICE and verdict is not Verdict.COULD_NOT_CHECK:
        action = SHORT_ACTION_VOICE[lang]
    elif verdict in (Verdict.LIKELY_FAKE, Verdict.SUSPICIOUS):
        action = SHORT_ACTION_FAKE[sender][lang]
    else:
        action = SHORT_ACTION[verdict][lang]
    reason = strongest_reason(verdict, signals, lang, reviewed)
    parts = [SHORT_VERDICT[lang][verdict], *([reason] if reason else []), action, FOOTER_SHORT[reviewed][lang]]
    return "\n\n".join(parts)


def why(verdict: Verdict, signals: list[Signal], lang: str, reviewed: bool = False, verify: str | None = None) -> str:
    """Everything behind "Why?": every reason in full, the official place to check, the review status."""
    lines = [s.reason_hi if lang == "hi" else s.reason_en for s in _pointing(verdict, signals)]
    if reviewed and verdict is Verdict.LIKELY_FAKE:
        lines.insert(0, KNOWN_FAKE[lang])
    if not lines and verdict is not Verdict.COULD_NOT_CHECK:
        lines = [NO_SIGNALS[lang]]
    parts = [WHY_HEADING[lang], "\n".join(f"• {line}" for line in dict.fromkeys(lines))] if lines else []
    if verify:
        parts.append(verify)
    parts.append((FOOTER_REVIEWED if reviewed else FOOTER_UNREVIEWED)[lang])
    return "\n\n".join(parts)
