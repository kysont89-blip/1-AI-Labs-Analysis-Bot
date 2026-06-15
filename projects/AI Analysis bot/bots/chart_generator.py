"""
Chart Generator for XOX Analysis Bot
Renders professional dark-theme charts with indicator overlays.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from datetime import datetime
import io
from typing import Tuple, Dict, List

# Dark theme defaults
plt.style.use('dark_background')
DARK_BG = '#0d1117'
GRID_COLOR = '#21262d'
BULL_COLOR = '#3fb950'
BEAR_COLOR = '#f85149'
TEXT_COLOR = '#c9d1d9'
ACCENT_COLOR = '#58a6ff'
VWAP_COLOR = '#d2a8ff'
EMA_COLORS = {
    8: '#ff7b72',
    21: '#79c0ff',
    50: '#56d364',
    200: '#e3b341'
}


class ChartGenerator:
    """Generate professional chart images with indicators."""

    def __init__(self, width: int = 1200, height: int = 800):
        self.width = width
        self.height = height
        self.dpi = 100

    def generate(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        indicators: Dict = None,
        patterns: List[Dict] = None,
        levels: Dict = None,
    ) -> bytes:
        """
        Generate chart image as PNG bytes.

        Args:
            df: OHLCV DataFrame with datetime index
            symbol: Trading pair (e.g., 'BTCUSDT')
            timeframe: Chart timeframe
            indicators: Dict with keys 'ema', 'vwap', 'poc', 'vah', 'val'
            patterns: List of detected patterns for annotation
            levels: Dict with 'support', 'resistance' lists
        """
        fig = plt.figure(figsize=(self.width/self.dpi, self.height/self.dpi),
                        facecolor=DARK_BG)
        gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.05)

        # Main price chart
        ax1 = fig.add_subplot(gs[0])
        ax1.set_facecolor(DARK_BG)

        # Volume chart
        ax2 = fig.add_subplot(gs[1], sharex=ax1)
        ax2.set_facecolor(DARK_BG)

        # RSI chart
        ax3 = fig.add_subplot(gs[2], sharex=ax1)
        ax3.set_facecolor(DARK_BG)

        # Plot candles
        self._plot_candles(ax1, df)

        # Plot EMAs
        if indicators and 'ema' in indicators:
            for period, values in indicators['ema'].items():
                color = EMA_COLORS.get(int(period), '#888888')
                ax1.plot(df.index, values, color=color, linewidth=1.2,
                        label=f'EMA{period}', alpha=0.9)

        # Plot VWAP
        if indicators and 'vwap' in indicators:
            ax1.plot(df.index, indicators['vwap'], color=VWAP_COLOR,
                    linewidth=1.5, label='VWAP', linestyle='--')

        # Plot Volume Profile levels
        if indicators and 'poc' in indicators:
            ax1.axhline(y=indicators['poc'], color='#ffa657', linewidth=1,
                       linestyle='-.', alpha=0.7, label='POC')
        if indicators and 'vah' in indicators:
            ax1.axhline(y=indicators['vah'], color='#3fb950', linewidth=1,
                       linestyle=':', alpha=0.6, label='VAH')
        if indicators and 'val' in indicators:
            ax1.axhline(y=indicators['val'], color='#f85149', linewidth=1,
                       linestyle=':', alpha=0.6, label='VAL')

        # Plot support/resistance levels
        if levels:
            for level in levels.get('support', []):
                ax1.axhline(y=level, color='#238636', linewidth=1.5,
                           linestyle='--', alpha=0.5)
            for level in levels.get('resistance', []):
                ax1.axhline(y=level, color='#da3633', linewidth=1.5,
                           linestyle='--', alpha=0.5)

        # Annotate patterns
        if patterns:
            for pat in patterns:
                self._annotate_pattern(ax1, df, pat)

        # Volume bars
        colors = [BULL_COLOR if df['close'].iloc[i] >= df['open'].iloc[i]
                  else BEAR_COLOR for i in range(len(df))]
        ax2.bar(df.index, df['volume'], color=colors, width=0.7, alpha=0.7)

        # RSI
        if indicators and 'rsi' in indicators:
            rsi = indicators['rsi']
            ax3.plot(df.index, rsi, color=ACCENT_COLOR, linewidth=1.2)
            ax3.axhline(y=70, color='#f85149', linestyle='--', alpha=0.5)
            ax3.axhline(y=30, color='#3fb950', linestyle='--', alpha=0.5)
            ax3.fill_between(df.index, 30, 70, alpha=0.05, color=ACCENT_COLOR)
            ax3.set_ylim(0, 100)

        # Formatting
        self._format_axes(ax1, ax2, ax3, symbol, timeframe)

        # Legend
        ax1.legend(loc='upper left', framealpha=0.8, fontsize=8)

        # Title
        fig.suptitle(f'{symbol} | {timeframe} | {datetime.now().strftime("%Y-%m-%d %H:%M")}',
                     color=TEXT_COLOR, fontsize=14, fontweight='bold',
                     y=0.98)

        # Watermark
        fig.text(0.5, 0.5, 'XOX ANALYSIS BOT',
                 fontsize=40, color='#21262d', alpha=0.15,
                 ha='center', va='center', rotation=30,
                 fontweight='bold', transform=fig.transFigure)

        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor=DARK_BG,
                   edgecolor='none', dpi=self.dpi,
                   bbox_inches='tight', pad_inches=0.2)
        buf.seek(0)
        plt.close(fig)

        return buf.getvalue()

    def _plot_candles(self, ax, df):
        """Plot candlestick chart."""
        width = 0.6
        width2 = 0.05

        for i, (idx, row) in enumerate(df.iterrows()):
            color = BULL_COLOR if row['close'] >= row['open'] else BEAR_COLOR

            # Body
            height = abs(row['close'] - row['open'])
            bottom = min(row['close'], row['open'])
            ax.bar(idx, height, width, bottom=bottom, color=color, alpha=0.9)

            # Wicks
            ax.plot([idx, idx], [row['low'], row['high']],
                   color=color, linewidth=0.8)

    def _annotate_pattern(self, ax, df, pattern):
        """Annotate detected pattern on chart."""
        name = pattern.get('name', 'Pattern')
        start_idx = pattern.get('start', 0)
        end_idx = pattern.get('end', len(df) - 1)
        confidence = pattern.get('confidence', 0)

        if start_idx >= len(df) or end_idx >= len(df):
            return

        y_pos = df['high'].iloc[start_idx:end_idx+1].max()
        y_pos *= 1.005

        color = '#3fb950' if 'Bull' in name or 'bull' in name.lower() else '#f85149'

        ax.annotate(
            f'{name}\n({confidence:.0%})',
            xy=(df.index[end_idx], y_pos),
            fontsize=8,
            color=color,
            fontweight='bold',
            ha='center',
            bbox=dict(boxstyle='round,pad=0.3', facecolor=DARK_BG,
                     edgecolor=color, alpha=0.8)
        )

    def _format_axes(self, ax1, ax2, ax3, symbol, timeframe):
        """Apply consistent dark formatting."""
        for ax in [ax1, ax2, ax3]:
            ax.tick_params(colors=TEXT_COLOR, labelsize=8)
            ax.grid(True, alpha=0.2, color=GRID_COLOR, linestyle='-')
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_color(GRID_COLOR)
                spine.set_linewidth(0.5)

        # Y-axis labels
        ax1.set_ylabel('Price', color=TEXT_COLOR, fontsize=9)
        ax2.set_ylabel('Volume', color=TEXT_COLOR, fontsize=9)
        ax3.set_ylabel('RSI', color=TEXT_COLOR, fontsize=9)

        # Hide x labels on upper charts
        ax1.tick_params(labelbottom=False)
        ax2.tick_params(labelbottom=False)

        # Format x-axis
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=30, ha='right')


# Quick test
def test_chart():
    """Generate a test chart."""
    import random

    # Generate fake OHLCV
    n = 100
    dates = pd.date_range(end=datetime.now(), periods=n, freq='1h')
    base = 67000

    opens = [base + random.gauss(0, 200) for _ in range(n)]
    closes = [o + random.gauss(0, 150) for o in opens]
    highs = [max(o, c) + abs(random.gauss(50, 30)) for o, c in zip(opens, closes)]
    lows = [min(o, c) - abs(random.gauss(50, 30)) for o, c in zip(opens, closes)]
    volumes = [abs(random.gauss(1000, 300)) for _ in range(n)]

    df = pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    }, index=dates)

    # Fake indicators
    ema8 = df['close'].ewm(span=8).mean()
    ema21 = df['close'].ewm(span=21).mean()
    indicators = {
        'ema': {8: ema8, 21: ema21},
        'vwap': df['close'].rolling(20).mean(),
        'poc': df['close'].median(),
        'vah': df['close'].quantile(0.7),
        'val': df['close'].quantile(0.3),
        'rsi': 50 + pd.Series([random.gauss(0, 10) for _ in range(n)]).cumsum().clip(0, 100)
    }

    gen = ChartGenerator()
    img = gen.generate(df, 'BTCUSDT', 'H1', indicators)

    with open('test_chart.png', 'wb') as f:
        f.write(img)

    print(f"Test chart saved: {len(img)} bytes")
    return img


if __name__ == '__main__':
    test_chart()
