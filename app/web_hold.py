"""What the web page just checked, held in memory only, so the person can report or dispute it.

Nothing here is written to disk. An entry lasts 30 minutes; a report copies it to the evidence store
only after the person ticks the consent box.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from app.models import Kind, Result

HOLD_SECONDS = 30 * 60
MAX_HELD = 200


@dataclass(frozen=True)
class Held:
    data: bytes
    kind: Kind
    ext: str
    result: Result
    expires: float


class WebHold:
    def __init__(self, seconds: int = HOLD_SECONDS, limit: int = MAX_HELD):
        self.seconds, self.limit = seconds, limit
        self._items: OrderedDict[str, Held] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, data: bytes, kind: Kind, ext: str, result: Result) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._items[token] = Held(data, kind, ext, result, time.monotonic() + self.seconds)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)
        return token

    def get(self, token: str) -> Held | None:
        with self._lock:
            held = self._items.get(token)
            if held is None or held.expires < time.monotonic():
                self._items.pop(token, None)
                return None
            return held
