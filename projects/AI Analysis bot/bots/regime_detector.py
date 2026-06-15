"""
Market Regime Detection
Classifies market as TRENDING, RANGING, or CHOPPY.

Uses:
- ADX: >25 = trending, <20 = ranging
- Bollinger Band Width: expanding = trending, contracting = ranging
- Price structure: HH/HL vs LH/LL
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MarketRegime(Enum):
    STRONG_UPTREND = "strong_uptrend"      # ADX>30, HH/HL
    UPTREND = "uptrend"                     # ADX 20-30, HH/HL
    RANGE = "range"                         # ADX<20, oscillating
    DOWNTREND = "downtrend"                 # ADX 20-30, LH/LL
    STRONG_DOWNTREND = "strong_downtrend"   # ADX>30, LH/LL
    CHOP = "chop"                           # ADX<15, BB contracting
    BREAKOUT = "breakout"                   # BB expansion + volume spike


@dataclass
class RegimeResult:
    """Market regime classification result."""
    regime: MarketRegime
    confidence: float  # 0.0-1.0
    adx: float
    adx_trend: str
    bb_width: float
    bb_width_pct: float
    structure: str  # HH_HL, LH_LL, MIXED
    volatility_regime: str  # high, normal, low
    
    # Trade recommendations per regime
    trend_following_ok: bool
    mean_reversion_ok: bool
    breakout_ok: bool
    warning: Optional[str] = None


class RegimeDetector:
    """Detect current market regime."""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._calc_bollinger()
        self._calc_adx()
        self._detect_structure()
    
    def _calc_bollinger(self, period: int = 20, std: float = 2.0):
        """Calculate Bollinger Bands."""
        sma = self.df['close'].rolling(window=period).mean()
        std_dev = self.df['close'].rolling(window=period).std()
        self.df['bb_upper'] = sma + (std_dev * std)
        self.df['bb_lower'] = sma - (std_dev * std)
        self.df['bb_width'] = self.df['bb_upper'] - self.df['bb_lower']
        self.df['bb_width_pct'] = (self.df['bb_width'] / sma) * 100
        self.df['bb_position'] = (self.df['close'] - self.df['bb_lower']) / self.df['bb_width']
    
    def _calc_adx(self, period: int = 14):
        """Calculate ADX manually."""
        high = self.df['high']
        low = self.df['low']
        close = self.df['close']
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        # Directional Movement
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        
        plus_di = 100 * plus_dm.rolling(window=period).mean() / atr
        minus_di = 100 * minus_dm.rolling(window=period).mean() / atr
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        self.df['adx'] = dx.rolling(window=period).mean()
        self.df['plus_di'] = plus_di
        self.df['minus_di'] = minus_di
    
    def _detect_structure(self, lookback: int = 20):
        """Detect price structure (HH/HL vs LH/LL)."""
        close = self.df['close'].values
        n = len(close)
        
        highs = []
        lows = []
        
        # Find swing points
        for i in range(2, min(lookback, n-2)):
            if close[n-i] > close[n-i-1] and close[n-i] > close[n-i+1]:
                highs.append(close[n-i])
            if close[n-i] < close[n-i-1] and close[n-i] < close[n-i+1]:
                lows.append(close[n-i])
        
        # Determine structure
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[-1] > highs[-2]
            hl = lows[-1] > lows[-2]
            lh = highs[-1] < highs[-2]
            ll = lows[-1] < lows[-2]
            
            if hh and hl:
                self.structure = "HH_HL"
            elif lh and ll:
                self.structure = "LH_LL"
            else:
                self.structure = "MIXED"
        else:
            self.structure = "MIXED"
    
    def detect(self) -> RegimeResult:
        """Classify current market regime."""
        if len(self.df) < 30:
            return RegimeResult(
                regime=MarketRegime.RANGE,
                confidence=0.5,
                adx=0, adx_trend="unknown",
                bb_width=0, bb_width_pct=0,
                structure="unknown",
                volatility_regime="normal",
                trend_following_ok=False,
                mean_reversion_ok=False,
                breakout_ok=False,
                warning="Insufficient data for regime detection"
            )
        
        adx = self.df['adx'].iloc[-1]
        plus_di = self.df['plus_di'].iloc[-1]
        minus_di = self.df['minus_di'].iloc[-1]
        bb_width_pct = self.df['bb_width_pct'].iloc[-1]
        
        # ADX trend direction
        if pd.isna(adx):
            adx = 0
        if pd.isna(plus_di):
            plus_di = 0
        if pd.isna(minus_di):
            minus_di = 0
        
        adx_trend = "strong" if adx > 25 else "weak" if adx < 20 else "moderate"
        
        # Volatility regime
        bb_mean = self.df['bb_width_pct'].rolling(20).mean().iloc[-1]
        if pd.isna(bb_mean):
            bb_mean = 2.0
        
        volatility = "high" if bb_width_pct > bb_mean * 1.5 else "low" if bb_width_pct < bb_mean * 0.5 else "normal"
        
        # Classify regime
        confidence = 0.5
        
        if adx > 30:
            if self.structure == "HH_HL":
                regime = MarketRegime.STRONG_UPTREND
                confidence = min(adx / 50, 1.0)
            elif self.structure == "LH_LL":
                regime = MarketRegime.STRONG_DOWNTREND
                confidence = min(adx / 50, 1.0)
            else:
                regime = MarketRegime.BREAKOUT
                confidence = 0.6
        elif adx > 20:
            if plus_di > minus_di:
                regime = MarketRegime.UPTREND
                confidence = 0.7
            else:
                regime = MarketRegime.DOWNTREND
                confidence = 0.7
        elif adx < 15:
            regime = MarketRegime.CHOP
            confidence = 0.6
        else:
            regime = MarketRegime.RANGE
            confidence = 0.5
        
        # Determine what strategies work
        trend_following = regime in [MarketRegime.STRONG_UPTREND, MarketRegime.UPTREND,
                                     MarketRegime.STRONG_DOWNTREND, MarketRegime.DOWNTREND,
                                     MarketRegime.BREAKOUT]
        mean_reversion = regime in [MarketRegime.RANGE, MarketRegime.CHOP]
        breakout = regime == MarketRegime.BREAKOUT or bb_width_pct > bb_mean * 1.3
        
        # Warning
        warning = None
        if regime == MarketRegime.CHOP:
            warning = "Market is choppy — avoid trading or reduce size by 50%"
        elif regime == MarketRegime.RANGE:
            warning = "Range-bound market — trade reversals at support/resistance"
        elif regime in [MarketRegime.STRONG_UPTREND, MarketRegime.STRONG_DOWNTREND]:
            warning = None
        
        return RegimeResult(
            regime=regime,
            confidence=round(confidence, 2),
            adx=round(adx, 1),
            adx_trend=adx_trend,
            bb_width=round(self.df['bb_width'].iloc[-1], 2) if not pd.isna(self.df['bb_width'].iloc[-1]) else 0,
            bb_width_pct=round(bb_width_pct, 2) if not pd.isna(bb_width_pct) else 0,
            structure=self.structure,
            volatility_regime=volatility,
            trend_following_ok=trend_following,
            mean_reversion_ok=mean_reversion,
            breakout_ok=breakout,
            warning=warning
        )
    
    def format_regime(self, result: RegimeResult) -> str:
        """Format regime for report."""
        emoji_map = {
            MarketRegime.STRONG_UPTREND: "🟢🟢",
            MarketRegime.UPTREND: "🟢",
            MarketRegime.RANGE: "🟡",
            MarketRegime.DOWNTREND: "🔴",
            MarketRegime.STRONG_DOWNTREND: "🔴🔴",
            MarketRegime.CHOP: "⚪",
            MarketRegime.BREAKOUT: "💥"
        }
        
        emoji = emoji_map.get(result.regime, "⚪")
        regime_name = result.regime.value.upper().replace("_", " ")
        
        text = f"""{emoji} **MARKET REGIME: {regime_name}**
