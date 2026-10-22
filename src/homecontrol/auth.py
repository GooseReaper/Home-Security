"""Dashboard login: password hashing, sessions and login rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

ITERATIONS = 600_000
SESSION_COOKIE = "hc_session"

# After MAX_FAILURES wrong passwords from one address within WINDOW seconds,
# that address is locked out for LOCKOUT seconds.
MAX_FAILURES = 5
WINDOW = 15 * 60
LOCKOUT = 15 * 60


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    """Return a 'pbkdf2_sha256$<iterations>$<salt>$<hash>' string for the config."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class Sessions:
    """In-memory login sessions. A restart logs everyone out, which is fine."""

    def __init__(self, hours: float) -> None:
        self.ttl = hours * 3600
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.monotonic() + self.ttl
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        expires = self._sessions.get(token)
        if expires is None:
            return False
        if expires < time.monotonic():
            del self._sessions[token]
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


class LoginLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def locked(self, client: str) -> bool:
        return self._locked_until.get(client, 0) > time.monotonic()

    def failed(self, client: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._failures.get(client, []) if now - t < WINDOW] + [now]
        self._failures[client] = recent
        if len(recent) >= MAX_FAILURES:
            self._locked_until[client] = now + LOCKOUT
            self._failures[client] = []

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
