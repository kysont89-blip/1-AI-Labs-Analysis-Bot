"""Auth routes: register, login, logout.

Mirrors the flow Telegram's `/verify` produces (a verified, tiered
account), but for users who arrive through the web. Web signups live
in the `web_users` table — they're separate from the `users` table
the Telegram bot writes to. Phase 5 will merge the two via the
`neon_user_id` link in web_users.

Password handling lives in deps.py (bcrypt + SHA-256 prefix).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..deps import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    current_user,
    hash_password,
    make_session_token,
    mint_web_user_id,
    verify_password,
)
from ..db import get_cursor

router = APIRouter(prefix="/auth", tags=["auth"])

# Templates resolved from main.py via app.state.templates — see
# web/main.py where the Jinja2Templates instance is attached.
# We re-import here as a fallback if routes are mounted standalone.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


# ── register ──────────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_form(
    request: Request,
    user=Depends(current_user),
):
    if user is not None:
        return RedirectResponse("/", status_code=302)
    return _templates(request).TemplateResponse(
        request,
        "auth/register.html",
        {"brand": {"name": "1% AI Lab"}, "error": None,
         "form": {"email": ""}},
    )


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return _templates(request).TemplateResponse(
            request,
            "auth/register.html",
            {"brand": {"name": "1% AI Lab"},
             "error": "Enter a valid email address.",
             "form": {"email": email}},
            status_code=400,
        )
    if len(password) < 8:
        return _templates(request).TemplateResponse(
            request,
            "auth/register.html",
            {"brand": {"name": "1% AI Lab"},
             "error": "Password must be at least 8 characters.",
             "form": {"email": email}},
            status_code=400,
        )
    if password != password_confirm:
        return _templates(request).TemplateResponse(
            request,
            "auth/register.html",
            {"brand": {"name": "1% AI Lab"},
             "error": "Passwords do not match.",
             "form": {"email": email}},
            status_code=400,
        )

    web_user_id = mint_web_user_id()
    pwd_hash = hash_password(password)
    try:
        async with get_cursor() as cur:
            # Create the matching `users` row first so verification_requests
            # and other FK-pointing tables have a parent. Same id as
            # web_user_id keeps the link unambiguous. Tier=free, no broker
            # yet — verification in Phase 2 upgrades the tier.
            await cur.execute(
                """
                INSERT INTO users
                    (user_id, first_name, tier, verified, daily_reset)
                VALUES (%s, %s, 'free', FALSE, NOW())
                ON CONFLICT (user_id) DO NOTHING
                """,
                (web_user_id, f"web-{web_user_id}"),
            )
            await cur.execute(
                """
                INSERT INTO web_users
                    (web_user_id, email, password_hash, neon_user_id, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (web_user_id, email, pwd_hash, web_user_id),
            )
    except Exception as e:
        # Unique-email violation is the only expected race; surface as form error.
        msg = str(e)
        if "web_users_email_key" in msg or "duplicate" in msg.lower():
            return _templates(request).TemplateResponse(
                request,
                "auth/register.html",
                {"brand": {"name": "1% AI Lab"},
                 "error": "An account with that email already exists.",
                 "form": {"email": email}},
                status_code=400,
            )
        raise

    resp = RedirectResponse("/auth/welcome", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        make_session_token(web_user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # Render sets the TLS edge; the cookie stays http on the wire inside
        path="/",
    )
    return resp


# ── login ─────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, user=Depends(current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=302)
    return _templates(request).TemplateResponse(
        request,
        "auth/login.html",
        {"brand": {"name": "1% AI Lab"}, "error": None,
         "form": {"email": ""}},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    email = email.strip().lower()
    async with get_cursor(commit=False) as cur:
        await cur.execute(
            "SELECT web_user_id, password_hash FROM web_users WHERE email = %s",
            (email,),
        )
        row = await cur.fetchone()

    if not row or not verify_password(password, row["password_hash"]):
        return _templates(request).TemplateResponse(
            request,
            "auth/login.html",
            {"brand": {"name": "1% AI Lab"},
             "error": "Email or password is incorrect.",
             "form": {"email": email}},
            status_code=400,
        )

    # Update last_login in its own commit.
    async with get_cursor() as cur:
        await cur.execute(
            "UPDATE web_users SET last_login = NOW() WHERE web_user_id = %s",
            (row["web_user_id"],),
        )

    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        make_session_token(row["web_user_id"]),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return resp


# ── logout ────────────────────────────────────────────────────────

@router.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ── welcome (post-signup landing) ────────────────────────────────

@router.get("/welcome", response_class=HTMLResponse)
async def welcome(
    request: Request,
    user=Depends(current_user),
):
    if user is None:
        return RedirectResponse("/auth/login", status_code=302)
    return _templates(request).TemplateResponse(
        request,
        "auth/welcome.html",
        {"brand": {"name": "1% AI Lab"}, "user": user},
    )
