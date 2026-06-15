"""
Report Builder for XOX Analysis Bot
Combines all signals into structured analysis reports.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


class SignalStrength(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


# ═══════════════════════════════════════════════
# TRADING PLAN TEMPLATES
# ═══════════════════════════════════════════════

@dataclass
class PlanTemplate:
    """Trading-plan template.

    Defines the entry style, SL/TP multipliers, R:R target, time-in-trade
    exit, partial-TP schedule, and signal-quality floors for a given
    trading style (scalp / day / swing / position).
    """
    name: str               # "Scalp", "Day", "Swing", "Position"
    entry_style: str        # "limit_pullback" | "market" | "breakout"
    sl_atr_mult: float      # Stop distance in ATR units (entry→SL)
    tp_atr_mult: float      # TP distance in ATR units (entry→TP)
    rr_target: float        # For display ("1:1.5")
    time_in_trade_minutes: int  # Exit-by-time if neither SL nor TP hit
    partial_tp_r: tuple     # R-multiples at which to take partial profits
    min_confluence: int     # Confluence score floor for this plan
    min_trend_score: int    # |trend_score| floor
    min_adx: float          # ADX floor (avoid trading chop with this plan)
    allowed_timeframes: tuple  # ('M5', 'M15') — used for auto-selection


PLANS: Dict[str, PlanTemplate] = {
    'scalp': PlanTemplate(
        name='Scalp',
        entry_style='limit_pullback',
        sl_atr_mult=1.0,
        tp_atr_mult=1.5,
        rr_target=1.5,
        time_in_trade_minutes=60,
        partial_tp_r=(),
        min_confluence=40,
        min_trend_score=30,
        min_adx=15,
        allowed_timeframes=('M5', 'M15'),
    ),
    'day': PlanTemplate(
        name='Day',
        entry_style='limit_pullback',
        sl_atr_mult=1.5,
        tp_atr_mult=2.5,
        rr_target=2.5,
        time_in_trade_minutes=360,
        partial_tp_r=(1.5,),
        min_confluence=45,
        min_trend_score=40,
        min_adx=18,
        allowed_timeframes=('M15', 'H1'),
    ),
    'swing': PlanTemplate(
        name='Swing',
        entry_style='limit_pullback',
        sl_atr_mult=2.5,
        tp_atr_mult=5.0,
        rr_target=2.0,
        time_in_trade_minutes=4320,
        partial_tp_r=(2.0, 4.0),
        min_confluence=50,
        min_trend_score=50,
        min_adx=20,
        allowed_timeframes=('H1', 'H4'),
    ),
    'swing_h4': PlanTemplate(
        name='Swing H4',
        entry_style='limit_pullback',
        sl_atr_mult=2.0,
        tp_atr_mult=2.5,
        rr_target=1.25,
        time_in_trade_minutes=4320,
        partial_tp_r=(1.0, 2.0),
        min_confluence=50,
        min_trend_score=50,
        min_adx=20,
        # H4-only — tighter TP is needed because 5*ATR = ~7.5% is unreachable
        # in 3 days on H4 (only 30% of 18-bar windows contain a 5*ATR move).
        # See backtest_v2 H4 deep-dive (2026-06-11).
        allowed_timeframes=('H4',),
    ),
    'position': PlanTemplate(
        name='Position',
        entry_style='limit_pullback',
        sl_atr_mult=4.0,
        tp_atr_mult=8.0,
        rr_target=2.0,
        time_in_trade_minutes=20160,
        partial_tp_r=(2.0, 4.0, 7.0),
        min_confluence=55,
        min_trend_score=60,
        min_adx=25,
        allowed_timeframes=('H4', 'D1'),
    ),
}

# Timeframe → default plan (used when trading_style is 'auto')
# H1 keeps the original 'swing' plan (5*ATR TP, 2:1 R:R) — 90d backtest showed
# 40.3% WR with +531R net. H4 uses 'swing_h4' with tighter TP (2.5*ATR) because
# the larger bar size at H4 makes 5*ATR moves unreachable in 3 days. See
# backtest_v2 H4 deep-dive (2026-06-11).
TF_TO_PLAN: Dict[str, str] = {
    'M5':  'scalp',
    'M15': 'day',
    'H1':  'swing',
    'H4':  'swing_h4',
    'D1':  'position',
}


def resolve_plan(trading_style: Optional[str], timeframe: str) -> PlanTemplate:
    """Resolve which plan to use.

    trading_style: 'auto' (or None) → pick from timeframe.
                   'scalp' / 'day' / 'swing' / 'swing_h4' / 'position' → use that plan.
    """
    if trading_style and trading_style in PLANS:
        return PLANS[trading_style]
    plan_key = TF_TO_PLAN.get(timeframe, 'swing')
    return PLANS[plan_key]


@dataclass
class AnalysisReport:
    """Complete analysis report."""
    symbol: str
    timeframe: str
    timestamp: str

    # Trend
    trend: str  # bullish / bearish / ranging
    trend_confidence: float
    trend_score: float  # -100 to +100

    # Entry/Exit
    entry_zone: tuple  # (min, max)
    stop_loss: float
    take_profit: float
    risk_reward: float
    position_size_suggestion: str

    # Patterns
    patterns_detected: List[Dict]
    pattern_bias: str

    # Key Levels
    support: List[float]
    resistance: List[float]
    vwap: float
    poc: float
    vah: float
    val: float

    # Indicators
    ema_alignment: str
    rsi: float
    rsi_signal: str
    adx: float
    adx_signal: str
    atr: float

    # Signals
    overall_signal: SignalStrength
    signal_confidence: float
    confluence_score: int  # 0-100

    # Risk
    risk_warning: Optional[str] = None
    news_impact: Optional[str] = None

    # Vision
    vision_analysis: Optional[Dict] = None

    # Fundamental
    fundamental_context: Optional[Dict] = None

    # Order Flow
    order_flow: Optional[Dict] = None

    # Metadata
    version: str = "2.0"
    data_source: str = "Binance"

    # Plan metadata (filled by build() from the resolved PlanTemplate)
    plan_style: str = 'swing'
    entry_style: str = 'limit_pullback'
    time_in_trade_minutes: int = 4320
    partial_tp_prices: tuple = ()  # absolute prices at which to take partials

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'timestamp': self.timestamp,
            'trend': {
                'direction': self.trend,
                'confidence': self.trend_confidence,
                'score': self.trend_score
            },
            'entry': {
                'zone': self.entry_zone,
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'risk_reward': self.risk_reward,
                'position_size': self.position_size_suggestion
            },
            'patterns': {
                'detected': self.patterns_detected,
                'bias': self.pattern_bias,
                'count': len(self.patterns_detected)
            },
            'key_levels': {
                'support': self.support,
                'resistance': self.resistance,
                'vwap': self.vwap,
                'poc': self.poc,
                'vah': self.vah,
                'val': self.val
            },
            'indicators': {
                'ema_alignment': self.ema_alignment,
                'rsi': self.rsi,
                'rsi_signal': self.rsi_signal,
                'adx': self.adx,
                'adx_signal': self.adx_signal,
                'atr': self.atr
            },
            'signal': {
                'strength': self.overall_signal.value,
                'confidence': self.signal_confidence,
                'confluence': self.confluence_score
            },
            'risk': {
                'warning': self.risk_warning,
                'news_impact': self.news_impact
            },
            'vision': self.vision_analysis,
            'fundamental': self.fundamental_context,
            'meta': {
                'version': self.version,
                'data_source': self.data_source
            }
        }

    def to_telegram_text(self, tier: str = "free", lang: str = "en") -> str:
        """Format report for Telegram message."""
        if tier == "free":
            return self._free_report_text(lang)
        elif tier == "internal" or tier == "admin":
            return self._internal_report_text(lang)
        return self._premium_report_text(lang)

    def _direction_text(self) -> str:
        """Get clear STRONG BUY / STRONG SELL / NO TRADE text."""
        signal_map = {
            "STRONG_BUY": "STRONG BUY",
            "BUY": "BUY (moderate)",
            "NEUTRAL": "NO TRADE",
            "SELL": "SELL (moderate)",
            "STRONG_SELL": "STRONG SELL"
        }
        return signal_map.get(self.overall_signal.value, "NO TRADE")

    def _direction_emoji(self) -> str:
        """Get emoji for direction."""
        if self.overall_signal.value == "STRONG_BUY":
            return "🟢 STRONG BUY"
        elif self.overall_signal.value == "BUY":
            return "🟢 BUY (moderate)"
        elif self.overall_signal.value == "STRONG_SELL":
            return "🔴 STRONG SELL"
        elif self.overall_signal.value == "SELL":
            return "🔴 SELL (moderate)"
        return "⚪ NO TRADE"

    def _free_report_text(self, lang: str) -> str:
        """Short free tier report — sanitized, minimal details."""

        # Generic trend description
        trend_desc = self._generic_trend_description()
        momentum_desc = self._generic_momentum_description()
        vol_desc = self._generic_volatility_description()

        text = f"""📊 {self.symbol} | {self.timeframe}

