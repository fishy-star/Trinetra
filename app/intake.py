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
from app.extract import ocr
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
        "media_soon": "Checking voice notes and videos is coming soon. For now, send a photo or PDF of the notice, "
        "or paste the text.",
        "unsupported": "We can check photos (JPG or PNG) and PDFs. Please send the notice in one of those formats.",
        "too_large": "This file is too large. Send a photo under 5 MB or a PDF under 100 MB.",
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
        "media_soon": "वॉइस नोट और वीडियो की जाँच जल्द आ रही है। अभी के लिए नोटिस की फ़ोटो या PDF भेजें, या टेक्स्ट पेस्ट करें।",
        "unsupported": "हम फ़ोटो (JPG या PNG) और PDF की जाँच कर सकते हैं। कृपया नोटिस इनमें से किसी रूप में भेजें।",
        "too_large": "यह फ़ाइल बहुत बड़ी है। 5 MB से छोटी फ़ोटो या 100 MB से छोटी PDF भेजें।",
        "report_soon": "रिपोर्ट करने की सुविधा जल्द आ रही है। अगर आप भुगतान कर चुके हैं, तो 2 भेजें।",
        "limit_hour": "आपने कम समय में बहुत सारे संदेश भेजे हैं। एक घंटे बाद फिर कोशिश करें।",
        "limit_day": "आज की सीमा पूरी हो गई है। कल फिर कोशिश करें।",
        "error": "जाँच के दौरान कुछ गड़बड़ हुई। कृपया फिर से कोशिश करें।",
    },
}

YES = {"YES", "Y", "हाँ", "हां"}
HINDI = {"HINDI", "हिंदी", "हिन्दी"}
ENGLISH = {"ENGLISH", "अंग्रेज़ी", "अंग्रेजी"}


# WhatsApp limits, which we apply on every channel.
MAX_BYTES = {"png": 5 * 1024 * 1024, "jpeg": 5 * 1024 * 1024, "pdf": 100 * 1024 * 1024}
IMAGE_TYPES = ("png", "jpeg")


@dataclass(frozen=True)
class Inbound:
    channel: str  # "telegram" | "whatsapp"
    chat_id: str
    text: str | None = None
    media: str | None = None  # "photo" | "document" | "voice" | "audio" | "video" | "other"
    lang_hint: str | None = None  # from the client app's language setting
    mime: str | None = None  # declared type of a document
    file_size: int | None = None  # declared size, checked before downloading
    fetch: Callable[[], bytes] | None = None  # downloads the media; called only after consent and rate limits


def declared_type(msg: Inbound) -> str | None:
    """"png", "jpeg" or "pdf" from what the channel says the file is; None if we do not check that type."""
    if msg.media == "photo":
        return "jpeg"  # chat apps send photos as JPEG
    if msg.media == "document":
        return {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg"}.get(msg.mime or "")
    return None


class Intake:
    def __init__(
        self,
        store: Store,
        salt: str,
        check: Callable[..., Result] = pipeline.check_text,
        check_file: Callable[..., Result] = pipeline.check_file,
    ):
        if not salt:
            raise ValueError("USER_ID_SALT must be set")
        self.store = store
        self._salt = salt.encode()
        self._check = check
        self._check_file = check_file

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
        elif msg.media in ("photo", "document"):
            self._file(uid, user["lang"], lang, msg, say, send)
        elif msg.media:
            send(say["media_soon"])
        elif msg.text and msg.text.strip():
            text = msg.text[:MAX_TEXT_CHARS]
            sha = pipeline.sha256_text(text)
            reply_lang = user["lang"] or reply.detect_lang(text)
            if self._allowed(uid, say, send):
                self._run(uid, sha, Kind.TEXT, reply_lang, say, send, lambda: self._check(text, kind=Kind.TEXT, sha256=sha, lang=reply_lang))

    def _allowed(self, uid: str, say: dict[str, str], send: Callable[[str], None]) -> bool:
        if self.store.items_since(uid, timedelta(days=1)) >= PER_DAY:
            send(say["limit_day"])
            return False
        if self.store.items_since(uid, timedelta(hours=1)) >= PER_HOUR:
            send(say["limit_hour"])
            return False
        return True

    def _file(self, uid: str, pref: str | None, lang: str, msg: Inbound, say: dict[str, str], send: Callable[[str], None]) -> None:
        declared = declared_type(msg)
        if declared is None or msg.fetch is None:
            send(say["unsupported"])
            return
        if msg.file_size and msg.file_size > MAX_BYTES[declared]:
            send(say["too_large"])
            return
        if not self._allowed(uid, say, send):
            return
        try:
            data = msg.fetch()
        except Exception:
            log.exception("download failed channel=%s", msg.channel)
            send(say["error"])
            return
        actual = ocr.sniff(data)
        # The bytes must be the type the channel declared: a ".pdf" that is really something else is refused.
        same_family = actual == declared or (actual in IMAGE_TYPES and declared in IMAGE_TYPES)
        if actual is None or not same_family:
            send(say["unsupported"])
            return
        if len(data) > MAX_BYTES[actual]:
            send(say["too_large"])
            return
        sha = pipeline.sha256_bytes(data)
        # Without a language choice, the reply follows the notice's own language, falling back to the app's.
        self._run(uid, sha, Kind.NOTICE, pref, say, send, lambda: self._check_file(data, sha256=sha, lang=pref, fallback_lang=lang),
                  fallback_lang=lang)

    def _run(
        self,
        uid: str,
        sha: str,
        kind: Kind,
        lang: str | None,
        say: dict[str, str],
        send: Callable[[str], None],
        check: Callable[[], Result],
        fallback_lang: str = "en",
    ) -> None:
        self.store.add_item(uid, sha, kind)
        cached = self.store.cached_result(sha, ENGINE_VERSION)
        if cached is not None:
            log.info("cache hit kind=%s verdict=%s", cached.kind.value, cached.verdict.value)
            group = reply.sender_group(cached.sender_type, cached.sender_id)
            send(reply.build(cached.verdict, cached.signals, cached.kind, lang or cached.lang or fallback_lang, group))
            return

        send(say["checking"])
        try:
            result = check()
        except Exception:
            log.exception("check failed sha256=%s", sha[:12])
            send(say["error"])
            return
        self.store.save_result(result)
        log.info("checked kind=%s verdict=%s row=%s", result.kind.value, result.verdict.value, result.rule_row)
        send(result.reply)
