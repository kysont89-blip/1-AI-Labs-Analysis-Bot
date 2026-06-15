"""
Fundamental Analysis Module for XOX Bot
Covers Crypto + Forex/XAU in one lightweight layer.

Data Sources:
  - Fear & Greed: alternative.me
  - Funding Rates: Binance API
  - BTC Dominance: CoinGecko
  - USD/DXY: Yahoo Finance
  - Economic Calendar: forex_factory_scraper (lightweight)
  - Gold Sentiment: DXY correlation proxy

Cache: 5 minutes to keep it fast.
"""

import requests
import time
import json
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from enum import Enum

CACHE_TTL = 300  # 5 minutes

# In-memory cache
_cache: Dict[str, tuple] = {}


class Sentiment(Enum):
    EXTREME_FEAR = "Extreme Fear"
    FEAR = "Fear"
    NEUTRAL = "Neutral"
    GREED = "Greed"
    EXTREME_GREED = "Extreme Greed"


class DirectionBias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


def _cached_fetch(key: str, fetch_fn):
    """Fetch with in-memory caching."""
    now = time.time()
    if key in _cache:
        value, cached_at = _cache[key]
        if now - cached_at < CACHE_TTL:
            return value
    try:
        value = fetch_fn()
        _cache[key] = (value, now)
        return value
    except Exception as e:
        print(f"[Fundamental] {key} fetch failed: {e}")
        # Return stale if available
        if key in _cache:
            return _cache[key][0]
        return None


def fetch_fear_greed() -> Optional[Dict]:
    """Fetch Fear & Greed Index from alternative.me."""
    url = "https://api.alternative.me/fng/?limit=1"
    r = requests.get(url, timeout=10)
    data = r.json()
    item = data.get("data", [{}])[0]
    return {
        "value": int(item.get("value", 50)),
        "classification": item.get("value_classification", "Neutral"),
        "timestamp": item.get("timestamp")
    }


def fetch_funding_rate(symbol: str = "BTCUSDT") -> Optional[Dict]:
    """Fetch funding rate from Binance."""
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
    r = requests.get(url, timeout=10)
    data = r.json()
    if data:
        item = data[0]
        return {
            "symbol": symbol,
            "rate": float(item.get("fundingRate", 0)) * 100,
            "time": item.get("fundingTime")
        }
    return None


def fetch_btc_dominance() -> Optional[float]:
    """Fetch BTC dominance from CoinGecko."""
    url = "https://api.coingecko.com/api/v3/global"
    r = requests.get(url, timeout=10)
    data = r.json()
    btc_dominance = data.get("data", {}).get("market_cap_percentage", {}).get("btc")
    return float(btc_dominance) if btc_dominance else None


