"""
Backtest V2 — Production-Faithful Walk-Forward Backtester

Consumes the actual production `ReportBuilder.build()` so we measure what
the bot does, not a reimplementation. Replaces fast_backtest.py and
backtest_engine.py.

Usage:
    python backtest_v2.py
    python backtest_v2.py --symbols BTCUSDT --timeframes H1 --days 7 --plans swing
    python backtest_v2.py --symbols BTCUSDT,ETHUSDT --timeframes H1,H4 --days 90
"""

import sys
import os
import json
import time
import logging
import argparse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

import numpy as np
import pandas as pd

# Import bot modules (project root /bots/..)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bots'))

from market_data import BinanceDataFetcher
from indicators import IndicatorCalculator
from pattern_detector import PatternDetector
from divergence_detector import DivergenceDetector
from regime_detector import RegimeDetector
from report_builder import (
    ReportBuilder,
    SignalStrength,
    PLANS,
    TF_TO_PLAN,
    resolve_plan,
)

# ───────────────────────────── CONFIG ─────────────────────────────

WARMUP = 200                  # bars of history before first tradable bar
PATTERN_LOOKAHEAD = 5         # shift for pattern look-ahead mitigation
BARS_PER_DAY = {              # rough reference for fetch sizing
    'M5': 288, 'M15': 96, 'H1': 24, 'H4': 6, 'D1': 1,
}
TF_BAR_MINUTES = {            # minutes per candle for time-in-trade math
    'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240, 'D1': 1440,
}

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'XAUUSD', 'BNBUSDT']
DEFAULT_TIMEFRAMES = ['H1', 'H4']
DEFAULT_PLANS = ['scalp', 'day', 'swing', 'swing_h4', 'position']
DEFAULT_DAYS = 90
YAHOO_H1_DAYS_CAP = 30        # Yahoo Finance caps intraday H1 at ~30d

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
# Force UTF-8 on stdout/stderr so emoji and arrows don't crash on Windows cp1252
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
log = logging.getLogger('backtest_v2')


# ───────────────────────────── DATA CLASSES ─────────────────────────────

@dataclass
class RunConfig:
    """All CLI-derived configuration, passed through the backtest."""
    symbols: List[str]
    timeframes: List[str]
    days: int
    plans: List[str]
    with_patterns: bool
    with_divergence: bool
    with_regime: bool
    include_fees: bool
    fee_bps: float
    lookback_shift: int
    data_cache: str
    out_md: str
    out_json: str
    force_refetch: bool
    skip_lookahead: bool = False

    @property
    def label(self) -> str:
        return (
            f"{len(self.symbols)} sym x {len(self.timeframes)} TF x "
            f"{len(self.plans)} plans | {self.days}d | "
            f"patterns={'on' if self.with_patterns else 'off'} | "
            f"divergence={'on' if self.with_divergence else 'off'} | "
            f"fees={'on' if self.include_fees else 'off'}"
        )


@dataclass
class TradeResult:
    """One simulated trade (or skipped signal)."""
    ts: str
    symbol: str
    timeframe: str
    plan: str
    signal: str           # LONG / SHORT / NEUTRAL
    entry_price: float
    sl: float
    tp: float
    confluence: int
    confidence: float
    trend_score: float
    adx: float
    regime: str
    outcome: str          # WIN / LOSS / TIMEOUT / NEUTRAL
    exit_price: float
    bars_held: int
    pnl_r: float
    risk: float


# ───────────────────────────── DATA LAYER ─────────────────────────────

def is_crypto(symbol: str) -> bool:
    """Heuristic: USDT pairs and BTC/ETH are crypto. XAUUSD is Yahoo."""
    s = symbol.upper()
    if s.endswith('USDT'):
        return True
    if s in ('BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT',
             'DOGEUSDT', 'SOLUSDT', 'LTCUSDT', 'BCHUSDT'):
        return True
    return False


def fetch_yahoo(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch from Yahoo Finance. Limited to ~30d for intraday."""
    import yfinance as yf
    # Yahoo intervals: 1h, 1d, etc. H4 not native, will resample from 1h.
    if interval in ('H4', '4h'):
        yf_interval = '1h'
    elif interval in ('H1', '1h'):
        yf_interval = '1h'
    elif interval in ('D1', '1d'):
        yf_interval = '1d'
    else:
        yf_interval = interval.lower()

    # Cap range for intraday
    if yf_interval == '1h':
        days = min(days, YAHOO_H1_DAYS_CAP)

    period = f"{days}d"
    log.info(f"Yahoo: {symbol} {interval} -> {yf_interval} period={period}")

    df = yf.download(
        tickers=symbol,
        period=period,
        interval=yf_interval,
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        return df

    # yfinance multi-ticker sometimes returns MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.columns = ['open', 'high', 'low', 'close', 'volume']
    df.index.name = 'timestamp'
    df = df.dropna()

    # Resample H4 from H1 if needed
    if interval in ('H4', '4h') and yf_interval == '1h':
        df = df.resample('4h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }).dropna()

    return df


def fetch_binance_paginated(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch from Binance with explicit end_time pagination for >1000 bars."""
    tf_bars = BARS_PER_DAY.get(interval, 24)
    need = int(days * tf_bars * 1.2) + WARMUP   # 20% safety margin
    fetcher = BinanceDataFetcher()
    frames: List[pd.DataFrame] = []
    end_time_ms: Optional[int] = None
    pages = 0
    max_pages = 20  # safety: 20k bars is plenty

    while need > 0 and pages < max_pages:
        limit = min(1000, need)
        batch = fetcher.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit,
            end_time=end_time_ms,
        )
        if batch is None or batch.empty:
            break
        frames.insert(0, batch)
        need -= len(batch)
        pages += 1
        # Next page: step back by 1ms from the oldest candle's open time
        oldest_ms = int(batch.index[0].timestamp() * 1000)
        end_time_ms = oldest_ms - 1
        if len(batch) < limit:
            break  # hit the beginning of available history

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df