━━━━━━━━━━━━━━━━━━━━━━
Confidence: {result.confidence:.0%}

📊 **REGIME METRICS**
ADX: {result.adx:.1f} ({result.adx_trend})
BB Width: {result.bb_width_pct:.1f}% ({result.volatility_regime} volatility)
Structure: {result.structure}

🎯 **TRADE STRATEGY FOR THIS REGIME**
"""
        
        if result.trend_following_ok:
            text += "✅ Trend Following: ENABLED\n"
        else:
            text += "❌ Trend Following: AVOID\n"
        
        if result.mean_reversion_ok:
            text += "✅ Mean Reversion: ENABLED\n"
        else:
            text += "❌ Mean Reversion: AVOID\n"
        
        if result.breakout_ok:
            text += "✅ Breakout: ENABLED\n"
        else:
            text += "❌ Breakout: AVOID\n"
        
        if result.warning:
            text += f"\n⚠️ **{result.warning}**"
        
        return text


# Quick test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    from unified_market_data import UnifiedDataFetcher
    
    print("=== REGIME DETECTION TEST ===\n")
    
    fetcher = UnifiedDataFetcher()
    
    for tf in ['M5', 'H1', 'H4']:
        df = fetcher.get_klines('BTCUSDT', tf, 50)
        detector = RegimeDetector(df)
        result = detector.detect()
        print(f"BTCUSDT {tf}: {result.regime.value} (ADX={result.adx:.1f}, {result.structure})")
        print()
