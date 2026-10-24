"""Intake: consent state, commands, rate limits, hashing and the hash cache.

Channel-neutral. A channel parses its update into an Inbound and passes a `send` function;
intake decides what to say. Message text is never logged and never stored.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from app import pipeline, reply
from app.config import ENGINE_VERSION
from app.models import Kind, Result
from app.store import Store

log = logging.getLogger(__name__)

PER_HOUR = 10
PER_DAY = 50
MAX_TEXT_CHARS = 5000

CONSENT = (
    "Trinetra checks notices, voice notes and messages you forward. We delete your file within 24 hours unless "
    "you report it. We do not collect your contacts, chats or location. Trinetra never asks for an OTP, PIN or "
    "payment. This service is for adults. Reply YES to continue. Send DELETE at any time to erase your data."
    "\n\n"
    "Trinetra आपके भेजे गए नोटिस, वॉइस नोट और संदेशों की जाँच करता है। रिपोर्ट न करने पर हम आपकी फ़ाइल 24 घंटे में "
    "मिटा देते हैं। हम आपके संपर्क, चैट या लोकेशन नहीं लेते। Trinetra कभी OTP, PIN या पैसे नहीं माँगता। यह सेवा "
    "वयस्कों के लिए है। जारी रखने के लिए YES भेजें। अपना डेटा मिटाने के लिए कभी भी DELETE भेजें।"
)
WELCOME = (
    "Thank you. Forward a suspicious notice, voice note or message to check it. Send HINDI or ENGLISH to choose "
    "the reply language."
    "\n\n"
    "धन्यवाद। जाँच के लिए कोई संदिग्ध नोटिस, वॉइस नोट या संदेश भेजें। जवाब की भाषा चुनने के लिए HINDI या ENGLISH भेजें।"
)
NOTHING_STORED = "We hold no data for you.\n\nहमारे पास आपका कोई डेटा नहीं है।"

MESSAGES = {
    "en": {
        "lang_set": "Replies will now be in English.",
        "deleted": "Your data has been erased. Items already in an approved evidence packet are kept, because they "
        "are evidence in a reported case. Send YES if you want to use Trinetra again.",
        "checking": "Checking… this takes up to a minute.",
        "media_soon": "Checking photos, PDFs and voice notes is coming soon. For now, paste the text of the message.",
        "report_soon": "Reporting is coming soon. If you already paid, send 2.",
        "limit_hour": "You have sent many items in a short time. Please wait an hour and try again.",
        "limit_day": "You have reached today's limit. Please try again tomorrow.",
        "error": "Something went wrong while checking. Please try again.",
    },
    "hi": {
        "lang_set": "अब से जवाब हिन्दी में आएँगे।",
        "deleted": "आपका डेटा मिटा दिया गया है। जो फ़ाइलें किसी स्वीकृत साक्ष्य पैकेट में हैं, वे रखी जाती हैं, क्योंकि वे "
        "किसी रिपोर्ट किए गए मामले में साक्ष्य हैं। Trinetra फिर से इस्तेमाल करने के लिए YES भेजें।",
        "checking": "जाँच हो रही है… इसमें एक मिनट तक लग सकता है।",
        "media_soon": "फ़ोटो, PDF और वॉइस नोट की जाँच जल्द आ रही है। अभी के लिए संदेश का टेक्स्ट भेजें।",
        "report_soon": "रिपोर्ट करने की सुविधा जल्द आ रही है। अगर आप भुगतान कर चुके हैं, तो 2 भेजें।",
        "limit_hour": "आपने कम समय में बहुत सारे संदेश भेजे हैं। एक घंटे बाद फिर कोशिश करें।",
        "limit_day": "आज की सीमा पूरी हो गई है। कल फिर कोशिश करें।",
        "error": "जाँच के दौरान कुछ गड़बड़ हुई। कृपया फिर से कोशिश करें।",
    },
}

YES = {"YES", "Y", "हाँ", "हां"}
HINDI = {"HINDI", "हिंदी", "हिन्दी"}
ENGLISH = {"ENGLISH", "अंग्रेज़ी", "अंग्रेजी"}


@dataclass(frozen=True)
class Inbound:
    channel: str  # "telegram" | "whatsapp"
    chat_id: str
    text: str | None = None
    media: str | None = None  # "photo" | "document" | "voice" | "audio" | "video" | "other"
    lang_hint: str | None = None  # from the client app's language setting


class Intake:
    def __init__(
        self,
        store: Store,
        salt: str,
        check: Callable[..., Result] = pipeline.check_text,
    ):
        if not salt:
            raise ValueError("USER_ID_SALT must be set")
        self.store = store
        self._salt = salt.encode()
        self._check = check

    def user_hash(self, channel: str, chat_id: str) -> str:
        return hmac.new(self._salt, f"{channel}:{chat_id}".encode(), hashlib.sha256).hexdigest()

    def handle(self, msg: Inbound, send: Callable[[str], None]) -> None:
        uid = self.user_hash(msg.channel, msg.chat_id)
        user = self.store.user(uid)
        command = (msg.text or "").strip().upper().removeprefix("/")

        if user is None or user["consent_at"] is None:
            if command in YES:
                self.store.give_consent(uid)
                send(WELCOME)
            elif command == "DELETE":
                send(NOTHING_STORED)
            else:
                send(CONSENT)
            return

        lang = user["lang"] or ("hi" if (msg.lang_hint or "").startswith("hi") else "en")
        say = MESSAGES[lang]

        if command in YES or command == "START":
            send(WELCOME)
        elif command in HINDI or command in ENGLISH:
            new = "hi" if command in HINDI else "en"
            self.store.set_lang(uid, new)
            send(MESSAGES[new]["lang_set"])
        elif command == "DELETE":
            self.store.delete_user(uid)
            send(say["deleted"])
        elif command == "1":
            send(say["report_soon"])
        elif command == "2":
            send(reply.ALREADY_PAID[lang])
        elif msg.media:
            send(say["media_soon"])
        elif msg.text and msg.text.strip():
            self._check_text(uid, user["lang"], msg.text[:MAX_TEXT_CHARS], say, send)

    def _check_text(self, uid: str, lang: str | None, text: str, say: dict[str, str], send: Callable[[str], None]) -> None:
        if self.store.items_since(uid, timedelta(days=1)) >= PER_DAY:
            send(say["limit_day"])
            return
        if self.store.items_since(uid, timedelta(hours=1)) >= PER_HOUR:
            send(say["limit_hour"])
            return

        sha = pipeline.sha256_text(text)
        self.store.add_item(uid, sha, Kind.TEXT)
        reply_lang = lang or reply.detect_lang(text)

        cached = self.store.cached_result(sha, ENGINE_VERSION)
        if cached is not None:
            log.info("cache hit kind=%s verdict=%s", cached.kind.value, cached.verdict.value)
            send(reply.build(cached.verdict, cached.signals, cached.kind, reply_lang))
            return

        send(say["checking"])
        try:
            result = self._check(text, kind=Kind.TEXT, sha256=sha, lang=reply_lang)
        except Exception:
            log.exception("check failed sha256=%s", sha[:12])
            send(say["error"])
            return
        self.store.save_result(result)
        log.info("checked kind=%s verdict=%s row=%s", result.kind.value, result.verdict.value, result.rule_row)
        send(result.reply)
