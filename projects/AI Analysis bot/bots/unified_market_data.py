"""
Unified Market Data Fetcher for XOX Analysis Bot
Supports both Crypto (Binance) and Forex/XAU (Yahoo Finance).

Auto-routes based on symbol:
  - Crypto (BTCUSDT, ETHUSDT, etc.) -> Binance
  - Forex (EURUSD, GBPUSD, USDJPY) -> Yahoo Finance
  - Gold (XAUUSD) -> Yahoo Finance
"""

import requests
import pandas as pd
import numpy as np
import time
from typing import Optional, Dict, List
from datetime import datetime, timedelta


class UnifiedDataFetcher:
    """Unified fetcher that auto-routes to correct data source."""

    # Crypto symbols (Binance)
    CRYPTO_PAIRS = {
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AVAXUSDT',
        'BNBUSDT', 'ADAUSDT', 'DOTUSDT', 'MATICUSDT',
        'LINKUSDT', 'LTCUSDT', 'BCHUSDT', 'XRPUSDT'
    }

    # Forex/XAU Yahoo Finance mappings
    YAHOO_SYMBOLS = {
        'XAUUSD': 'GC=F',      # Gold futures
        'EURUSD': 'EURUSD=X',  # EUR/USD
        'GBPUSD': 'GBPUSD=X',  # GBP/USD
        'USDJPY': 'USDJPY=X',  # USD/JPY
        'AUDUSD': 'AUDUSD=X',  # AUD/USD
        'USDCAD': 'CAD=X',     # USD/CAD (inverse)
    }

    # Yahoo timeframe mappings
    YAHOO_INTERVALS = {
        'M5': '5m',
        'M15': '15m',
        'M30': '30m',
        'H1': '1h',
        'H2': '1h',   # Will resample
        'H4': '1h',   # Will resample
        'D1': '1d',
        'W1': '1wk',
    }

    def __init__(self):
        self.binance = BinanceDataFetcher()
        self.session = requests.Session()
        self._last_request_time = 0
        self._min_interval = 0.05

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def is_crypto(self, symbol: str) -> bool:
        """Check if symbol is a crypto pair."""
        sym = symbol.upper()
        return sym in self.CRYPTO_PAIRS or sym.endswith('USDT')

    def get_klines(self, symbol: str, interval: str, limit: int = 150) -> pd.DataFrame:
        """Fetch klines - auto-routes to correct source."""
        if self.is_crypto(symbol):
            return self.binance.get_klines(symbol, interval, limit)
        else:
            return self._fetch_yahoo(symbol, interval, limit)

    def get_price(self, symbol: str) -> float:
        """Get current price."""
        if self.is_crypto(symbol):
            return self.binance.get_price(symbol)
        else:
            return self._fetch_yahoo_price(symbol)

    def _fetch_yahoo(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        """Fetch OHLCV from Yahoo Finance."""
        self._rate_limit()

        yahoo_sym = self.YAHOO_SYMBOLS.get(symbol.upper(), symbol.upper())
        yahoo_interval = self.YAHOO_INTERVALS.get(interval, '1h')

        # Determine period based on interval and limit
        if yahoo_interval in ['5m', '15m', '30m']:
            period = '5d'  # Yahoo limits intraday data
        elif yahoo_interval == '1h':
            period = '30d'
        else:
            period = '1y'

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
            params = {
                'interval': yahoo_interval,
                'range': period,
                'includeAdjustedClose': 'false'
            }
            headers = {'User-Agent': 'Mozilla/5.0'}

            response = self.session.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()

            result = data.get('chart', {}).get('result', [{}])[0]
            timestamps = result.get('timestamp', [])
            ohlcv = result.get('indicators', {}).get('quote', [{}])[0]

            if not timestamps or not ohlcv:
                return pd.DataFrame()

            df = pd.DataFrame({
                'open': ohlcv.get('open', []),
                'high': ohlcv.get('high', []),
                'low': ohlcv.get('low', []),
                'close': ohlcv.get('close', []),
                'volume': ohlcv.get('volume', []),
                'quote_volume': ohlcv.get('volume', [])  # Approximate
            })

            df['timestamp'] = pd.to_datetime(timestamps, unit='s')
            df.set_index('timestamp', inplace=True)

            # Drop NaN rows
            df = df.dropna()

            # Resample if needed (H2, H4 from 1h)
            if interval == 'H2':
                df = df.resample('2h').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum',
                    'quote_volume': 'sum'
                }).dropna()
            elif interval == 'H4':
                df = df.resample('4h').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum',
                    'quote_volume': 'sum'
                }).dropna()

            # Return last N candles
            return df.tail(limit)

        except Exception as e:
            print(f"[Yahoo] Error fetching {symbol}: {e}")
            return pd.DataFrame()

    def _fetch_yahoo_price(self, symbol: str) -> float:
        """Get current price from Yahoo Finance."""
        self._rate_limit()

        yahoo_sym = self.YAHOO_SYMBOLS.get(symbol.upper(), symbol.upper())

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
            params = {'interval': '1d', 'range': '1d'}
            headers = {'User-Agent': 'Mozilla/5.0'}

            response = self.session.get(url, params=params, headers=headers, timeout=10)
            data = response.json()

            result = data.get('chart', {}).get('result', [{}])[0]
            closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])

            return float(closes[-1]) if closes else 0.0

        except Exception as e:
            print(f"[Yahoo] Error getting price for {symbol}: {e}")
            return 0.0


# Import Binance fetcher
from market_data import BinanceDataFetcher


# Quick test
if __name__ == '__main__':
    fetcher = UnifiedDataFetcher()

    # Test crypto
    print("=== BTCUSDT (Crypto) ===")
    df = fetcher.get_klines('BTCUSDT', 'H1', 10)
    print(f"Shape: {df.shape}")
    print(df.tail(3))

    # Test forex
    print("\n=== EURUSD (Forex) ===")
    df = fetcher.get_klines('EURUSD', 'H1', 10)
    print(f"Shape: {df.shape}")
    print(df.tail(3))

    # Test gold
    print("\n=== XAUUSD (Gold) ===")
    df = fetcher.get_klines('XAUUSD', 'H1', 10)
    print(f"Shape: {df.shape}")
    print(df.tail(3))

    # Test price
    print(f"\nBTC price: {fetcher.get_price('BTCUSDT')}")
    print(f"EURUSD price: {fetcher.get_price('EURUSD')}")
    print(f"XAUUSD price: {fetcher.get_price('XAUUSD')}")
