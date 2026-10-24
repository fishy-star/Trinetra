"""Background housekeeping: delete unreported media once it expires."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.evidence import MediaDir
from app.store import Store

log = logging.getLogger(__name__)

PURGE_EVERY_SECONDS = 600


def purge_media(store: Store, media: MediaDir) -> int:
    """Delete media past MEDIA_TTL_HOURS unless it was reported. Returns how many files were deleted."""
    paths = store.expire_media()
    for path in paths:
        media.delete(Path(path))
    if paths:
        store.audit("system", "media_purged", f"files={len(paths)}")
        log.info("purged %d expired media files", len(paths))
    return len(paths)


def start_purger(store: Store, media: MediaDir, stop: threading.Event) -> threading.Thread:
    """Purge now, then every PURGE_EVERY_SECONDS until `stop` is set."""

    def loop() -> None:
        while True:
            try:
                purge_media(store, media)
            except Exception:
                log.exception("media purge failed")
            if stop.wait(PURGE_EVERY_SECONDS):
                return

    thread = threading.Thread(target=loop, name="media-purger", daemon=True)
    thread.start()
    return thread
