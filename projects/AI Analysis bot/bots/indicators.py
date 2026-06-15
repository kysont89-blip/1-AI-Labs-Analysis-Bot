"""
Technical Indicator Calculator for XOX Analysis Bot
Computes EMA, VWAP, Volume Profile, ATR, RSI, ADX, Market Structure.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class IndicatorSet:
    """Container for all computed indicators."""
    ema: Dict[int, pd.Series]
    vwap: pd.Series
    vwap_std: pd.Series
    volume_profile: Dict[str, float]
    atr: pd.Series
    atr_value: float
    rsi: pd.Series
    adx: pd.Series
    adx_value: float
    trend_score: float  # -100 to +100
    swing_highs: List[Tuple[int, float]]
    swing_lows: List[Tuple[int, float]]
    support_levels: List[float]
    resistance_levels: List[float]

    def to_dict(self) -> Dict:
        return {
            'ema': {k: v.tolist() for k, v in self.ema.items()},
            'vwap': self.vwap.iloc[-1] if len(self.vwap) > 0 else None,
            'vwap_std': self.vwap_std.iloc[-1] if len(self.vwap_std) > 0 else None,
            'volume_profile': self.volume_profile,
            'atr': self.atr_value,
            'rsi': self.rsi.iloc[-1] if len(self.rsi) > 0 else 50,
            'adx': self.adx_value,
            'trend_score': self.trend_score,
            'swing_highs': self.swing_highs[-5:] if self.swing_highs else [],
            'swing_lows': self.swing_lows[-5:] if self.swing_lows else [],
            'support': self.support_levels[:3],
            'resistance': self.resistance_levels[:3]
        }


class IndicatorCalculator:
    """Calculate all technical indicators from OHLCV DataFrame."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.n = len(df)

    def calculate_all(self) -> IndicatorSet:
        """Compute complete indicator set."""
        ema = self._calc_ema([8, 21, 50, 200])
        vwap, vwap_std = self._calc_vwap()
        vp = self._calc_volume_profile()
        atr = self._calc_atr(14)
        rsi = self._calc_rsi(14)
        adx = self._calc_adx(14)
        trend = self._calc_trend_score(ema)
        swings = self._find_swings()
        levels = self._find_key_levels(atr_value=atr.iloc[-1] if len(atr) > 0 else 0)

        return IndicatorSet(
            ema=ema,
            vwap=vwap,
            vwap_std=vwap_std,
            volume_profile=vp,
            atr=atr,
            atr_value=atr.iloc[-1] if len(atr) > 0 else 0,
            rsi=rsi,
            adx=adx,
            adx_value=adx.iloc[-1] if len(adx) > 0 else 0,
            trend_score=trend,
            swing_highs=swings[0],
            swing_lows=swings[1],
            support_levels=levels[0],
            resistance_levels=levels[1]
        )

    def _calc_ema(self, periods: List[int]) -> Dict[int, pd.Series]:
        """Calculate EMAs for given periods."""
        return {p: self.df['close'].ewm(span=p, adjust=False).mean() for p in periods}

    def _calc_vwap(self, anchor: str = 'session') -> Tuple[pd.Series, pd.Series]:
        """
        Calculate VWAP.
        For intraday: reset daily.
        For daily+: use all available data.
        """
        tp = (self.df['high'] + self.df['low'] + self.df['close']) / 3
        pv = tp * self.df['volume']

        # For simplicity, use rolling VWAP (20-period)
        vwap = pv.rolling(window=20).sum() / self.df['volume'].rolling(window=20).sum()
        vwap_std = tp.rolling(window=20).std()

        return vwap, vwap_std

    def _calc_volume_profile(self, num_bins: int = 24) -> Dict[str, float]:
        """Calculate Volume Profile (POC, VAH, VAL)."""
        if self.n < 20:
            return {'poc': self.df['close'].iloc[-1], 'vah': self.df['high'].max(), 'val': self.df['low'].min()}

        # Price bins
        price_min = self.df['low'].min()
        price_max = self.df['high'].max()
        bins = np.linspace(price_min, price_max, num_bins)

        # Volume per bin
        bin_volumes = np.zeros(num_bins - 1)
        for _, row in self.df.iterrows():
            # Distribute volume across the candle's range
            idx = np.digitize([row['low'], row['high']], bins)
            if idx[0] == idx[1]:
                if 0 <= idx[0] < len(bin_volumes):
                    bin_volumes[idx[0]] += row['volume']
            else:
                for i in range(max(0, idx[0]), min(len(bin_volumes), idx[1] + 1)):
                    bin_volumes[i] += row['volume'] / max(1, idx[1] - idx[0] + 1)

        # POC = price level with most volume
        poc_idx = np.argmax(bin_volumes)
        poc = (bins[poc_idx] + bins[poc_idx + 1]) / 2

        # Value Area (70% of volume)
        total_vol = bin_volumes.sum()
        cumulative = np.cumsum(np.sort(bin_volumes)[::-1])
        va_threshold = total_vol * 0.70

        # Find VAH and VAL from sorted
        sorted_indices = np.argsort(bin_volumes)[::-1]
        included = set()
        running_vol = 0
        for idx in sorted_indices:
            running_vol += bin_volumes[idx]
            included.add(idx)
            if running_vol >= va_threshold:
                break

        vah = max((bins[i] + bins[i+1])/2 for i in included) if included else price_max
        val = min((bins[i] + bins[i+1])/2 for i in included) if included else price_min

        return {
            'poc': round(poc, 2),
            'vah': round(vah, 2),
            'val': round(val, 2),
            'value_area_width_pct': round((vah - val) / poc * 100, 2)
        }

    def _calc_atr(self, period: int = 14) -> pd.Series:
        """Average True Range."""
        high_low = self.df['high'] - self.df['low']
        high_close = np.abs(self.df['high'] - self.df['close'].shift())
        low_close = np.abs(self.df['low'] - self.df['close'].shift())

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def _calc_rsi(self, period: int = 14) -> pd.Series:
        """Relative Strength Index."""
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calc_adx(self, period: int = 14) -> pd.Series:
        """Average Directional Index (Wilder)."""
        high = self.df['high']
        low = self.df['low']
        close = self.df['close']
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        # Directional movement (Wilder's rules)
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=self.df.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=self.df.index,
        )

        # True Range
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        # Wilder smoothing (RMA): y[i] = y[i-1] - y[i-1]/n + x[i]
        # ewm(alpha=1/n, adjust=False).mean() is the pandas equivalent.
        alpha = 1.0 / period
        tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

        # Directional Indicators
        tr_safe = tr_smooth.replace(0, np.nan)
        plus_di = 100.0 * plus_dm_smooth / tr_safe
        minus_di = 100.0 * minus_dm_smooth / tr_safe

        # Directional Index
        di_sum = plus_di.fillna(0) + minus_di.fillna(0)
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
        dx = dx.fillna(0)

        # ADX is Wilder-smoothed DX
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return adx

    def _calc_trend_score(self, ema: Dict[int, pd.Series]) -> float:
        """
        Calculate trend score from -100 (strong bear) to +100 (strong bull).
        Based on EMA alignment + price position.
        """
        if not all(p in ema for p in [8, 21, 50]):
            return 0

        price = self.df['close'].iloc[-1]

        # EMA alignment score
        ema8 = ema[8].iloc[-1]
        ema21 = ema[21].iloc[-1]
        ema50 = ema[50].iloc[-1]

        score = 0

        # Price vs EMAs
        if price > ema8: score += 20
        if price > ema21: score += 20
        if price > ema50: score += 20

        # EMA alignment
        if ema8 > ema21: score += 15
        if ema21 > ema50: score += 15
        if ema8 > ema21 > ema50: score += 10  # Perfect alignment bonus

        # Bearish deductions
        if price < ema8: score -= 20
        if price < ema21: score -= 20
        if price < ema50: score -= 20
        if ema8 < ema21: score -= 15
        if ema21 < ema50: score -= 15
        if ema8 < ema21 < ema50: score -= 10

        return max(-100, min(100, score))

    def _find_swings(self, window: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """Find swing highs and lows (causal — no look-ahead).

        A swing high at bar i is one where high[i] > high[i-window..i-1] AND
        high[i] > high[i+1..i+window]. The second condition requires `window`
        future bars to be known — we cannot use this for live trading or for
        a backtest that wants to be causal.

        The standard causal rule: the swing is CONFIRMED at bar (i + window),
        not at bar i. We label the swing at the confirmation bar so the index
        reflects when the signal could have been known in real time.

        Returned tuples are (bar_index, price). The bar_index is the
        confirmation bar (i + window), NOT the apex bar i.
        """
        highs = self.df['high'].values
        lows = self.df['low'].values

        swing_highs = []
        swing_lows = []

        for i in range(window, self.n - window):
            if all(highs[i] > highs[i-j] for j in range(1, window+1)) and \
               all(highs[i] > highs[i+j] for j in range(1, window+1)):
                # Label the swing at the confirmation bar (i + window).
                # The apex price is still at bar i — that's the actual high.
                swing_highs.append((i + window, highs[i]))

            if all(lows[i] < lows[i-j] for j in range(1, window+1)) and \
               all(lows[i] < lows[i+j] for j in range(1, window+1)):
                swing_lows.append((i + window, lows[i]))

        return swing_highs, swing_lows

    def _find_key_levels(self, cluster_threshold: float = 0.005, lookback_pct: float = 0.5, atr_value: float = None) -> Tuple[List[float], List[float]]:
        """Find support and resistance levels from RECENT swing points only.
        
        Args:
            cluster_threshold: Price clustering threshold
            lookback_pct: Only use swing points from last X% of data (0.5 = last 50%)
            atr_value: Pre-calculated ATR value
        """
        swing_highs, swing_lows = self._find_swings()

        if not swing_highs or not swing_lows:
            return [self.df['low'].min()], [self.df['high'].max()]

        # Only use recent swing points (last lookback_pct of data)
        recent_start = int(self.n * (1 - lookback_pct))
        
        recent_highs = [(i, p) for i, p in swing_highs if i >= recent_start]
        recent_lows = [(i, p) for i, p in swing_lows if i >= recent_start]
        
        # If not enough recent points, use last 10 swings
        if len(recent_highs) < 3:
            recent_highs = swing_highs[-10:]
        if len(recent_lows) < 3:
            recent_lows = swing_lows[-10:]

        # Cluster swing highs (resistance)
        highs = sorted([p for _, p in recent_highs])
        resistance = self._cluster_levels(highs, cluster_threshold)

        # Cluster swing lows (support)
        lows = sorted([p for _, p in recent_lows])
        support = self._cluster_levels(lows, cluster_threshold)

        # Filter: 
        # - Support must be BELOW current price (or within small buffer)
        # - Resistance must be ABOVE current price (or within small buffer)
        # - Also filter out levels too far from current price (>3x ATR)
        current_price = self.df['close'].iloc[-1]
        atr = atr_value if atr_value is not None else current_price * 0.01
        max_distance = atr * 3
        buffer = atr * 0.5  # Small buffer above/below price
        
        # Support = levels BELOW price (with small buffer)
        support = [s for s in support if s <= current_price + buffer and s >= current_price - max_distance]
        
        # Resistance = levels ABOVE price (with small buffer)
        resistance = [r for r in resistance if r >= current_price - buffer and r <= current_price + max_distance]
        
        # Ensure at least 1 level each
        if not support:
            support = [self.df['low'].iloc[-20:].min()]
        if not resistance:
            resistance = [self.df['high'].iloc[-20:].max()]

        return support, resistance

    def _cluster_levels(self, prices: List[float], threshold: float) -> List[float]:
        """Cluster similar price levels."""
        if not prices:
            return []

        clusters = []
        current = [prices[0]]

        for price in prices[1:]:
            if abs(price - np.mean(current)) / np.mean(current) < threshold:
                current.append(price)
            else:
                clusters.append(np.mean(current))
                current = [price]

        if current:
            clusters.append(np.mean(current))

        return sorted(clusters, reverse=True)  # Highest first for resistance


# Test
if __name__ == '__main__':
    import random

    np.random.seed(42)
    n = 100
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq='1h')

    # Generate trending data
    trend = np.linspace(100, 130, n)
    noise = np.random.normal(0, 1, n)
    closes = trend + noise

    df = pd.DataFrame({
        'open': closes + np.random.normal(0, 0.5, n),
        'high': closes + abs(np.random.normal(1, 0.5, n)),
        'low': closes - abs(np.random.normal(1, 0.5, n)),
        'close': closes,
        'volume': np.random.normal(1000, 200, n)
    }, index=dates)

    calc = IndicatorCalculator(df)
    indicators = calc.calculate_all()

    print("Indicator Summary:")
    print(f"  Trend Score: {indicators.trend_score:+.0f}/100")
    print(f"  ATR: {indicators.atr_value:.2f}")
    print(f"  RSI: {indicators.rsi.iloc[-1]:.1f}")
    print(f"  ADX: {indicators.adx_value:.1f}")
    print(f"  Volume Profile: POC={indicators.volume_profile['poc']}, VAH={indicators.volume_profile['vah']}, VAL={indicators.volume_profile['val']}")
    print(f"  Support: {indicators.support_levels}")
    print(f"  Resistance: {indicators.resistance_levels}")
    print(f"  Swing Highs: {len(indicators.swing_highs)}")
    print(f"  Swing Lows: {len(indicators.swing_lows)}")
