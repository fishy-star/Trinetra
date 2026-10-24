"""SQLite storage: users, items, results (the hash cache), cases, reports, reviews and the audit log.

Message text never goes into the database: items keep the hash and a path to the media file,
which is deleted after MEDIA_TTL_HOURS unless reported. Results keep masked fields. Full fields
are kept only in reports, after the user has reported the item and consented again.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import Fields, Kind, PointsTo, Result, Signal, Strength, Verdict
from app.registry import normalize_payee

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_hash   TEXT PRIMARY KEY,
    lang        TEXT,
    consent_at  TEXT,
    created_at  TEXT NOT NULL,
    pending     TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    received_at TEXT NOT NULL,
    media_path  TEXT,
    expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS items_user ON items (user_hash, received_at);
CREATE TABLE IF NOT EXISTS results (
    sha256          TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    rule_row        TEXT NOT NULL,
    signals_json    TEXT NOT NULL,
    fields_json     TEXT NOT NULL,
    needs_review    INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    lang            TEXT,
    PRIMARY KEY (sha256, engine_version)
);
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL REFERENCES cases (id),
    sha256          TEXT NOT NULL,
    user_hash       TEXT,
    kind            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    evidence_path   TEXT NOT NULL,
    reported_at     TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    rule_row        TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    signals_json    TEXT NOT NULL,
    fields_json     TEXT NOT NULL,
    lang            TEXT
);
CREATE INDEX IF NOT EXISTS reports_case ON reports (case_id);
CREATE INDEX IF NOT EXISTS reports_sha ON reports (sha256);
CREATE TABLE IF NOT EXISTS case_payees (
    case_id     INTEGER NOT NULL REFERENCES cases (id),
    payee       TEXT NOT NULL,
    PRIMARY KEY (case_id, payee)
);
CREATE INDEX IF NOT EXISTS case_payees_payee ON case_payees (payee);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL REFERENCES cases (id),
    reviewer    TEXT NOT NULL,
    action      TEXT NOT NULL,
    note        TEXT,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS known_fakes (
    kind        TEXT NOT NULL,               -- "hash" or "payee"
    value       TEXT NOT NULL,               -- SHA-256, or a normalized payee
    case_id     INTEGER NOT NULL REFERENCES cases (id),
    added_at    TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    PRIMARY KEY (kind, value, case_id)
);
CREATE TABLE IF NOT EXISTS issuers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,           -- court | bank | police | government | other
    domains_json    TEXT NOT NULL,
    contacts_json   TEXT NOT NULL,
    payees_json     TEXT NOT NULL,
    match_names_json TEXT NOT NULL,          -- names that, found in a notice, mean it claims to be this issuer
    requires_code   INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issuer_users (
    username        TEXT PRIMARY KEY,
    issuer_id       INTEGER NOT NULL REFERENCES issuers (id),
    password_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issued_notices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    issuer_id       INTEGER NOT NULL REFERENCES issuers (id),
    notice_type     TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    issued_by       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'valid',   -- valid | revoked
    revoked_at      TEXT,
    revoked_by      TEXT,
    source_kind     TEXT NOT NULL,                   -- png | jpeg | pdf | text
    source_sha256   TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    key_fields_json TEXT NOT NULL,
    stamped_path    TEXT NOT NULL,
    stamped_sha256  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issued_notices_issuer ON issued_notices (issuer_id, issued_at);
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256      TEXT NOT NULL,               -- the checked item's hash; nothing about who pressed
    verdict     TEXT NOT NULL,
    vote        TEXT NOT NULL                -- helpful | not_helpful
);
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,               -- counts only: no hash, no user, no content
    kind        TEXT NOT NULL,
    channel     TEXT NOT NULL,
    verdict     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checks_at ON checks (at);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT
);
"""

# Columns added after the first schema, for databases made by earlier versions.
MIGRATIONS = {"results": {"lang": "TEXT"}, "users": {"pending": "TEXT"}}

CASE_STATUSES = ("open", "approved_fake", "not_fake", "needs_info")


def now() -> datetime:
    return datetime.now(UTC)


