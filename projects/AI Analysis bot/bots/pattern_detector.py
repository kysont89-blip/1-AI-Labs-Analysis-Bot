"""
Programmatic Pattern Detector for XOX Analysis Bot
Detects 26+ classic chart patterns from OHLC data.
Strict validation rules — must pass ALL checks to be "detected".
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class PatternType(Enum):
    REVERSAL = "reversal"
    CONTINUATION = "continuation"
    CANDLESTICK = "candlestick"
    HARMONIC = "harmonic"


@dataclass
class Pattern:
    name: str
    pattern_type: PatternType
    confidence: float  # 0.0 - 1.0
    start_idx: int
    end_idx: int
    direction: str  # 'bullish' or 'bearish'
    details: Dict = None

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'type': self.pattern_type.value,
            'confidence': round(self.confidence, 2),
            'start': self.start_idx,
            'end': self.end_idx,
            'direction': self.direction,
            'details': self.details or {}
        }


class PatternDetector:
    """Detect chart patterns from OHLC DataFrame."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.n = len(df)
        self.patterns: List[Pattern] = []

        # Pre-compute helpers
        self._find_swings()

    def detect_all(self) -> List[Dict]:
        """Run all pattern detectors and return deduplicated results."""
        self.patterns = []

        # Reversal patterns
        self._detect_double_top()
        self._detect_double_bottom()
        self._detect_head_and_shoulders()
        self._detect_inverse_head_and_shoulders()
        self._detect_triple_top()
        self._detect_triple_bottom()
        self._detect_rounding_top()
        self._detect_rounding_bottom()

        # Continuation patterns
        self._detect_bull_flag()
        self._detect_bear_flag()
        self._detect_bull_pennant()
        self._detect_bear_pennant()
        self._detect_ascending_triangle()
        self._detect_descending_triangle()
        self._detect_symmetrical_triangle()
        self._detect_rectangle()

        # Candlestick patterns
        self._detect_engulfing()
        self._detect_hammer()
        self._detect_shooting_star()
        self._detect_morning_star()
        self._detect_evening_star()
        self._detect_doji()

        # Market structure
        self._detect_higher_highs_lows()
        self._detect_lower_highs_lows()
        self._detect_break_of_structure()

        # Deduplicate overlapping patterns of same name
        return self._deduplicate_patterns()

    def _deduplicate_patterns(self) -> List[Dict]:
        """Keep highest-confidence pattern when overlaps detected."""
        if not self.patterns:
            return []

        # Sort by confidence descending
        sorted_patterns = sorted(self.patterns, key=lambda p: p.confidence, reverse=True)

        kept = []
        for p in sorted_patterns:
            # Check if this overlaps with any kept pattern of same name
            overlap = False
            for k in kept:
                if k.name == p.name:
                    # Check time overlap (>50% overlap = duplicate)
                    overlap_start = max(p.start_idx, k.start_idx)
                    overlap_end = min(p.end_idx, k.end_idx)
                    if overlap_end > overlap_start:
                        p_range = p.end_idx - p.start_idx
                        overlap_len = overlap_end - overlap_start
                        if overlap_len > p_range * 0.3:  # 30% overlap threshold
                            overlap = True
                            break

            if not overlap:
                kept.append(p)

        return [p.to_dict() for p in kept]

    def _find_swings(self, window: int = 5):
        """Find swing highs and lows (causal — no look-ahead).

        Same logic as indicators._find_swings: the swing is labeled at the
        CONFIRMATION bar (i + window), not at the apex (i). This means a
        bar can only be identified as a swing once the next `window` bars
        have closed — which is the earliest we could know in real time.

        Note: the backtest in backtest_v2.py applies a 5-bar backshift to
        the pattern window before calling detect_all(), so this is
        double-defended for the backtest. Fixing the live leak here means
        the live bot stops peeking at future bars when labeling swings.
        """
        highs = self.df['high'].values
        lows = self.df['low'].values

        self.swing_highs = []
        self.swing_lows = []

        for i in range(window, self.n - window):
            # Swing high: higher than window candles on both sides
            if all(highs[i] > highs[i-j] for j in range(1, window+1)) and \
               all(highs[i] > highs[i+j] for j in range(1, window+1)):
                self.swing_highs.append((i + window, highs[i]))

            # Swing low: lower than window candles on both sides
            if all(lows[i] < lows[i-j] for j in range(1, window+1)) and \
               all(lows[i] < lows[i+j] for j in range(1, window+1)):
                self.swing_lows.append((i + window, lows[i]))

    # ═══════════════════════════════════════════════
    # REVERSAL PATTERNS
    # ═══════════════════════════════════════════════

    def _detect_double_top(self, tolerance: float = 0.015):
        """Two peaks at similar price, valley between, break below neckline."""
        if len(self.swing_highs) < 2:
            return

        for i in range(len(self.swing_highs) - 1):
            idx1, peak1 = self.swing_highs[i]
            idx2, peak2 = self.swing_highs[i + 1]

            # Check spacing (10-50 candles between peaks)
            if not (10 <= idx2 - idx1 <= 50):
                continue

            # Check similar height (within 1.5%)
            if abs(peak1 - peak2) / max(peak1, peak2) > tolerance:
                continue

            # Find valley between
            valley_idx = idx1 + np.argmin(self.df['low'].iloc[idx1:idx2].values)
            valley_price = self.df['low'].iloc[valley_idx]

            # Check neckline break after second peak
            if idx2 >= self.n - 5:
                continue

            post_low = self.df['low'].iloc[idx2:].min()
            if post_low < valley_price * 0.995:  # Break below neckline
                confidence = 0.85 - abs(peak1 - peak2) / max(peak1, peak2) * 10
                self.patterns.append(Pattern(
                    'Double Top', PatternType.REVERSAL,
                    min(confidence, 0.95), idx1, idx2, 'bearish',
                    {'peak1': peak1, 'peak2': peak2, 'neckline': valley_price}
                ))

    def _detect_double_bottom(self, tolerance: float = 0.015):
        """Two troughs at similar price, peak between, break above neckline."""
        if len(self.swing_lows) < 2:
            return

        for i in range(len(self.swing_lows) - 1):
            idx1, trough1 = self.swing_lows[i]
            idx2, trough2 = self.swing_lows[i + 1]

            if not (10 <= idx2 - idx1 <= 50):
                continue

            if abs(trough1 - trough2) / max(trough1, trough2) > tolerance:
                continue

            # Find peak between
            peak_idx = idx1 + np.argmax(self.df['high'].iloc[idx1:idx2].values)
            peak_price = self.df['high'].iloc[peak_idx]

            if idx2 >= self.n - 5:
                continue

            post_high = self.df['high'].iloc[idx2:].max()
            if post_high > peak_price * 1.005:
                confidence = 0.85 - abs(trough1 - trough2) / max(trough1, trough2) * 10
                self.patterns.append(Pattern(
                    'Double Bottom', PatternType.REVERSAL,
                    min(confidence, 0.95), idx1, idx2, 'bullish',
                    {'trough1': trough1, 'trough2': trough2, 'neckline': peak_price}
                ))

    def _detect_head_and_shoulders(self, tolerance: float = 0.02):
        """Three peaks: middle highest, neckline break."""
        if len(self.swing_highs) < 3:
            return

        for i in range(len(self.swing_highs) - 2):
            idx_l, peak_l = self.swing_highs[i]
            idx_h, peak_h = self.swing_highs[i + 1]
            idx_r, peak_r = self.swing_highs[i + 2]

            # Head must be highest
            if not (peak_h > peak_l and peak_h > peak_r):
                continue

            # Shoulders similar height
            if abs(peak_l - peak_r) / max(peak_l, peak_r) > tolerance:
                continue

            # Head not too tall (not more than 5% above shoulders)
            if (peak_h - max(peak_l, peak_r)) / max(peak_l, peak_r) > 0.05:
                continue

            # Find neckline (valley between shoulders)
            neck_idx = idx_l + np.argmin(self.df['low'].iloc[idx_l:idx_r].values)
            neck_price = self.df['low'].iloc[neck_idx]

            if idx_r >= self.n - 5:
                continue

            post_low = self.df['low'].iloc[idx_r:].min()
            if post_low < neck_price * 0.995:
                self.patterns.append(Pattern(
                    'Head & Shoulders', PatternType.REVERSAL,
                    0.80, idx_l, idx_r, 'bearish',
                    {'left_shoulder': peak_l, 'head': peak_h, 'right_shoulder': peak_r}
                ))

    def _detect_inverse_head_and_shoulders(self, tolerance: float = 0.02):
        """Three troughs: middle lowest, neckline break."""
        if len(self.swing_lows) < 3:
            return

        for i in range(len(self.swing_lows) - 2):
            idx_l, trough_l = self.swing_lows[i]
            idx_h, trough_h = self.swing_lows[i + 1]
            idx_r, trough_r = self.swing_lows[i + 2]

            if not (trough_h < trough_l and trough_h < trough_r):
                continue

            if abs(trough_l - trough_r) / max(trough_l, trough_r) > tolerance:
                continue

            neck_idx = idx_l + np.argmax(self.df['high'].iloc[idx_l:idx_r].values)
            neck_price = self.df['high'].iloc[neck_idx]

            if idx_r >= self.n - 5:
                continue

            post_high = self.df['high'].iloc[idx_r:].max()
            if post_high > neck_price * 1.005:
                self.patterns.append(Pattern(
                    'Inverse Head & Shoulders', PatternType.REVERSAL,
                    0.80, idx_l, idx_r, 'bullish',
                    {'left_shoulder': trough_l, 'head': trough_h, 'right_shoulder': trough_r}
                ))

    def _detect_triple_top(self, tolerance: float = 0.02):
        """Three peaks at similar price, break below support."""
        if len(self.swing_highs) < 3:
            return

        for i in range(len(self.swing_highs) - 2):
            idx1, p1 = self.swing_highs[i]
            idx2, p2 = self.swing_highs[i + 1]
            idx3, p3 = self.swing_highs[i + 2]

            max_diff = max(abs(p1-p2), abs(p2-p3), abs(p1-p3)) / max(p1, p2, p3)
            if max_diff > tolerance:
                continue

            # Find support between peaks
            support = min(self.df['low'].iloc[idx1:idx3])

            if idx3 >= self.n - 5:
                continue

            if self.df['low'].iloc[idx3:].min() < support * 0.995:
                self.patterns.append(Pattern(
                    'Triple Top', PatternType.REVERSAL,
                    0.78, idx1, idx3, 'bearish',
                    {'peaks': [p1, p2, p3], 'support': support}
                ))

    def _detect_triple_bottom(self, tolerance: float = 0.02):
        """Three troughs at similar price, break above resistance."""
        if len(self.swing_lows) < 3:
            return

        for i in range(len(self.swing_lows) - 2):
            idx1, t1 = self.swing_lows[i]
            idx2, t2 = self.swing_lows[i + 1]
            idx3, t3 = self.swing_lows[i + 2]

            max_diff = max(abs(t1-t2), abs(t2-t3), abs(t1-t3)) / max(t1, t2, t3)
            if max_diff > tolerance:
                continue

            resistance = max(self.df['high'].iloc[idx1:idx3])

            if idx3 >= self.n - 5:
                continue

            if self.df['high'].iloc[idx3:].max() > resistance * 1.005:
                self.patterns.append(Pattern(
                    'Triple Bottom', PatternType.REVERSAL,
                    0.78, idx1, idx3, 'bullish',
                    {'troughs': [t1, t2, t3], 'resistance': resistance}
                ))

    def _detect_rounding_top(self, min_candles: int = 20):
        """Gradual curve up then down."""
        if self.n < min_candles * 2:
            return

        window = min_candles
        for start in range(self.n - window * 2):
            mid = start + window
            end = mid + window

            # Gradual rise
            rise = self.df['close'].iloc[start:mid]
            # Gradual fall
            fall = self.df['close'].iloc[mid:end]

            if rise.iloc[-1] <= rise.iloc[0] * 1.02:
                continue
            if fall.iloc[-1] >= fall.iloc[0] * 0.98:
                continue

            # Smooth curves (low volatility during formation)
            if rise.std() / rise.mean() > 0.01:
                continue
            if fall.std() / fall.mean() > 0.01:
                continue

            self.patterns.append(Pattern(
                'Rounding Top', PatternType.REVERSAL,
                0.70, start, end, 'bearish'
            ))

    def _detect_rounding_bottom(self, min_candles: int = 20):
        """Gradual curve down then up."""
        if self.n < min_candles * 2:
            return

        window = min_candles
        for start in range(self.n - window * 2):
            mid = start + window
            end = mid + window

            fall = self.df['close'].iloc[start:mid]
            rise = self.df['close'].iloc[mid:end]

            if fall.iloc[-1] >= fall.iloc[0] * 0.98:
                continue
            if rise.iloc[-1] <= rise.iloc[0] * 1.02:
                continue

            if fall.std() / fall.mean() > 0.01:
                continue
            if rise.std() / rise.mean() > 0.01:
                continue

            self.patterns.append(Pattern(
                'Rounding Bottom', PatternType.REVERSAL,
                0.70, start, end, 'bullish'
            ))

    # ═══════════════════════════════════════════════
    # CONTINUATION PATTERNS
    # ═══════════════════════════════════════════════

    def _detect_bull_flag(self):
        """Strong impulse up, then tight parallel channel down/sideways."""
        for start in range(self.n - 20):
            # Look for strong impulse (5-10 candles)
            impulse_end = start + 5
            if impulse_end >= self.n:
                continue

            impulse = self.df.iloc[start:impulse_end]
            impulse_move = (impulse['close'].iloc[-1] - impulse['open'].iloc[0]) / impulse['open'].iloc[0]

            if impulse_move < 0.03:  # Need 3%+ impulse
                continue

            # Flag period (3-15 candles)
            for flag_len in range(3, 16):
                flag_end = impulse_end + flag_len
                if flag_end >= self.n:
                    continue

                flag = self.df.iloc[impulse_end:flag_end]

                # Tight range (max 50% of impulse)
                flag_range = (flag['high'].max() - flag['low'].min()) / impulse['close'].iloc[-1]
                if flag_range > impulse_move * 0.5:
                    continue

                # Declining volume
                if flag['volume'].mean() >= impulse['volume'].mean() * 0.8:
                    continue

                # Breakout
                if flag_end >= self.n - 3:
                    continue

                post = self.df.iloc[flag_end:flag_end+3]
                if post['close'].iloc[-1] > flag['high'].max():
                    self.patterns.append(Pattern(
                        'Bull Flag', PatternType.CONTINUATION,
                        0.82, start, flag_end, 'bullish',
                        {'impulse_pct': impulse_move, 'flag_candles': flag_len}
                    ))
                    break  # One flag per impulse

    def _detect_bear_flag(self):
        """Strong impulse down, then tight parallel channel up/sideways."""
        for start in range(self.n - 20):
            impulse_end = start + 5
            if impulse_end >= self.n:
                continue

            impulse = self.df.iloc[start:impulse_end]
            impulse_move = (impulse['open'].iloc[0] - impulse['close'].iloc[-1]) / impulse['open'].iloc[0]

            if impulse_move < 0.03:
                continue

            for flag_len in range(3, 16):
                flag_end = impulse_end + flag_len
                if flag_end >= self.n:
                    continue

                flag = self.df.iloc[impulse_end:flag_end]
                flag_range = (flag['high'].max() - flag['low'].min()) / impulse['close'].iloc[-1]

                if flag_range > impulse_move * 0.5:
                    continue

                if flag['volume'].mean() >= impulse['volume'].mean() * 0.8:
                    continue

                if flag_end >= self.n - 3:
                    continue

                post = self.df.iloc[flag_end:flag_end+3]
                if post['close'].iloc[-1] < flag['low'].min():
                    self.patterns.append(Pattern(
                        'Bear Flag', PatternType.CONTINUATION,
                        0.82, start, flag_end, 'bearish',
                        {'impulse_pct': impulse_move, 'flag_candles': flag_len}
                    ))
                    break

    def _detect_bull_pennant(self):
        """Strong impulse up, then symmetrical triangle converging."""
        for start in range(self.n - 20):
            impulse_end = start + 5
            if impulse_end >= self.n:
                continue

            impulse = self.df.iloc[start:impulse_end]
            impulse_move = (impulse['close'].iloc[-1] - impulse['open'].iloc[0]) / impulse['open'].iloc[0]
            if impulse_move < 0.03:
                continue

            for pennant_len in range(5, 20):
                end = impulse_end + pennant_len
                if end >= self.n:
                    continue

                pennant = self.df.iloc[impulse_end:end]

                # Converging highs and lows
                highs = pennant['high'].values
                lows = pennant['low'].values

                # Linear regression slopes
                x = np.arange(len(highs))
                if len(x) < 3:
                    continue

                high_slope = np.polyfit(x, highs, 1)[0]
                low_slope = np.polyfit(x, lows, 1)[0]

                # Highs declining, lows rising (convergence)
                if high_slope >= 0 or low_slope <= 0:
                    continue

                # Converging
                if abs(high_slope) < abs(low_slope) * 0.5 or abs(high_slope) > abs(low_slope) * 2:
                    continue

                # Breakout
                if end >= self.n - 3:
                    continue

                post = self.df.iloc[end:end+3]
                if post['close'].iloc[-1] > pennant['high'].max():
                    self.patterns.append(Pattern(
                        'Bull Pennant', PatternType.CONTINUATION,
                        0.78, start, end, 'bullish'
                    ))
                    break

    def _detect_bear_pennant(self):
        """Strong impulse down, then symmetrical triangle converging."""
        for start in range(self.n - 20):
            impulse_end = start + 5
            if impulse_end >= self.n:
                continue

            impulse = self.df.iloc[start:impulse_end]
            impulse_move = (impulse['open'].iloc[0] - impulse['close'].iloc[-1]) / impulse['open'].iloc[0]
            if impulse_move < 0.03:
                continue

            for pennant_len in range(5, 20):
                end = impulse_end + pennant_len
                if end >= self.n:
                    continue

                pennant = self.df.iloc[impulse_end:end]
                highs = pennant['high'].values
                lows = pennant['low'].values

                x = np.arange(len(highs))
                if len(x) < 3:
                    continue

                high_slope = np.polyfit(x, highs, 1)[0]
                low_slope = np.polyfit(x, lows, 1)[0]

                if high_slope >= 0 or low_slope <= 0:
                    continue

                if abs(high_slope) < abs(low_slope) * 0.5 or abs(high_slope) > abs(low_slope) * 2:
                    continue

                if end >= self.n - 3:
                    continue

                post = self.df.iloc[end:end+3]
                if post['close'].iloc[-1] < pennant['low'].min():
                    self.patterns.append(Pattern(
                        'Bear Pennant', PatternType.CONTINUATION,
                        0.78, start, end, 'bearish'
                    ))
                    break

    def _detect_ascending_triangle(self, min_touches: int = 2):
        """Flat resistance, rising support."""
        if self.n < 20:
            return

        highs = self.df['high'].values
        lows = self.df['low'].values

        # Find flat resistance (multiple touches)
        for res_level in np.linspace(highs.max()*0.98, highs.max(), 10):
            touches = np.where(np.abs(highs - res_level) / res_level < 0.005)[0]
            if len(touches) < min_touches:
                continue

            # Check if touches are spread out
            if max(touches) - min(touches) < 10:
                continue

            # Check rising support
            support_period = self.df.iloc[min(touches):max(touches)+5]
            low_slope = np.polyfit(range(len(support_period)), support_period['low'].values, 1)[0]

            if low_slope <= 0:
                continue

            self.patterns.append(Pattern(
                'Ascending Triangle', PatternType.CONTINUATION,
                0.82, min(touches), max(touches), 'bullish',
                {'resistance': res_level}
            ))

    def _detect_descending_triangle(self, min_touches: int = 2):
        """Flat support, falling resistance."""
        if self.n < 20:
            return

        highs = self.df['high'].values
        lows = self.df['low'].values

        for sup_level in np.linspace(lows.min(), lows.min()*1.02, 10):
            touches = np.where(np.abs(lows - sup_level) / sup_level < 0.005)[0]
            if len(touches) < min_touches:
                continue

            if max(touches) - min(touches) < 10:
                continue

            resistance_period = self.df.iloc[min(touches):max(touches)+5]
            high_slope = np.polyfit(range(len(resistance_period)), resistance_period['high'].values, 1)[0]

            if high_slope >= 0:
                continue

            self.patterns.append(Pattern(
                'Descending Triangle', PatternType.CONTINUATION,
                0.82, min(touches), max(touches), 'bearish',
                {'support': sup_level}
            ))

    def _detect_symmetrical_triangle(self):
        """Converging support and resistance."""
        if self.n < 20:
            return

        highs = self.df['high'].values[-20:]
        lows = self.df['low'].values[-20:]

        x = np.arange(len(highs))
        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]

        # Converging
        if high_slope >= 0 or low_slope <= 0:
            return

        if abs(high_slope) < 0.0001 or abs(low_slope) < 0.0001:
            return

        # Prior trend determines expected direction
        prior = self.df['close'].iloc[-40:-20].values
        trend = 'bullish' if prior[-1] > prior[0] else 'bearish'

        self.patterns.append(Pattern(
            'Symmetrical Triangle', PatternType.CONTINUATION,
            0.75, self.n-20, self.n, trend
        ))

    def _detect_rectangle(self, min_touches: int = 2, min_candles: int = 15):
        """Parallel support and resistance, at least 2 touches each."""
        if self.n < min_candles:
            return

        highs = self.df['high'].values[-min_candles:]
        lows = self.df['low'].values[-min_candles:]

        # Find resistance touches
        res_level = np.percentile(highs, 90)
        res_touches = np.where(np.abs(highs - res_level) / res_level < 0.01)[0]

        # Find support touches
        sup_level = np.percentile(lows, 10)
        sup_touches = np.where(np.abs(lows - sup_level) / sup_level < 0.01)[0]

        if len(res_touches) < min_touches or len(sup_touches) < min_touches:
            return

        # Parallel check
        range_pct = (res_level - sup_level) / res_level
        if range_pct > 0.05:  # Too wide
            return

        # Direction = breakout direction
        last_close = self.df['close'].iloc[-1]
        trend = 'bullish' if last_close > res_level else 'bearish'

        self.patterns.append(Pattern(
            'Rectangle', PatternType.CONTINUATION,
            0.85, self.n-min_candles, self.n, trend,
            {'support': sup_level, 'resistance': res_level}
        ))

    # ═══════════════════════════════════════════════
    # CANDLESTICK PATTERNS
    # ═══════════════════════════════════════════════

    def _detect_engulfing(self):
        """Bullish/bearish engulfing patterns."""
        for i in range(1, self.n):
            prev = self.df.iloc[i-1]
            curr = self.df.iloc[i]

            prev_body = abs(prev['close'] - prev['open'])
            curr_body = abs(curr['close'] - curr['open'])

            if prev_body == 0:
                continue

            # Bullish engulfing
            if prev['close'] < prev['open'] and curr['close'] > curr['open']:
                if curr['open'] < prev['close'] and curr['close'] > prev['open']:
                    if curr_body > prev_body * 1.2:
                        self.patterns.append(Pattern(
                            'Bullish Engulfing', PatternType.CANDLESTICK,
                            0.88, i-1, i, 'bullish'
                        ))

            # Bearish engulfing
            if prev['close'] > prev['open'] and curr['close'] < curr['open']:
                if curr['open'] > prev['close'] and curr['close'] < prev['open']:
                    if curr_body > prev_body * 1.2:
                        self.patterns.append(Pattern(
                            'Bearish Engulfing', PatternType.CANDLESTICK,
                            0.88, i-1, i, 'bearish'
                        ))

    def _detect_hammer(self):
        """Small body at top, long lower wick (2x+ body), after downtrend."""
        for i in range(5, self.n):
            # Check prior downtrend
            if self.df['close'].iloc[i-5] <= self.df['close'].iloc[i] * 1.01:
                continue

            candle = self.df.iloc[i]
            body = abs(candle['close'] - candle['open'])
            range_total = candle['high'] - candle['low']

            if range_total == 0 or body == 0:
                continue

            lower_wick = min(candle['open'], candle['close']) - candle['low']
            upper_wick = candle['high'] - max(candle['open'], candle['close'])

            if lower_wick > body * 2 and upper_wick < body * 0.5:
                if body / range_total < 0.3:  # Small body
                    self.patterns.append(Pattern(
                        'Hammer', PatternType.CANDLESTICK,
                        0.85, i, i, 'bullish'
                    ))

    def _detect_shooting_star(self):
        """Small body at bottom, long upper wick (2x+ body), after uptrend."""
        for i in range(5, self.n):
            if self.df['close'].iloc[i] <= self.df['close'].iloc[i-5] * 1.01:
                continue

            candle = self.df.iloc[i]
            body = abs(candle['close'] - candle['open'])
            range_total = candle['high'] - candle['low']

            if range_total == 0 or body == 0:
                continue

            upper_wick = candle['high'] - max(candle['open'], candle['close'])
            lower_wick = min(candle['open'], candle['close']) - candle['low']

            if upper_wick > body * 2 and lower_wick < body * 0.5:
                if body / range_total < 0.3:
                    self.patterns.append(Pattern(
                        'Shooting Star', PatternType.CANDLESTICK,
                        0.85, i, i, 'bearish'
                    ))

    def _detect_morning_star(self):
        """Bear → small doji/spinning top → bull, at support."""
        for i in range(2, self.n):
            c1 = self.df.iloc[i-2]
            c2 = self.df.iloc[i-1]
            c3 = self.df.iloc[i]

            # First candle bearish
            if c1['close'] >= c1['open']:
                continue

            # Second candle small
            body2 = abs(c2['close'] - c2['open'])
            range2 = c2['high'] - c2['low']
            if range2 == 0 or body2 / range2 > 0.3:
                continue

            # Third candle bullish
            if c3['close'] <= c3['open']:
                continue

            # Third closes above midpoint of first
            mid1 = (c1['open'] + c1['close']) / 2
            if c3['close'] < mid1:
                continue

            # Recent low (support)
            if c2['low'] > self.df['low'].iloc[i-10:i].min() * 1.01:
                continue

            self.patterns.append(Pattern(
                'Morning Star', PatternType.CANDLESTICK,
                0.82, i-2, i, 'bullish'
            ))

    def _detect_evening_star(self):
        """Bull → small doji/spinning top → bear, at resistance."""
        for i in range(2, self.n):
            c1 = self.df.iloc[i-2]
            c2 = self.df.iloc[i-1]
            c3 = self.df.iloc[i]

            if c1['close'] <= c1['open']:
                continue

            body2 = abs(c2['close'] - c2['open'])
            range2 = c2['high'] - c2['low']
            if range2 == 0 or body2 / range2 > 0.3:
                continue

            if c3['close'] >= c3['open']:
                continue

            mid1 = (c1['open'] + c1['close']) / 2
            if c3['close'] > mid1:
                continue

            if c2['high'] < self.df['high'].iloc[i-10:i].max() * 0.99:
                continue

            self.patterns.append(Pattern(
                'Evening Star', PatternType.CANDLESTICK,
                0.82, i-2, i, 'bearish'
            ))

    def _detect_doji(self):
        """Open ≈ Close, body < 10% of total range."""
        doji_indices = []
        for i in range(self.n):
            candle = self.df.iloc[i]
            body = abs(candle['close'] - candle['open'])
            range_total = candle['high'] - candle['low']

            if range_total == 0:
                continue

            if body / range_total < 0.1:
                doji_indices.append(i)

        # Group consecutive Dojis
        if not doji_indices:
            return

        groups = []
        current = [doji_indices[0]]
        for idx in doji_indices[1:]:
            if idx - current[-1] <= 2:  # Within 2 candles
                current.append(idx)
            else:
                groups.append(current)
                current = [idx]
        groups.append(current)

        for group in groups:
            mid = group[len(group)//2]
            # Direction depends on context
            prev_trend = 'bullish' if self.df['close'].iloc[max(0,mid-5):mid].mean() < \
                         self.df['close'].iloc[mid] else 'bearish'
            self.patterns.append(Pattern(
                'Doji', PatternType.CANDLESTICK,
                0.90, group[0], group[-1], prev_trend,
                {'count': len(group)}
            ))

    # ═══════════════════════════════════════════════
    # MARKET STRUCTURE
    # ═══════════════════════════════════════════════

    def _detect_higher_highs_lows(self, lookback: int = 20):
        """Series of higher highs and higher lows."""
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return

        recent_highs = [(i, p) for i, p in self.swing_highs if i >= self.n - lookback]
        recent_lows = [(i, p) for i, p in self.swing_lows if i >= self.n - lookback]

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return

        highs_ascending = all(recent_highs[i][1] > recent_highs[i-1][1]
                              for i in range(1, len(recent_highs)))
        lows_ascending = all(recent_lows[i][1] > recent_lows[i-1][1]
                             for i in range(1, len(recent_lows)))

        if highs_ascending and lows_ascending:
            self.patterns.append(Pattern(
                'Higher Highs & Higher Lows', PatternType.CONTINUATION,
                0.75, self.n-lookback, self.n, 'bullish'
            ))

    def _detect_lower_highs_lows(self, lookback: int = 20):
        """Series of lower highs and lower lows."""
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return

        recent_highs = [(i, p) for i, p in self.swing_highs if i >= self.n - lookback]
        recent_lows = [(i, p) for i, p in self.swing_lows if i >= self.n - lookback]

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return

        highs_descending = all(recent_highs[i][1] < recent_highs[i-1][1]
                               for i in range(1, len(recent_highs)))
        lows_descending = all(recent_lows[i][1] < recent_lows[i-1][1]
                              for i in range(1, len(recent_lows)))

        if highs_descending and lows_descending:
            self.patterns.append(Pattern(
                'Lower Highs & Lower Lows', PatternType.CONTINUATION,
                0.75, self.n-lookback, self.n, 'bearish'
            ))

    def _detect_break_of_structure(self):
        """Close beyond prior swing high/low."""
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return

        last_close = self.df['close'].iloc[-1]

        # Bullish BOS: close above prior swing high
        if len(self.swing_highs) >= 2:
            prior_high = self.swing_highs[-2][1]
            if last_close > prior_high * 1.005:
                self.patterns.append(Pattern(
                    'Break of Structure (Bullish)', PatternType.CONTINUATION,
                    0.80, self.swing_highs[-2][0], self.n-1, 'bullish',
                    {'prior_high': prior_high}
                ))

        # Bearish BOS: close below prior swing low
        if len(self.swing_lows) >= 2:
            prior_low = self.swing_lows[-2][1]
            if last_close < prior_low * 0.995:
                self.patterns.append(Pattern(
                    'Break of Structure (Bearish)', PatternType.CONTINUATION,
                    0.80, self.swing_lows[-2][0], self.n-1, 'bearish',
                    {'prior_low': prior_low}
                ))


# Test
def test_patterns():
    """Test pattern detection on synthetic data."""
    import random

    np.random.seed(42)
    n = 100
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq='1h')

    # Create a double bottom pattern
    base = 100
    prices = []
    for i in range(n):
        if 20 <= i <= 30:
            prices.append(base - 5 + random.gauss(0, 0.5))
        elif 40 <= i <= 50:
            prices.append(base - 5 + random.gauss(0, 0.5))
        elif i > 55:
            prices.append(base + (i-55) * 0.3 + random.gauss(0, 0.5))
        else:
            prices.append(base + random.gauss(0, 1))

    df = pd.DataFrame({
        'open': [p + random.gauss(0, 0.3) for p in prices],
        'high': [p + abs(random.gauss(0, 0.5)) for p in prices],
        'low': [p - abs(random.gauss(0, 0.5)) for p in prices],
        'close': [p + random.gauss(0, 0.3) for p in prices],
        'volume': [abs(random.gauss(1000, 200)) for _ in prices]
    }, index=dates)

    detector = PatternDetector(df)
    patterns = detector.detect_all()

    print(f"Found {len(patterns)} patterns:")
    for p in patterns:
        print(f"  - {p['name']} ({p['direction']}): {p['confidence']:.0%}")

    return patterns


if __name__ == '__main__':
    test_patterns()
