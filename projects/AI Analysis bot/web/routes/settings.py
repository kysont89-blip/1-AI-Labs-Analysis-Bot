"""Settings routes — web mirror of Telegram's `/settings`.

Web and Telegram share the same `users` row in Neon, so updating
risk_percent from the web immediately reflects in the bot's next
`/analyze` call. The bot's `bots/database.UserDatabase.update_user_settings`
uses sync sqlite3 — we mirror its surface here via async db.py.

Allowed fields (locked in bots/database.py):
  account_balance, risk_percent, leverage, leverage_crypto,
  leverage_mt5, default_timeframe, trading_style
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..deps import require_user
from ..db import db

router = APIRouter(prefix="/app/settings", tags=["settings"])

# Allowed values — match what the bot path validates on input.
ALLOWED_TIMEFRAMES = {"M1", "M3", "M5", "M15", "M30",
                      "H1", "H2", "H4", "H6", "H8", "H12",
                      "D1", "D3", "W1", "Mo1"}
ALLOWED_STYLES = {"auto", "scalp", "day", "swing", "swing_h4",
                  "position", "btc_hold"}


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


async def _render(request: Request, user, *, error: str | None = None,
                  saved: bool = False, status_code: int = 200):
    u = await db.get_user(user.web_user_id)
    return _templates(request).TemplateResponse(
        request,
        "app/settings.html",
        {
            "brand": {"name": "1% AI Lab"},
            "user": user,
            "u": u,
            "timeframes": sorted(ALLOWED_TIMEFRAMES),
            "styles": sorted(ALLOWED_STYLES),
            "saved": saved,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def settings_form(request: Request, user=Depends(require_user)):
    u = await db.get_user(user.web_user_id)
    if u is None:
        raise HTTPException(404, "User row missing")
    return await _render(request, user,
                         saved=request.query_params.get("saved") == "1")


@router.post("")
@router.post("/")
async def settings_submit(
    request: Request,
    account_balance: float = Form(...),
    risk_percent: float = Form(...),
    leverage: float = Form(...),
    leverage_crypto: float = Form(...),
    leverage_mt5: float = Form(...),
    default_timeframe: str = Form(...),
    trading_style: str = Form(...),
    user=Depends(require_user),
):
    # Range checks mirror the bot's expectations.
    if account_balance <= 0:
        return await _render(request, user,
                             error="Account balance must be > 0.",
                             status_code=400)
    if not (0 < risk_percent <= 10):
        return await _render(request, user,
                             error="Risk must be between 0 and 10%.",
                             status_code=400)
    if leverage <= 0:
        return await _render(request, user,
                             error="Leverage must be > 0.",
                             status_code=400)
    if default_timeframe not in ALLOWED_TIMEFRAMES:
        return await _render(request, user,
                             error=f"Unknown timeframe: {default_timeframe}.",
                             status_code=400)
    if trading_style not in ALLOWED_STYLES:
        return await _render(request, user,
                             error=f"Unknown trading style: {trading_style}.",
                             status_code=400)

    ok = await db.update_user_settings(
        user.web_user_id,
        account_balance=account_balance,
        risk_percent=risk_percent,
        leverage=leverage,
        leverage_crypto=leverage_crypto,
        leverage_mt5=leverage_mt5,
        default_timeframe=default_timeframe,
        trading_style=trading_style,
    )
    if not ok:
        return await _render(request, user,
                             error="No settings updated (bad field names?)",
                             status_code=400)

    return RedirectResponse("/app/settings?saved=1", status_code=302)