"""Issue a notice through the issuer portal: extract, fingerprint, code, stamp and store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app import issuers as codes
from app.evidence import write_once
from app.extract import fields as field_extractor
from app.extract import ocr
from app.models import Kind, Verdict
from app.pipeline import analyse_text, is_readable, strip_test_footer
from app.store import Store


class IssueError(ValueError):
    """The upload cannot be issued. `reason` and `detail` go to the audit log.

    For display, the portal shows `headline`, then `summary`, then each of `reasons` as a bullet,
    then `after`. The plain message (str) carries the same words in one line.
    """

    def __init__(self, message: str, reason: str = "invalid_upload", detail: str = "", *,
                 headline: str = "This notice can't be issued", summary: str | None = None,
                 reasons: list[str] = (), after: str = ""):
        super().__init__(message)
        self.reason = reason
        self.detail = detail
        self.headline = headline
        self.summary = message if summary is None else summary
        self.reasons = list(reasons)
        self.after = after


@dataclass(frozen=True)
class Issued:
    notice_id: int
    code: str
    url: str
    stamped_path: Path
    media_type: str
    confirmed_signals: tuple[str, ...] = ()  # signal codes the issuer confirmed past, for a suspicious notice


CONFIRM_LABEL = "I confirm this notice is genuine and its payment details are official"

# Generic bodies from agencies.json, and the issuer types that may name themselves that way ("Court" for a court).
GENERIC_SENDERS = {"court": {"court"}, "police": {"police"}, "government": {"government"}, "bank": {"bank"},
                   "mutual_fund": {"bank", "other"}}


def names_other_body(fields, issuer: dict) -> str | None:
    """The body the notice claims to come from, if it is not the issuer. None when it is the issuer or none is named."""
    if fields.sender_id is None:
        return None
    claimed = (fields.claimed_sender or "").strip()
    own_names = [issuer["name"], *issuer.get("match_names", [])]
    if any(claimed.lower() in n.lower() or n.lower() in claimed.lower() for n in own_names if claimed):
        return None
    if issuer["type"] in GENERIC_SENDERS.get(fields.sender_id, set()):
        return None
    return claimed or fields.sender_id


def pre_issue_check(store: Store, issuer: dict, body: str, source_sha: str, confirmed: bool) -> tuple[str, ...]:
    """Refuse what Trinetra would not trust. Returns the signal codes of a suspicious notice the issuer confirmed."""
    result, fields = analyse_text(body, kind=Kind.NOTICE, known=store.is_known_fake, issuer_context=issuer)
    reason_list = [s.reason_en for s in result.signals if s.points_to.value == "fake"]
    reasons = " ".join(reason_list)
    signal_codes = ",".join(s.code for s in result.signals)

    if result.verdict is Verdict.LIKELY_FAKE:
        raise IssueError(
            f"Trinetra would rate this notice likely fake, so it cannot be issued. {reasons}",
            "likely_fake", f"sha256={source_sha} signals={signal_codes}",
            headline="This notice can't be issued: Trinetra rates it likely fake",
            summary="Trinetra would rate this notice likely fake, so it cannot be issued.", reasons=reason_list,
        )
    other = names_other_body(fields, issuer)
    if other:
        raise IssueError(
            f"This notice names {other}, not {issuer['name']}. You can only issue notices from your own organisation.",
            "other_body", f"sha256={source_sha} claimed={other}",
            headline=f"This notice can't be issued: it names {other}",
        )
    if result.verdict is Verdict.SUSPICIOUS:
        if not confirmed:
            raise IssueError(
                f"Trinetra would rate this notice suspicious: {reasons} If it is genuine, tick "
                f"\u201c{CONFIRM_LABEL}\u201d and issue it again.",
                "suspicious_unconfirmed", f"sha256={source_sha} signals={signal_codes}",
                headline="Confirmation needed: Trinetra rates this notice suspicious",
                summary="Trinetra would rate this notice suspicious:", reasons=reason_list,
                after=f"If it is genuine, tick \u201c{CONFIRM_LABEL}\u201d and issue it again.",
            )
        return tuple(s.code for s in result.signals if s.points_to.value == "fake")
    return ()


MEDIA_TYPES = {"png": "image/png", "pdf": "application/pdf", "text": "text/plain; charset=utf-8"}
SUFFIX = {"png": ".png", "pdf": ".pdf", "text": ".txt"}


def issue_notice(
    store: Store,
    issuer: dict,
    username: str,
    notice_type: str,
    issued_dir: Path,
    base_url: str,
    data: bytes | None = None,
    text: str | None = None,
    confirmed: bool = False,
) -> Issued:
    """Raises IssueError for anything that cannot be issued, including notices that fail the pre-issue checks."""
    if notice_type not in codes.NOTICE_TYPES:
        raise IssueError("Choose a notice type.")
    if data:
        kind = ocr.sniff(data)
        if kind is None:
            raise IssueError("Upload a PDF, PNG or JPEG.")
        try:
            body = ocr.file_text(data)
        except ocr.UnreadableFile:
            body = ""
        source_sha = hashlib.sha256(data).hexdigest()
    elif text and text.strip():
        kind, body = "text", text.strip()
        source_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    else:
        raise IssueError("Upload a file or paste the notice text.")

    if codes.find_codes(body) or (data and codes.find_codes(" ".join(ocr.qr_texts(data)))):
        raise IssueError("This notice already carries a Trinetra code. Upload the original, unstamped notice.")
    body = codes.strip_stamp(strip_test_footer(body))
    if not is_readable(body):
        raise IssueError("We could not read any text in this file. Upload a clearer copy or paste the text.")
    confirmed_signals = pre_issue_check(store, issuer, body, source_sha, confirmed)

    fields = field_extractor.extract(body)
    code = codes.new_code()
    while store.notice_by_code(code):  # 6.5 × 10^11 codes, but never reuse one
        code = codes.new_code()
    url = codes.verify_url(base_url, code)

    if kind == "pdf":
        stamped, out_kind = codes.stamp_pdf(data, code, url, issuer["name"]), "pdf"
    elif kind in ("png", "jpeg"):
        stamped, out_kind = codes.stamp_image(data, code, url, issuer["name"]), "png"
    else:
        stamped, out_kind = codes.stamp_text(body, code, url).encode("utf-8"), "text"
    path = issued_dir / f"{code}{SUFFIX[out_kind]}"
    write_once(path, stamped)

    notice_id = store.add_notice(
        code=code,
        issuer_id=issuer["id"],
        notice_type=notice_type,
        issued_by=username,
        source_kind=kind,
        source_sha256=source_sha,
        fingerprint=codes.fingerprint(body),
        key_fields_json=json.dumps(codes.key_fields(fields)),
        stamped_path=str(path),
        stamped_sha256=hashlib.sha256(stamped).hexdigest(),
    )
    # Answers cached before this notice existed may now be wrong (a copy of it, or a forged one).
    store.clear_results()
    return Issued(notice_id, code, url, path, MEDIA_TYPES[out_kind], confirmed_signals)
