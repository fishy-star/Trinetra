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
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

from pathlib import Path

from app import pipeline, reply
from app.config import ENGINE_VERSION
from app.evidence import EXTENSIONS, EvidenceStore, MediaDir, sha256_of
from app.extract import ocr
from app.extract.mask import mask_text
from app.models import Kind, Result, Verdict
from app.samples import sample_paths
from app.store import NewReport, Store, report_payees

log = logging.getLogger(__name__)

PER_HOUR = 10
PER_DAY = 50
MAX_TEXT_CHARS = 5000

# One language per message, never both. Before consent the language comes from the chat app's setting.
CONSENT = {  # the full privacy text, behind "Privacy details"
    "en": "Trinetra checks notices, voice notes and messages you forward. We delete your file within 24 hours "
    "unless you report it. We do not collect your contacts, chats or location. Trinetra never asks for an OTP, PIN "
    "or payment. This service is for adults. Trinetra is a student prototype. It does not replace official advice. "
    "If in doubt, call 1930. Reply YES or tap Agree to continue. Send DELETE at any time to erase your data.",
    "hi": "Trinetra आपके भेजे गए नोटिस, वॉइस नोट और संदेशों की जाँच करता है। रिपोर्ट न करने पर हम आपकी फ़ाइल 24 घंटे में "
    "मिटा देते हैं। हम आपके संपर्क, चैट या लोकेशन नहीं लेते। Trinetra कभी OTP, PIN या पैसे नहीं माँगता। यह सेवा "
    "वयस्कों के लिए है। Trinetra एक छात्र प्रोटोटाइप है। यह आधिकारिक सलाह का विकल्प नहीं है। संदेह हो तो 1930 पर कॉल करें। "
    "जारी रखने के लिए YES भेजें या 'सहमत हूँ' दबाएँ। अपना डेटा मिटाने के लिए कभी भी DELETE भेजें।",
}
CONSENT_SHORT = {  # the one line a new user sees first
    "en": "Before I check anything: I keep what you send for 24 hours only, and I never ask for an OTP, PIN or money. "
    "Trinetra is a student prototype. It does not replace official advice. If in doubt, call 1930.",
    "hi": "जाँच से पहले: आप जो भेजते हैं, मैं उसे सिर्फ़ 24 घंटे रखता हूँ, और कभी OTP, PIN या पैसे नहीं माँगता। "
    "Trinetra एक छात्र प्रोटोटाइप है। यह आधिकारिक सलाह का विकल्प नहीं है। संदेह हो तो 1930 पर कॉल करें।",
}
WELCOME = {
    "en": "You're all set. Forward me any notice, message or voice note and I'll tell you if it looks fake. "
    "Not sure how? Tap Help.",
    "hi": "सब तैयार है। कोई भी नोटिस, संदेश या वॉइस नोट मुझे फ़ॉरवर्ड करें, मैं बताऊँगा कि वह नकली लगता है या नहीं। "
    "तरीका नहीं पता? मदद दबाएँ।",
}
HELP = {
    "en": "How to check something:\n\n"
    "1. WhatsApp: press and hold the photo or message, tap Share (or Forward), choose Telegram, then Trinetra.\n"
    "2. Instagram or SMS: take a screenshot and send it here. For an SMS you can also copy the text and paste it.\n"
    "3. A PDF or email attachment: tap Share, choose Telegram, then Trinetra.\n\n"
    "I answer in under a minute. I never ask for an OTP, PIN or money.",
    "hi": "किसी चीज़ की जाँच कैसे करें:\n\n"
    "1. WhatsApp: फ़ोटो या संदेश को दबाकर रखें, Share (या Forward) दबाएँ, Telegram चुनें, फिर Trinetra।\n"
    "2. Instagram या SMS: स्क्रीनशॉट लेकर यहाँ भेजें। SMS का टेक्स्ट कॉपी करके पेस्ट भी कर सकते हैं।\n"
    "3. PDF या ईमेल अटैचमेंट: Share दबाएँ, Telegram चुनें, फिर Trinetra।\n\n"
    "मैं एक मिनट के अंदर जवाब देता हूँ। मैं कभी OTP, PIN या पैसे नहीं माँगता।",
}
NOTHING_STORED = {"en": "We hold no data for you.", "hi": "हमारे पास आपका कोई डेटा नहीं है।"}
SAMPLES_INTRO = {
    "en": "Here are three sample notices. Forward each one back to me to see what Trinetra says.",
    "hi": "ये तीन नमूना नोटिस हैं। हर एक को मुझे वापस फ़ॉरवर्ड करें और देखें कि Trinetra क्या कहता है।",
}
SAMPLE_CAPTION = {"en": "Forward this back to me to check it", "hi": "जाँच के लिए इसे मुझे वापस फ़ॉरवर्ड करें"}
FEEDBACK_THANKS = {"en": "Thank you for your feedback.", "hi": "आपकी राय के लिए धन्यवाद।"}
# After "Not helpful": a person can say the message was genuine. A reviewer then decides; rules never change on their own.
DISPUTE_ASK = {"en": "Is this message actually genuine?", "hi": "क्या यह संदेश असल में असली है?"}
DISPUTE_THANKS = {
    "en": "Thanks. A person will review this. Until then, confirm through an official number before paying.",
    "hi": "धन्यवाद। कोई व्यक्ति इसकी समीक्षा करेगा। तब तक, भुगतान से पहले किसी आधिकारिक नंबर से पुष्टि करें।",
}
NOTE_ASK = {"en": "What was wrong? Reply in one line if you like. It's optional.",
            "hi": "क्या गलत था? चाहें तो एक लाइन में बताएँ। यह ज़रूरी नहीं है।"}
