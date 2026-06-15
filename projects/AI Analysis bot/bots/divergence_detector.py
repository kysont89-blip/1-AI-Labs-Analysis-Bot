"""
Divergence Detection Engine
Identifies bullish/bearish divergences between price and RSI/MACD.
Professional-grade reversal signal.

Types:
- Regular Bullish Divergence: Price LL, RSI HL → buy signal
- Regular Bearish Divergence: Price HH, RSI LH → sell signal
- Hidden Bullish Divergence: Price HL, RSI LL → continuation buy
- Hidden Bearish Divergence: Price LH, RSI HH → continuation sell
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Dict
from enum import Enum


class DivergenceType(Enum):
    REGULAR_BULLISH = "regular_bullish"   # Price LL, RSI HL → reversal up
    REGULAR_BEARISH = "regular_bearish"   # Price HH, RSI LH → reversal down
    HIDDEN_BULLISH = "hidden_bullish"     # Price HL, RSI LL → continuation up
    HIDDEN_BEARISH = "hidden_bearish"     # Price LH, RSI HH → continuation down
    NONE = "none"


@dataclass
class DivergenceSignal:
    """Detected divergence signal."""
    type: DivergenceType
    strength: float  # 0.0-1.0
    price_swing_high: float
    price_swing_low: float
    indicator_swing_high: float
    indicator_swing_low: float
    bars_ago: int
    description: str
    recommendation: str


class DivergenceDetector:
    """Detect divergences between price and RSI/MACD."""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._find_swing_points()
        self._calc_macd()
    
    def _find_swing_points(self, lookback: int = 5):
        """Find local highs and lows in price (causal — no look-ahead).

        A swing high at bar i is a local max over the window
        close[i-lookback..i+lookback]. The right-side condition
        (close[i] > close[i+1]) peeks one bar into the future.

        Causal fix: label the swing at the CONFIRMATION bar (i + 1 for
        1-bar look-ahead), not at the apex. The price is still the price
        at the apex bar i.
        """
        self.swing_highs = []
        self.swing_lows = []

        close = self.df['close'].values

        for i in range(lookback, len(close) - lookback):
            # Check if current is highest in window
            window = close[i-lookback:i+lookback+1]
            if close[i] == max(window) and close[i] > close[i-1] and close[i] > close[i+1]:
                self.swing_highs.append((i + 1, close[i]))
            # Check if current is lowest in window
            if close[i] == min(window) and close[i] < close[i-1] and close[i] < close[i+1]:
                self.swing_lows.append((i + 1, close[i]))
    
    def _calc_macd(self):
        """Calculate MACD for divergence detection."""
        ema12 = self.df['close'].ewm(span=12).mean()
        ema26 = self.df['close'].ewm(span=26).mean()
        self.df['macd'] = ema12 - ema26
        self.df['macd_signal'] = self.df['macd'].ewm(span=9).mean()
        self.df['macd_hist'] = self.df['macd'] - self.df['macd_signal']
    
    def detect_rsi_divergence(self, rsi_series: pd.Series) -> Optional[DivergenceSignal]:
        """
        Detect RSI divergence.
        
        Regular Bullish: Price makes lower low, RSI makes higher low
        Regular Bearish: Price makes higher high, RSI makes lower high
        """
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return None
        
        # Check for bearish divergence (swing highs)
        if len(self.swing_highs) >= 2:
            for i in range(len(self.swing_highs)-1, 0, -1):
                idx1, price1 = self.swing_highs[i-1]
                idx2, price2 = self.swing_highs[i]
                
                # Price HH
                if price2 > price1:
                    rsi1 = rsi_series.iloc[idx1]
                    rsi2 = rsi_series.iloc[idx2]
                    
                    # RSI LH = bearish divergence
                    if rsi2 < rsi1:
                        strength = (rsi1 - rsi2) / 100.0
                        return DivergenceSignal(
                            type=DivergenceType.REGULAR_BEARISH,
                            strength=min(strength, 1.0),
                            price_swing_high=price2,
                            price_swing_low=price1,
                            indicator_swing_high=rsi1,
                            indicator_swing_low=rsi2,
                            bars_ago=len(self.df) - idx2,
                            description=f"Bearish divergence: Price HH ({price1:.2f}→{price2:.2f}), RSI LH ({rsi1:.1f}→{rsi2:.1f})",
                            recommendation="SELL or reduce longs — reversal likely"
                        )
        
        # Check for bullish divergence (swing lows)
        if len(self.swing_lows) >= 2:
            for i in range(len(self.swing_lows)-1, 0, -1):
                idx1, price1 = self.swing_lows[i-1]
                idx2, price2 = self.swing_lows[i]
                
                # Price LL
                if price2 < price1:
                    rsi1 = rsi_series.iloc[idx1]
                    rsi2 = rsi_series.iloc[idx2]
                    
                    # RSI HL = bullish divergence
                    if rsi2 > rsi1:
                        strength = (rsi2 - rsi1) / 100.0
                        return DivergenceSignal(
                            type=DivergenceType.REGULAR_BULLISH,
                            strength=min(strength, 1.0),
                            price_swing_high=price1,
                            price_swing_low=price2,
                            indicator_swing_high=rsi2,
                            indicator_swing_low=rsi1,
                            bars_ago=len(self.df) - idx2,
                            description=f"Bullish divergence: Price LL ({price1:.2f}→{price2:.2f}), RSI HL ({rsi1:.1f}→{rsi2:.1f})",
                            recommendation="BUY or reduce shorts — bottom forming"
                        )
        
        return None
    
    def detect_macd_divergence(self) -> Optional[DivergenceSignal]:
        """
        Detect MACD divergence.
        More reliable than RSI divergence for trend reversals.
        """
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return None
        
        macd = self.df['macd'].values
        
        # Check for bearish MACD divergence (swing highs)
        if len(self.swing_highs) >= 2:
            for i in range(len(self.swing_highs)-1, 0, -1):
                idx1, price1 = self.swing_highs[i-1]
                idx2, price2 = self.swing_highs[i]
                
                if price2 > price1:  # Price HH
                    macd1 = macd[idx1]
                    macd2 = macd[idx2]
                    
                    if macd2 < macd1:  # MACD LH
                        strength = (macd1 - macd2) / abs(macd1) if macd1 != 0 else 0
                        return DivergenceSignal(
                            type=DivergenceType.REGULAR_BEARISH,
                            strength=min(abs(strength), 1.0),
                            price_swing_high=price2,
                            price_swing_low=price1,
                            indicator_swing_high=macd1,
                            indicator_swing_low=macd2,
                            bars_ago=len(self.df) - idx2,
                            description=f"MACD bearish divergence: Price HH ({price1:.2f}→{price2:.2f}), MACD LH ({macd1:.4f}→{macd2:.4f})",
                            recommendation="STRONG SELL — MACD divergence is high-probability reversal"
                        )
        
        # Check for bullish MACD divergence (swing lows)
        if len(self.swing_lows) >= 2:
            for i in range(len(self.swing_lows)-1, 0, -1):
                idx1, price1 = self.swing_lows[i-1]
                idx2, price2 = self.swing_lows[i]
                
                if price2 < price1:  # Price LL
                    macd1 = macd[idx1]
                    macd2 = macd[idx2]
                    
                    if macd2 > macd1:  # MACD HL
                        strength = (macd2 - macd1) / abs(macd1) if macd1 != 0 else 0
                        return DivergenceSignal(
                            type=DivergenceType.REGULAR_BULLISH,
                            strength=min(abs(strength), 1.0),
                            price_swing_high=price1,
                            price_swing_low=price2,
                            indicator_swing_high=macd2,
                            indicator_swing_low=macd1,
                            bars_ago=len(self.df) - idx2,
                            description=f"MACD bullish divergence: Price LL ({price1:.2f}→{price2:.2f}), MACD HL ({macd1:.4f}→{macd2:.4f})",
                            recommendation="STRONG BUY — MACD divergence is high-probability reversal"
                        )
        
        return None
    
    def detect_all(self, rsi_series: pd.Series) -> List[DivergenceSignal]:
        """Detect all divergences."""
        signals = []
        
        # RSI divergence
        rsi_div = self.detect_rsi_divergence(rsi_series)
        if rsi_div:
            signals.append(rsi_div)
        
        # MACD divergence
        macd_div = self.detect_macd_divergence()
        if macd_div:
            signals.append(macd_div)
        
        # Sort by strength
        signals.sort(key=lambda x: x.strength, reverse=True)
        return signals
    
    def format_signal(self, signal: DivergenceSignal) -> str:
        """Format divergence for report."""
        emoji_map = {
            DivergenceType.REGULAR_BULLISH: "🟢",
            DivergenceType.REGULAR_BEARISH: "🔴",
            DivergenceType.HIDDEN_BULLISH: "🟡",
            DivergenceType.HIDDEN_BEARISH: "🟡",
            DivergenceType.NONE: "⚪"
        }
        
        emoji = emoji_map.get(signal.type, "⚪")
        
        return f"""{emoji} **DIVERGENCE ALERT** ({signal.type.value.upper()})
