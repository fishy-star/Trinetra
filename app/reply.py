"""Reply templates, Hindi and English. No scores and no model names, ever."""

from __future__ import annotations

from app.models import Kind, PointsTo, Signal, Strength, Verdict

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

ACTION_FAKE = {
    "en": "Do not pay. No police, CBI, ED or court arrests anyone on a video call or asks for money to clear your "
    "name. Reply 1 to report this. Reply 2 if you already paid.",
    "hi": "भुगतान न करें। पुलिस, CBI, ED या कोई न्यायालय वीडियो कॉल पर गिरफ़्तारी नहीं करता और नाम साफ़ करने के लिए "
    "पैसे नहीं माँगता। रिपोर्ट करने के लिए 1 भेजें। अगर आप भुगतान कर चुके हैं तो 2 भेजें।",
}
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
# Every reply names its evidence. When no check fired, say so without implying safety.
NO_SIGNALS = {
    "en": "We found no warning signs, and we could not confirm who sent it.",
    "hi": "हमें कोई चेतावनी संकेत नहीं मिला, और हम भेजने वाले की पुष्टि नहीं कर सके।",
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


def build(verdict: Verdict, signals: list[Signal], kind: Kind, lang: str, reviewed: bool = False) -> str:
    parts = [VERDICT_LINE[lang][verdict]]
    if verdict is not Verdict.COULD_NOT_CHECK:
        lines = reasons(verdict, signals, lang)
        parts.append("\n".join(f"• {r}" for r in lines) if lines else NO_SIGNALS[lang])
    if verdict in (Verdict.LIKELY_FAKE, Verdict.SUSPICIOUS):
        parts.append(ACTION_FAKE[lang])
    if kind is Kind.VOICE:
        parts.append(ACTION_VOICE[lang])
    parts.append((FOOTER_REVIEWED if reviewed else FOOTER_UNREVIEWED)[lang])
    return "\n\n".join(parts)

