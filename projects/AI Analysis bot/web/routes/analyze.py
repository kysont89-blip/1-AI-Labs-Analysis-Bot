"""Analyze routes — web mirror of Telegram's `/analyze`.

Phase 4: full pipeline. We fetch candles via `bots.market_data`,
compute indicators via `bots.indicators.IndicatorCalculator`, detect
patterns via `bots.pattern_detector.PatternDetector`, and finally
build the report via `bots.report_builder.ReportBuilder.build()` —
the SAME call chain the Telegram bot uses (see bots/main.py:60-68).

This means web and Telegram produce identical numbers for the same
input — no math duplication, no drift.

The endpoint is JSON only. The dashboard's htmx script renders the
report into the side panel and overlays entry/SL/TP on the chart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# bots/ lives two levels above web/routes/. Add it to sys.path so we
# can import market_data / indicators / pattern_detector / report_builder
# without touching the bot's own module layout.
_BOTS_DIR = Path(__file__).resolve().parent.parent.parent / "bots"
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..deps import require_user

router = APIRouter(prefix="/analyze", tags=["analyze"])

SUPPORTED_TIMEFRAMES = {"M1", "M3", "M5", "M15", "M30",
                        "H1", "H2", "H4", "H6", "H8", "H12",
                        "D1", "D3", "W1", "Mo1"}

_fetcher = None


def _get_fetcher():
    global _fetcher
    if _fetcher is None:
        from market_data import BinanceDataFetcher
        _fetcher = BinanceDataFetcher()
    return _fetcher


def _normalize_indicators_for_builder(ind_set) -> dict:
    """IndicatorSet → flat dict the ReportBuilder.build() expects.

    The bot's main path calls `indicators.to_dict()` then passes the
    resulting flat dict straight into build(). We replicate that
    contract here.
    """
    return {
        "atr": ind_set.atr_value,
        "rsi": float(ind_set.rsi.iloc[-1]) if len(ind_set.rsi) > 0 else 50.0,
        "adx": ind_set.adx_value,
        "trend_score": ind_set.trend_score,
        "vwap": float(ind_set.vwap.iloc[-1]) if len(ind_set.vwap) > 0 else None,
        "vwap_std": float(ind_set.vwap_std.iloc[-1]) if len(ind_set.vwap_std) > 0 else None,
        "support": ind_set.support_levels[:3],
        "resistance": ind_set.resistance_levels[:3],
        "volume_profile": ind_set.volume_profile,
        "ema": {k: v for k, v in ind_set.ema.items()},
    }


@router.post("")
@router.post("/")
async def analyze(payload: dict[str, Any], user=Depends(require_user)):
    """Run a full analysis and return the report as JSON.

    Body: { "symbol": "BTCUSDT", "timeframe": "H1" }

    Response: {
        "symbol": ..., "timeframe": ..., "price": ...,
        "report": <AnalysisReport.to_dict()>,
        "telegram_text": <same report rendered as premium Telegram text>
    }
    """
    symbol = (payload.get("symbol") or "").upper().strip()
    timeframe = (payload.get("timeframe") or "H1").upper().strip()
    if not symbol:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return JSONResponse(
            {"error": f"unsupported timeframe: {timeframe}"},
            status_code=400,
        )

    fetcher = _get_fetcher()
    try:
        df = fetcher.get_klines(symbol, timeframe, limit=200)
    except Exception as e:
        return JSONResponse(
            {"error": f"upstream market data error: {e}"},
            status_code=502,
        )
    if df is None or df.empty:
        return JSONResponse(
            {"error": "no data returned from market"},
            status_code=502,
        )

    from indicators import IndicatorCalculator
    from pattern_detector import PatternDetector
    from report_builder import ReportBuilder

    ind = IndicatorCalculator(df).calculate_all()
    patterns = PatternDetector(df).detect_all()
    indicators_dict = _normalize_indicators_for_builder(ind)

    # Risk comes from the user's account_balance + risk_percent if known.
    risk_per_trade = 0.02  # default 2%
    tier = "free"
    trading_style = "auto"
    if user is not None:
        try:
            from db import db
            u = await db.get_user(user.web_user_id)
            if u is not None:
                risk_per_trade = float(u.risk_percent) / 100.0
                tier = u.tier.value if hasattr(u.tier, "value") else str(u.tier)
                trading_style = u.trading_style or "auto"
        except Exception:
            # If the user-row read fails (e.g. db hiccup), fall back to
            # defaults — don't 500 the whole analysis.
            pass

    builder = ReportBuilder(risk_per_trade=risk_per_trade)
    report = builder.build(
        symbol=symbol,
        timeframe=timeframe,
        price=float(df["close"].iloc[-1]),
        indicators=indicators_dict,
        patterns=patterns,
        tier=tier,
        trading_style=trading_style,
    )

    # Compute the real position size from the user's settings using the
    # same PositionSizer the bot uses for Telegram. We override the
    # report's hardcoded `position_size_suggestion` (which only ever says
    # "$10k account") with the actual numbers the user configured.
    position_dict: dict | None = None
    try:
        from position_sizer import PositionSizer
        account_balance = 10000.0
        leverage = 20.0
        if user is not None:
            try:
                from db import db
                u = await db.get_user(user.web_user_id)
                if u is not None:
                    account_balance = float(u.account_balance)
                    leverage = float(u.leverage_crypto if symbol.endswith("USDT") else u.leverage)
            except Exception:
                pass
        sizer = PositionSizer(account_balance=account_balance, risk_percent=risk_per_trade * 100)
        if report.entry_zone and report.stop_loss:
            entry_mid = (report.entry_zone[0] + report.entry_zone[1]) / 2
            ps = sizer.calculate(
                symbol, entry_mid, report.stop_loss, leverage=leverage,
            )
            position_dict = {
                "instrument": ps.instrument,
                "account_balance": ps.account_balance,
                "risk_percent": ps.risk_percent,
                "risk_amount": round(ps.risk_amount, 2),
                "stop_distance": round(ps.stop_distance, 2),
                "lot_size": round(ps.lot_size, 4),
                "units": round(ps.units, 4),
                "margin_required": round(ps.margin_required, 2),
                "leverage_used": round(ps.leverage_used, 2),
                "is_valid": ps.is_valid,
                "warning": ps.warning,
                "report_text": sizer.format_report(ps),
            }
    except Exception as e:
        position_dict = {"error": f"position sizing failed: {e}"}

    report_dict = report.to_dict()
    # Override the report's hardcoded position size text with the real one.
    if position_dict and "entry" in report_dict:
        report_dict["entry"]["position_size"] = position_dict.get("report_text",
            f"Risk {risk_per_trade*100:.1f}% = ${position_dict.get('risk_amount', 0):.0f} on ${account_balance:.0f}")

    body = {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": float(df["close"].iloc[-1]),
        "report": report_dict,
        "position": position_dict,
        "telegram_text": report.to_telegram_text(tier="premium"),
    }
    return JSONResponse(
        content=json.loads(json.dumps(body, default=str)),
    )