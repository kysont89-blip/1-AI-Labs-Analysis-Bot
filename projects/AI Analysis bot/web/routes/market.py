"""Market data routes — feeds the TradingView chart on the dashboard.

Wraps `bots.market_data.BinanceDataFetcher.get_klines()` and returns
the candles in lightweight-charts' expected format. We do the indicator
computation here (using `bots.indicators.IndicatorCalculator`) so the
chart can show EMA overlays once we get to Phase 6 polish — for now
we just return OHLCV + VWAP.

The endpoint is JSON only — it's called from JavaScript on the
dashboard page, not by the user directly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# web/routes/market.py lives two levels under the project root, so the
# bots/ package is at ../../bots. Add it to sys.path so we can import
# market_data / indicators directly without modifying bots/.
_BOTS_DIR = Path(__file__).resolve().parent.parent.parent / "bots"
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..deps import require_user

router = APIRouter(prefix="/market", tags=["market"])

# Cached fetcher: BinanceDataFetcher keeps a requests.Session that we
# want to reuse across requests. Pure in-process cache, no thread
# safety concerns on the web side (single-worker uvicorn in dev).
_fetcher = None


def _get_fetcher():
    global _fetcher
    if _fetcher is None:
        from market_data import BinanceDataFetcher
        _fetcher = BinanceDataFetcher()
    return _fetcher


SUPPORTED_TIMEFRAMES = {"M1", "M3", "M5", "M15", "M30",
                        "H1", "H2", "H4", "H6", "H8", "H12",
                        "D1", "D3", "W1", "Mo1"}


@router.get("/candles")
async def candles(
    symbol: str = Query(..., min_length=3, max_length=20),
    timeframe: str = Query("H1"),
    limit: int = Query(200, ge=10, le=500),
    user=Depends(require_user),
):
    """Return candles in lightweight-charts format.

    Shape per candle: { "time": 1700000000, "open": ..., "high": ...,
    "low": ..., "close": ..., "volume": ... }

    `time` is unix seconds (lightweight-charts accepts that directly).
    """
    symbol = symbol.upper().strip()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe: {timeframe}")

    fetcher = _get_fetcher()
    try:
        df = fetcher.get_klines(symbol, timeframe, limit=limit)
    except Exception as e:
        raise HTTPException(502, f"Upstream market data error: {e}")

    if df is None or df.empty:
        return JSONResponse(
            {"symbol": symbol, "timeframe": timeframe, "candles": []},
        )

    # Binance kline open_time is ms. The DataFrame's DatetimeIndex has dtype
    # `datetime64[ms]` (see bots/market_data.py:106), so .view("int64")
    # returns ms-since-epoch directly. Convert to seconds for lightweight-charts.
    out = []
    ms_values = df.index.view("int64")
    for (ms, (o, h, l, c, v, _)) in zip(
        ms_values,
        df[["open", "high", "low", "close", "volume", "quote_volume"]].itertuples(index=False, name=None),
    ):
        out.append({
            "time": int(ms // 1000),  # ms → seconds
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v or 0),
        })
    return JSONResponse(
        {"symbol": symbol, "timeframe": timeframe, "candles": out},
    )


@router.get("/indicators")
async def indicators(
    symbol: str = Query(..., min_length=3, max_length=20),
    timeframe: str = Query("H1"),
    limit: int = Query(200, ge=50, le=500),
    user=Depends(require_user),
):
    """Return the current indicator snapshot.

    Lightweight endpoint for the dashboard side panel. We don't return
    the full series here — just the last-bar values plus a 20-point
    EMA-20 series for chart overlay.
    """
    symbol = symbol.upper().strip()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe: {timeframe}")

    fetcher = _get_fetcher()
    try:
        df = fetcher.get_klines(symbol, timeframe, limit=limit)
    except Exception as e:
        raise HTTPException(502, f"Upstream market data error: {e}")

    if df is None or df.empty:
        raise HTTPException(404, "No data")

    from indicators import IndicatorCalculator
    calc = IndicatorCalculator(df)
    ind = calc.calculate_all()
    d = ind.to_dict()

    # EMA series for chart overlay (truncate to last `limit` bars).
    ema20 = d.get("ema", {}).get(20, [])
    ema50 = d.get("ema", {}).get(50, [])
    ema200 = d.get("ema", {}).get(200, [])

    return JSONResponse({
        "symbol": symbol,
        "timeframe": timeframe,
        "price": float(df["close"].iloc[-1]),
        "rsi": float(d.get("rsi", 50)),
        "adx": float(d.get("adx", 0)),
        "atr": float(d.get("atr", 0)),
        "trend_score": float(d.get("trend_score", 0)),
        "vwap": d.get("vwap"),
        "support": d.get("support", []),
        "resistance": d.get("resistance", []),
        "ema20": ema20[-limit:] if ema20 else [],
        "ema50": ema50[-limit:] if ema50 else [],
        "ema200": ema200[-limit:] if ema200 else [],
    })
