"""Write-once storage: reported files (the evidence vault) and short-lived media.

Offline this is an append-only local directory: a file is created once, never overwritten, and
checked against its SHA-256 on every read. It is locked (made read-only, and in AWS given an
S3 Object Lock retention) only when a reviewer approves its case. Until then a user's DELETE
can still erase it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf", "text": ".txt"}
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8"}


class IntegrityError(Exception):
    """Stored bytes no longer match their recorded hash."""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


READ_ONLY = stat.S_IRUSR | stat.S_IRGRP


class LockedError(Exception):
    """The file is locked as evidence in an approved case and cannot be erased."""


def write_once(path: Path, data: bytes, lock: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:  # "x": fail rather than overwrite
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if lock:
        path.chmod(READ_ONLY)


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, sha256: str, ext: str) -> Path:
        return self.root / f"{sha256}{ext}"

    def put(self, data: bytes, sha256: str, ext: str) -> Path:
        """Store `data` under its hash. Storing the same bytes again is a no-op; different bytes are refused."""
        if sha256_of(data) != sha256:
            raise IntegrityError("data does not match the given hash")
        path = self.path(sha256, ext)
        if path.exists():
            self.get(path)  # verifies the stored copy
            return path
        write_once(path, data, lock=False)
        return path

    def lock(self, path: Path) -> bool:
        """Lock a file once its case is approved. Returns False if it was already locked."""
        if self.is_locked(path):
            return False
        path.chmod(READ_ONLY)
        return True

    @staticmethod
    def is_locked(path: Path) -> bool:
        return not path.stat().st_mode & stat.S_IWUSR

    def get(self, path: Path) -> bytes:
        data = path.read_bytes()
        if sha256_of(data) != path.name.split(".", 1)[0]:
            raise IntegrityError(f"{path.name} does not match its hash")
        return data

    def erase(self, path: Path) -> None:
        """Only for a user's DELETE of a report whose case is not approved. The caller audits it."""
        if not path.exists():
            return
        if self.is_locked(path):
            raise LockedError(path.name)
        path.unlink()


class MediaDir:
    """Checked files kept for MEDIA_TTL_HOURS, so the user can still report them."""

    def __init__(self, root: Path):
        self.root = root

    def save(self, data: bytes, sha256: str, ext: str) -> Path:
        path = self.root / f"{sha256}{ext}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        return path

    def read(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, path: Path) -> None:
        path.unlink(missing_ok=True)
