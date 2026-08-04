"""Staff authentication for the privileged surfaces.

Two trust zones, not one. The customer chat is deliberately unauthenticated —
it sits on a public page, and identity is established *inside* the conversation
only when an action needs it (see `flows.py`). Everything else is the opposite:
the agent console shows other customers' transcripts, the knowledge base decides
what the assistant is allowed to claim, and the settings screen accepts an API
key. Those are staff surfaces and are gated here.

The rule this module exists to enforce: **authorise at the API, not in the UI.**
Hiding a tab in JavaScript is not a control — the endpoint still answers anyone
who calls it directly.

Demo-grade, deliberately:

  * Credentials live in a dict below, hashed with PBKDF2. A real deployment
    authenticates against the bank's IdP over OIDC — staff already have a
    corporate identity, and building a second one to manage and leak is the
    wrong move.
  * Sessions are in memory, so a restart logs everyone out.
  * The cookie is HttpOnly and SameSite=Lax. It is not marked Secure because
    the demo runs over plain HTTP on localhost; over TLS it must be.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from threading import Lock

COOKIE_NAME = "rtb_staff"
SESSION_TTL_SECONDS = 8 * 60 * 60      # a working day
_PBKDF2_ROUNDS = 200_000


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                               _PBKDF2_ROUNDS)


@dataclass(frozen=True)
class StaffUser:
    username: str
    display_name: str
    role: str


# One demo account. The password is stated openly because writing a credential
# into source is only defensible when it protects nothing real, and pretending
# otherwise would be worse than saying so.
_SALT = b"regional-trust-demo-salt"
DEMO_USERNAME = "agent"
DEMO_PASSWORD = "demo1234"
_STAFF: dict[str, tuple[bytes, StaffUser]] = {
    DEMO_USERNAME: (
        _hash(DEMO_PASSWORD, _SALT),
        StaffUser(DEMO_USERNAME, "Priya Raman", "contact-centre agent"),
    ),
}


@dataclass
class StaffSession:
    token: str
    user: StaffUser
    created_at: float

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS


class AuthStore:
    def __init__(self) -> None:
        self._sessions: dict[str, StaffSession] = {}
        self._lock = Lock()

    def login(self, username: str, password: str) -> StaffSession | None:
        record = _STAFF.get((username or "").strip().lower())

        # Hash even when the user does not exist, and compare in constant time,
        # so a wrong username and a wrong password take the same path. Otherwise
        # response timing enumerates valid usernames.
        candidate = _hash(password or "", _SALT)
        expected, user = record if record else (_hash(os.urandom(16).hex(), _SALT), None)
        if not hmac.compare_digest(candidate, expected) or user is None:
            return None

        session = StaffSession(
            token=secrets.token_urlsafe(32), user=user, created_at=time.time()
        )
        with self._lock:
            self._sessions[session.token] = session
        return session

    def resolve(self, token: str | None) -> StaffSession | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expired:
                del self._sessions[token]
                return None
            return session

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


def parse_cookie(header: str | None) -> str | None:
    """Pull our cookie out of a Cookie header without importing http.cookies.

    `SimpleCookie` silently drops the whole header on a malformed pair, which
    turns one bad third-party cookie into a logout.
    """
    if not header:
        return None
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value.strip() or None
    return None


def cookie_header(token: str, max_age: int = SESSION_TTL_SECONDS) -> str:
    # HttpOnly: JavaScript must not be able to read it, so an XSS in the console
    # cannot lift a staff session. SameSite=Lax: a cross-site POST cannot carry
    # it, which is what stops a drive-by from deleting the knowledge base.
    return (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={max_age}")


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