🎯 ANALYSIS BASED ON: {self.timeframe} CHART
━━━━━━━━━━━━━━━━━━━━━━

{self._direction_emoji()}
━━━━━━━━━━━━━━━━━━━━━━

🎯 **SIGNAL ({self.timeframe})**: {self._direction_text()}
Confidence: {self.signal_confidence:.0%}

📈 **TRADE LEVELS**
Entry Zone: {self.entry_zone[0]:,.2f} - {self.entry_zone[1]:,.2f}
Stop Loss: {self.stop_loss:,.2f}
Take Profit: {self.take_profit:,.2f}
⚖️ Risk:Reward ≈ 1:{self.risk_reward:.1f}

📋 **MARKET CONTEXT**
• Trend Direction: {trend_desc}
• Momentum: {momentum_desc}
• Volatility: {vol_desc}

{'='*20}
{self._trade_recommendation_block()}
{'='*20}

---
💎 Full report with deeper analysis?
Upgrade to PREMIUM via /upgrade
"""
        return text

    def _premium_report_text(self, lang: str) -> str:
        """Premium report — more detail but STILL OBSCURED to protect IP.
        No exact indicator values, no thresholds, no pattern names revealed."""
        emojis = {
            "STRONG_BUY": "🟢", "BUY": "🟢",
            "NEUTRAL": "⚪",
            "SELL": "🔴", "STRONG_SELL": "🔴"
        }
        emoji = emojis.get(self.overall_signal.value, "⚪")

        # Generic descriptions instead of exact values
        trend_text = self._generic_trend_description()
        momentum_text = self._generic_momentum_description()
        structure_text = self._generic_structure_description()
        vol_text = self._generic_volatility_description()
        ema_generic = self._generic_ema_description()

        # Pattern summary — count only, no names
        bullish_count = sum(1 for p in self.patterns_detected if p.get('direction') == 'bullish')
        bearish_count = sum(1 for p in self.patterns_detected if p.get('direction') == 'bearish')
        total_patterns = len(self.patterns_detected)

        pattern_summary = ""
        if total_patterns > 0:
            if bullish_count > bearish_count:
                pattern_summary = f"{total_patterns} patterns detected, mostly bullish"
            elif bearish_count > bullish_count:
                pattern_summary = f"{total_patterns} patterns detected, mostly bearish"
            else:
                pattern_summary = f"{total_patterns} patterns detected, mixed bias"
        else:
            pattern_summary = "No significant patterns"

        text = f"""📊 {self.symbol} | {self.timeframe} | {self.timestamp}

