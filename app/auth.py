"""Sessions, CSRF tokens and login lockout, shared by the review console and the issuer portal."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from fastapi.responses import Response

SESSION_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60


@dataclass(frozen=True)
class Sessions:
    """A signed, expiring session cookie scoped to one area of the site (its own name, path and key)."""

    cookie: str
    path: str
    secret: bytes

    def _sign(self, value: str) -> str:
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def user(self, request: Request) -> str | None:
        """The signed-in name, or None. The caller still checks the name is a current account."""
        raw = request.cookies.get(self.cookie, "")
        try:
            user, expiry, sig = raw.rsplit("|", 2)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, self._sign(f"{user}|{expiry}")) or int(expiry) < time.time():
            return None
        return user

    def start(self, response: Response, request: Request, user: str) -> None:
        body = f"{user}|{int(time.time()) + SESSION_SECONDS}"
        response.set_cookie(
            self.cookie, f"{body}|{self._sign(body)}", max_age=SESSION_SECONDS, httponly=True, samesite="strict",
            secure=request.url.scheme == "https", path=self.path,
        )

    def end(self, response: Response) -> None:
        response.delete_cookie(self.cookie, path=self.path)

    def csrf(self, request: Request) -> str:
        return self._sign("csrf|" + request.cookies.get(self.cookie, ""))[:32]

    def check_csrf(self, request: Request, token: str) -> None:
        if not hmac.compare_digest(token, self.csrf(request)):
            raise HTTPException(403, "Invalid form token. Reload the page and try again.")


@dataclass
class LoginLimiter:
    """Lock a client out for LOCKOUT_SECONDS after MAX_FAILED_LOGINS failed logins."""

    failed: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def locked(self, client: str) -> bool:
        recent = [t for t in self.failed[client] if t > time.time() - LOCKOUT_SECONDS]
        self.failed[client] = recent
        return len(recent) >= MAX_FAILED_LOGINS

    def fail(self, client: str) -> None:
        self.failed[client].append(time.time())

    def clear(self) -> None:
        self.failed.clear()


def client_of(request: Request) -> str:
    return request.client.host if request.client else "unknown"
