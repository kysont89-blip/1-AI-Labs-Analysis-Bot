"""Targeted verification: re-backtest H1 ADX>=30 with the NEW scalp sizing
(sl=1.0*ATR, tp=1.5*ATR, 60min hold) and compare to the old swing sizing
(sl=2.5*ATR, tp=5.0*ATR, 3-day hold) which lost at every TP setting.

Why: the 90d sweep showed the old swing plan loses 100% of the time on H1
strong-ADX trades. After the regime-downgrade change in
ReportBuilder.build(), the bot now uses the scalp plan for those trades.
This script measures: does the new plan actually print positive R?

Sister script: bots/backtest_sweep.py (does the full TP×ATR sweep).
This one reuses simulate_trade but with a single (sl, tp) pair to keep
runtime short — we only need a yes/no answer for the strong-ADX subset.

Run:  py bots/backtest_h1_strong_check.py
"""
from __future__ import annotations

import sys
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

import sys
from pathlib import Path as _P
_THIS_DIR = str(_P(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
# Reuse the same data-pipeline as backtest_sweep.py
from backtest_sweep import (
    fetch_1m_paged, resample_ohlcv, simulate_trade,
    FEE_PER_SIDE,
)
# precompute_indicators lives in this file too — re-import to avoid the
# 30-bar warmup duplication.
import backtest_sweep

import pandas as pd
import numpy as np
from pathlib import Path
import time

SYMBOL = 'BTCUSDT'
TIMEFRAME = 'H1'
RESAMPLE_RULE = '1h'  # pandas offset alias (lowercase 'h' since pandas 2.x)
DAYS = 90
WARMUP_BARS = 30  # matches the 30-bar warmup inside precompute_indicators

# Three scenarios to compare head-to-head
OLD_PLAN = {
    'name': 'OLD swing (loses on H1 strong-ADX)',
    'sl_atr_mult': 2.5,
    'tp_atr_mult': 5.0,
    'min_trend_score': 50,
    'min_adx': 20,
    'time_stop_bars': 24,  # 24 * 1H = 24h
}
# Option A: just shrink TP (the user's hypothesis: lower TP = higher WR)
TP_ONLY_FIX = {
    'name': 'TP-only (lower TP, same SL+time-stop)',
    'sl_atr_mult': 2.5,
    'tp_atr_mult': 1.75,  # the optimal TP from the 90d sweep
    'min_trend_score': 50,
    'min_adx': 20,
    'time_stop_bars': 24,
}
# Option B: scalp plan (current code change)
NEW_PLAN = {
    'name': 'NEW scalp (downgraded on H1 strong-ADX)',
    'sl_atr_mult': 1.0,
    'tp_atr_mult': 1.5,
    'min_trend_score': 30,  # scalp plan floors
    'min_adx': 20,
    'time_stop_bars': 4,
}
# Option C: tight swing — keep the patient 24h time-stop but tighten BOTH SL and TP
TIGHT_SWING = {
    'name': 'Tight swing (sl=1.5, tp=2.0, time=24h)',
    'sl_atr_mult': 1.5,
    'tp_atr_mult': 2.0,
    'min_trend_score': 50,
    'min_adx': 20,
    'time_stop_bars': 24,
}


def run_scenario(plan, df_h1, ind):
    """Run the simulation with one (sl, tp) pair on the H1 strong-ADX subset.

    Mirrors the loop in bots/backtest_sweep.run_sweep but restricted to
    ADX>=30 trades so we get a clean head-to-head between the old swing
    plan and the new scalp plan on the same trade population.
    """
    trades = []
    atr_arr = ind['atr'].values
    adx_arr = ind['adx'].values
    trend_arr = ind['trend_score'].values

    n = len(df_h1)
    for i in range(WARMUP_BARS, n - plan['time_stop_bars'] - 1):
        if atr_arr[i] != atr_arr[i] or adx_arr[i] != adx_arr[i] or trend_arr[i] != trend_arr[i]:
            continue
        adx = adx_arr[i]
        if adx < 30:  # only strong-ADX trades — this is the regime filter
            continue
        trend = trend_arr[i]
        if abs(trend) < plan['min_trend_score']:
            continue

        direction = 'long' if trend > 0 else 'short'
        entry_price = df_h1['close'].iloc[i + 1]  # next bar "open" proxy
        atr_at_signal = atr_arr[i]

        # Compute absolute SL/TP from ATR multiples
        if direction == 'long':
            sl_price = entry_price - atr_at_signal * plan['sl_atr_mult']
            tp_price = entry_price + atr_at_signal * plan['tp_atr_mult']
        else:
            sl_price = entry_price + atr_at_signal * plan['sl_atr_mult']
            tp_price = entry_price - atr_at_signal * plan['tp_atr_mult']

        outcome = simulate_trade(
            df=df_h1,
            signal_idx=i,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            time_stop_bars=plan['time_stop_bars'],
        )
        if outcome is None:
            continue
        trades.append({
            'entry_idx': i + 1,
            'adx': adx,
            'trend_score': trend,
            'r_multiple': outcome.r_multiple,
            'exit_reason': outcome.exit_reason,
        })
    return trades


def summarize(trades, plan):
    if not trades:
        return None
    n = len(trades)
    rs = np.array([t['r_multiple'] for t in trades])
    wins = rs > 0
    wr = wins.sum() / n
    avg_r = rs.mean()
    std_r = rs.std(ddof=1) if n > 1 else 0.0
    sharpe = avg_r / std_r if std_r > 0 else 0.0
    gross_win = rs[wins].sum() if wins.any() else 0.0
    gross_loss = abs(rs[~wins].sum()) if (~wins).any() else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf') if gross_win > 0 else 0.0
    reasons = pd.Series([t['exit_reason'] for t in trades]).value_counts(normalize=True).mul(100).round(1)
    return {
        'plan': plan['name'],
        'n': n,
        'wr': wr * 100,
        'avg_r': avg_r,
        'std_r': std_r,
        'pf': pf,
        'sharpe': sharpe,
        'pct_tp': reasons.get('tp', 0),
        'pct_sl': reasons.get('sl', 0),
        'pct_time': reasons.get('time', 0),
    }


def print_summary(s):
    if s is None:
        print("  No trades.")
        return
    print(f"  Plan: {s['plan']}")
    print(f"    n={s['n']}  WR={s['wr']:.1f}%  avg-R={s['avg_r']:+.3f}  "
          f"PF={s['pf']:.2f}  Sharpe={s['sharpe']:+.3f}")
    print(f"    exits: TP={s['pct_tp']}%  SL={s['pct_sl']}%  time={s['pct_time']}%")


def main():
    t0 = time.time()
    print(f"Fetching {DAYS}d of {SYMBOL} 1m candles...")
    from market_data import BinanceDataFetcher
    fetcher = BinanceDataFetcher()
    df_1m = fetch_1m_paged(fetcher, SYMBOL, days=DAYS)
    print(f"  Got {len(df_1m)} 1m bars in {time.time()-t0:.1f}s")

    print(f"Resampling to {TIMEFRAME} (preserving high/low)...")
    df_h1 = resample_ohlcv(df_1m, RESAMPLE_RULE)
    print(f"  Got {len(df_h1)} {TIMEFRAME} bars")

    print("Precomputing indicators (no-lookahead)...")
    t1 = time.time()
    ind = backtest_sweep.precompute_indicators(df_h1)
    print(f"  Done in {time.time()-t1:.1f}s")

    print(f"\n{'='*60}")
    print(f"H1 ADX>=30 head-to-head: 3 scenarios")
    print(f"{'='*60}")

    print("\n[OLD swing plan — sl=2.5*ATR, tp=5.0*ATR, time=24h]")
    old_trades = run_scenario(OLD_PLAN, df_h1, ind)
    print_summary(summarize(old_trades, OLD_PLAN))

    print("\n[TP-only fix — sl=2.5*ATR, tp=1.75*ATR, time=24h]")
    tp_only_trades = run_scenario(TP_ONLY_FIX, df_h1, ind)
    print_summary(summarize(tp_only_trades, TP_ONLY_FIX))

    print("\n[NEW scalp plan — sl=1.0*ATR, tp=1.5*ATR, time=4h]")
    new_trades = run_scenario(NEW_PLAN, df_h1, ind)
    print_summary(summarize(new_trades, NEW_PLAN))

    print("\n[TIGHT swing — sl=1.5*ATR, tp=2.0*ATR, time=24h]")
    tight_trades = run_scenario(TIGHT_SWING, df_h1, ind)
    print_summary(summarize(tight_trades, TIGHT_SWING))

    # Side-by-side delta
    o = summarize(old_trades, OLD_PLAN)
    t = summarize(tp_only_trades, TP_ONLY_FIX)
    n = summarize(new_trades, NEW_PLAN)
    ts = summarize(tight_trades, TIGHT_SWING)
    if o and n:
        print(f"\nDelta (new - old):")
        print(f"  WR:    {o['wr']:.1f}% → {n['wr']:.1f}%  (delta {n['wr']-o['wr']:+.1f}pp)")
        print(f"  avg-R: {o['avg_r']:+.3f} → {n['avg_r']:+.3f}  (delta {n['avg_r']-o['avg_r']:+.3f})")
        print(f"  PF:    {o['pf']:.2f} → {n['pf']:.2f}")
        if t:
            print(f"\nDelta (TP-only - old):")
            print(f"  WR:    {o['wr']:.1f}% → {t['wr']:.1f}%  (delta {t['wr']-o['wr']:+.1f}pp)")
            print(f"  avg-R: {o['avg_r']:+.3f} → {t['avg_r']:+.3f}  (delta {t['avg_r']-o['avg_r']:+.3f})")
            print(f"  PF:    {o['pf']:.2f} → {t['pf']:.2f}")
        if n['avg_r'] > o['avg_r'] or (t and t['avg_r'] > o['avg_r']):
            winner = 'NEW scalp' if n['avg_r'] > o['avg_r'] else 'TP-only'
            best = n if n['avg_r'] > o['avg_r'] else t
            print(f"\n  Best fix: {winner} (avg-R={best['avg_r']:+.3f}, PF={best['pf']:.2f})")
        if ts:
            print(f"\nTight swing (sl=1.5, tp=2.0, time=24h):")
            print(f"  WR:    {o['wr']:.1f}% → {ts['wr']:.1f}%  (delta {ts['wr']-o['wr']:+.1f}pp)")
            print(f"  avg-R: {o['avg_r']:+.3f} → {ts['avg_r']:+.3f}  (delta {ts['avg_r']-o['avg_r']:+.3f})")
            print(f"  PF:    {o['pf']:.2f} → {ts['pf']:.2f}")
            if ts['avg_r'] > o['avg_r']:
                print(f"\n  ★ BEST: Tight swing (sl=1.5, tp=2.0) is the best fix so far.")
            else:
                print(f"\n  H1 strong-ADX regime is structurally hard. May need to skip it entirely.")


if __name__ == '__main__':
    main()
