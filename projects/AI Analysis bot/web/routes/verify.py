"""XOX verify routes — web mirror of Telegram's /verify.

Users come in three flavours:
  - tier1:  $100-499 deposit
  - tier2:  $500-1999
  - tier3:  $2000+
  - vip:    $5000+ (ShelbyMarket)

The flow mirrors the Telegram bot:
  1. User picks a broker and enters their broker UID.
  2. Optionally enters a wallet address.
  3. Optionally uploads a deposit proof image.
  4. Admin (matched on ADMIN_EMAIL) approves or rejects via the
     /admin/verify/{user_id}/{action} endpoint.
  5. On approval, the user's tier flips and verified=true.

Web users don't have a row in `users` until the link is made in
Phase 5 — for now we store verification data in a flat table so the
admin can act on it. Phase 5 will reconcile web_users → users via
neon_user_id.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..deps import require_admin, require_user
from ..db import db, get_cursor

router = APIRouter(prefix="/verify", tags=["verify"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_BROKERS = {"shelbymarket", "vantage", "binance", "bybit", "other"}
ALLOWED_TIERS = {"tier1", "tier2", "tier3", "vip"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


# ── helpers ───────────────────────────────────────────────────────

def _tier_for_amount(amount: float) -> Optional[str]:
    if amount >= 5000:
        return "vip"
    if amount >= 2000:
        return "tier3"
    if amount >= 500:
        return "tier2"
    if amount >= 100:
        return "tier1"
    return None


# ── GET /verify — broker selector + status ────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def verify_index(request: Request, user=Depends(require_user)):
    pending = await _latest_request(user.web_user_id)
    return _templates(request).TemplateResponse(
        request,
        "verify/index.html",
        {"brand": {"name": "1% AI Lab"}, "user": user,
         "pending": pending, "error": None,
         "form": {"broker": "shelbymarket", "broker_uid": "",
                  "wallet_address": "", "amount": ""}},
    )


@router.get("/status", response_class=HTMLResponse)
async def verify_status(request: Request, user=Depends(require_user)):
    pending = await _latest_request(user.web_user_id)
    return _templates(request).TemplateResponse(
        request,
        "verify/status.html",
        {"brand": {"name": "1% AI Lab"}, "user": user,
         "pending": pending},
    )


async def _latest_request(web_user_id: int) -> Optional[dict]:
    # Web verification requests live in the same table the bot writes to,
    # keyed by web_user_id (>= 10^15) so they never collide with Telegram ids.
    async with get_cursor(commit=False) as cur:
        await cur.execute(
            """
            SELECT id, broker, broker_uid, wallet_address, deposit_proof,
                   status, requested_at, verified_at
            FROM verification_requests
            WHERE user_id = %s
            ORDER BY requested_at DESC LIMIT 1
            """,
            (web_user_id,),
        )
        return await cur.fetchone()


# ── POST /verify — submit ─────────────────────────────────────────

@router.post("")
@router.post("/")
async def verify_submit(
    request: Request,
    broker: str = Form(...),
    broker_uid: str = Form(...),
    wallet_address: str = Form(""),
    amount: str = Form(""),
    proof: UploadFile = File(None),
    user=Depends(require_user),
):
    broker_l = broker.strip().lower()
    if broker_l not in ALLOWED_BROKERS:
        return _render_index_error(request, user, "Unknown broker.", broker_l)

    uid = broker_uid.strip()
    if not uid:
        return _render_index_error(request, user, "Broker UID is required.",
                                    broker_l)

    amount_f: Optional[float] = None
    if amount.strip():
        try:
            amount_f = float(amount)
        except ValueError:
            return _render_index_error(
                request, user,
                "Deposit amount must be a number.", broker_l,
            )
        if amount_f < 0:
            return _render_index_error(
                request, user,
                "Deposit amount cannot be negative.", broker_l,
            )

    proof_path: Optional[str] = None
    if proof is not None and proof.filename:
        ext = Path(proof.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            return _render_index_error(
                request, user,
                f"Unsupported file type: {ext}.", broker_l,
            )
        contents = await proof.read()
        if len(contents) > MAX_UPLOAD_BYTES:
            return _render_index_error(
                request, user,
                "File too large (max 5 MB).", broker_l,
            )
        # Mint a random filename so /uploads never exposes the original.
        safe_name = f"{secrets.token_urlsafe(16)}{ext}"
        full = UPLOAD_DIR / safe_name
        full.write_bytes(contents)
        proof_path = str(full.name)

    # Reject duplicate pending request
    existing = await _latest_request(user.web_user_id)
    if existing and existing["status"] == "pending":
        return _render_index_error(
            request, user,
            "You already have a verification pending — wait for admin review.",
            broker_l,
        )

    async with get_cursor() as cur:
        await cur.execute(
            """
            INSERT INTO verification_requests
                (user_id, broker, broker_uid, wallet_address, deposit_proof,
                 status, requested_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', NOW())
            """,
            (user.web_user_id, broker_l, uid,
             wallet_address.strip() or None, proof_path),
        )

    return RedirectResponse("/verify/status", status_code=302)


def _render_index_error(request, user, msg, broker_l):
    return _templates(request).TemplateResponse(
        request,
        "verify/index.html",
        {"brand": {"name": "1% AI Lab"}, "user": user,
         "pending": None, "error": msg,
         "form": {"broker": broker_l, "broker_uid": "",
                  "wallet_address": "", "amount": ""}},
        status_code=400,
    )


# ── Admin endpoints ──────────────────────────────────────────────

@router.get("/admin/pending", response_class=HTMLResponse)
async def admin_pending(
    request: Request,
    user=Depends(require_admin),
):
    async with get_cursor(commit=False) as cur:
        await cur.execute(
            """
            SELECT v.id, v.user_id, v.broker, v.broker_uid,
                   v.wallet_address, v.deposit_proof, v.requested_at,
                   w.email
            FROM verification_requests v
            LEFT JOIN web_users w ON w.web_user_id = v.user_id
            WHERE v.status = 'pending'
            ORDER BY v.requested_at
            """,
        )
        rows = await cur.fetchall()
    return _templates(request).TemplateResponse(
        request,
        "verify/admin_pending.html",
        {"brand": {"name": "1% AI Lab"}, "user": user, "rows": rows},
    )


@router.post("/admin/{web_user_id}/approve")
async def admin_approve(
    web_user_id: int,
    new_tier: str = Form(...),
    user=Depends(require_admin),
):
    new_tier_l = new_tier.strip().lower()
    if new_tier_l not in ALLOWED_TIERS:
        raise HTTPException(400, f"Invalid tier: {new_tier_l}")

    async with get_cursor() as cur:
        await cur.execute(
            """
            UPDATE verification_requests
            SET status = 'approved', verified_at = NOW()
            WHERE user_id = %s AND status = 'pending'
            """,
            (web_user_id,),
        )
        # Make sure a users row exists for this web user (mirrors the bot
        # path) and is marked verified with the new tier.
        await cur.execute(
            """
            INSERT INTO users (user_id, first_name, tier, verified, daily_reset)
            VALUES (%s, %s, %s, TRUE, NOW())
            ON CONFLICT (user_id) DO UPDATE
                SET tier = EXCLUDED.tier, verified = TRUE
            """,
            (web_user_id, f"web-{web_user_id}", new_tier_l),
        )
        await cur.execute(
            """
            UPDATE web_users SET neon_user_id = %s WHERE web_user_id = %s
            """,
            (web_user_id, web_user_id),
        )
    return RedirectResponse("/verify/admin/pending", status_code=302)


@router.post("/admin/{web_user_id}/reject")
async def admin_reject(
    web_user_id: int,
    user=Depends(require_admin),
):
    async with get_cursor() as cur:
        await cur.execute(
            """
            UPDATE verification_requests
            SET status = 'rejected', verified_at = NOW()
            WHERE user_id = %s AND status = 'pending'
            """,
            (web_user_id,),
        )
    return RedirectResponse("/verify/admin/pending", status_code=302)


# ── Static proof download (admin only) ───────────────────────────

from fastapi.responses import FileResponse  # noqa: E402

@router.get("/admin/proof/{filename}")
async def admin_proof(
    filename: str,
    user=Depends(require_admin),
):
    safe = (UPLOAD_DIR / filename).resolve()
    if UPLOAD_DIR.resolve() not in safe.parents and safe != UPLOAD_DIR.resolve():
        raise HTTPException(400, "Bad filename")
    if not safe.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(safe)
