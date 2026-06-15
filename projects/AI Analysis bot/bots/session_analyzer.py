"""
Session Analysis — MODERN (2024-2026 Update)

Corrected understanding: Asian session is now STRONG for XAU and crypto.
Uses volume + volatility per session, not old blanket rules.
"""

import pandas as pd
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum


class SessionType(Enum):
    ASIAN = "asian"           # 00:00-08:00 GMT
    LONDON = "london"         # 08:00-12:00 GMT
    OVERLAP = "overlap"       # 12:00-17:00 GMT (London-NY)
    NEW_YORK = "new_york"     # 17:00-21:00 GMT
    POST_NY = "post_ny"       # 21:00-00:00 GMT


@dataclass
class SessionResult:
    """Current session analysis."""
    current_session: SessionType
    session_name: str
    
    # Volume quality (vs session average)
    current_volume: float
    session_avg_volume: float
    volume_quality: str  # strong, moderate, weak
    
    # Volatility quality
    current_atr: float
    session_avg_atr: float
    volatility_quality: str
    
    # For instrument type
    instrument_type: str  # crypto, gold, fx
    
    # Overall quality score
    quality_score: float  # 0-100
    quality_label: str
    
    recommendation: str


class SessionAnalyzer:
    """Analyze current trading session quality."""
    
    # Session boundaries (GMT)
    SESSION_HOURS = {
        SessionType.ASIAN: (0, 8),
        SessionType.LONDON: (8, 12),
        SessionType.OVERLAP: (12, 17),
        SessionType.NEW_YORK: (17, 21),
        SessionType.POST_NY: (21, 24),
    }
    
    def __init__(self, df: pd.DataFrame, symbol: str):
        self.df = df.copy()
        self.symbol = symbol
        self.instrument_type = self._detect_instrument_type()
        self.session = self._get_current_session()
    
    def _detect_instrument_type(self) -> str:
        """Detect if crypto, gold, or FX."""
        if 'USD' in self.symbol and len(self.symbol) == 6:
            return 'fx'
        elif 'USDT' in self.symbol:
            return 'crypto'
        elif 'XAU' in self.symbol or 'GOLD' in self.symbol:
            return 'gold'
        return 'other'
    
    def _get_current_session(self, hour: int = None) -> SessionType:
        """Determine current trading session."""
        if hour is None:
            hour = datetime.now(timezone.utc).hour
        
        for session, (start, end) in self.SESSION_HOURS.items():
            if start <= hour < end:
                return session
        return SessionType.ASIAN  # Default
    
    def analyze(self) -> SessionResult:
        """Analyze current session quality for this instrument."""
        if len(self.df) < 10:
            return SessionResult(
                current_session=self.session,
                session_name=self.session.value.upper(),
                current_volume=0, session_avg_volume=0,
                volume_quality="unknown",
                current_atr=0, session_avg_atr=0,
                volatility_quality="unknown",
                instrument_type=self.instrument_type,
                quality_score=50, quality_label="MODERATE",
                recommendation="Insufficient data"
            )
        
        # Calculate current volume vs historical
        current_volume = self.df['volume'].iloc[-1]
        avg_volume = self.df['volume'].rolling(20).mean().iloc[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Calculate current ATR vs historical
        if 'high' in self.df.columns and 'low' in self.df.columns:
            current_range = self.df['high'].iloc[-1] - self.df['low'].iloc[-1]
            avg_range = (self.df['high'] - self.df['low']).rolling(20).mean().iloc[-1]
            atr_ratio = current_range / avg_range if avg_range > 0 else 1.0
        else:
            current_range = 0
            avg_range = 1
            atr_ratio = 1.0
        
        # Volume quality
        if volume_ratio > 1.5:
            volume_quality = "strong"
        elif volume_ratio > 0.7:
            volume_quality = "moderate"
        else:
            volume_quality = "weak"
        
        # Volatility quality
        if atr_ratio > 1.3:
            vol_quality = "high"
        elif atr_ratio > 0.7:
            vol_quality = "normal"
        else:
            vol_quality = "low"
        
        # Session-specific base scores (MODERN 2024-2026)
        # Based on actual market data, not old assumptions
        base_scores = {
            'crypto': {
                SessionType.ASIAN: 75,      # Korea/Japan active
                SessionType.LONDON: 60,
                SessionType.OVERLAP: 95,    # Global volume peak
                SessionType.NEW_YORK: 65,
                SessionType.POST_NY: 40,
            },
            'gold': {
                SessionType.ASIAN: 80,      # China/India demand
                SessionType.LONDON: 85,     # LBMA fixing
                SessionType.OVERLAP: 95,    # Best liquidity
                SessionType.NEW_YORK: 70,
                SessionType.POST_NY: 30,
            },
            'fx': {
                SessionType.ASIAN: 45,      # Only JPY active
                SessionType.LONDON: 85,     # EUR/GBP active
                SessionType.OVERLAP: 95,    # All pairs active
                SessionType.NEW_YORK: 80,   # USD pairs active
                SessionType.POST_NY: 20,
            },
            'other': {
                SessionType.ASIAN: 50,
                SessionType.LONDON: 70,
                SessionType.OVERLAP: 90,
                SessionType.NEW_YORK: 60,
                SessionType.POST_NY: 30,
            }
        }
        
        base = base_scores.get(self.instrument_type, base_scores['other']).get(self.session, 50)
        
        # Adjust based on current conditions
        volume_bonus = 10 if volume_quality == "strong" else 0
        vol_bonus = 5 if vol_quality == "high" else 0
        vol_penalty = -10 if vol_quality == "low" else 0
        
        quality_score = min(100, base + volume_bonus + vol_bonus + vol_penalty)
        quality_score = max(0, quality_score)
        
        # Label
        if quality_score >= 80:
            label = "STRONG"
        elif quality_score >= 60:
            label = "GOOD"
        elif quality_score >= 40:
            label = "MODERATE"
        else:
            label = "WEAK"
        
        # Recommendation
        if quality_score >= 80:
            rec = "✅ EXCELLENT session for trading"
        elif quality_score >= 60:
            rec = "🟢 Good session — trade with normal size"
        elif quality_score >= 40:
            rec = "🟡 Moderate session — reduce size by 25%"
        else:
            rec = "🔴 Poor session — avoid new trades"
        
        return SessionResult(
            current_session=self.session,
            session_name=self.session.value.upper().replace("_", " "),
            current_volume=current_volume,
            session_avg_volume=avg_volume,
            volume_quality=volume_quality,
            current_atr=current_range,
            session_avg_atr=avg_range,
            volatility_quality=vol_quality,
            instrument_type=self.instrument_type,
            quality_score=quality_score,
            quality_label=label,
            recommendation=rec
        )
    
    def format_session(self, result: SessionResult) -> str:
        """Format session analysis for report."""
        emoji_map = {
            "STRONG": "🟢",
            "GOOD": "🟢",
            "MODERATE": "🟡",
            "WEAK": "🔴"
        }
        emoji = emoji_map.get(result.quality_label, "⚪")
        
        return f"""{emoji} **SESSION: {result.session_name}**
━━━━━━━━━━━━━━━━━━━━━━
Quality: {result.quality_label} ({result.quality_score}/100)
Instrument: {result.instrument_type.upper()}

📊 **SESSION METRICS**
Volume: {result.volume_quality} ({result.current_volume:,.0f} vs avg)
Volatility: {result.volatility_quality}

{result.recommendation}
"""


# Quick test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    from unified_market_data import UnifiedDataFetcher
    
    print("=== SESSION ANALYSIS (MODERN) ===\n")
    
    fetcher = UnifiedDataFetcher()
    
    pairs = ['BTCUSDT', 'XAUUSD', 'EURUSD']
    for pair in pairs:
        df = fetcher.get_klines(pair, 'H1', 50)
        analyzer = SessionAnalyzer(df, pair)
        result = analyzer.analyze()
        print(f"{pair}:")
        print(analyzer.format_session(result))
        print()