def _signals(raw: str) -> list[Signal]:
    return [
        Signal(s["check"], s["code"], Strength(s["strength"]), PointsTo(s["points_to"]), s["reason_en"], s["reason_hi"])
        for s in json.loads(raw)
    ]


@dataclass(frozen=True)
class CachedResult:
    kind: Kind
    verdict: Verdict
    rule_row: str
    signals: list[Signal]
    sender_type: str | None
    sender_id: str | None
    lang: str | None  # language of the checked item, for replying when the user has not chosen one


@dataclass(frozen=True)
class NewReport:
    sha256: str
    user_hash: str
    kind: Kind
    size: int
    evidence_path: str
    result: Result
    fields: Fields  # full, unmasked: the user has reported and consented
    payees: list[str]  # payees the item asks money to be sent to


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._db() as db:
            # WAL lets the bot's worker threads and the web service read while one of them writes.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            for table, columns in MIGRATIONS.items():
                have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                for name, type_ in columns.items():
                    if name not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_}")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    # --- users ---------------------------------------------------------------

    def user(self, user_hash: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM users WHERE user_hash = ?", (user_hash,)).fetchone()

    def give_consent(self, user_hash: str) -> None:
        at = now().isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO users (user_hash, consent_at, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT (user_hash) DO UPDATE SET consent_at = excluded.consent_at",
                (user_hash, at, at),
            )

    def set_lang(self, user_hash: str, lang: str) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET lang = ? WHERE user_hash = ?", (lang, user_hash))

    def set_pending(self, user_hash: str, pending: str | None) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET pending = ? WHERE user_hash = ?", (pending, user_hash))

    def delete_user(
        self, user_hash: str, is_locked: Callable[[str], bool] = lambda path: False
    ) -> tuple[list[str], list[str]]:
        """Remove the user's rows. Returns (media paths, evidence paths) no longer referenced, for the caller to delete.

        Reports in an approved case, or whose evidence is locked because the case was approved
        before, stay as evidence, unlinked from the user. Cached results that no other user's
        item points to are removed.
        """
        with self._db() as db:
            items = db.execute("SELECT sha256, media_path FROM items WHERE user_hash = ?", (user_hash,)).fetchall()
            db.execute("DELETE FROM items WHERE user_hash = ?", (user_hash,))

            reports = db.execute(
                "SELECT r.id, r.case_id, r.evidence_path, c.status FROM reports r JOIN cases c ON c.id = r.case_id "
                "WHERE r.user_hash = ?",
                (user_hash,),
            ).fetchall()
            evidence: list[str] = []
            for r in reports:
                if r["status"] == "approved_fake" or is_locked(r["evidence_path"]):
                    db.execute("UPDATE reports SET user_hash = NULL WHERE id = ?", (r["id"],))
                    continue
                db.execute("DELETE FROM reports WHERE id = ?", (r["id"],))
                if not db.execute("SELECT 1 FROM reports WHERE evidence_path = ?", (r["evidence_path"],)).fetchone():
                    evidence.append(r["evidence_path"])
                self._tidy_case(db, r["case_id"])

            media: list[str] = []
            for item in items:
                still_used = db.execute("SELECT 1 FROM items WHERE sha256 = ? LIMIT 1", (item["sha256"],)).fetchone()
                if not still_used:
                    db.execute("DELETE FROM results WHERE sha256 = ?", (item["sha256"],))
                    if item["media_path"] and not self._is_reported(db, item["sha256"]):
                        media.append(item["media_path"])
            db.execute("DELETE FROM users WHERE user_hash = ?", (user_hash,))
        return sorted(set(media)), evidence

    # --- items, media and rate limits -------------------------------------------

    def add_item(
        self, user_hash: str, sha256: str, kind: Kind, media_path: str | None = None, ttl: timedelta | None = None
    ) -> int:
        at = now()
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO items (user_hash, sha256, kind, received_at, media_path, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_hash, sha256, kind.value, at.isoformat(), media_path, (at + ttl).isoformat() if ttl else None),
            )
            return cur.lastrowid

    def last_item(self, user_hash: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM items WHERE user_hash = ? ORDER BY id DESC LIMIT 1", (user_hash,)
            ).fetchone()

    def user_item(self, user_hash: str, sha_prefix: str) -> sqlite3.Row | None:
        """The user's latest item whose hash starts with `sha_prefix` (what a button carries)."""
        if not sha_prefix or not sha_prefix.isalnum():
            return None
        with self._db() as db:
            return db.execute(
                "SELECT * FROM items WHERE user_hash = ? AND sha256 LIKE ? ORDER BY id DESC LIMIT 1",
                (user_hash, sha_prefix.lower() + "%"),
            ).fetchone()

    def item(self, item_id: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def items_since(self, user_hash: str, since: timedelta) -> int:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM items WHERE user_hash = ? AND received_at > ?",
                (user_hash, (now() - since).isoformat()),
            ).fetchone()
            return row[0]

    def expire_media(self, at: datetime | None = None) -> list[str]:
        """Forget media past its expiry unless reported. Returns paths no item needs any more, for deleting."""
        at = (at or now()).isoformat()
        with self._db() as db:
            expired = db.execute(
                "SELECT id, sha256, media_path FROM items WHERE media_path IS NOT NULL AND expires_at <= ?", (at,)
            ).fetchall()
            gone: list[str] = []
            for item in expired:
                if self._is_reported(db, item["sha256"]):
                    continue
                db.execute("UPDATE items SET media_path = NULL WHERE id = ?", (item["id"],))
                if not db.execute("SELECT 1 FROM items WHERE media_path = ? LIMIT 1", (item["media_path"],)).fetchone():
                    gone.append(item["media_path"])
        return sorted(set(gone))

    @staticmethod
    def _is_reported(db: sqlite3.Connection, sha256: str) -> bool:
        return db.execute("SELECT 1 FROM reports WHERE sha256 = ? LIMIT 1", (sha256,)).fetchone() is not None

    # --- hash cache ---------------------------------------------------------

    def cached_result(self, sha256: str, engine_version: str) -> CachedResult | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM results WHERE sha256 = ? AND engine_version = ?", (sha256, engine_version)
            ).fetchone()
        if row is None:
            return None
        fields = json.loads(row["fields_json"])
        return CachedResult(
            Kind(row["kind"]),
            Verdict(row["verdict"]),
            row["rule_row"],
            _signals(row["signals_json"]),
            fields.get("sender_type"),
            fields.get("sender_id"),
            row["lang"],
        )

    def save_result(self, result: Result) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO results (sha256, engine_version, kind, verdict, rule_row, signals_json, "
                "fields_json, needs_review, created_at, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.sha256,
                    result.engine_version,
                    result.kind.value,
                    result.verdict.value,
                    result.rule_row,
                    json.dumps([asdict(s) for s in result.signals], ensure_ascii=False),
                    json.dumps(asdict(result.fields), ensure_ascii=False),
                    int(result.needs_review),
                    now().isoformat(),
                    result.lang,
                ),
            )

    def clear_results(self) -> None:
        """Drop the hash cache, so the next forward of anything is checked against the current known fakes."""
        with self._db() as db:
            db.execute("DELETE FROM results")

    # --- reports and cases --------------------------------------------------------

    def already_reported(self, user_hash: str, sha256: str) -> int | None:
        with self._db() as db:
            row = db.execute(
                "SELECT case_id FROM reports WHERE user_hash = ? AND sha256 = ?", (user_hash, sha256)
            ).fetchone()
            return row["case_id"] if row else None

    def add_report(self, report: NewReport) -> int:
        """File a report into a case: same SHA-256 or same payee joins the existing case. Returns the case id.

        If the report links several open cases, they are merged into the oldest.
        """
        payees = sorted({normalize_payee(p) for p in report.payees if normalize_payee(p)})
        at = now().isoformat()
        with self._db() as db:
            linked = {r[0] for r in db.execute("SELECT case_id FROM reports WHERE sha256 = ?", (report.sha256,))}
            for p in payees:
                linked |= {r[0] for r in db.execute("SELECT case_id FROM case_payees WHERE payee = ?", (p,))}
            if linked:
                case_id = min(linked)
                for other in sorted(linked - {case_id}):
                    status = db.execute("SELECT status FROM cases WHERE id = ?", (other,)).fetchone()["status"]
                    if status == "open":
                        self._merge(db, other, into=case_id)
            else:
                case_id = db.execute("INSERT INTO cases (created_at) VALUES (?)", (at,)).lastrowid

            r = report.result
            db.execute(
                "INSERT INTO reports (case_id, sha256, user_hash, kind, size, evidence_path, reported_at, verdict, "
                "rule_row, engine_version, signals_json, fields_json, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    report.sha256,
                    report.user_hash,
                    report.kind.value,
                    report.size,
                    report.evidence_path,
                    at,
                    r.verdict.value,
                    r.rule_row,
                    r.engine_version,
                    json.dumps([asdict(s) for s in r.signals], ensure_ascii=False),
                    json.dumps(asdict(report.fields), ensure_ascii=False),
                    r.lang,
                ),
            )
            db.executemany(
                "INSERT OR IGNORE INTO case_payees (case_id, payee) VALUES (?, ?)", [(case_id, p) for p in payees]
            )
        return case_id

    @staticmethod
    def _merge(db: sqlite3.Connection, source: int, into: int) -> None:
        db.execute("UPDATE reports SET case_id = ? WHERE case_id = ?", (into, source))
        db.execute("UPDATE OR IGNORE case_payees SET case_id = ? WHERE case_id = ?", (into, source))
        db.execute("DELETE FROM case_payees WHERE case_id = ?", (source,))
        db.execute("UPDATE reviews SET case_id = ? WHERE case_id = ?", (into, source))
        db.execute("DELETE FROM cases WHERE id = ?", (source,))

    @staticmethod
    def _tidy_case(db: sqlite3.Connection, case_id: int) -> None:
        """After a report is removed: drop the case if empty, else rebuild its payees from what is left."""
        remaining = db.execute("SELECT fields_json FROM reports WHERE case_id = ?", (case_id,)).fetchall()
        db.execute("DELETE FROM case_payees WHERE case_id = ?", (case_id,))
        if not remaining:
            db.execute("DELETE FROM known_fakes WHERE case_id = ?", (case_id,))
            db.execute("DELETE FROM reviews WHERE case_id = ?", (case_id,))
            db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
            return
        for row in remaining:
            for p in report_payees(json.loads(row["fields_json"])):
                db.execute("INSERT OR IGNORE INTO case_payees VALUES (?, ?)", (case_id, normalize_payee(p)))

    def cases(self) -> list[sqlite3.Row]:
        """All cases, most-reported first."""
        with self._db() as db:
            return db.execute(
                "SELECT c.*, COUNT(r.id) AS reports, MIN(r.reported_at) AS first_report, MAX(r.reported_at) AS last_report, "
                "(SELECT GROUP_CONCAT(payee, ', ') FROM case_payees p WHERE p.case_id = c.id) AS payees "
                "FROM cases c JOIN reports r ON r.case_id = c.id GROUP BY c.id ORDER BY reports DESC, c.id"
            ).fetchall()

    def case(self, case_id: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()

    def case_reports(self, case_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM reports WHERE case_id = ? ORDER BY reported_at, id", (case_id,)).fetchall()

    def latest_report_for(self, sha256: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM reports WHERE sha256 = ? ORDER BY id DESC LIMIT 1", (sha256,)).fetchone()

    def report(self, report_id: int) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()

    def case_payees(self, case_id: int) -> list[str]:
        with self._db() as db:
            return [r[0] for r in db.execute("SELECT payee FROM case_payees WHERE case_id = ? ORDER BY payee", (case_id,))]

    def case_reviews(self, case_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM reviews WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()

    def review(self, case_id: int, reviewer: str, action: str, note: str = "") -> None:
        status = {"approve_fake": "approved_fake", "not_fake": "not_fake", "needs_info": "needs_info"}[action]
        at = now().isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO reviews (case_id, reviewer, action, note, at) VALUES (?, ?, ?, ?, ?)",
                (case_id, reviewer, action, note, at),
            )
            db.execute(
                "UPDATE cases SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?", (status, at, reviewer, case_id)
            )

    # --- known fakes ------------------------------------------------------------

    def add_known_fakes(
        self, case_id: int, reviewer: str, hashes: list[str], payees: list[str], domains: list[str] = ()
    ) -> None:
        at = now().isoformat()
        rows = [("hash", h, case_id, at, reviewer) for h in hashes]
        rows += [("payee", normalize_payee(p), case_id, at, reviewer) for p in payees if normalize_payee(p)]
        rows += [("domain", d.lower(), case_id, at, reviewer) for d in domains if d]
        with self._db() as db:
            db.executemany("INSERT OR IGNORE INTO known_fakes VALUES (?, ?, ?, ?, ?)", rows)

    def remove_known_fakes(self, case_id: int) -> None:
        """Undo a case's approval. Entries another approved case also confirmed stay."""
        with self._db() as db:
            db.execute("DELETE FROM known_fakes WHERE case_id = ?", (case_id,))

    def is_known_fake(self, sha256: str, payees: list[str] = ()) -> bool:
        """`payees` may also hold "domain:<host>" entries, for links from confirmed fakes."""
        keys = [("hash", sha256)]
        for p in payees:
            if p.startswith("domain:"):
                keys.append(("domain", p[7:].lower()))
            elif normalize_payee(p):
                keys.append(("payee", normalize_payee(p)))
        with self._db() as db:
            return any(
                db.execute("SELECT 1 FROM known_fakes WHERE kind = ? AND value = ? LIMIT 1", key).fetchone()
                for key in keys
            )

    # --- issuers and issued notices -------------------------------------------------

    def upsert_issuer(
        self, name: str, type_: str, domains: list[str], contacts: list[str], payees: list[str],
        match_names: list[str], requires_code: bool,
    ) -> int:
        with self._db() as db:
            db.execute(
                "INSERT INTO issuers (name, type, domains_json, contacts_json, payees_json, match_names_json, "
                "requires_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET "
                "type = excluded.type, domains_json = excluded.domains_json, contacts_json = excluded.contacts_json, "
                "payees_json = excluded.payees_json, match_names_json = excluded.match_names_json, "
                "requires_code = excluded.requires_code",
                (name, type_, json.dumps(domains), json.dumps(contacts), json.dumps(payees), json.dumps(match_names),
                 int(requires_code), now().isoformat()),
            )
            return db.execute("SELECT id FROM issuers WHERE name = ?", (name,)).fetchone()["id"]

    def issuers(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM issuers ORDER BY id").fetchall()
        return [_issuer(r) for r in rows]

    def issuer(self, issuer_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM issuers WHERE id = ?", (issuer_id,)).fetchone()
        return _issuer(row) if row else None

    def set_issuer_user(self, username: str, issuer_id: int, password_hash: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO issuer_users VALUES (?, ?, ?, ?) ON CONFLICT (username) DO UPDATE SET "
                "issuer_id = excluded.issuer_id, password_hash = excluded.password_hash",
                (username, issuer_id, password_hash, now().isoformat()),
            )

    def issuer_user(self, username: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM issuer_users WHERE username = ?", (username,)).fetchone()

    def add_notice(self, **notice) -> int:
        cols = ", ".join(notice)
        with self._db() as db:
            return db.execute(
                f"INSERT INTO issued_notices ({cols}, issued_at) VALUES ({', '.join('?' * len(notice))}, ?)",
                (*notice.values(), now().isoformat()),
            ).lastrowid

    def notice_by_code(self, code: str) -> dict | None:
        """The notice with the issuer's name and type, for the pipeline and the public page."""
        with self._db() as db:
            row = db.execute(
                "SELECT n.*, i.name AS issuer_name, i.type AS issuer_type, i.payees_json, i.domains_json "
                "FROM issued_notices n JOIN issuers i ON i.id = n.issuer_id WHERE n.code = ?",
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def issuer_notices(self, issuer_id: int) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM issued_notices WHERE issuer_id = ? ORDER BY id DESC", (issuer_id,)
            ).fetchall()

    def issuer_notice(self, issuer_id: int, notice_id: int) -> sqlite3.Row | None:
        """A notice only if it belongs to `issuer_id`: issuers never see each other's notices."""
        with self._db() as db:
            return db.execute(
                "SELECT * FROM issued_notices WHERE id = ? AND issuer_id = ?", (notice_id, issuer_id)
            ).fetchone()

    def revoke_notice(self, issuer_id: int, notice_id: int, username: str) -> bool:
        with self._db() as db:
            cur = db.execute(
                "UPDATE issued_notices SET status = 'revoked', revoked_at = ?, revoked_by = ? "
                "WHERE id = ? AND issuer_id = ? AND status = 'valid'",
                (now().isoformat(), username, notice_id, issuer_id),
            )
            return cur.rowcount == 1

    # --- feedback and usage counts ---------------------------------------------------

    def add_feedback(self, sha_prefix: str, verdict: str, vote: str) -> None:
        """Store the item's hash (completed from the prefix a button carries), the verdict and the vote."""
        with self._db() as db:
            row = db.execute(
                "SELECT sha256 FROM items WHERE sha256 LIKE ? LIMIT 1", (sha_prefix.lower() + "%",)
            ).fetchone()
            db.execute(
                "INSERT INTO feedback (sha256, verdict, vote) VALUES (?, ?, ?)",
                (row["sha256"] if row else sha_prefix.lower(), verdict, vote),
            )

    def record_check(self, kind: str, channel: str, verdict: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO checks (at, kind, channel, verdict) VALUES (?, ?, ?, ?)",
                (now().isoformat(), kind, channel, verdict),
            )

    def stats(self, hours: int = 24) -> dict:
        """Counts only, for the reviewers' stats page. No hashes, no users, no content."""
        since = (now() - timedelta(hours=hours)).isoformat()
        with self._db() as db:
            def grouped(sql: str, *args) -> dict[str, int]:
                return {r[0]: r[1] for r in db.execute(sql, args)}

            return {
                "users": db.execute("SELECT COUNT(*) FROM users WHERE consent_at IS NOT NULL").fetchone()[0],
                "checks": db.execute("SELECT COUNT(*) FROM checks").fetchone()[0],
                "by_kind": grouped("SELECT kind, COUNT(*) FROM checks GROUP BY kind"),
                "by_channel": grouped("SELECT channel, COUNT(*) FROM checks GROUP BY channel"),
                "by_verdict": grouped("SELECT verdict, COUNT(*) FROM checks GROUP BY verdict"),
                "feedback": grouped("SELECT vote, COUNT(*) FROM feedback GROUP BY vote"),
                "feedback_by_verdict": {
                    (r[0], r[1]): r[2]
                    for r in db.execute("SELECT verdict, vote, COUNT(*) FROM feedback GROUP BY verdict, vote")
                },
                "per_hour": grouped(
                    "SELECT substr(at, 1, 13), COUNT(*) FROM checks WHERE at > ? GROUP BY substr(at, 1, 13)", since
                ),
            }

    # --- audit log ------------------------------------------------------------

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                (now().isoformat(), actor, action, detail),
            )

    def audit_entries(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def report_payees(fields: dict | Fields) -> list[str]:
    """Payees an item asks money to be sent to. Payees only mentioned (a debit alert's merchant) do not cluster."""
    f = asdict(fields) if isinstance(fields, Fields) else fields
    if not f.get("demands_payment"):
        return []
    return list(f.get("upi_ids", [])) + [a["number"] for a in f.get("accounts", [])]



def _issuer(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "domains": json.loads(row["domains_json"]),
        "contacts": json.loads(row["contacts_json"]),
        "payees": json.loads(row["payees_json"]),
        "match_names": json.loads(row["match_names_json"]),
        "requires_code": bool(row["requires_code"]),
    }