NOTE_THANKS = {"en": "Thank you. We've noted it.", "hi": "धन्यवाद। हमने इसे नोट कर लिया है।"}
NOTE_MAX_CHARS = 280
# Sent by the review console when a reviewer decides a dispute.
DISPUTE_OUTCOME = {
    "confirmed_genuine": {
        "en": "Update on the message you said was genuine: a person reviewed it and agrees it looks genuine. "
        "Thank you, this helps us improve. Still, confirm through an official number before paying.",
        "hi": "जिस संदेश को आपने असली बताया था, उस पर अपडेट: एक व्यक्ति ने इसकी समीक्षा की और सहमत हैं कि यह असली लगता है। "
        "धन्यवाद, इससे हमें सुधार में मदद मिलती है। फिर भी, भुगतान से पहले किसी आधिकारिक नंबर से पुष्टि करें।",
    },
    "kept": {
        "en": "Update on the message you said was genuine: a person reviewed it and kept our warning. Please "
        "confirm through an official number before paying anything.",
        "hi": "जिस संदेश को आपने असली बताया था, उस पर अपडेट: एक व्यक्ति ने इसकी समीक्षा की और हमारी चेतावनी बनी रहेगी। "
        "कुछ भी भुगतान करने से पहले किसी आधिकारिक नंबर से पुष्टि करें।",
    },
}

LABELS = {
    "en": {"agree_check": "Agree and check", "agree": "Agree", "privacy": "Privacy details",
           "sample": "Try a sample", "help": "Help", "why": "Why?", "report": "Report",
           "paid": "I already paid", "helpful": "Helpful", "not_helpful": "Not helpful",
           "genuine_yes": "Yes, it's genuine", "genuine_no": "No, other reason",
           "report_yes": "Yes, report it", "report_no": "Cancel", "other_lang": "हिन्दी"},
    "hi": {"agree_check": "सहमत हूँ, जाँचें", "agree": "सहमत हूँ", "privacy": "गोपनीयता विवरण",
           "sample": "नमूना आज़माएँ", "help": "मदद", "why": "क्यों?", "report": "रिपोर्ट करें",
           "paid": "मैंने भुगतान कर दिया", "helpful": "मददगार", "not_helpful": "मददगार नहीं",
           "genuine_yes": "हाँ, यह असली है", "genuine_no": "नहीं, कोई और कारण",
           "report_yes": "हाँ, रिपोर्ट करें", "report_no": "रद्द करें", "other_lang": "English"},
}
# Verdicts as one letter, so a button's data fits Telegram's 64 bytes together with the item's hash.
VERDICT_CODES = {"likely_fake": "L", "suspicious": "S", "cannot_verify": "C", "verified_official": "V",
                 "could_not_check": "N"}
