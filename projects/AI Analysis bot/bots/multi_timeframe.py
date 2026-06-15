"""
Multi-Timeframe Confluence Module
Checks higher timeframe alignment before confirming a trade signal.

Rules:
  - If analyzing H1, check H4 trend
  - If analyzing M15, check H1 trend  
  - If analyzing M5, check H1 trend
  - If analyzing H4, check D1 trend
  - If higher TF disagrees, warn user
"""

import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class ConfluenceCheck:
    higher_tf: str
    higher_trend: str  # bullish / bearish / ranging
    higher_adx: float
    primary_signal: str  # LONG / SHORT / NEUTRAL
    alignment: str  # aligned / mixed / opposing
    confidence_adjustment: float  # +0.2 for aligned, -0.3 for opposing
    warning: Optional[str] = None


class MultiTimeframeAnalyzer:
    """Analyze multiple timeframes for confluence."""

    TF_HIERARCHY = {
        'M5': 'H1',
        'M15': 'H1',
        'M30': 'H1',
        'H1': 'H4',
        'H2': 'H4',
        'H4': 'D1',
        'D1': 'W1',
    }

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def get_higher_tf(self, tf: str) -> Optional[str]:
        """Get the next higher timeframe to check."""
        return self.TF_HIERARCHY.get(tf)

    def analyze_confluence(self, symbol: str, tf: str, primary_signal: str) -> Optional[ConfluenceCheck]:
        """
        Check if higher timeframe aligns with primary signal.
        Returns ConfluenceCheck or None if not applicable.
        """
        from indicators import IndicatorCalculator

        higher_tf = self.get_higher_tf(tf)
        if not higher_tf:
            return None

        try:
            # Fetch higher timeframe data. Drop the last (in-progress) bar
            # so we only use CLOSED higher-TF bars for trend determination.
            # Without this, the H4 bar containing the current H1 decision
            # would still be forming, leaking future data into the trend
            # classification.
            df_higher = self.fetcher.get_klines(symbol, higher_tf, limit=51)
            if df_higher.empty or len(df_higher) < 20:
                return None
            # Drop the last bar (still forming) if it appears to be in-progress.
            # Conservative: always drop the last bar when limit>=20 since the
            # exchange returns the current partial candle as the last row.
            df_higher = df_higher.iloc[:-1]

            # Calculate indicators on higher TF
            calc = IndicatorCalculator(df_higher)
            indicators = calc.calculate_all()

            higher_trend_score = indicators.trend_score
            higher_adx = indicators.adx_value

            # Determine higher TF trend
            if higher_trend_score > 30:
                higher_trend = "bullish"
            elif higher_trend_score < -30:
                higher_trend = "bearish"
            else:
                higher_trend = "ranging"

            # Check alignment
            primary_direction = primary_signal.upper()
            # Normalize signal text (e.g. "SELL (moderate)" → "SHORT")
            if "BUY" in primary_direction:
                primary_direction = "LONG"
            elif "SELL" in primary_direction:
                primary_direction = "SHORT"
            
            if primary_direction == "NEUTRAL":
                alignment = "neutral"
                confidence_adjustment = 0.0
                warning = None
            elif (primary_direction == "LONG" and higher_trend == "bullish") or \
                 (primary_direction == "SHORT" and higher_trend == "bearish"):
                alignment = "aligned"
                confidence_adjustment = 0.15
                warning = None
            elif higher_trend == "ranging":
                alignment = "mixed"
                confidence_adjustment = -0.05
                warning = f"Higher timeframe ({higher_tf}) is ranging — reduce position size"
            else:
                alignment = "opposing"
                confidence_adjustment = -0.25
                # Show user-friendly signal text, not "SHORT"
                user_friendly = "BUY" if primary_direction == "LONG" else "SELL" if primary_direction == "SHORT" else "NEUTRAL"
                warning = f"⚠️ HIGHER TF CONFLICT: {higher_tf} shows {higher_trend.upper()} trend but signal is {user_friendly}"

            return ConfluenceCheck(
                higher_tf=higher_tf,
                higher_trend=higher_trend,
                higher_adx=higher_adx,
                primary_signal=primary_direction,
                alignment=alignment,
                confidence_adjustment=confidence_adjustment,
                warning=warning
            )

        except Exception as e:
            print(f"[Confluence] Error checking {higher_tf}: {e}")
            return None

    def format_confluence_text(self, check: ConfluenceCheck) -> str:
        """Format confluence check for Telegram report."""
        if not check:
            return ""

        alignment_emoji = {
            "aligned": "✅",
            "mixed": "⚪",
            "opposing": "⚠️",
            "neutral": "⚪"
        }
        emoji = alignment_emoji.get(check.alignment, "⚪")

        lines = [
            f"",
            f"📊 MULTI-TIMEFRAME CHECK ({check.higher_tf})",
            f"{'━' * 22}",
            f"Higher TF ({check.higher_tf}): {check.higher_trend.upper()} (ADX {check.higher_adx:.1f})",
            f"Signal ({check.primary_signal}): {emoji} {check.alignment.upper()}",
        ]

        if check.warning:
            lines.append(f"")
            lines.append(f"{check.warning}")

        lines.append(f"{'━' * 22}")

        return "\n".join(lines)


if __name__ == '__main__':
    from unified_market_data import UnifiedDataFetcher

    fetcher = UnifiedDataFetcher()
    analyzer = MultiTimeframeAnalyzer(fetcher)

    result = analyzer.analyze_confluence('BTCUSDT', 'H1', 'LONG')
    if result:
        print(analyzer.format_confluence_text(result))
    else:
        print("No confluence check available")