def fetch_usd_dxy() -> Optional[float]:
    """Fetch DXY (USD Index) from Yahoo Finance."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=2d"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10)
    data = r.json()
    result = data.get("chart", {}).get("result", [{}])[0]
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    return float(closes[-1]) if closes and closes[-1] else None


def fetch_economic_events() -> List[Dict]:
    """
    Fetch today's high-impact economic events (lightweight).
    Uses forex_factory_scraper or falls back to manual list.
    """
    try:
        # Try a lightweight approach - if available
        url = "https://cdn.n8n.io/workflows/forex-events.json"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()[:5]
    except:
        pass
    return []


def fetch_gold_sentiment_proxy() -> Dict:
    """
    Proxy for XAU sentiment:
    - DXY up = Gold likely down (inverse correlation)
    - DXY down = Gold likely up
    """
    dxy = _cached_fetch("dxy", fetch_usd_dxy)
    if dxy is None:
        return {"available": False}

    # Simple sentiment based on DXY level
    if dxy > 105:
        sentiment = "BEARISH for Gold (strong dollar)"
        bias = DirectionBias.BEARISH
    elif dxy < 100:
        sentiment = "BULLISH for Gold (weak dollar)"
        bias = DirectionBias.BULLISH
    else:
        sentiment = "NEUTRAL for Gold"
        bias = DirectionBias.NEUTRAL

    return {
        "available": True,
        "dxy": dxy,
        "sentiment": sentiment,
        "bias": bias,
        "note": "DXY and Gold typically inversely correlated"
    }


@dataclass
class FundamentalContext:
    """Complete fundamental context for any symbol."""

    # Crypto specific
    fear_greed: Optional[Dict] = None
    fear_greed_signal: str = "N/A"
    funding_rate: Optional[Dict] = None
    funding_signal: str = "N/A"
    btc_dominance: Optional[float] = None
    btc_dominance_signal: str = "N/A"

    # Forex/XAU specific
    dxy: Optional[float] = None
    dxy_signal: str = "N/A"
    gold_sentiment: Dict = field(default_factory=dict)

    # Shared
    economic_events: List[Dict] = field(default_factory=list)
    overall_bias: str = "NEUTRAL"
    warnings: List[str] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return {
            "crypto": {
                "fear_greed": self.fear_greed,
                "fear_greed_signal": self.fear_greed_signal,
                "funding_rate": self.funding_rate,
                "funding_signal": self.funding_signal,
                "btc_dominance": self.btc_dominance,
                "btc_dominance_signal": self.btc_dominance_signal,
            },
            "forex_xau": {
                "dxy": self.dxy,
                "dxy_signal": self.dxy_signal,
                "gold_sentiment": self.gold_sentiment,
            },
            "shared": {
                "economic_events": self.economic_events[:3],
                "overall_bias": self.overall_bias,
                "warnings": self.warnings,
            },
            "timestamp": self.timestamp,
        }

    def to_telegram_text(self, symbol: str = "") -> str:
        """Format as Telegram message section."""
        lines = ["FUNDAMENTAL CONTEXT", "=" * 20]

        # Crypto section
        if self.fear_greed:
            fg = self.fear_greed
            lines.append(f"Fear & Greed: {fg['value']} ({fg['classification']})")
            lines.append(f"  -> {self.fear_greed_signal}")

        if self.funding_rate:
            fr = self.funding_rate
            lines.append(f"Funding Rate: {fr['rate']:+.4f}%")
            lines.append(f"  -> {self.funding_signal}")

        if self.btc_dominance:
            lines.append(f"BTC Dominance: {self.btc_dominance:.1f}%")
            lines.append(f"  -> {self.btc_dominance_signal}")

        # Forex/XAU section
        if self.dxy:
            lines.append(f"USD (DXY): {self.dxy:.2f}")
            lines.append(f"  -> {self.dxy_signal}")

        if self.gold_sentiment.get("available"):
            lines.append(f"Gold Proxy: {self.gold_sentiment.get('sentiment', '')}")

        # Warnings
        if self.warnings:
            lines.append("")
            lines.append("WARNINGS:")
            for w in self.warnings:
                lines.append(f"  * {w}")

        # Overall bias
        bias_map = {"BULLISH": "BULLISH", "BEARISH": "BEARISH", "NEUTRAL": "NEUTRAL"}
        bias = bias_map.get(self.overall_bias, "NEUTRAL")
        lines.append("")
        lines.append(f"Fundamental Bias: {bias}")

        return "\n".join(lines)


class FundamentalAnalyzer:
    """Lightweight fundamental analyzer."""

    def analyze(self, symbol: str) -> FundamentalContext:
        """Analyze fundamental context for symbol."""
        import datetime
        ctx = FundamentalContext(timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

        is_crypto = symbol.endswith("USDT") or symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT")
        is_gold = "XAU" in symbol or "GOLD" in symbol
        is_forex = symbol in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD")

        if is_crypto:
            self._analyze_crypto(ctx, symbol)

        if is_gold or is_forex:
            self._analyze_forex(ctx, symbol)

        if is_gold:
            self._analyze_gold(ctx)

        # Overall bias
        ctx.overall_bias = self._calculate_overall_bias(ctx)

        return ctx

    def _analyze_crypto(self, ctx: FundamentalContext, symbol: str):
        # Fear & Greed
        fg = _cached_fetch("fear_greed", fetch_fear_greed)
        if fg:
            ctx.fear_greed = fg
            val = fg["value"]
            if val <= 20:
                ctx.fear_greed_signal = "Extreme Fear -> Possible BOTTOM (watch for reversal)"
                ctx.warnings.append("Market in extreme fear — potential capitulation")
            elif val <= 40:
                ctx.fear_greed_signal = "Fear -> Be cautious on longs"
            elif val <= 60:
                ctx.fear_greed_signal = "Neutral -> No extreme sentiment"
            elif val <= 80:
                ctx.fear_greed_signal = "Greed -> Be cautious on longs"
            else:
                ctx.fear_greed_signal = "Extreme Greed -> Possible TOP (watch for reversal)"
                ctx.warnings.append("Market in extreme greed — potential euphoria top")

        # Funding Rate
        fr = _cached_fetch(f"funding_{symbol}", lambda: fetch_funding_rate(symbol))
        if fr:
            ctx.funding_rate = fr
            rate = fr["rate"]
            if rate > 0.05:
                ctx.funding_signal = "Expensive longs -> Short squeeze risk OR pullback likely"
                ctx.warnings.append(f"High funding rate ({rate:+.4f}%) — longs are crowded")
            elif rate < -0.05:
                ctx.funding_signal = "Expensive shorts -> Shorts are crowded, bounce possible"
                ctx.warnings.append(f"Negative funding ({rate:+.4f}%) — shorts paying longs")
            else:
                ctx.funding_signal = "Normal funding -> No extreme positioning"

        # BTC Dominance (for all crypto)
        dom = _cached_fetch("btc_dominance", fetch_btc_dominance)
        if dom:
            ctx.btc_dominance = dom
            if dom > 55:
                ctx.btc_dominance_signal = "BTC leading -> Alts may underperform"
            elif dom < 45:
                ctx.btc_dominance_signal = "BTC weak -> Alt season possible"
            else:
                ctx.btc_dominance_signal = "Balanced market"

    def _analyze_forex(self, ctx: FundamentalContext, symbol: str):
        # DXY
        dxy = _cached_fetch("dxy", fetch_usd_dxy)
        if dxy:
            ctx.dxy = dxy
            if dxy > 105:
                ctx.dxy_signal = "Strong dollar -> Pressure on commodities & non-USD pairs"
            elif dxy < 100:
                ctx.dxy_signal = "Weak dollar -> Support for commodities & non-USD pairs"
            else:
                ctx.dxy_signal = "Moderate dollar strength"

            # Impact on specific pairs
            if "USD" in symbol and symbol.startswith("USD"):
                ctx.warnings.append(f"DXY at {dxy:.1f} — directly impacts {symbol}")

    def _analyze_gold(self, ctx: FundamentalContext):
        gs = fetch_gold_sentiment_proxy()
        if gs.get("available"):
            ctx.gold_sentiment = gs
            if gs["bias"] == DirectionBias.BEARISH:
                ctx.warnings.append("Strong DXY -> Gold likely under pressure")
            elif gs["bias"] == DirectionBias.BULLISH:
                ctx.warnings.append("Weak DXY -> Gold likely supported")

    def _calculate_overall_bias(self, ctx: FundamentalContext) -> str:
        """Calculate overall fundamental bias."""
        scores = []

        # Fear & Greed (reverse: high = overbought = bearish)
        if ctx.fear_greed:
            val = ctx.fear_greed["value"]
            if val >= 75:
                scores.append(-1)
            elif val <= 25:
                scores.append(1)

        # Funding (high positive = crowded longs = bearish)
        if ctx.funding_rate:
            rate = ctx.funding_rate["rate"]
            if rate > 0.03:
                scores.append(-0.5)
            elif rate < -0.03:
                scores.append(0.5)

        # DXY (high = bearish for gold/commodities)
        if ctx.dxy and ctx.dxy > 0:
            if ctx.dxy > 105:
                scores.append(-1)
            elif ctx.dxy < 100:
                scores.append(1)

        # Gold sentiment
        if ctx.gold_sentiment.get("bias"):
            bias = ctx.gold_sentiment["bias"]
            if bias == DirectionBias.BULLISH:
                scores.append(1)
            elif bias == DirectionBias.BEARISH:
                scores.append(-1)

        if not scores:
            return "NEUTRAL"

        avg = sum(scores) / len(scores)
        if avg > 0.3:
            return "BULLISH"
        elif avg < -0.3:
            return "BEARISH"
        return "NEUTRAL"


# ═══════════════════════════════════════════════
# QUICK TEST
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    analyzer = FundamentalAnalyzer()

    print("=== BTCUSDT ===")
    ctx = analyzer.analyze("BTCUSDT")
    print(ctx.to_telegram_text("BTCUSDT"))

    print("\n=== XAUUSD ===")
    ctx = analyzer.analyze("XAUUSD")
    print(ctx.to_telegram_text("XAUUSD"))

    print("\n=== EURUSD ===")
    ctx = analyzer.analyze("EURUSD")
    print(ctx.to_telegram_text("EURUSD"))