KEY_CHARS = 48  # how much of the item's SHA-256 a button carries
FIRST_ITEM_TTL_SECONDS = 1800  # how long an item sent before consent is held, in memory only
ANSWERS_KEPT = 2000  # recent verdicts kept in memory for "Why?" and the language toggle


MESSAGES = {
    "en": {
        "lang_set": "Replies will now be in English.",
        "deleted": "Your data has been erased. Items already in an approved evidence packet are kept, because they "
        "are evidence in a reported case. Forward anything to check it again.",
        "checking": "Checking… this takes up to a minute.",
        "media_soon": "We can't check voice or video yet. Please send the text or a screenshot.",
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
        "report_ask": "Report this to the Trinetra team? Your file will be kept as evidence.",
        "report_cancelled": "Not reported.",
        "why_expired": "That check is no longer available. Forward the item again to see the reasons.",
    },
    "hi": {
        "lang_set": "अब से जवाब हिन्दी में आएँगे।",
        "deleted": "आपका डेटा मिटा दिया गया है। जो फ़ाइलें किसी स्वीकृत साक्ष्य पैकेट में हैं, वे रखी जाती हैं, क्योंकि वे "
        "किसी रिपोर्ट किए गए मामले में साक्ष्य हैं। किसी भी चीज़ की फिर से जाँच के लिए उसे फ़ॉरवर्ड करें।",
        "checking": "जाँच हो रही है… इसमें एक मिनट तक लग सकता है।",
        "media_soon": "हम अभी वॉइस या वीडियो की जाँच नहीं कर सकते। कृपया टेक्स्ट या स्क्रीनशॉट भेजें।",
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
        "report_ask": "इसे Trinetra टीम को रिपोर्ट करें? आपकी फ़ाइल साक्ष्य के रूप में रखी जाएगी।",
        "report_cancelled": "रिपोर्ट नहीं किया गया।",
        "why_expired": "यह जाँच अब उपलब्ध नहीं है। कारण देखने के लिए इसे फिर से फ़ॉरवर्ड करें।",
    },
}

class Out(str):
    """A reply. Plain text to most code; a channel may also show its buttons, photo or toast.

    `buttons` are (label, data) pairs, or `rows` of them for a set layout; pressing one comes back as a
    message whose text is `data`. `toast` asks the channel to acknowledge a button press quietly.
    """

    buttons: tuple[tuple[str, str], ...]
    rows: tuple[tuple[tuple[str, str], ...], ...]
    photo: bytes | None
    toast: bool

    def __new__(cls, text: str, *, buttons=(), rows=(), photo: bytes | None = None, toast: bool = False):
        out = super().__new__(cls, text)
        out.rows = tuple(tuple(r) for r in rows)
        out.buttons = tuple(b for r in out.rows for b in r) if rows else tuple(buttons)
        out.photo, out.toast = photo, toast
        return out


def verdict_rows(key: str, verdict: Verdict, lang: str) -> list[list[tuple[str, str]]]:
    """[Why?] [Report] / [I already paid] / [Helpful] [Not helpful] [हिन्दी or English] under every verdict."""
    t = LABELS[lang]
    code = VERDICT_CODES[verdict.value]
    other = "en" if lang == "hi" else "hi"
    if verdict is Verdict.COULD_NOT_CHECK:  # nothing was read, so there is nothing to report or explain
        return [[(t["help"], "help")], [(t["helpful"], f"fb:u:{code}:{key}"), (t["not_helpful"], f"fb:d:{code}:{key}")]]
    return [
        [(t["why"], f"why:{key}"), (t["report"], f"rep:{key}")],
        [(t["paid"], "paid")],
        [(t["helpful"], f"fb:u:{code}:{key}"), (t["not_helpful"], f"fb:d:{code}:{key}"),
         (t["other_lang"], f"lang:{other}:{key}")],
    ]


