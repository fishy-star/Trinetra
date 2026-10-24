"""Run chat messages in parallel across users, in order within each chat.

One user's slow OCR must not hold up anyone else, but one user's messages (YES, then a notice,
then 1) must still be handled in the order they were sent.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

WORKERS = 8


class Dispatcher:
    def __init__(self, workers: int = WORKERS):
        self.workers = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trinetra-chat")
        self._queues: dict[str, deque[Callable[[], None]]] = {}
        self._guard = threading.Lock()

    def submit(self, key: str, job: Callable[[], None]) -> None:
        """Queue `job` for chat `key`. A chat's jobs run one at a time, in order; different chats run in parallel."""
        with self._guard:
            queue = self._queues.setdefault(key, deque())
            queue.append(job)
            if len(queue) > 1:
                return  # the worker already draining this chat will reach it
        self._pool.submit(self._drain, key)

    def _drain(self, key: str) -> None:
        while True:
            with self._guard:
                job = self._queues[key][0]
            try:
                job()
            except Exception:
                log.exception("chat job failed")
            with self._guard:
                queue = self._queues[key]
                queue.popleft()
                if not queue:
                    del self._queues[key]
                    return

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
