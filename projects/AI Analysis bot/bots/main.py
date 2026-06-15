"""
XOX Analysis Bot — Test Entry Point

Runs a one-shot analysis of BTCUSDT on H1 to verify the engine.
"""

import asyncio
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def test_analysis():
    """Run a test analysis on BTCUSDT H1."""
    from market_data import BinanceDataFetcher
    from indicators import IndicatorCalculator
    from pattern_detector import PatternDetector
    from chart_generator import ChartGenerator
    from report_builder import ReportBuilder

    async def _test():
        fetcher = BinanceDataFetcher()
        print("Fetching BTCUSDT 1h...")
        df = fetcher.get_klines('BTCUSDT', '1h', limit=100)

        print(f"Got {len(df)} candles")
        print(df.tail())

        calc = IndicatorCalculator(df)
        indicators = calc.calculate_all()
        print(f"\nTrend Score: {indicators.trend_score:+.0f}")
        print(f"ATR: {indicators.atr_value:.2f}")
        print(f"RSI: {indicators.rsi.iloc[-1]:.1f}")
        print(f"ADX: {indicators.adx_value:.1f}")

        detector = PatternDetector(df)
        patterns = detector.detect_all()
        print(f"\nPatterns found: {len(patterns)}")
        for p in patterns[:5]:
            print(f"  - {p['name']} ({p['direction']}): {p['confidence']:.0%}")

        indicator_dict = {
            'ema': indicators.ema,
            'vwap': indicators.vwap,
            'poc': indicators.volume_profile.get('poc'),
            'vah': indicators.volume_profile.get('vah'),
            'val': indicators.volume_profile.get('val'),
            'rsi': indicators.rsi,
        }
        levels = {
            'support': indicators.support_levels,
            'resistance': indicators.resistance_levels,
        }

        chart_gen = ChartGenerator()
        chart_bytes = chart_gen.generate(df, 'BTCUSDT', 'H1', indicator_dict, patterns, levels)

        with open('test_output.png', 'wb') as f:
            f.write(chart_bytes)
        print(f"\nChart saved: test_output.png ({len(chart_bytes)} bytes)")

        builder = ReportBuilder()
        report = builder.build('BTCUSDT', 'H1', df['close'].iloc[-1],
                               indicators.to_dict(), patterns)
        print("\n=== REPORT ===")
        print(report.to_telegram_text(tier="premium"))

    asyncio.run(_test())


if __name__ == '__main__':
    test_analysis()