━━━━━━━━━━━━━━━━━━━━━━
Strength: {signal.strength:.0%}
Bars ago: {signal.bars_ago}

{signal.description}

🎯 RECOMMENDATION: {signal.recommendation}
"""
    
    def format_all(self, signals: List[DivergenceSignal]) -> str:
        """Format all divergences."""
        if not signals:
            return "✅ No divergences detected — trend intact"
        
        text = "🔄 **DIVERGENCE ANALYSIS**\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for sig in signals[:2]:  # Show top 2
            text += self.format_signal(sig) + "\n"
        
        return text


# Quick test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    from unified_market_data import UnifiedDataFetcher
    
    print("=== DIVERGENCE DETECTION TEST ===\n")
    
    fetcher = UnifiedDataFetcher()
    df = fetcher.get_klines('BTCUSDT', 'H1', 100)
    
    detector = DivergenceDetector(df)
    
    # Calculate RSI manually for test
    from indicators import IndicatorCalculator
    calc = IndicatorCalculator(df)
    indicators = calc.calculate_all()
    
    signals = detector.detect_all(indicators.rsi)
    
    if signals:
        print(detector.format_all(signals))
    else:
        print("No divergences on current BTC H1 data")
        print(f"Swing highs found: {len(detector.swing_highs)}")
        print(f"Swing lows found: {len(detector.swing_lows)}")