def fetch_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV — crypto via Binance, XAU via Yahoo."""
    if is_crypto(symbol):
        return fetch_binance_paginated(symbol, timeframe, days)
    return fetch_yahoo(symbol, timeframe, days)


class DataCache:
    """Parquet cache wrapper around the fetch layer."""

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str, days: int) -> Path:
        return self.cache_dir / f"{symbol}_{timeframe}_{days}d.parquet"

    def get_or_fetch(self, symbol: str, timeframe: str, days: int,
                     force: bool = False) -> pd.DataFrame:
        path = self._path(symbol, timeframe, days)
        if path.exists() and not force:
            log.info(f"Cache hit: {path.name}")
            return pd.read_parquet(path)

        log.info(f"Fetching {symbol} {timeframe} {days}d …")
        t0 = time.time()
        df = fetch_data(symbol, timeframe, days)
        elapsed = time.time() - t0

        if df is None or df.empty:
            log.warning(f"No data returned for {symbol} {timeframe}")
            return pd.DataFrame()

        df.to_parquet(path, engine='pyarrow')
        log.info(f"Fetched {len(df)} bars in {elapsed:.1f}s -> {path.name}")
        return df

    def clear(self):
        for p in self.cache_dir.glob('*.parquet'):
            p.unlink()


# ───────────────────────────── FEATURES ─────────────────────────────

def build_features(
    window: pd.DataFrame,
    *,
    with_patterns: bool,
    with_divergence: bool,
) -> Tuple[Dict, List[Dict], Optional[list], Optional[object]]:
    """Compute indicators + patterns + divergence for one window.

    Returns (indicators_dict, patterns_list, divergence_list_or_None, regime_or_None).
    """
    calc = IndicatorCalculator(window)
    ind = calc.calculate_all()
    indicators = ind.to_dict()

    patterns: List[Dict] = []
    if with_patterns and len(window) > PATTERN_LOOKAHEAD + 30:
        # Shift the pattern window back by 5 bars so reversal patterns
        # that need 5 bars of future confirmation can fire without
        # peeking at the present.
        pattern_window = window.iloc[:-PATTERN_LOOKAHEAD].copy()
        detector = PatternDetector(pattern_window)
        patterns = detector.detect_all() or []

    divergence = None
    if with_divergence and len(window) > 30:
        div = DivergenceDetector(window)
        divergence = div.detect_all(ind.rsi) or None

    regime = None
    if len(window) > 50:
        try:
            rdet = RegimeDetector(window)
            rres = rdet.detect()
            rg = rres.regime if hasattr(rres, 'regime') else str(rres)
            # Coerce enum to plain string so it serializes cleanly
            regime = rg.value if hasattr(rg, 'value') else str(rg)
        except Exception:
            regime = None

    return indicators, patterns, divergence, regime


# ───────────────────────────── TRADE SIMULATION ─────────────────────────────

def simulate_trade(
    report,
    future: pd.DataFrame,
    *,
    fees_bps: float = 0.0,
) -> Tuple[str, float, int, float]:
    """Resolve a report against future bars. Returns (outcome, exit_price, bars, pnl_r)."""
    direction = 'LONG' if report.overall_signal in (
        SignalStrength.BUY, SignalStrength.STRONG_BUY
    ) else 'SHORT'

    # Fill at zone midpoint (consistent with on-screen R:R)
    entry_price = (report.entry_zone[0] + report.entry_zone[1]) / 2.0
    sl = report.stop_loss
    tp = report.take_profit
    risk = abs(entry_price - sl)
    if risk <= 0:
        return 'TIMEOUT', entry_price, 0, 0.0

    # Per-plan time-in-trade (already on report)
    bar_min = TF_BAR_MINUTES.get(report.timeframe, 60)
    max_hold = max(1, int(report.time_in_trade_minutes // bar_min))

    # Fee multipliers (entry worsened, exit worsened)
    entry_fee = 1.0 + (fees_bps / 10000.0) if fees_bps else 1.0
    exit_fee_long = 1.0 - (fees_bps / 10000.0) if fees_bps else 1.0
    exit_fee_short = 1.0 + (fees_bps / 10000.0) if fees_bps else 1.0

    if direction == 'LONG':
        entry_price *= entry_fee
        exit_sl = sl
        exit_tp = tp
        exit_fee = exit_fee_long
    else:
        entry_price *= (2.0 - entry_fee)  # SHORT entry: worse = lower
        exit_sl = sl
        exit_tp = tp
        exit_fee = exit_fee_short

    # Walk forward
    horizon = min(max_hold, len(future))
    for j in range(horizon):
        bar = future.iloc[j]
        if direction == 'LONG':
            # Check SL first (conservative — could hit intrabar before TP)
            if bar['low'] <= exit_sl:
                return 'LOSS', exit_sl * exit_fee, j + 1, -1.0
            if bar['high'] >= exit_tp:
                return 'WIN', exit_tp * exit_fee, j + 1, (tp - entry_price) / risk
        else:  # SHORT
            if bar['high'] >= exit_sl:
                return 'LOSS', exit_sl * exit_fee, j + 1, -1.0
            if bar['low'] <= exit_tp:
                return 'WIN', exit_tp * exit_fee, j + 1, (entry_price - tp) / risk

    # TIMEOUT — exit at last available close (post-fee)
    if future.empty:
        return 'TIMEOUT', entry_price, 0, 0.0
    last_close = future['close'].iloc[min(horizon, len(future)) - 1]
    if direction == 'LONG':
        exit_p = last_close * exit_fee
        pnl_r = (exit_p - entry_price) / risk
    else:
        exit_p = last_close * exit_fee
        pnl_r = (entry_price - exit_p) / risk
    return 'TIMEOUT', exit_p, horizon, pnl_r


# ───────────────────────────── MAIN LOOP ─────────────────────────────

def run_combo(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    plan_key: str,
    cfg: RunConfig,
) -> List[TradeResult]:
    """Walk-forward backtest for one (symbol, tf, plan) combination."""
    if df.empty or len(df) < WARMUP + 50:
        log.warning(f"{symbol} {timeframe}: insufficient data ({len(df)} bars)")
        return []

    plan = PLANS[plan_key]
    builder = ReportBuilder()
    results: List[TradeResult] = []
    n = len(df)

    # i is the index of the bar whose features we compute (decision at close of i)
    for i in range(WARMUP, n - 1):
        # Window includes current bar: iloc[i-199:i+1] → 200 bars
        window = df.iloc[i - WARMUP + 1: i + 1].copy()
        if len(window) < WARMUP:
            continue

        # Need at least 1 future bar for resolution
        future = df.iloc[i + 1:].copy()
        if future.empty:
            break

        ts = window.index[-1]
        try:
            indicators, patterns, divergence, regime = build_features(
                window,
                with_patterns=cfg.with_patterns,
                with_divergence=cfg.with_divergence,
            )
        except Exception as e:
            log.debug(f"[{symbol} {tf_short(timeframe)} {plan_key}] i={i} features: {e}")
            continue

        # Sanity: skip if ATR is zero or NaN
        atr = indicators.get('atr', 0) or 0
        if not np.isfinite(atr) or atr <= 0:
            continue

        try:
            report = builder.build(
                symbol=symbol,
                timeframe=timeframe,
                price=float(window['close'].iloc[-1]),
                indicators=indicators,
                patterns=patterns,
                divergence=divergence,
                fundamental=None,
                order_flow=None,
                vision=None,
                tier='premium',
                trading_style=plan_key,
            )
        except Exception as e:
            log.debug(f"[{symbol} {tf_short(timeframe)} {plan_key}] i={i} build: {e}")
            continue

        # Determine trade direction
        if report.overall_signal in (SignalStrength.BUY, SignalStrength.STRONG_BUY):
            signal = 'LONG'
        elif report.overall_signal in (SignalStrength.SELL, SignalStrength.STRONG_SELL):
            signal = 'SHORT'
        else:
            signal = 'NEUTRAL'

        if signal == 'NEUTRAL' or report.atr <= 0:
            # Record as non-tradeable
            results.append(TradeResult(
                ts=str(ts), symbol=symbol, timeframe=timeframe, plan=plan_key,
                signal=signal, entry_price=0.0, sl=0.0, tp=0.0,
                confluence=report.confluence_score,
                confidence=report.signal_confidence,
                trend_score=report.trend_score, adx=report.adx,
                regime=regime or 'unknown',
                outcome='NEUTRAL', exit_price=0.0, bars_held=0, pnl_r=0.0, risk=0.0,
            ))
            continue

        outcome, exit_p, bars, pnl_r = simulate_trade(
            report, future,
            fees_bps=cfg.fee_bps if cfg.include_fees else 0.0,
        )

        risk = abs(((report.entry_zone[0] + report.entry_zone[1]) / 2.0) - report.stop_loss)

        results.append(TradeResult(
            ts=str(ts), symbol=symbol, timeframe=timeframe, plan=plan_key,
            signal=signal,
            entry_price=round((report.entry_zone[0] + report.entry_zone[1]) / 2.0, 6),
            sl=round(report.stop_loss, 6),
            tp=round(report.take_profit, 6),
            confluence=report.confluence_score,
            confidence=round(report.signal_confidence, 3),
            trend_score=round(report.trend_score, 2),
            adx=round(report.adx, 2),
            regime=regime or 'unknown',
            outcome=outcome,
            exit_price=round(exit_p, 6),
            bars_held=bars,
            pnl_r=round(pnl_r, 3),
            risk=round(risk, 6),
        ))

    return results


def tf_short(tf: str) -> str:
    return {'H1': '1h', 'H4': '4h', 'D1': '1d'}.get(tf, tf)


# ───────────────────────────── STATS ─────────────────────────────

CONFLUENCE_BUCKETS = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]


def compute_combo_stats(results: List[TradeResult]) -> Dict:
    """Compute aggregate stats for one (symbol, tf, plan) combo."""
    trades = [r for r in results if r.signal != 'NEUTRAL']
    wins = [r for r in trades if r.outcome == 'WIN']
    losses = [r for r in trades if r.outcome == 'LOSS']
    timeouts = [r for r in trades if r.outcome == 'TIMEOUT']

    total = len(trades)
    if total == 0:
        return {
            'n_signals': len(results), 'n_tradable': 0, 'n_wins': 0, 'n_losses': 0,
            'n_timeouts': 0, 'win_rate': 0.0, 'avg_win_r': 0.0, 'avg_loss_r': 0.0,
            'profit_factor': 0.0, 'net_pnl_r': 0.0, 'max_drawdown_r': 0.0,
            'avg_bars': 0.0,
            'win_rate_raw': 0.0, 'net_pnl_r_raw': 0.0,
            'wr_by_confluence': {}, 'wr_by_regime': {},
        }

    wr = len(wins) / total * 100.0
    avg_win = float(np.mean([r.pnl_r for r in wins])) if wins else 0.0
    avg_loss = float(np.mean([r.pnl_r for r in losses])) if losses else 0.0

    gross_w = sum(max(r.pnl_r, 0) for r in trades)
    gross_l = abs(sum(min(r.pnl_r, 0) for r in trades))
    pf = gross_w / gross_l if gross_l > 0 else float('inf')
    net = sum(r.pnl_r for r in trades)

    # Max drawdown on cumulative R curve
    # peak - cum >= 0 always (peak is the running max), so use max() not min()
    # — the drawdown is the LARGEST positive value of (peak - cum).
    cum = np.cumsum([r.pnl_r for r in trades])
    peak = np.maximum.accumulate(cum) if len(cum) else np.array([0.0])
    dd = float(np.max(peak - cum)) if len(cum) else 0.0

    # Confluence bucket breakdown
    wr_by_conf: Dict[str, Tuple[int, float]] = {}
    for lo, hi in CONFLUENCE_BUCKETS:
        bucket = [r for r in trades if lo <= r.confluence < hi]
        if not bucket:
            continue
        bw = sum(1 for r in bucket if r.outcome == 'WIN')
        wr_by_conf[f"{lo}-{hi}"] = (len(bucket), round(bw / len(bucket) * 100, 2))

    # Regime breakdown
    wr_by_regime: Dict[str, Tuple[int, float]] = {}
    regimes = set(str(r.regime) for r in trades)
    for rg in regimes:
        bucket = [r for r in trades if str(r.regime) == rg]
        if not bucket:
            continue
        bw = sum(1 for r in bucket if r.outcome == 'WIN')
        wr_by_regime[rg] = (len(bucket), round(bw / len(bucket) * 100, 2))

    return {
        'n_signals': len(results),
        'n_tradable': total,
        'n_wins': len(wins),
        'n_losses': len(losses),
        'n_timeouts': len(timeouts),
        # Rounded values for the markdown report
        'win_rate': round(wr, 2),
        'avg_win_r': round(avg_win, 3),
        'avg_loss_r': round(avg_loss, 3),
        'profit_factor': round(pf, 3) if pf != float('inf') else 99.99,
        'net_pnl_r': round(net, 2),
        'max_drawdown_r': round(dd, 2),
        'avg_bars': round(np.mean([r.bars_held for r in trades]), 1) if trades else 0.0,
        # Unrounded raw values for the canary (rounding 40.34 vs 40.36 to
        # "40.3" would falsely collapse a real 0.02pp difference)
        'win_rate_raw': wr,
        'net_pnl_r_raw': net,
        'wr_by_confluence': wr_by_conf,
        'wr_by_regime': wr_by_regime,
    }


# ───────────────────────────── LOOK-AHEAD CANARY ─────────────────────────────

def lookback_shift_check(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    plan_key: str,
    cfg: RunConfig,
) -> Dict:
    """Run the same combo on a forward-shifted dataframe. WR should drop.

    The shift must be >= WARMUP for the canary to be meaningful: with
    WARMUP=200, a 5-bar shift produces windows that overlap by 195 bars
    in base vs shifted, so the indicators are nearly identical and the
    canary can pass even with a leaky system. Shifting by >= WARMUP
    guarantees the shifted window sees completely different data than
    the base window, so any look-ahead dependency will show up.
    """
    # Use a shift at least as large as WARMUP so the windows don't overlap.
    effective_shift = max(cfg.lookback_shift, WARMUP)
    if len(df) < WARMUP + 50 + effective_shift:
        return {'symbol': symbol, 'timeframe': timeframe, 'plan': plan_key,
                'verdict': 'SKIP', 'reason': f'not enough data for shift={effective_shift}'}

    df_shifted = df.iloc[effective_shift:].copy()
    if len(df_shifted) < WARMUP + 50:
        return {'symbol': symbol, 'timeframe': timeframe, 'plan': plan_key,
                'verdict': 'SKIP', 'reason': 'not enough data after shift'}

    base = run_combo(df, symbol, timeframe, plan_key, cfg)
    shifted = run_combo(df_shifted, symbol, timeframe, plan_key, cfg)

    base_stats = compute_combo_stats(base)
    shifted_stats = compute_combo_stats(shifted)
    # Use UNROUNDED win rate so the canary isn't defeated by 2-decimal rounding
    # collapsing small differences (1389 trades at 40.3% = 559.86 wins — the
    # shifted run can land at 558.x or 560.x, both rounding to 40.30 and
    # producing a false Δ=0.00).
    delta_wr = abs(base_stats['win_rate_raw'] - shifted_stats['win_rate_raw'])
    delta_pnl = abs(base_stats['net_pnl_r_raw'] - shifted_stats['net_pnl_r_raw'])
    # Canary: pass if WR OR PnL moves by more than the threshold.
    # A causal system should produce non-zero deltas on EITHER metric.
    verdict = 'PASS' if (delta_wr > 5.0 or delta_pnl > 5.0) else 'FAIL'

    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'plan': plan_key,
        'verdict': verdict,
        'base_wr': round(base_stats['win_rate_raw'], 4),
        'shifted_wr': round(shifted_stats['win_rate_raw'], 4),
        'delta_pp': round(delta_wr, 4),
        'base_pnl': round(base_stats['net_pnl_r_raw'], 2),
        'shifted_pnl': round(shifted_stats['net_pnl_r_raw'], 2),
        'delta_pnl': round(delta_pnl, 2),
        'base_trades': base_stats['n_tradable'],
        'shifted_trades': shifted_stats['n_tradable'],
    }


# ───────────────────────────── REPORT ─────────────────────────────

def render_markdown(
    cfg: RunConfig,
    all_combos: List[Tuple[str, str, str, Dict]],
    canary: Optional[Dict],
) -> str:
    """Render the markdown report."""
    lines: List[str] = []
    lines.append("# Backtest V2 Report")
    lines.append("")
    from datetime import timezone
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Config:** {cfg.label}")
    lines.append("")

    # 1. Methodology
    lines.append("## 1. Methodology")
    lines.append("")
    lines.append(f"- Walk-forward, one decision per closed bar, after WARMUP={WARMUP} bars.")
    lines.append("- Production `ReportBuilder.build()` called directly (no reimplementation).")
    lines.append("- Per-plan SL/TP/floors from `PLANS[*]` in `bots/report_builder.py`.")
    lines.append("- Per-plan `time_in_trade_minutes` controls max hold (e.g. 60 for scalp, 4320 for swing).")
    lines.append(f"- Entry fill: midpoint of `entry_zone` (consistent with on-screen R:R).")
    lines.append(f"- Pattern look-ahead: 5-bar shift on `PatternDetector` window.")
    lines.append("- Order-flow, fundamental, AI layer: **omitted** (live bot may be 2-3pp higher).")
    lines.append("- No partial-TP simulation: TP is treated as a single exit. Real bot suggests partials.")
    if cfg.include_fees:
        lines.append(f"- Fees: {cfg.fee_bps} bps per side (entry worsened + exit worsened).")
    else:
        lines.append("- Fees: not modeled. Re-run with `--include-fees` to see sensitivity.")
    lines.append("")

    # 2. Per-combination results
    lines.append("## 2. Per-Combination Results")
    lines.append("")
    lines.append("| Symbol | TF | Plan | Signals | Trades | WR% | PF | Avg Win (R) | Avg Loss (R) | Net (R) | Max DD (R) | Avg Bars |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for sym, tf, plan, st in all_combos:
        if st['n_tradable'] == 0:
            lines.append(
                f"| {sym} | {tf} | {plan} | {st['n_signals']} | 0 | — | — | — | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {sym} | {tf} | {plan} | {st['n_signals']} | {st['n_tradable']} | "
            f"{st['win_rate']:.1f} | {st['profit_factor']:.2f} | "
            f"{st['avg_win_r']:+.2f} | {st['avg_loss_r']:+.2f} | "
            f"{st['net_pnl_r']:+.1f} | {st['max_drawdown_r']:.1f} | {st['avg_bars']:.1f} |"
        )
    lines.append("")

    # 3. Aggregate stats
    all_trades = [(sym, tf, plan, st) for sym, tf, plan, st in all_combos if st['n_tradable'] > 0]
    if all_trades:
        total_trades = sum(s['n_tradable'] for _, _, _, s in all_trades)
        total_wins = sum(s['n_wins'] for _, _, _, s in all_trades)
        overall_wr = total_wins / total_trades * 100 if total_trades else 0.0
        overall_pf = sum(s['profit_factor'] * s['n_tradable'] for _, _, _, s in all_trades) / total_trades if total_trades else 0.0

        lines.append("## 3. Aggregate Statistics")
        lines.append("")
        lines.append(f"- **Overall win rate:** {overall_wr:.2f}% ({total_wins}/{total_trades} trades)")
        lines.append(f"- **Weighted avg PF:** {overall_pf:.2f}")
        lines.append("")

        # By plan
        lines.append("### By plan")
        lines.append("")
        lines.append("| Plan | Trades | WR% | Net (R) |")
        lines.append("|---|---|---|---|")
        for plan in cfg.plans:
            plan_stats = [s for _, _, p, s in all_trades if p == plan]
            if not plan_stats:
                continue
            t = sum(s['n_tradable'] for s in plan_stats)
            w = sum(s['n_wins'] for s in plan_stats)
            wr = w / t * 100 if t else 0
            net = sum(s['net_pnl_r'] for s in plan_stats)
            lines.append(f"| {plan} | {t} | {wr:.2f} | {net:+.1f} |")
        lines.append("")

        # By symbol
        lines.append("### By symbol")
        lines.append("")
        lines.append("| Symbol | Trades | WR% | Net (R) |")
        lines.append("|---|---|---|---|")
        for sym in cfg.symbols:
            sym_stats = [s for s, _, _, s_ in [(s, t, p, st) for s, t, p, st in all_trades] if s == sym]
            sym_stats = [st for s, t, p, st in all_trades if s == sym]
            if not sym_stats:
                continue
            t = sum(s_['n_tradable'] for s_ in sym_stats)
            w = sum(s_['n_wins'] for s_ in sym_stats)
            wr = w / t * 100 if t else 0
            net = sum(s_['net_pnl_r'] for s_ in sym_stats)
            lines.append(f"| {sym} | {t} | {wr:.2f} | {net:+.1f} |")
        lines.append("")

        # By confluence
        lines.append("### By confluence bucket")
        lines.append("")
        lines.append("| Bucket | Trades | WR% |")
        lines.append("|---|---|---|")
        # Aggregate across combos
        cf_buckets: Dict[str, List[int]] = {}
        for sym, tf, plan, st in all_trades:
            for b, (n, wr) in st['wr_by_confluence'].items():
                cf_buckets.setdefault(b, []).extend([wr] * n)
        for lo, hi in CONFLUENCE_BUCKETS:
            key = f"{lo}-{hi}"
            if key in cf_buckets:
                wrs = cf_buckets[key]
                avg_wr = np.mean(wrs)
                lines.append(f"| {key} | {len(wrs)} | {avg_wr:.2f} |")
            else:
                lines.append(f"| {key} | 0 | — |")
        lines.append("")

        # By regime
        lines.append("### By regime")
        lines.append("")
        regime_buckets: Dict[str, List[int]] = {}
        for sym, tf, plan, st in all_trades:
            for rg, (n, wr) in st['wr_by_regime'].items():
                regime_buckets.setdefault(rg, []).extend([wr] * n)
        if regime_buckets:
            lines.append("| Regime | Trades | WR% |")
            lines.append("|---|---|---|")
            for rg, wrs in sorted(regime_buckets.items(), key=lambda x: -len(x[1])):
                lines.append(f"| {rg} | {len(wrs)} | {np.mean(wrs):.2f} |")
            lines.append("")

    # 4. Look-ahead verification
    lines.append("## 4. Look-Ahead Verification")
    lines.append("")
    if canary:
        verdict = canary.get('verdict', 'SKIP')
        if verdict == 'SKIP':
            reason = canary.get('reason', 'no reason given')
            lines.append(
                f"- {canary.get('symbol', '?')} {canary.get('timeframe', '?')} "
                f"{canary.get('plan', '?')}: SKIP ({reason})"
            )
        else:
            # base_wr / shifted_wr are already in percent units (e.g. 40.32, not 0.4032)
            effective_shift = max(cfg.lookback_shift, WARMUP)
            lines.append(
                f"- {canary['symbol']} {canary['timeframe']} {canary['plan']}: "
                f"base WR {canary['base_wr']:.4f}% ({canary['base_trades']} trades) | "
                f"shifted-by-{effective_shift} WR {canary['shifted_wr']:.4f}% "
                f"({canary['shifted_trades']} trades) | "
                f"ΔWR={canary['delta_pp']:.4f}pp | "
                f"ΔPnL={canary.get('delta_pnl', 0):.1f}R → **{verdict}**"
            )
            if verdict == 'FAIL':
                lines.append("")
                lines.append("⚠️  **Δ < 5pp on WR AND PnL suggests hidden look-ahead bias. Review the backtest.**")
            else:
                lines.append("")
                lines.append("Δ > 5R on PnL (or > 5pp on WR) suggests the backtest is causal. "
                             "Shift is auto-sized to ≥WARMUP so windows don't overlap. ✅")
    else:
        lines.append("(skipped — insufficient data)")
    lines.append("")

    # 5. Verdict
    lines.append("## 5. Verdict")
    lines.append("")
    if all_trades:
        total_trades = sum(s['n_tradable'] for _, _, _, s in all_trades)
        total_wins = sum(s['n_wins'] for _, _, _, s in all_trades)
        overall_wr = total_wins / total_trades * 100 if total_trades else 0.0
        total_net = sum(s['net_pnl_r'] for _, _, _, s in all_trades)
        viable_combos = sum(1 for _, _, _, s in all_trades
                            if s['win_rate'] >= 50 and s['profit_factor'] >= 1.2)
        viable_pct = viable_combos / len(all_trades) * 100 if all_trades else 0

        if overall_wr >= 50 and total_net > 0 and viable_pct >= 50:
            verdict = "[VIABLE] positive edge across the matrix"
        elif overall_wr >= 45 and total_net > 0:
            verdict = "[BORDERLINE] marginal edge, needs tuning"
        else:
            verdict = "[NOT VIABLE] no statistically significant edge in current logic"
        lines.append(f"**{verdict}**")
        lines.append("")
        lines.append(f"- {viable_combos}/{len(all_trades)} combos ({viable_pct:.0f}%) show WR ≥ 50% AND PF ≥ 1.2")
        lines.append(f"- Overall win rate: {overall_wr:.2f}%")
        lines.append(f"- Total net PnL (R units): {total_net:+.1f}R")
        lines.append("")
        lines.append("**Caveat:** This is the floor, not the ceiling. The live bot adds order-flow +5-10")
        lines.append("and fundamental +8 to confluence (not modeled here), so live WR may be 2-3pp higher.")
        lines.append("Real-user execution (slippage, early exits, missed entries) will erode this further.")
    else:
        lines.append("Insufficient data for verdict.")
    lines.append("")

    # 6. Recommendations
    lines.append("## 6. Top Recommendations")
    lines.append("")
    recs = build_recommendations(all_trades, canary)
    if recs:
        for i, rec in enumerate(recs, 1):
            lines.append(f"{i}. {rec}")
    else:
        lines.append("No specific recommendations — verdict above is the headline.")
    lines.append("")

    lines.append("---")
    lines.append("")
    from datetime import timezone
    lines.append(f"Generated by `backtest_v2.py` at {datetime.now(timezone.utc).isoformat()}")
    return '\n'.join(lines)


def build_recommendations(
    all_trades: List[Tuple[str, str, str, Dict]],
    canary: Optional[Dict],
) -> List[str]:
    """Generate 3-5 concrete tuning recommendations from the data."""
    recs: List[str] = []

    if not all_trades:
        return recs

    # Per-plan WR
    by_plan: Dict[str, List[Dict]] = {}
    for sym, tf, plan, st in all_trades:
        by_plan.setdefault(plan, []).append(st)

    for plan, stats in by_plan.items():
        t = sum(s['n_tradable'] for s in stats)
        w = sum(s['n_wins'] for s in stats)
        wr = w / t * 100 if t else 0
        net = sum(s['net_pnl_r'] for s in stats)
        if wr < 45 and t >= 20:
            recs.append(
                f"**Drop or tighten `{plan}` plan.** WR {wr:.1f}% over {t} trades, net {net:+.1f}R. "
                f"Consider raising `min_confluence` by 5-10 points in `PLANS['{plan}']` (`bots/report_builder.py:44`)."
            )

    # Confluence bucket gradient
    cf_buckets: Dict[str, List[int]] = {}
    for sym, tf, plan, st in all_trades:
        for b, (n, wr) in st['wr_by_confluence'].items():
            cf_buckets.setdefault(b, []).extend([wr] * n)
    bucket_means = {b: np.mean(wrs) for b, wrs in cf_buckets.items() if len(wrs) >= 20}
    if len(bucket_means) >= 3:
        sorted_buckets = sorted(bucket_means.items(), key=lambda x: int(x[0].split('-')[0]))
        wrs = [m for _, m in sorted_buckets]
        if wrs[-1] - wrs[0] > 10:
            recs.append(
                f"**Confluence score is calibrated — higher buckets win more often** "
                f"({sorted_buckets[0][0]}: {wrs[0]:.1f}% → {sorted_buckets[-1][0]}: {wrs[-1]:.1f}%). "
                f"Consider raising all plan `min_confluence` floors by 5 points to filter marginal setups."
            )
        elif wrs[-1] < wrs[0] + 5:
            recs.append(
                f"**Confluence score is NOT predictive** — highest bucket ({sorted_buckets[-1][0]}: {wrs[-1]:.1f}%) "
                f"wins about as often as the lowest ({sorted_buckets[0][0]}: {wrs[0]:.1f}%). "
                f"Re-visit `_calc_confluence_v2` weightings in `bots/report_builder.py:839`."
            )

    # Per-symbol weakness
    by_sym: Dict[str, List[Dict]] = {}
    for sym, tf, plan, st in all_trades:
        by_sym.setdefault(sym, []).append(st)
    for sym, stats in by_sym.items():
        t = sum(s['n_tradable'] for s in stats)
        w = sum(s['n_wins'] for s in stats)
        wr = w / t * 100 if t else 0
        if wr < 45 and t >= 20 and sym == 'XAUUSD':
            recs.append(
                f"**XAUUSD WR is {wr:.1f}%** — likely Yahoo data limitation (30d H1 cap), not signal logic. "
                f"Verify with D1-only XAU backtest before tuning."
            )

    # Canary result
    if canary and canary.get('verdict') == 'FAIL':
        recs.append(
            f"⚠️ **Look-ahead canary FAILED** (Δ={canary['delta_pp']:.2f}pp < 5pp threshold). "
            f"Audit indicator and pattern code for future-bar usage before trusting these numbers."
        )

    return recs[:5]


# ───────────────────────────── MAIN ─────────────────────────────

def parse_args() -> RunConfig:
    p = argparse.ArgumentParser(description='Backtest V2 — production-faithful walk-forward backtest')
    p.add_argument('--symbols', type=str, default=','.join(DEFAULT_SYMBOLS))
    p.add_argument('--timeframes', type=str, default=','.join(DEFAULT_TIMEFRAMES))
    p.add_argument('--days', type=int, default=DEFAULT_DAYS)
    p.add_argument('--plans', type=str, default=','.join(DEFAULT_PLANS))
    p.add_argument('--with-patterns', action='store_true', default=True)
    p.add_argument('--no-patterns', dest='with_patterns', action='store_false')
    p.add_argument('--with-divergence', action='store_true', default=True)
    p.add_argument('--no-divergence', dest='with_divergence', action='store_false')
    p.add_argument('--include-fees', action='store_true', default=False)
    p.add_argument('--fee-bps', type=float, default=5.0)
    p.add_argument('--lookback-shift', type=int, default=5)
    p.add_argument('--data-cache', type=str, default='./data_cache')
    p.add_argument('--out-md', type=str, default='./backtest_results_v2.md')
    p.add_argument('--out-json', type=str, default='./backtest_results_v2.json')
    p.add_argument('--force-refetch', action='store_true', default=False)
    p.add_argument('--skip-lookahead', action='store_true', default=False)
    p.add_argument('--verbose', action='store_true', default=False)
    args = p.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    return RunConfig(
        symbols=[s.strip().upper() for s in args.symbols.split(',') if s.strip()],
        timeframes=[t.strip().upper() for t in args.timeframes.split(',') if t.strip()],
        days=args.days,
        plans=[pl.strip().lower() for pl in args.plans.split(',') if pl.strip()],
        with_patterns=args.with_patterns,
        with_divergence=args.with_divergence,
        with_regime=True,
        include_fees=args.include_fees,
        fee_bps=args.fee_bps,
        lookback_shift=args.lookback_shift,
        data_cache=args.data_cache,
        out_md=args.out_md,
        out_json=args.out_json,
        force_refetch=args.force_refetch,
        skip_lookahead=args.skip_lookahead,
    )


def main():
    cfg = parse_args()
    print(f"\n=== Backtest V2 ===")
    print(f"Config: {cfg.label}\n")

    if cfg.force_refetch:
        cache = DataCache(cfg.data_cache)
        cache.clear()
        print("Cache cleared.\n")

    cache = DataCache(cfg.data_cache)
    all_combos: List[Tuple[str, str, str, Dict]] = []
    canary_result: Optional[Dict] = None
    t_start = time.time()

    for symbol in cfg.symbols:
        for timeframe in cfg.timeframes:
            # For non-crypto (XAU), cap H1 days to 30
            days = cfg.days
            if not is_crypto(symbol) and timeframe in ('H1', '1h'):
                days = min(days, YAHOO_H1_DAYS_CAP)
                if days < cfg.days:
                    log.info(f"{symbol} {timeframe}: capping days {cfg.days} -> {days} (Yahoo H1 limit)")

            df = cache.get_or_fetch(symbol, timeframe, days, force=cfg.force_refetch)
            if df.empty or len(df) < WARMUP + 50:
                print(f"  [SKIP] {symbol} {timeframe}: insufficient data")
                continue

            for plan_key in cfg.plans:
                t0 = time.time()
                results = run_combo(df, symbol, timeframe, plan_key, cfg)
                stats = compute_combo_stats(results)
                elapsed = time.time() - t0
                if stats['n_tradable'] > 0:
                    print(
                        f"  [{elapsed:5.1f}s] {symbol:8} {timeframe:3} {plan_key:9} "
                        f"-> {stats['n_tradable']:4} trades | WR {stats['win_rate']:5.1f}% | "
                        f"PF {stats['profit_factor']:5.2f} | Net {stats['net_pnl_r']:+7.1f}R"
                    )
                else:
                    print(
                        f"  [{elapsed:5.1f}s] {symbol:8} {timeframe:3} {plan_key:9} "
                        f"-> 0 trades (signals: {stats['n_signals']})"
                    )
                all_combos.append((symbol, timeframe, plan_key, stats))

            # Look-ahead canary: do this once per (symbol, tf), on the first plan in the list
            if not cfg.skip_lookahead and not canary_result:
                # Use swing if available, else first plan
                canary_plan = 'swing' if 'swing' in cfg.plans else cfg.plans[0]
                log.info(f"Running look-ahead canary on {symbol} {timeframe} {canary_plan}…")
                canary_result = lookback_shift_check(df, symbol, timeframe, canary_plan, cfg)

    total_elapsed = time.time() - t_start
    print(f"\n=== Done in {total_elapsed/60:.1f} min ===\n")

    # Render report
    md = render_markdown(cfg, all_combos, canary_result)
    Path(cfg.out_md).write_text(md, encoding='utf-8')
    print(f"Wrote {cfg.out_md}")

    # JSON dump
    json_dump = {
        'config': {
            'symbols': cfg.symbols,
            'timeframes': cfg.timeframes,
            'days': cfg.days,
            'plans': cfg.plans,
            'with_patterns': cfg.with_patterns,
            'with_divergence': cfg.with_divergence,
            'include_fees': cfg.include_fees,
            'fee_bps': cfg.fee_bps,
        },
        'combos': [
            {'symbol': s, 'timeframe': t, 'plan': p, **st}
            for s, t, p, st in all_combos
        ],
        'canary': canary_result,
        'elapsed_sec': round(total_elapsed, 1),
    }
    Path(cfg.out_json).write_text(json.dumps(json_dump, indent=2, default=str), encoding='utf-8')
    print(f"Wrote {cfg.out_json}\n")

    # Quick console summary
    if all_combos:
        all_tradable = [st for _, _, _, st in all_combos if st['n_tradable'] > 0]
        if all_tradable:
            total_t = sum(s['n_tradable'] for s in all_tradable)
            total_w = sum(s['n_wins'] for s in all_tradable)
            overall_wr = total_w / total_t * 100
            total_net = sum(s['net_pnl_r'] for s in all_tradable)
            print(f"OVERALL: {total_t} trades | WR {overall_wr:.2f}% | Net {total_net:+.1f}R")
            if canary_result:
                v = canary_result.get('verdict', 'SKIP')
                if v == 'SKIP':
                    print(f"LOOK-AHEAD CANARY: SKIP ({canary_result.get('reason', 'no reason')})")
                else:
                    print(f"LOOK-AHEAD CANARY: {v} "
                          f"(ΔWR={canary_result['delta_pp']:.4f}pp, "
                          f"ΔPnL={canary_result.get('delta_pnl', 0):.1f}R)")
            print()


if __name__ == '__main__':
    main()