@dataclass(frozen=True)
class Answer:
    """A verdict, kept so its "Why?" and other language can be shown without checking again."""

    verdict: Verdict
    signals: list
    kind: Kind
    sender: str  # reply.sender_group
    reviewed: bool
    verify: dict  # language -> where to check at the source
    rule_row: str = ""


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


AV_MEDIA = ("voice", "audio", "video", "video_note")


def is_voice_or_video(msg: Inbound) -> bool:
    return msg.media in AV_MEDIA or (msg.media == "document" and (msg.mime or "").startswith(("audio/", "video/")))


def sniff_av(data: bytes) -> bool:
    """Audio or video by magic bytes: OGG, MP3, M4A/MP4/MOV, WAV, WebM/MKV, FLAC, AMR."""
    head = data[:16]
    return (head.startswith((b"OggS", b"ID3", b"\x1aE\xdf\xa3", b"fLaC", b"#!AMR"))
            or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
            or head[4:8] == b"ftyp" or (head.startswith(b"RIFF") and head[8:12] in (b"WAVE", b"AVI ")))


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
        # Before consent nothing is stored: an item sent first is held here, in memory, until the user agrees.
        self._first_items: dict[str, tuple[Inbound, float]] = {}
        self._answers: OrderedDict[tuple[str, str], Answer] = OrderedDict()
        self._lock = threading.Lock()

    def _lookups(self) -> dict:
        """Approved fakes and issued notices, both in the runtime database."""
        return {"known": self.store.is_known_fake, "issuers": self.store}

    def user_hash(self, channel: str, chat_id: str) -> str:
        return hmac.new(self._salt, f"{channel}:{chat_id}".encode(), hashlib.sha256).hexdigest()

    def handle(self, msg: Inbound, send: Callable[[str], None]) -> None:
        uid = self.user_hash(msg.channel, msg.chat_id)
        user = self.store.user(uid)
        text = (msg.text or "").strip()
        command = text.upper()
        if command.startswith("/"):
            command = command[1:].split("@")[0].split()[0] if len(command) > 1 else ""
        app_lang = "hi" if (msg.lang_hint or "").startswith("hi") else "en"

        if command == "HELP":  # works before consent: it holds no data
            lang = (user["lang"] if user else None) or app_lang
            send(Out(HELP[lang], buttons=[(LABELS[lang]["sample"], "samples")]))
            return

        if user is None or user["consent_at"] is None:
            self._before_consent(uid, msg, command, app_lang, send)
            return

        lang = user["lang"] or app_lang
        say = MESSAGES[lang]

        # Button presses. They never cancel a pending report.
        if text.startswith(("fb:", "why:", "rep:", "repy:", "lang:", "dsp:")) or text in ("repn", "paid"):
            self._button(uid, user, text, lang, msg, send)
            return

        if user["pending"]:
            # Any reply other than YES cancels the pending report and is handled as usual.
            self.store.set_pending(uid, None)
            if user["pending"].startswith("note:") and self._take_note(user["pending"][5:], msg, command, lang, send):
                return
            if command in YES and user["pending"].startswith("report:"):
                self._report(uid, int(user["pending"].split(":", 1)[1]), user["lang"], lang, say, send)
                return

        if command in YES or command in ("START", "AGREE"):
            self._welcome(lang, send)
        elif command in HINDI or command in ENGLISH or command == "LANGUAGE":
            new = "hi" if command in HINDI else "en" if command in ENGLISH else ("en" if lang == "hi" else "hi")
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
        elif command in ("SAMPLES", "SAMPLE", "TRY A SAMPLE"):
            self._send_samples(lang, send)
        elif command == "PRIVACY":
            send(CONSENT[lang])
        elif msg.callback_id:
            # A button's data is never something to check, even an old or unknown button.
            log.info("ignored a button press with unknown data")
        elif is_voice_or_video(msg):
            # Voice and video checks are not built yet: never crash, say so, and count it as "could not check".
            self.store.record_check(Kind.VOICE.value, msg.channel, Verdict.COULD_NOT_CHECK.value)
            send(say["media_soon"])
        elif msg.media in ("photo", "document"):
            self._file(uid, chosen_lang(user["lang"], app_lang), lang, msg, say, send)
        elif msg.media:
            send(say["unsupported"])
        elif text:
            text = msg.text[:MAX_TEXT_CHARS]
            sha = pipeline.sha256_text(text)
            reply_lang = chosen_lang(user["lang"], app_lang) or reply.detect_lang(text)
            if self._allowed(uid, say, send):
                check = partial(self._check, text, kind=Kind.TEXT, sha256=sha, lang=reply_lang, **self._lookups())
                self._run(uid, sha, Kind.TEXT, text.encode("utf-8"), EXTENSIONS["text"], reply_lang, say, send, check,
                          channel=msg.channel)

    # --- consent ----------------------------------------------------------------------

    def _before_consent(self, uid: str, msg: Inbound, command: str, lang: str, send: Callable[[str], None]) -> None:
        t = LABELS[lang]
        if command in YES or command == "AGREE":
            self.store.give_consent(uid)
            first = self._take_first_item(uid)
            if first is not None:
                self.handle(first, send)  # check what they sent first, without asking them to send it again
            else:
                self._welcome(lang, send)
            return
        if command == "DELETE":
            self._take_first_item(uid)
            send(NOTHING_STORED[lang])
            return
        if command == "PRIVACY":
            label = t["agree_check"] if self._has_first_item(uid) else t["agree"]
            send(Out(CONSENT[lang], buttons=[(label, "agree")]))
            return
        if looks_like_an_item(msg, command):
            with self._lock:
                self._first_items[uid] = (msg, time.monotonic())  # memory only; files are not even downloaded
            agree = t["agree_check"]
        else:
            agree = t["agree_check"] if self._has_first_item(uid) else t["agree"]
        send(Out(CONSENT_SHORT[lang], rows=[[(agree, "agree")], [(t["privacy"], "privacy")]]))

    def _take_first_item(self, uid: str) -> Inbound | None:
        with self._lock:
            held = self._first_items.pop(uid, None)
        if held and time.monotonic() - held[1] <= FIRST_ITEM_TTL_SECONDS:
            return held[0]
        return None

    def _has_first_item(self, uid: str) -> bool:
        with self._lock:
            held = self._first_items.get(uid)
        return bool(held) and time.monotonic() - held[1] <= FIRST_ITEM_TTL_SECONDS

    def _welcome(self, lang: str, send: Callable[[str], None]) -> None:
        t = LABELS[lang]
        send(Out(WELCOME[lang], buttons=[(t["sample"], "samples"), (t["help"], "help")]))

    # --- buttons -------------------------------------------------------------------------

    def _button(self, uid: str, user, data: str, lang: str, msg: Inbound, send: Callable[[str], None]) -> None:
        say = MESSAGES[lang]
        if data.startswith("fb:"):
            self._feedback(data, lang, send)
            if data.startswith("fb:d:"):  # "Not helpful": was the message actually genuine?
                code, key = data.split(":")[2:4]
                t = LABELS[lang]
                send(Out(DISPUTE_ASK[lang], rows=[[(t["genuine_yes"], f"dsp:y:{code}:{key}"),
                                                  (t["genuine_no"], f"dsp:n:{key}")]]))
        elif data.startswith("dsp:y:"):
            self._dispute(uid, msg, data, lang, send)
        elif data.startswith("dsp:n:"):
            self.store.set_pending(uid, f"note:{data[6:]}")
            send(NOTE_ASK[lang])
        elif data == "paid":
            send(reply.ALREADY_PAID[lang])
        elif data.startswith("why:"):
            answer = self._answer_for(uid, data[4:])
            if answer is None:
                send(say["why_expired"])
            else:
                send(reply.why(answer.verdict, answer.signals, lang, answer.reviewed, answer.verify.get(lang)))
        elif data.startswith("lang:"):
            parts = data.split(":")
            new = parts[1] if len(parts) > 1 and parts[1] in ("en", "hi") else lang
            self.store.set_lang(uid, new)
            answer = self._answer_for(uid, parts[2]) if len(parts) > 2 else None
            if answer is None:
                send(MESSAGES[new]["lang_set"])
            else:  # show the same verdict again in the other language
                self._send_answer(parts[2], answer, new, send)
        elif data.startswith("rep:"):
            self._ask_to_report(uid, say, send, key=data[4:], lang=lang)
        elif data.startswith("repy:"):
            item = self.store.user_item(uid, data[5:])
            self.store.set_pending(uid, None)
            if item is None:
                send(say["report_nothing"])
            else:
                self._report(uid, item["id"], user["lang"], lang, say, send)
        elif data == "repn":
            self.store.set_pending(uid, None)
            send(Out(say["report_cancelled"], toast=True))

    def _dispute(self, uid: str, msg: Inbound, data: str, lang: str, send: Callable[[str], None]) -> None:
        """"Yes, it's genuine": open a dispute with the verdict and signals. No media is kept unless it was reported."""
        parts = data.split(":")
        if len(parts) != 4 or not parts[3].isalnum():
            return
        code, key = parts[2], parts[3]
        verdicts = {v: k for k, v in VERDICT_CODES.items()}
        answer = self._answer_for(uid, key)
        item = self.store.user_item(uid, key)
        if answer is None and item is None:
            send(MESSAGES[lang]["why_expired"])
            return
        verdict = answer.verdict.value if answer else verdicts.get(code, "cannot_verify")
        dispute_id, created = self.store.add_dispute(
            channel=msg.channel, chat_id=msg.chat_id, user_hash=uid, lang=lang,
            sha256=item["sha256"] if item else key, kind=(answer.kind.value if answer else item["kind"]),
            verdict=verdict, rule_row=answer.rule_row if answer else "",
            signals=[[s.code, s.strength.value, s.reason_en] for s in (answer.signals if answer else [])],
        )
        if created:
            self.store.audit("user", "dispute_opened", f"dispute={dispute_id} verdict={verdict}")
        send(DISPUTE_THANKS[lang])

    def _take_note(self, key: str, msg: Inbound, command: str, lang: str, send: Callable[[str], None]) -> bool:
        """The optional one line after "No, other reason". Anything else is handled as a normal message."""
        text = (msg.text or "").strip()
        if (msg.media or msg.callback_id or not text or "\n" in text or len(text) > NOTE_MAX_CHARS
                or command in YES | HINDI | ENGLISH | {"DELETE", "1", "2", "HELP", "START"}):
            return False
        self.store.add_feedback_note(key, mask_text(text))
        send(NOTE_THANKS[lang])
        return True

    def _answer_for(self, uid: str, key: str) -> Answer | None:
        """A recent verdict for this user's item: from memory, or rebuilt from the hash cache."""
        with self._lock:
            answer = self._answers.get((uid, key))
        if answer is not None:
            return answer
        item = self.store.user_item(uid, key) if key.isalnum() else None
        if item is None:
            return None
        sha = item["sha256"]
        if pipeline.is_known_fake(sha, known=self.store.is_known_fake):
            return Answer(Verdict.LIKELY_FAKE, [], Kind(item["kind"]), "general", True, {}, "R1")
        cached = self.store.cached_result(sha, ENGINE_VERSION)
        if cached is None:
            return None
        verify = {lg: reply.verify_line(cached.sender_type, cached.sender_id, lg) for lg in ("en", "hi")}
        return Answer(cached.verdict, cached.signals, cached.kind, reply.sender_group(cached.sender_type, cached.sender_id),
                      cached.rule_row == "R1", verify, cached.rule_row)

    def _send_answer(self, key: str, answer: Answer, lang: str, send: Callable[[str], None]) -> None:
        text = reply.short(answer.verdict, answer.signals, answer.kind, lang, answer.sender, answer.reviewed)
        send(Out(text, rows=verdict_rows(key, answer.verdict, lang)))

    def _remember(self, uid: str, key: str, answer: Answer) -> None:
        with self._lock:
            self._answers[(uid, key)] = answer
            self._answers.move_to_end((uid, key))
            while len(self._answers) > ANSWERS_KEPT:
                self._answers.popitem(last=False)

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
            self._deliver(uid, sha, Answer(Verdict.LIKELY_FAKE, [], kind, group, True, {}, "R1"), reply_lang, send)
            return

        cached = self.store.cached_result(sha, ENGINE_VERSION)
        if cached is not None:
            log.info("cache hit kind=%s verdict=%s", cached.kind.value, cached.verdict.value)
            group = reply.sender_group(cached.sender_type, cached.sender_id)
            reply_lang = lang or cached.lang or fallback_lang
            verify = {lg: reply.verify_line(cached.sender_type, cached.sender_id, lg) for lg in ("en", "hi")}
            self.store.record_check(kind.value, channel, cached.verdict.value)
            answer = Answer(cached.verdict, cached.signals, cached.kind, group, cached.rule_row == "R1", verify,
                            cached.rule_row)
            self._deliver(uid, sha, answer, reply_lang, send)
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
        group = reply.sender_group(result.fields.sender_type, result.fields.sender_id)
        answer = Answer(result.verdict, result.signals, result.kind, group, result.reviewed, result.verify,
                        result.rule_row)
        self._deliver(uid, sha, answer, result.lang, send)

    def _deliver(self, uid: str, sha: str, answer: Answer, lang: str, send: Callable[[str], None]) -> None:
        """The short verdict with its buttons; the answer is kept for "Why?" and the language toggle."""
        key = sha[:KEY_CHARS]
        self._remember(uid, key, answer)
        self._send_answer(key, answer, lang, send)

    # --- samples and feedback ------------------------------------------------------

    def _send_samples(self, lang: str, send: Callable[[str], None]) -> None:
        paths = [p for p in self._samples() if p.is_file()]
        if not paths:
            log.warning("no sample files found")
            return
        send(SAMPLES_INTRO[lang])
        for path in paths:
            send(Out(SAMPLE_CAPTION[lang], photo=path.read_bytes()))

    def _feedback(self, data: str, lang: str, send: Callable[[str], None]) -> None:
        """Store only the item's hash, the verdict and the button pressed. Nothing about who pressed it."""
        parts = data.split(":")
        verdicts = {v: k for k, v in VERDICT_CODES.items()}
        if len(parts) != 4 or parts[1] not in ("u", "d") or parts[2] not in verdicts or not parts[3].isalnum():
            return
        self.store.add_feedback(parts[3], verdicts[parts[2]], "helpful" if parts[1] == "u" else "not_helpful")
        send(Out(FEEDBACK_THANKS[lang], toast=True))

    # --- reporting ------------------------------------------------------------

    def _ask_to_report(self, uid: str, say: dict[str, str], send: Callable[[str], None], key: str | None = None,
                       lang: str = "en") -> None:
        """Typed "1" reports the latest item; the Report button reports the item it was under."""
        item = self.store.user_item(uid, key) if key else self.store.last_item(uid)
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
        if key is None:
            send(say["report_confirm"])  # typed flow: reply YES
            return
        t = LABELS[lang]
        send(Out(say["report_ask"], rows=[[(t["report_yes"], f"repy:{key}"), (t["report_no"], "repn")]]))

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


def looks_like_an_item(msg: Inbound, command: str) -> bool:
    """Something to check, not a greeting or a command: media, or text with a few words, a number or a link."""
    if msg.callback_id:
        return False  # a button press, e.g. an old button tapped after DELETE, is never an item
    if msg.media:
        return True
    text = (msg.text or "").strip()
    if not text or text.startswith("/") or command in YES | HINDI | ENGLISH | {"START", "AGREE", "PRIVACY", "DELETE"}:
        return False
    return len(text.split()) >= 4 or bool(re.search(r"\d{3,}|@|https?://|www\.", text))


def chosen_lang(user_lang: str | None, app_lang: str) -> str | None:
    """The verdict's language, before looking at the message itself.

    1. The user's explicit choice (HINDI, ENGLISH, /language or the language button).
    2. Hindi if the phone's Telegram app is set to Hindi.
    3. Otherwise None: the reply follows the message's own language.
    """
    return user_lang or ("hi" if app_lang == "hi" else None)
