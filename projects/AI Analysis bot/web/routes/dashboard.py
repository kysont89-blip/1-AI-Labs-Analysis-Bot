"""Dashboard shell — symbol selector, timeframe, TradingView chart.

Phase 3 ships:
  - GET /app         — full dashboard with chart and Run analysis
  - The chart calls /market/candles and /market/indicators
  - "Run analysis" calls /analyze (Phase 4) and overlays entry/SL/TP

We deliberately don't compute the analysis in this route — it's
defined in routes/analyze.py (Phase 4) so the JSON surface is
reusable from the bot path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import current_user

router = APIRouter(prefix="/app", tags=["dashboard"])


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


# Symbols the dashboard lets the user pick from. Crypto only for now —
# Phase 4 adds XAUUSD and FX once the report builder supports them.
SYMBOLS = [
    {"id": "BTCUSDT", "label": "BTC/USDT", "kind": "crypto"},
    {"id": "ETHUSDT", "label": "ETH/USDT", "kind": "crypto"},
    {"id": "SOLUSDT", "label": "SOL/USDT", "kind": "crypto"},
    {"id": "BNBUSDT", "label": "BNB/USDT", "kind": "crypto"},
    {"id": "XRPUSDT", "label": "XRP/USDT", "kind": "crypto"},
]
TIMEFRAMES = [
    {"id": "M15", "label": "15m"},
    {"id": "H1",  "label": "1h"},
    {"id": "H4",  "label": "4h"},
    {"id": "D1",  "label": "1d"},
]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def app_index(request: Request, user=Depends(current_user)):
    if user is None:
        # Marketing site is on /, dashboard is on /app — bounce to login.
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login", status_code=302)
    return _templates(request).TemplateResponse(
        request,
        "app/dashboard.html",
        {
            "brand": {"name": "1% AI Lab"},
            "user": user,
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "default_symbol": "BTCUSDT",
            "default_timeframe": "H1",
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def app_history(request: Request, user=Depends(current_user)):
    if user is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login", status_code=302)
    return _templates(request).TemplateResponse(
        request,
        "app/history.html",
        {"brand": {"name": "1% AI Lab"}, "user": user},
    )