{emoji} **{self._direction_text()}**
Confidence: {self.signal_confidence:.0%}
━━━━━━━━━━━━━━━━━━━━━━

{'⚠️ NO TRADE — Market conditions unclear.\nWait for stronger setup.' if self.trend == 'ranging' else f"📈 **TRADE SETUP**\n━━━━━━━━━━━━━━━━━━━━━━\nEntry Zone: {self.entry_zone[0]:,.2f} - {self.entry_zone[1]:,.2f}\nStop Loss:  {self.stop_loss:,.2f}\nTake Profit: {self.take_profit:,.2f}\nRisk:Reward ≈ 1:{self.risk_reward:.1f}"}

📋 **ANALYSIS SUMMARY**
━━━━━━━━━━━━━━━━━━━━━━
• Trend Direction: {trend_text}
• Momentum Condition: {momentum_text}
• Market Structure: {structure_text}
• Volatility: {vol_text}
• Trend Alignment: {ema_generic}

📐 **KEY LEVELS**
━━━━━━━━━━━━━━━━━━━━━━
• Primary Resistance Zone: {', '.join(f'${r:,.0f}' for r in self.resistance[:2]) if self.resistance else 'N/A'}
• Primary Support Zone: {', '.join(f'${s:,.0f}' for s in self.support[:2]) if self.support else 'N/A'}
• Key Reference Level: ${self.vwap:,.2f}

🎯 **PATTERN OVERVIEW**
━━━━━━━━━━━━━━━━━━━━━━
• {pattern_summary}

📊 **TECHNICAL BRIEFING**
━━━━━━━━━━━━━━━━━━━━━━
• Momentum reading suggests {self._momentum_direction()}
• Trend strength is {self._generic_trend_strength_description()}
• Volatility environment is {self._generic_volatility_short()}
"""

        # Risk warning only if present — but sanitize it
        if self.risk_warning:
            text += f"""
⚠️ **RISK CONSIDERATIONS**
━━━━━━━━━━━━━━━━━━━━━━
{self._sanitize_risk_warning(self.risk_warning)}
"""

        # Order Flow — heavily obscured
        if self.order_flow:
            text += self._orderflow_section_obscured()

        # Vision — generic only
        if self.vision_analysis:
            text += f"""
👁️ **CHART PERSPECTIVE**
━━━━━━━━━━━━━━━━━━━━━━
Overall Bias: {self.vision_analysis.get('bias', 'N/A')}
Key Observation: {self.vision_analysis.get('trend', 'N/A')}
"""

        # Fundamental — keep but generic
        if self.fundamental_context:
            text += self._fundamental_section_obscured()

        return text

    def _fundamental_section(self) -> str:
        """Render fundamental context section."""
        if not self.fundamental_context:
            return ""
        fc = self.fundamental_context
        lines = ["\n📊 FUNDAMENTAL CONTEXT", "━" * 22]

        crypto = fc.get('crypto', {})
        if crypto.get('fear_greed'):
            fg = crypto['fear_greed']
            lines.append(f"Fear & Greed: {fg['value']} ({fg['classification']})")
            lines.append(f"  -> {crypto.get('fear_greed_signal', '')}")
        if crypto.get('funding_rate'):
            fr = crypto['funding_rate']
            lines.append(f"Funding Rate: {fr['rate']:+.4f}%")
            lines.append(f"  -> {crypto.get('funding_signal', '')}")
        if crypto.get('btc_dominance'):
            lines.append(f"BTC Dominance: {crypto['btc_dominance']:.1f}%")
            lines.append(f"  -> {crypto.get('btc_dominance_signal', '')}")

        fx = fc.get('forex_xau', {})
        if fx.get('dxy'):
            lines.append(f"USD (DXY): {fx['dxy']:.2f}")
            lines.append(f"  -> {fx.get('dxy_signal', '')}")
        gs = fx.get('gold_sentiment', {})
        if gs.get('available'):
            lines.append(f"Gold Proxy: {gs.get('sentiment', '')}")

        shared = fc.get('shared', {})
        warnings = shared.get('warnings', [])
        if warnings:
            lines.append("")
            lines.append("⚠️ FUNDAMENTAL WARNINGS:")
            for w in warnings:
                lines.append(f"  • {w}")

        bias = shared.get('overall_bias', 'NEUTRAL')
        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
        emoji = bias_emoji.get(bias, "⚪")
        lines.append("")
        lines.append(f"{emoji} Fundamental Bias: {bias}")
        lines.append("━" * 22)

        return "\n".join(lines)

    def _orderflow_section(self) -> str:
        """Render order flow section — INTERNAL USE ONLY."""
        if not self.order_flow:
            return ""

        of = self.order_flow
        lines = ["\n📊 ORDER FLOW [INTERNAL]", "━" * 22]

        mid = of.get('mid_price', 0)
        spread_pct = of.get('spread_pct', 0)
        lines.append(f"Mid: {mid:,.2f} | Spread: {spread_pct:.3f}%")

        imbalance = of.get('imbalance', {})
        direction = imbalance.get('direction', 'neutral')
        strength = imbalance.get('strength', 0)

        if direction == 'bid_heavy':
            imb_text = f"Bid Heavy ({strength:.0f}%)"
        elif direction == 'ask_heavy':
            imb_text = f"Ask Heavy ({strength:.0f}%)"
        else:
            imb_text = f"Balanced"

        lines.append(f"Imbalance: {imb_text}")

        total_bid = imbalance.get('total_bid', 0)
        total_ask = imbalance.get('total_ask', 0)
        lines.append(f"Bids: {total_bid:,.4f} | Asks: {total_ask:,.4f}")

        walls = of.get('liquidity_walls', {})
        bid_walls = walls.get('bid_walls', [])
        ask_walls = walls.get('ask_walls', [])

        if bid_walls or ask_walls:
            lines.append("")
            lines.append("Liquidity Walls:")
            if bid_walls:
                for w in bid_walls[:3]:
                    lines.append(f"  {w['price']:,.2f} | {w['volume']:,.4f} ({w['ratio']:.1f}x)")
            if ask_walls:
                for w in ask_walls[:3]:
                    lines.append(f"  {w['price']:,.2f} | {w['volume']:,.4f} ({w['ratio']:.1f}x)")

        lines.append("━" * 22)
        return "\n".join(lines)

    def _orderflow_section_obscured(self) -> str:
        """Obscured order flow for user-facing reports."""
        if not self.order_flow:
            return ""

        of = self.order_flow
        imbalance = of.get('imbalance', {})
        direction = imbalance.get('direction', 'neutral')
        strength = imbalance.get('strength', 0)

        if direction == 'bid_heavy' and strength >= 30:
            flow_text = "Buying interest detected in order book"
        elif direction == 'ask_heavy' and strength >= 30:
            flow_text = "Selling interest detected in order book"
        else:
            flow_text = "Order flow relatively balanced"

        return f"""
