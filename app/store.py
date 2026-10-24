"""SQLite storage: users, items, results (the hash cache) and the audit log.

Cases and reviews arrive with the report flow. Message text is never stored: items keep
only the hash, and results keep masked fields.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import Kind, PointsTo, Result, Signal, Strength, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_hash   TEXT PRIMARY KEY,
    lang        TEXT,
    consent_at  TEXT,
    created_at  TEXT NOT NULL
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
    PRIMARY KEY (sha256, engine_version)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT
);
"""


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CachedResult:
    kind: Kind
    verdict: Verdict
    rule_row: str
    signals: list[Signal]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
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

    def delete_user(self, user_hash: str) -> None:
        """Remove the user's rows. Cached results no other user's item points to go too.

        Items in an approved evidence packet are kept; packets arrive with the report flow.
        """
        with self._db() as db:
            hashes = [r[0] for r in db.execute("SELECT DISTINCT sha256 FROM items WHERE user_hash = ?", (user_hash,))]
            db.execute("DELETE FROM items WHERE user_hash = ?", (user_hash,))
            for h in hashes:
                if not db.execute("SELECT 1 FROM items WHERE sha256 = ? LIMIT 1", (h,)).fetchone():
                    db.execute("DELETE FROM results WHERE sha256 = ?", (h,))
            db.execute("DELETE FROM users WHERE user_hash = ?", (user_hash,))

    # --- items and rate limits ------------------------------------------------

    def add_item(self, user_hash: str, sha256: str, kind: Kind) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO items (user_hash, sha256, kind, received_at) VALUES (?, ?, ?, ?)",
                (user_hash, sha256, kind.value, now().isoformat()),
            )

    def items_since(self, user_hash: str, since: timedelta) -> int:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM items WHERE user_hash = ? AND received_at > ?",
                (user_hash, (now() - since).isoformat()),
            ).fetchone()
            return row[0]

    # --- hash cache ---------------------------------------------------------

    def cached_result(self, sha256: str, engine_version: str) -> CachedResult | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM results WHERE sha256 = ? AND engine_version = ?", (sha256, engine_version)
            ).fetchone()
        if row is None:
            return None
        signals = [
            Signal(s["check"], s["code"], Strength(s["strength"]), PointsTo(s["points_to"]), s["reason_en"], s["reason_hi"])
            for s in json.loads(row["signals_json"])
        ]
        return CachedResult(Kind(row["kind"]), Verdict(row["verdict"]), row["rule_row"], signals)

    def save_result(self, result: Result) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

    # --- audit log ------------------------------------------------------------

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                (now().isoformat(), actor, action, detail),
            )
