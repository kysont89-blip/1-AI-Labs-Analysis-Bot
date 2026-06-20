"""Shared FastAPI dependencies for the web layer.

This module is the single source of truth for:
  - Session cookie signing (itsdangerous)
  - Current-user resolution (cookie -> web_user_id -> neon user_id)
  - Password hashing (bcrypt)
  - Admin gating

It is imported by routes/auth.py, routes/verify.py, and (later) routes/dashboard.py.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from .db import db

SESSION_COOKIE = "xox_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
WEB_USER_ID_COOKIE = "xox_wuid"      # signed Telegram-style id we mint for web users
WEB_USER_ID_MAX_AGE = 60 * 60 * 24 * 365 * 5  # 5 years


# ── Session secret ────────────────────────────────────────────────

def _session_secret() -> bytes:
    s = os.environ.get("SESSION_SECRET")
    if not s:
        # Dev fallback only — never used in production because Render
        # always sets SESSION_SECRET from .env.
        s = "dev-secret-do-not-use-in-prod"
    return s.encode("utf-8")


_session_signer = TimestampSigner(_session_secret(), salt="xox-session-v1")


def make_session_token(web_user_id: int) -> str:
    """Sign a session token carrying the web_user_id."""
    return _session_signer.sign(str(web_user_id)).decode("ascii")


def read_session_token(token: str) -> Optional[int]:
    try:
        value = _session_signer.unsign(
            token, max_age=SESSION_MAX_AGE,
        )
        return int(value.decode("ascii"))
    except (BadSignature, SignatureExpired, ValueError):
        return None


# ── Password hashing ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    # bcrypt rejects passwords > 72 bytes; pre-hash with SHA-256 to be
    # safe with long passphrases, then bcrypt the digest. The hash
    # includes the SHA-256 prefix so verify() can detect it.
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return "$sha256$" + bcrypt.hashpw(digest, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("$sha256$"):
        digest = hashlib.sha256(password.encode("utf-8")).digest()
        return bcrypt.checkpw(digest, stored[len("$sha256$"):].encode("ascii"))
    # Legacy / direct bcrypt fallback
    return bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))


# ── Web user identity ─────────────────────────────────────────────

@dataclass
class CurrentUser:
    """The user the web request is acting as.

    `web_user_id` is the integer id we mint for the web account. It
    IS the primary key used in the Neon `users` table for web-only
    signups. Telegram-linked accounts share their Telegram user_id as
    the Neon user_id (Phase 5 wires that up).
    """
    web_user_id: int
    email: str
    is_admin: bool


_admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()


def _mint_web_user_id() -> int:
    """Generate a fresh bigint-style id outside Telegram's range.

    Telegram user_ids are positive 32-bit-ish integers (typically < 10^10).
    We mint web ids starting at 10^15 so the two namespaces never collide
    and a future Phase 5 link is unambiguous (id < 10^10 = Telegram,
    id >= 10^15 = web).
    """
    while True:
        candidate = secrets.randbelow(10**15) + 10**15
        # Conservative: keep this small enough to fit BIGINT (signed 64-bit
        # max is 9.2e18). 10^15 + 10^15 = 2e15 is well within range.
        return candidate


# ── Cookie-based dependencies ─────────────────────────────────────

async def current_user(
    request: Request,
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> Optional[CurrentUser]:
    """Resolve the request's CurrentUser from the session cookie.

    Returns None when there is no valid cookie. Routes that require
    auth should call `require_user` instead.
    """
    if not session:
        return None
    web_user_id = read_session_token(session)
    if web_user_id is None:
        return None

    # Look up email + admin status. We don't join users here because
    # the route layer already pulls the User row when it needs to.
    from .db import get_cursor  # local import keeps deps.py import-cheap
    async with get_cursor(commit=False) as cur:
        await cur.execute(
            "SELECT web_user_id, email, is_admin FROM web_users "
            "WHERE web_user_id = %s",
            (web_user_id,),
        )
        row = await cur.fetchone()

    if not row:
        return None

    is_admin = bool(row["is_admin"]) or (
        _admin_email and row["email"].lower() == _admin_email
    )
    return CurrentUser(
        web_user_id=row["web_user_id"],
        email=row["email"],
        is_admin=is_admin,
    )


async def require_user(
    user: Optional[CurrentUser] = Depends(current_user),
) -> CurrentUser:
    """FastAPI dependency that 401s if no valid session."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required",
            headers={"Location": "/auth/login"},
        )
    return user


async def require_admin(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return user


# Public alias so routes can mint ids without re-importing the helper.
mint_web_user_id = _mint_web_user_id