📊 **ORDER FLOW READ**
━━━━━━━━━━━━━━━━━━━━━━
• {flow_text}
"""

    # ── GENERIC DESCRIPTION HELPERS (obscure exact values) ──

    def _generic_trend_description(self) -> str:
        """Return generic trend text instead of exact trend score."""
        if self.trend_score > 50:
            return "Strongly bullish — price advancing with momentum"
        elif self.trend_score > 30:
            return "Moderately bullish — upward bias present"
        elif self.trend_score < -50:
            return "Strongly bearish — price declining with momentum"
        elif self.trend_score < -30:
            return "Moderately bearish — downward bias present"
        elif abs(self.trend_score) < 15:
            return "Sideways/ranging — no clear directional bias"
        else:
            return "Mixed — conflicting signals across timeframes"

    def _generic_momentum_description(self) -> str:
        """Return generic momentum text instead of exact RSI."""
        if self.rsi > 75:
            return "Elevated — potential for pullback"
        elif self.rsi > 60:
            return "Bullish momentum building"
        elif self.rsi < 25:
            return "Compressed — potential for bounce"
        elif self.rsi < 40:
            return "Bearish momentum building"
        else:
            return "Neutral — neither overbought nor oversold"

    def _generic_volatility_description(self) -> str:
        """Return generic volatility text instead of exact ATR."""
        if self.atr > 500:
            return "Elevated — wider stops advised"
        elif self.atr > 200:
            return "Normal range"
        else:
            return "Contracted — breakout potential"

    def _generic_structure_description(self) -> str:
        """Return generic market structure text."""
        if self.trend == 'bullish':
            return "Higher highs and higher lows forming"
        elif self.trend == 'bearish':
            return "Lower highs and lower lows forming"
        else:
            return "Range-bound between defined levels"

    def _generic_ema_description(self) -> str:
        """Return generic moving average text instead of exact EMA alignment."""
        if "Bullish" in self.ema_alignment:
            return "Short and medium-term averages aligned upward"
        elif "Bearish" in self.ema_alignment:
            return "Short and medium-term averages aligned downward"
        elif "Short-term bullish" in self.ema_alignment:
            return "Near-term recovery, awaiting broader confirmation"
        else:
            return "Mixed alignment — transition phase"

    def _generic_trend_strength_description(self) -> str:
        """Single-word ADX-based trend strength label for inline use."""
        if self.adx > 25:
            return "elevated"
        elif self.adx > 15:
            return "moderate"
        else:
            return "low"

    def _generic_volatility_short(self) -> str:
        """Single-word volatility label for inline use."""
        if self.atr > 500:
            return "high"
        elif self.atr > 200:
            return "normal"
        else:
            return "contracted"

    def _momentum_direction(self) -> str:
        """Generic momentum direction text."""
        if self.trend == 'bullish':
            return "buying pressure"
        elif self.trend == 'bearish':
            return "selling pressure"
        else:
            return "tug-of-war between buyers and sellers"

    def _sanitize_risk_warning(self, raw_warning: str) -> str:
        """Sanitize risk warnings to remove exact thresholds/formulas."""
        # Remove exact indicator references
        sanitized = raw_warning.replace("⚠️ Contradictions detected: ", "")
        # Split and clean each item
        items = sanitized.split(";")
        clean_items = []
        for item in items:
            item = item.strip()
            # Skip exact percentage mentions
            if "%" in item and any(char.isdigit() for char in item):
                # Generic version
                if "buying pressure" in item.lower():
                    clean_items.append("Order flow shows buying interest")
                elif "selling pressure" in item.lower():
                    clean_items.append("Order flow shows selling interest")
                elif "divergence" in item.lower():
                    clean_items.append("Momentum divergence observed")
                elif "fundamental" in item.lower():
                    clean_items.append("Fundamental outlook differs from technical view")
            else:
                clean_items.append(item)
        return "\n• ".join(clean_items) if clean_items else raw_warning

    def _trade_recommendation_block(self) -> str:
        """Generic trade recommendation without revealing internals."""
        dt = self._direction_text()
        if dt == "STRONG BUY":
            return f"🟢 STRONG BUY: Consider LONG at entry zone with SL {self.stop_loss:,.2f}"
        elif dt == "BUY":
            return f"🟢 BUY: Consider LONG at entry zone with SL {self.stop_loss:,.2f}"
        elif dt == "STRONG SELL":
            return f"🔴 STRONG SELL: Consider SHORT at entry zone with SL {self.stop_loss:,.2f}"
        elif dt == "SELL":
            return f"🔴 SELL: Consider SHORT at entry zone with SL {self.stop_loss:,.2f}"
        else:
            return "⚪ WAIT: No clear setup — patience pays"

    def _fundamental_section_obscured(self) -> str:
        """Obscured fundamental section for user reports."""
        if not self.fundamental_context:
            return ""
        fc = self.fundamental_context
        lines = ["\n📊 MARKET BACKDROP", "━" * 22]

        crypto = fc.get('crypto', {})
        if crypto.get('fear_greed'):
            fg = crypto['fear_greed']
            val = fg.get('value', 50)
            cls = fg.get('classification', 'Neutral')
            # Generic description instead of exact number
            if val < 25:
                mood = "Extreme fear prevailing"
            elif val < 40:
                mood = "Fear dominating sentiment"
            elif val > 75:
                mood = "Extreme greed prevailing"
            elif val > 60:
                mood = "Greed dominating sentiment"
            else:
                mood = "Sentiment is balanced"
            lines.append(f"Market Mood: {mood}")

        fx = fc.get('forex_xau', {})
        if fx.get('dxy'):
            dxy = fx['dxy']
            if dxy > 105:
                usd_note = "USD exceptionally strong"
            elif dxy > 102:
                usd_note = "USD moderately strong"
            elif dxy < 98:
                usd_note = "USD relatively weak"
            else:
                usd_note = "USD in neutral territory"
            lines.append(f"USD Outlook: {usd_note}")

        bias = fc.get('shared', {}).get('overall_bias', 'NEUTRAL')
        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
        emoji = bias_emoji.get(bias, "⚪")
        lines.append(f"")
        lines.append(f"{emoji} Market Backdrop Bias: {bias}")
        lines.append("━" * 22)

        return "\n".join(lines)

    def _internal_report_text(self, lang: str) -> str:
        """INTERNAL admin report with ALL raw data (never send to users)."""
        lines = [
            "═" * 40,
            "INTERNAL ANALYSIS REPORT — CONFIDENTIAL",
            "═" * 40,
            f"Symbol: {self.symbol} | TF: {self.timeframe} | Time: {self.timestamp}",
            "",
            "── RAW INDICATOR VALUES ──",
            f"  Trend Score: {self.trend_score:.2f}",
            f"  Trend Confidence: {self.trend_confidence:.2%}",
            f"  RSI(14): {self.rsi:.2f} → {self.rsi_signal}",
            f"  ADX(14): {self.adx:.2f} → {self.adx_signal}",
            f"  ATR(14): {self.atr:.4f}",
            f"  EMA Alignment: {self.ema_alignment}",
            f"  VWAP: {self.vwap:.2f}",
            f"  POC: {self.poc:.2f} | VAH: {self.vah:.2f} | VAL: {self.val:.2f}",
            "",
            "── KEY LEVELS ──",
            f"  Resistance: {self.resistance}",
            f"  Support: {self.support}",
            "",
            "── PATTERNS (RAW) ──",
        ]
        for p in self.patterns_detected:
            lines.append(f"  {p.get('name')} | {p.get('direction')} | conf={p.get('confidence', 0):.2%}")

        lines.extend([
            "",
            "── SIGNAL OUTPUT ──",
            f"  Overall: {self.overall_signal.value}",
            f"  Confidence: {self.signal_confidence:.2%}",
            f"  Confluence: {self.confluence_score}/100",
            "",
            "── TRADE PARAMS ──",
            f"  Entry: {self.entry_zone[0]:.2f} - {self.entry_zone[1]:.2f}",
            f"  SL: {self.stop_loss:.2f}",
            f"  TP: {self.take_profit:.2f}",
            f"  R:R: 1:{self.risk_reward:.2f}",
            f"  Position: {self.position_size_suggestion}",
            "",
            "── RISK ──",
            f"  Warning: {self.risk_warning or 'None'}",
            "═" * 40,
        ])

        # Append raw order flow and fundamental
        if self.order_flow:
            lines.append(self._orderflow_section())
        if self.fundamental_context:
            lines.append(self._fundamental_section_raw())

        return "\n".join(lines)

    def _fundamental_section_raw(self) -> str:
        """Raw fundamental section for internal use."""
        if not self.fundamental_context:
            return ""
        fc = self.fundamental_context
        lines = ["\n── FUNDAMENTAL (RAW) ──"]

        crypto = fc.get('crypto', {})
        if crypto.get('fear_greed'):
            fg = crypto['fear_greed']
            lines.append(f"  F&G: {fg['value']} ({fg['classification']})")
        if crypto.get('funding_rate'):
            fr = crypto['funding_rate']
            lines.append(f"  Funding: {fr['rate']:+.4f}%")
        if crypto.get('btc_dominance'):
            lines.append(f"  BTC Dom: {crypto['btc_dominance']:.1f}%")

        fx = fc.get('forex_xau', {})
        if fx.get('dxy'):
            lines.append(f"  DXY: {fx['dxy']:.2f}")

        shared = fc.get('shared', {})
        lines.append(f"  Overall Bias: {shared.get('overall_bias', 'N/A')}")
        return "\n".join(lines)


class ReportBuilder:
    """Build analysis reports from all signals."""

    def __init__(self, risk_per_trade: float = 0.02):
        self.risk_per_trade = risk_per_trade

    def _adjust_trend_with_vwap(self, trend_score: float, price: float, vwap: float) -> float:
        """Adjust trend score based on VWAP position."""
        if price > vwap:
            # Price above VWAP = bullish
            trend_score += 10
        elif price < vwap:
            # Price below VWAP = bearish
            trend_score -= 10
        return max(-100, min(100, trend_score))

    def _check_contradictions(self, trend: str, order_flow: Optional[Dict],
                              fundamental: Optional[Dict],
                              divergence: Optional[List[Dict]]) -> List[str]:
        """Check for contradictions between signal and supporting data."""
        contradictions = []
        
        # Order Flow check
        if order_flow:
            of_imb = order_flow.get('imbalance', {}).get('direction', '')
            of_strength = order_flow.get('imbalance', {}).get('strength', 0)
            
            # Only count if significant (>=30%)
            if trend == "bearish" and of_imb == 'bid_heavy' and of_strength >= 30:
                contradictions.append(f"Order Flow shows buying pressure ({of_strength:.0f}%)")
            elif trend == "bullish" and of_imb == 'ask_heavy' and of_strength >= 30:
                contradictions.append(f"Order Flow shows selling pressure ({of_strength:.0f}%)")
        
        # Divergence check
        if divergence:
            for div in divergence:
                # Handle both dict and DivergenceSignal object
                if hasattr(div, 'get'):
                    # It's a dict
                    direction = div.get('direction', '')
                    strength = div.get('strength', 0)
                else:
                    # It's a DivergenceSignal dataclass
                    div_type = getattr(div, 'type', None)
                    if div_type:
                        direction = 'bullish' if 'bullish' in str(div_type.value) else 'bearish'
                    else:
                        direction = ''
                    strength = getattr(div, 'strength', 0)
                
                if direction == 'bullish' and trend == 'bearish':
                    contradictions.append(f"Bullish divergence detected ({strength:.0%} strength)")
                elif direction == 'bearish' and trend == 'bullish':
                    contradictions.append(f"Bearish divergence detected ({strength:.0%} strength)")
        
        # Fundamental check
        if fundamental:
            shared = fundamental.get('shared', {})
            bias = shared.get('overall_bias', 'NEUTRAL')
            
            if trend == "bearish" and bias == "BULLISH":
                contradictions.append("Fundamental analysis is BULLISH")
            elif trend == "bullish" and bias == "BEARISH":
                contradictions.append("Fundamental analysis is BEARISH")
        
        return contradictions

    def _calc_confluence_v2(self, trend_score: float, patterns: List[Dict],
                            rsi: float, adx: float, ema: str,
                            order_flow: Optional[Dict] = None,
                            fundamental: Optional[Dict] = None,
                            divergence: Optional[List[Dict]] = None) -> tuple:
        """Calculate confluence score 0-100 with contradictions.

        Trend contribution is linear with |trend_score| (0-30 points). Earlier
        7-day backtest results suggested an inversion (80+ bucket = 4.5% WR vs
        50-60 = 93% WR) but a wider 90-day backtest with 1389 trades showed
        all buckets within 36-44% WR — the original inversion was small-sample
        noise, not a real bug. See backtest_results_v2.md for the full history.
        """
        score = 0

        # Trend alignment (0-30) — linear with |trend_score|
        score += min(abs(trend_score) / 100 * 30, 30)

        # Patterns (0-25) — weighted by direction
        bullish_patterns = sum(1 for p in patterns if p.get('direction') == 'bullish')
        bearish_patterns = sum(1 for p in patterns if p.get('direction') == 'bearish')
        
        if trend_score > 0:
            # Bullish trend — bullish patterns add more
            score += min(bullish_patterns * 6, 18)
            score -= min(bearish_patterns * 3, 9)  # Bearish patterns reduce score
        elif trend_score < 0:
            # Bearish trend — bearish patterns add more
            score += min(bearish_patterns * 6, 18)
            score -= min(bullish_patterns * 3, 9)  # Bullish patterns reduce score
        else:
            score += min(len(patterns) * 4, 16)

        # RSI alignment (0-15)
        if trend_score > 0 and 30 < rsi < 70:
            score += 15
        elif trend_score < 0 and 30 < rsi < 70:
            score += 15
        elif 20 < rsi < 80:
            score += 10

        # ADX (0-15)
        if adx > 25:
            score += 15
        elif adx > 15:
            score += 10

        # EMA alignment (0-15)
        if trend_score > 0 and "Bullish" in ema:
            score += 15
        elif trend_score < 0 and "Bearish" in ema:
            score += 15
        elif "Short-term" in ema:
            score += 10

        # Supporting data bonuses
        if order_flow:
            of_imb = order_flow.get('imbalance', {}).get('direction', '')
            of_strength = order_flow.get('imbalance', {}).get('strength', 0)
            
            # Order Flow alignment bonus
            if trend_score > 0 and of_imb == 'bid_heavy' and of_strength >= 30:
                score += 10  # Bullish trend + buying pressure
            elif trend_score < 0 and of_imb == 'ask_heavy' and of_strength >= 30:
                score += 10  # Bearish trend + selling pressure
            elif of_strength >= 30:
                score -= 5  # Contradictory order flow

        if fundamental:
            shared = fundamental.get('shared', {})
            bias = shared.get('overall_bias', 'NEUTRAL')
            if trend_score > 0 and bias == "BULLISH":
                score += 8
            elif trend_score < 0 and bias == "BEARISH":
                score += 8

        if divergence:
            for div in divergence:
                # Handle both dict and DivergenceSignal object
                if hasattr(div, 'get'):
                    direction = div.get('direction', '')
                else:
                    div_type = getattr(div, 'type', None)
                    if div_type:
                        direction = 'bullish' if 'bullish' in str(div_type.value) else 'bearish'
                    else:
                        direction = ''
                
                if direction == 'bullish' and trend_score > 0:
                    score += 5
                elif direction == 'bearish' and trend_score < 0:
                    score += 5

        # Find contradictions
        trend = "bullish" if trend_score > 30 else "bearish" if trend_score < -30 else "ranging"
        contradictions = self._check_contradictions(trend, order_flow, fundamental, divergence)
        
        # Penalize for contradictions
        score -= len(contradictions) * 15

        return min(100, max(0, int(score))), contradictions

    def _get_overall_signal_v2(self, trend_score: float, pattern_bias: str,
                               confluence: int, contradictions: List[str],
                               plan: PlanTemplate) -> tuple:
        """Determine final signal with contradiction handling and plan-aware floors.

        The plan sets the per-style floors for trend score and confluence:
        - scalp can fire on weaker setups (min_trend=30, min_conf=40)
        - position requires strong, high-confluence setups (min_trend=60, min_conf=55)

        Returns: (SignalStrength, adjusted_trend_score)
        """
        num_contra = len(contradictions)

        # Downgrade trend score based on contradictions
        adjusted_score = trend_score
        if num_contra >= 3:
            adjusted_score = trend_score * 0.3  # Severe downgrade
        elif num_contra == 2:
            adjusted_score = trend_score * 0.5  # Major downgrade
        elif num_contra == 1:
            adjusted_score = trend_score * 0.7  # Moderate downgrade

        # Per-plan tier thresholds
        base_trend    = plan.min_trend_score
        strong_trend  = max(80, base_trend + 30)
        base_conf     = plan.min_confluence
        strong_conf   = base_conf + 5

        # Extremely strong trend + confluence + no contradictions
        if adjusted_score >= strong_trend and confluence >= base_conf and num_contra == 0:
            return SignalStrength.STRONG_BUY, adjusted_score
        if adjusted_score <= -strong_trend and confluence >= base_conf and num_contra == 0:
            return SignalStrength.STRONG_SELL, adjusted_score

        # Strong trend + confluence + minimal contradictions
        if adjusted_score >= 50 and pattern_bias in ["bullish", "mixed"] and confluence >= strong_conf and num_contra <= 1:
            return SignalStrength.STRONG_BUY, adjusted_score
        if adjusted_score <= -50 and pattern_bias in ["bearish", "mixed"] and confluence >= strong_conf and num_contra <= 1:
            return SignalStrength.STRONG_SELL, adjusted_score

        # Moderate signals with some contradictions — gated by plan's base floor
        if adjusted_score >= base_trend and confluence >= base_conf:
            return SignalStrength.BUY, adjusted_score
        if adjusted_score <= -base_trend and confluence >= base_conf:
            return SignalStrength.SELL, adjusted_score

        # Too many contradictions or weak signal
        return SignalStrength.NEUTRAL, adjusted_score

    def build(
        self,
        symbol: str,
        timeframe: str,
        price: float,
        indicators: Dict,
        patterns: List[Dict],
        vision: Optional[Dict] = None,
        fundamental: Optional[Dict] = None,
        order_flow: Optional[Dict] = None,
        divergence: Optional[List[Dict]] = None,
        tier: str = "free",
        trading_style: str = "auto"
    ) -> AnalysisReport:
        """Build complete report from all inputs."""
        import datetime

        # Resolve the trading plan for this timeframe (or user override)
        plan = resolve_plan(trading_style, timeframe)
        sl_mult = plan.sl_atr_mult
        tp_mult = plan.tp_atr_mult

        # Calculate entry/exit based on ATR and trend direction
        atr = indicators.get('atr', price * 0.01)
        trend_score = indicators.get('trend_score', 0)
        vwap = indicators.get('vwap', price)

        # Adjust trend score with VWAP
        trend_score = self._adjust_trend_with_vwap(trend_score, price, vwap)

        # Determine trend
        if trend_score > 30:
            trend = "bullish"
            trend_conf = min(abs(trend_score) / 100, 1.0)
            # For BUY: Entry BELOW current price (buy the dip).
            # Entry-zone width is 0.5·sl_mult·ATR; total SL = sl_mult·ATR.
            entry = (price - atr * sl_mult * 0.8, price - atr * sl_mult * 0.3)
            sl = entry[0] - atr * sl_mult
            tp = entry[1] + atr * tp_mult
        elif trend_score < -30:
            trend = "bearish"
            trend_conf = min(abs(trend_score) / 100, 1.0)
            # For SELL: Entry ABOVE current price (sell the rally/retest).
            entry = (price + atr * sl_mult * 0.3, price + atr * sl_mult * 0.8)
            sl = entry[1] + atr * sl_mult
            tp = entry[0] - atr * tp_mult
        else:
            trend = "ranging"
            trend_conf = 0.5
            # Ranging: symmetrical zone around current price.
            entry = (price - atr * sl_mult * 0.5, price + atr * sl_mult * 0.5)
            sl = price - atr * sl_mult
            tp = price + atr * tp_mult

        # Calculate R:R using ENTRY midpoint
        entry_mid = (entry[0] + entry[1]) / 2
        if trend == "bullish":
            risk = abs(entry_mid - sl)
            reward = abs(tp - entry_mid)
        elif trend == "bearish":
            risk = abs(sl - entry_mid)
            reward = abs(entry_mid - tp)
        else:
            risk = abs(entry[0] - sl)
            reward = abs(tp - entry[1])
        rr = reward / risk if risk > 0 else 1.0

        # Compute partial-TP absolute prices from the plan's R-multiples.
        # Each partial sits at entry_mid + risk_per_unit * r, signed by direction.
        # Cast to plain Python float so the dataclass field is JSON-serializable
        # and the displayed prices don't have a "np.float64(...)" wrapper.
        risk_per_unit = abs(entry_mid - sl)
        if plan.partial_tp_r and risk_per_unit > 0 and trend in ("bullish", "bearish"):
            sign = 1 if trend == "bullish" else -1
            partial_tp_prices = tuple(
                float(entry_mid + risk_per_unit * r * sign) for r in plan.partial_tp_r
            )
        else:
            partial_tp_prices = ()

        # EMA alignment
        ema_alignment = self._get_ema_alignment(indicators)

        # RSI signal
        rsi = indicators.get('rsi', 50)
        rsi_signal = self._get_rsi_signal(rsi)

        # ADX signal
        adx = indicators.get('adx', 0)
        adx_signal = "Strong trend" if adx > 25 else "Weak trend" if adx > 15 else "No trend"

        # Pattern bias
        pattern_bias = self._get_pattern_bias(patterns)

        # Calculate confluence score WITH contradictions
        confluence, contradictions = self._calc_confluence_v2(
            trend_score, patterns, rsi, adx, ema_alignment,
            order_flow, fundamental, divergence
        )

        # Overall signal (adjusted for contradictions and plan floors)
        overall, adjusted_trend_score = self._get_overall_signal_v2(
            trend_score, pattern_bias, confluence, contradictions, plan
        )

        # Risk warning if contradictions exist
        risk_warning = None
        if contradictions:
            risk_warning = "⚠️ Contradictions detected: " + "; ".join(contradictions)

        # Position size suggestion
        risk_amount = f"Risk 2% of account = ${self.risk_per_trade * 10000:.0f} on $10k account"

        return AnalysisReport(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            trend=trend,
            trend_confidence=trend_conf,
            trend_score=adjusted_trend_score,
            entry_zone=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            position_size_suggestion=risk_amount,
            plan_style=plan.name,
            entry_style=plan.entry_style,
            time_in_trade_minutes=plan.time_in_trade_minutes,
            partial_tp_prices=partial_tp_prices,
            patterns_detected=patterns,
            pattern_bias=pattern_bias,
            support=indicators.get('support', []),
            resistance=indicators.get('resistance', []),
            vwap=vwap,
            poc=indicators.get('volume_profile', {}).get('poc', price),
            vah=indicators.get('volume_profile', {}).get('vah', price),
            val=indicators.get('volume_profile', {}).get('val', price),
            ema_alignment=ema_alignment,
            rsi=rsi,
            rsi_signal=rsi_signal,
            adx=adx,
            adx_signal=adx_signal,
            atr=atr,
            overall_signal=overall,
            signal_confidence=confluence / 100,
            confluence_score=confluence,
            risk_warning=risk_warning,
            vision_analysis=vision,
            fundamental_context=fundamental,
            order_flow=order_flow
        )

    def _get_ema_alignment(self, indicators: Dict) -> str:
        """Describe EMA alignment."""
        ema = indicators.get('ema', {})
        if not ema:
            return "Unknown"

        try:
            ema8 = ema.get(8, [0])[-1] if isinstance(ema.get(8), list) else ema.get(8, 0)
            ema21 = ema.get(21, [0])[-1] if isinstance(ema.get(21), list) else ema.get(21, 0)
            ema50 = ema.get(50, [0])[-1] if isinstance(ema.get(50), list) else ema.get(50, 0)

            if ema8 > ema21 > ema50:
                return "Bullish (8>21>50)"
            elif ema8 < ema21 < ema50:
                return "Bearish (8<21<50)"
            elif ema8 > ema21:
                return "Short-term bullish"
            else:
                return "Mixed / Crossing"
        except:
            return "Unknown"

    def _get_rsi_signal(self, rsi: float) -> str:
        if rsi > 70: return "Overbought"
        if rsi < 30: return "Oversold"
        if 40 <= rsi <= 60: return "Neutral zone"
        return "Momentum building"

    def _get_pattern_bias(self, patterns: List[Dict]) -> str:
        if not patterns:
            return "neutral"

        bullish = sum(1 for p in patterns if p.get('direction') == 'bullish')
        bearish = sum(1 for p in patterns if p.get('direction') == 'bearish')

        if bullish > bearish:
            return "bullish"
        elif bearish > bullish:
            return "bearish"
        return "mixed"


# Test
if __name__ == '__main__':
    builder = ReportBuilder()

    mock_indicators = {
        'trend_score': 45,
        'atr': 150.0,
        'rsi': 58,
        'adx': 28,
        'ema': {8: [67200], 21: [66800], 50: [65500]},
        'vwap': 67000,
        'volume_profile': {'poc': 66800, 'vah': 67500, 'val': 66200},
        'support': [66200, 65500],
        'resistance': [67500, 68200]
    }

    mock_patterns = [
        {'name': 'Bull Flag', 'direction': 'bullish', 'confidence': 0.82},
        {'name': 'Higher Highs', 'direction': 'bullish', 'confidence': 0.75}
    ]

    report = builder.build('BTCUSDT', 'H1', 67000, mock_indicators, mock_patterns)

    print("=== FREE REPORT ===")
    print(report.to_telegram_text(tier="free"))
    print("\n=== PREMIUM REPORT ===")
    print(report.to_telegram_text(tier="premium"))
