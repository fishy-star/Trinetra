"""Intake: consent state, commands, rate limits, hashing, the hash cache and the report flow.

Channel-neutral. A channel parses its update into an Inbound and passes a `send` function;
intake decides what to say. Message text is never logged and never goes into the database:
the checked file or text is kept in the media folder for MEDIA_TTL_HOURS so it can be
reported, then deleted unless it was.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

from pathlib import Path

from app import pipeline, reply
from app.config import ENGINE_VERSION
from app.evidence import EXTENSIONS, EvidenceStore, MediaDir, sha256_of
from app.extract import ocr
from app.models import Kind, Result, Verdict
from app.samples import sample_paths
from app.store import NewReport, Store, report_payees

log = logging.getLogger(__name__)

PER_HOUR = 10
PER_DAY = 50
MAX_TEXT_CHARS = 5000

CONSENT = (
    "Trinetra checks notices, voice notes and messages you forward. We delete your file within 24 hours unless "
    "you report it. We do not collect your contacts, chats or location. Trinetra never asks for an OTP, PIN or "
    "payment. This service is for adults. Trinetra is a student prototype. It does not replace official advice. "
    "If in doubt, call 1930. Reply YES to continue. Send DELETE at any time to erase your data."
    "\n\n"
    "Trinetra आपके भेजे गए नोटिस, वॉइस नोट और संदेशों की जाँच करता है। रिपोर्ट न करने पर हम आपकी फ़ाइल 24 घंटे में "
    "मिटा देते हैं। हम आपके संपर्क, चैट या लोकेशन नहीं लेते। Trinetra कभी OTP, PIN या पैसे नहीं माँगता। यह सेवा "
    "वयस्कों के लिए है। Trinetra एक छात्र प्रोटोटाइप है। यह आधिकारिक सलाह का विकल्प नहीं है। संदेह हो तो 1930 पर कॉल करें। "
    "जारी रखने के लिए YES भेजें। अपना डेटा मिटाने के लिए कभी भी DELETE भेजें।"
)
WELCOME = (
    "Thank you. Forward a suspicious notice, voice note or message to check it. Send HINDI or ENGLISH to choose "
    "the reply language. Tap \u201cTry a sample\u201d to see how it works."
    "\n\n"
    "धन्यवाद। जाँच के लिए कोई संदिग्ध नोटिस, वॉइस नोट या संदेश भेजें। जवाब की भाषा चुनने के लिए HINDI या ENGLISH भेजें। "
    "यह कैसे काम करता है, देखने के लिए \u201cनमूना आज़माएँ\u201d दबाएँ।"
)
SAMPLES_INTRO = (
    "Here are sample notices. Forward each one back to me to see what Trinetra says."
    "\n\n"
    "ये नमूना नोटिस हैं। हर एक को मुझे वापस फ़ॉरवर्ड करें और देखें कि Trinetra क्या कहता है।"
)
SAMPLE_CAPTION = "Forward this back to me to check it\nजाँच के लिए इसे मुझे वापस फ़ॉरवर्ड करें"
TRY_SAMPLE = ("Try a sample · नमूना आज़माएँ", "samples")
FEEDBACK_BUTTONS = {"en": ("👍 Helpful", "👎 Not helpful"), "hi": ("👍 मददगार", "👎 मददगार नहीं")}
FEEDBACK_THANKS = {"en": "Thank you for your feedback.", "hi": "आपकी राय के लिए धन्यवाद।"}
# Verdicts as one letter, so a feedback button fits Telegram's 64-byte limit with the item's hash.
VERDICT_CODES = {"likely_fake": "L", "suspicious": "S", "cannot_verify": "C", "verified_official": "V",
                 "could_not_check": "N"}
FEEDBACK_HASH_CHARS = 48
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
        "report_confirm": "Report this to the Trinetra team? Your file will be kept as evidence. Reply YES to confirm.",
        "report_done": "Reported. Your case number is {case}. The Trinetra team will review it. Thank you for helping "
        "protect others.",
        "report_nothing": "There is nothing to report yet. Send the notice or message first, then reply 1.",
        "report_expired": "We no longer have that file, because files are deleted after 24 hours. Send it again, "
        "then reply 1.",
        "already_reported": "You have already reported this. Your case number is {case}.",
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
        "report_confirm": "इसे Trinetra टीम को रिपोर्ट करें? आपकी फ़ाइल साक्ष्य के रूप में रखी जाएगी। पुष्टि के लिए YES भेजें।",
        "report_done": "रिपोर्ट हो गई। आपका केस नंबर {case} है। Trinetra टीम इसकी समीक्षा करेगी। दूसरों को बचाने में मदद के "
        "लिए धन्यवाद।",
        "report_nothing": "अभी रिपोर्ट करने के लिए कुछ नहीं है। पहले नोटिस या संदेश भेजें, फिर 1 भेजें।",
        "report_expired": "वह फ़ाइल अब हमारे पास नहीं है, क्योंकि फ़ाइलें 24 घंटे बाद मिटा दी जाती हैं। उसे फिर से भेजें, फिर 1 भेजें।",
        "already_reported": "आप इसे पहले ही रिपोर्ट कर चुके हैं। आपका केस नंबर {case} है।",
        "limit_hour": "आपने कम समय में बहुत सारे संदेश भेजे हैं। एक घंटे बाद फिर कोशिश करें।",
        "limit_day": "आज की सीमा पूरी हो गई है। कल फिर कोशिश करें।",
        "error": "जाँच के दौरान कुछ गड़बड़ हुई। कृपया फिर से कोशिश करें।",
    },
}

class Out(str):
    """A reply. Plain text to most code; a channel may also show its buttons, photo or toast.

    `buttons` are (label, data) pairs; pressing one comes back as a message whose text is `data`.
    `toast` asks the channel to acknowledge a button press quietly instead of sending a message.
    """

    buttons: tuple[tuple[str, str], ...]
    photo: bytes | None
    toast: bool

    def __new__(cls, text: str, *, buttons=(), photo: bytes | None = None, toast: bool = False):
        out = super().__new__(cls, text)
        out.buttons, out.photo, out.toast = tuple(buttons), photo, toast
        return out


def verdict_reply(text: str, verdict: Verdict, sha256: str, lang: str) -> Out:
    """The verdict with "Helpful" / "Not helpful" buttons."""
    code = VERDICT_CODES[verdict.value]
    up, down = FEEDBACK_BUTTONS[lang]
    key = sha256[:FEEDBACK_HASH_CHARS]
    return Out(text, buttons=[(up, f"fb:u:{code}:{key}"), (down, f"fb:d:{code}:{key}")])


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
    callback_id: str | None = None  # set when this is a button press; its data is in `text`
    message_id: int | None = None  # the message whose button was pressed


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
        data_dir: Path | None = None,
        media_ttl_hours: int = 24,
        samples: Callable[[], list[Path]] | None = None,
    ):
        if not salt:
            raise ValueError("USER_ID_SALT must be set")
        self.store = store
        self._salt = salt.encode()
        self._check = check
        self._check_file = check_file
        data_dir = data_dir or store.path.parent
        self.media = MediaDir(data_dir / "media")
        self.evidence = EvidenceStore(data_dir / "evidence")
        self.media_ttl = timedelta(hours=media_ttl_hours)
        self._samples = samples or sample_paths

    def _lookups(self) -> dict:
        """Approved fakes and issued notices, both in the runtime database."""
        return {"known": self.store.is_known_fake, "issuers": self.store}

    def user_hash(self, channel: str, chat_id: str) -> str:
        return hmac.new(self._salt, f"{channel}:{chat_id}".encode(), hashlib.sha256).hexdigest()

    def handle(self, msg: Inbound, send: Callable[[str], None]) -> None:
        uid = self.user_hash(msg.channel, msg.chat_id)
        user = self.store.user(uid)
        command = (msg.text or "").strip().upper().removeprefix("/")

        if user is None or user["consent_at"] is None:
            if command in YES:
                self.store.give_consent(uid)
                send(Out(WELCOME, buttons=[TRY_SAMPLE]))
            elif command == "DELETE":
                send(NOTHING_STORED)
            else:
                send(CONSENT)
            return

        lang = user["lang"] or ("hi" if (msg.lang_hint or "").startswith("hi") else "en")
        say = MESSAGES[lang]

        if (msg.text or "").startswith("fb:"):
            self._feedback(msg.text, lang, send)  # a button press: it does not cancel a pending report
            return

        if user["pending"]:
            # Any reply other than YES cancels the pending report and is handled as usual.
            self.store.set_pending(uid, None)
            if command in YES and user["pending"].startswith("report:"):
                self._report(uid, int(user["pending"].split(":", 1)[1]), user["lang"], lang, say, send)
                return

        if command in YES or command == "START":
            send(Out(WELCOME, buttons=[TRY_SAMPLE]))
        elif command in ("SAMPLES", "SAMPLE", "TRY A SAMPLE"):
            self._send_samples(send)
        elif command in HINDI or command in ENGLISH:
            new = "hi" if command in HINDI else "en"
            self.store.set_lang(uid, new)
            send(MESSAGES[new]["lang_set"])
        elif command == "DELETE":
            media, evidence = self.store.delete_user(
                uid, is_locked=lambda p: Path(p).exists() and self.evidence.is_locked(Path(p))
            )
            for path in media:
                self.media.delete(Path(path))
            for path in evidence:
                self.evidence.erase(Path(path))
            self.store.audit("user", "delete", f"media={len(media)} evidence={len(evidence)}")
            send(say["deleted"])
        elif command == "1":
            self._ask_to_report(uid, say, send)
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
                check = partial(self._check, text, kind=Kind.TEXT, sha256=sha, lang=reply_lang, **self._lookups())
                self._run(uid, sha, Kind.TEXT, text.encode("utf-8"), EXTENSIONS["text"], reply_lang, say, send, check,
                          channel=msg.channel)

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
        check = partial(self._check_file, data, sha256=sha, lang=pref, fallback_lang=lang, **self._lookups())
        self._run(uid, sha, Kind.NOTICE, data, EXTENSIONS[actual], pref, say, send, check, fallback_lang=lang,
                  channel=msg.channel)

    def _run(
        self,
        uid: str,
        sha: str,
        kind: Kind,
        data: bytes,
        ext: str,
        lang: str | None,
        say: dict[str, str],
        send: Callable[[str], None],
        check: Callable[[], Result],
        fallback_lang: str = "en",
        channel: str = "telegram",
    ) -> None:
        path = self.media.save(data, sha, ext)
        self.store.add_item(uid, sha, kind, str(path), self.media_ttl)

        # A reviewer-confirmed fake is answered at once, without checking again.
        if pipeline.is_known_fake(sha, known=self.store.is_known_fake):
            report = self.store.latest_report_for(sha)
            group = reply.sender_group(*report_sender(report)) if report else "general"
            reply_lang = lang or (report["lang"] if report else None) or fallback_lang
            log.info("known fake kind=%s", kind.value)
            self.store.record_check(kind.value, channel, Verdict.LIKELY_FAKE.value)
            text = reply.build(Verdict.LIKELY_FAKE, [], kind, reply_lang, group, reviewed=True)
            send(verdict_reply(text, Verdict.LIKELY_FAKE, sha, reply_lang))
            return

        cached = self.store.cached_result(sha, ENGINE_VERSION)
        if cached is not None:
            log.info("cache hit kind=%s verdict=%s", cached.kind.value, cached.verdict.value)
            group = reply.sender_group(cached.sender_type, cached.sender_id)
            reply_lang = lang or cached.lang or fallback_lang
            verify = reply.verify_line(cached.sender_type, cached.sender_id, reply_lang)
            text = reply.build(cached.verdict, cached.signals, cached.kind, reply_lang, group,
                               reviewed=cached.rule_row == "R1", verify=verify)
            self.store.record_check(kind.value, channel, cached.verdict.value)
            send(verdict_reply(text, cached.verdict, sha, reply_lang))
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
        self.store.record_check(kind.value, channel, result.verdict.value)
        send(verdict_reply(result.reply, result.verdict, sha, result.lang))

    # --- samples and feedback ------------------------------------------------------

    def _send_samples(self, send: Callable[[str], None]) -> None:
        paths = [p for p in self._samples() if p.is_file()]
        if not paths:
            log.warning("no sample files found")
            return
        send(SAMPLES_INTRO)
        for path in paths:
            send(Out(SAMPLE_CAPTION, photo=path.read_bytes()))

    def _feedback(self, data: str, lang: str, send: Callable[[str], None]) -> None:
        """Store only the item's hash, the verdict and the button pressed. Nothing about who pressed it."""
        parts = data.split(":")
        verdicts = {v: k for k, v in VERDICT_CODES.items()}
        if len(parts) != 4 or parts[1] not in ("u", "d") or parts[2] not in verdicts or not parts[3].isalnum():
            return
        self.store.add_feedback(parts[3], verdicts[parts[2]], "helpful" if parts[1] == "u" else "not_helpful")
        send(Out(FEEDBACK_THANKS[lang], toast=True))

    # --- reporting ------------------------------------------------------------

    def _ask_to_report(self, uid: str, say: dict[str, str], send: Callable[[str], None]) -> None:
        item = self.store.last_item(uid)
        if item is None:
            send(say["report_nothing"])
            return
        if not item["media_path"] or not Path(item["media_path"]).exists():
            send(say["report_expired"])
            return
        case = self.store.already_reported(uid, item["sha256"])
        if case is not None:
            send(say["already_reported"].format(case=case))
            return
        self.store.set_pending(uid, f"report:{item['id']}")
        send(say["report_confirm"])

    def _report(self, uid: str, item_id: int, pref: str | None, lang: str, say: dict[str, str], send: Callable[[str], None]) -> None:
        item = self.store.item(item_id)
        data = self.media.read(Path(item["media_path"])) if item and item["media_path"] else None
        if data is None or sha256_of(data) != item["sha256"]:
            send(say["report_expired"])
            return
        sha, kind = item["sha256"], Kind(item["kind"])
        case = self.store.already_reported(uid, sha)
        if case is not None:
            send(say["already_reported"].format(case=case))
            return
        try:
            path = self.evidence.put(data, sha, Path(item["media_path"]).suffix)
            if kind is Kind.TEXT:
                result, fields = pipeline.analyse_text(data.decode("utf-8"), sha256=sha, lang=pref, **self._lookups())
            else:
                result, fields = pipeline.analyse_file(
                    data, sha256=sha, lang=pref, fallback_lang=lang, **self._lookups()
                )
            case = self.store.add_report(
                NewReport(sha, uid, kind, len(data), str(path), result, fields, report_payees(fields))
            )
        except Exception:
            log.exception("report failed sha256=%s", sha[:12])
            send(say["error"])
            return
        self.store.audit("user", "report", f"case={case} sha256={sha}")
        log.info("reported case=%s kind=%s", case, kind.value)
        send(say["report_done"].format(case=case))


def report_sender(report) -> tuple[str | None, str | None]:
    fields = json.loads(report["fields_json"])
    return fields.get("sender_type"), fields.get("sender_id")
