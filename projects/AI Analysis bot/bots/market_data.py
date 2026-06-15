"""
Market Data Fetcher for XOX Analysis Bot
Fetches OHLCV from Binance REST API.
"""

import requests
import pandas as pd
import time
from typing import Optional, Dict, List
from datetime import datetime, timedelta


class BinanceDataFetcher:
    """Fetch market data from Binance."""

    BASE_URL = "https://api.binance.com"
    FAPI_URL = "https://fapi.binance.com"  # Futures

    # Supported timeframes (Binance format)
    INTERVALS = {
        # User-friendly → Binance
        'M1': '1m', '1m': '1m',
        'M3': '3m', '3m': '3m',
        'M5': '5m', '5m': '5m',
        'M15': '15m', '15m': '15m',
        'M30': '30m', '30m': '30m',
        'H1': '1h', '1h': '1h',
        'H2': '2h', '2h': '2h',
        'H4': '4h', '4h': '4h',
        'H6': '6h', '6h': '6h',
        'H8': '8h', '8h': '8h',
        'H12': '12h', '12h': '12h',
        'D1': '1d', '1d': '1d',
        'D3': '3d', '3d': '3d',
        'W1': '1w', '1w': '1w',
        'Mo1': '1M', '1M': '1M'
    }

    def __init__(self, use_futures: bool = False):
        self.base_url = self.FAPI_URL if use_futures else self.BASE_URL
        self.session = requests.Session()
        self._last_request_time = 0
        self._min_interval = 0.05  # 20 req/sec max

    def _rate_limit(self):
        """Respect rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 150,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch kline/candlestick data.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            interval: Timeframe (e.g., '1h', '4h')
            limit: Number of candles (max 1000)
            start_time: Start time in milliseconds
            end_time: End time in milliseconds

        Returns:
            DataFrame with columns: open, high, low, close, volume, quote_volume
        """
        self._rate_limit()

        params = {
            'symbol': symbol.upper(),
            'interval': self.INTERVALS.get(interval, interval),
            'limit': min(limit, 1000)
        }
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        endpoint = f"{self.base_url}/api/v3/klines" if self.base_url == self.BASE_URL else f"{self.base_url}/fapi/v1/klines"

        response = self.session.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])

        # Convert types
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'quote_volume']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('open_time', inplace=True)
        df.index.name = 'timestamp'

        return df[numeric_cols]

    def get_ticker(self, symbol: str) -> Dict:
        """Get 24hr ticker stats."""
        self._rate_limit()

        endpoint = f"{self.base_url}/api/v3/ticker/24hr" if self.base_url == self.BASE_URL else f"{self.base_url}/fapi/v1/ticker/24hr"
        response = self.session.get(endpoint, params={'symbol': symbol.upper()}, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_orderbook(self, symbol: str, limit: int = 100) -> Dict:
        """Get order book snapshot."""
        self._rate_limit()

        endpoint = f"{self.base_url}/api/v3/depth" if self.base_url == self.BASE_URL else f"{self.base_url}/fapi/v1/depth"
        response = self.session.get(endpoint, params={'symbol': symbol.upper(), 'limit': limit}, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_price(self, symbol: str) -> float:
        """Get current price."""
        self._rate_limit()

        endpoint = f"{self.base_url}/api/v3/ticker/price" if self.base_url == self.BASE_URL else f"{self.base_url}/fapi/v1/ticker/price"
        response = self.session.get(endpoint, params={'symbol': symbol.upper()}, timeout=10)
        response.raise_for_status()
        data = response.json()
        return float(data['price'])

    def get_all_symbols(self, quote_asset: str = 'USDT') -> List[str]:
        """Get all trading pairs."""
        self._rate_limit()

        endpoint = f"{self.base_url}/api/v3/exchangeInfo" if self.base_url == self.BASE_URL else f"{self.base_url}/fapi/v1/exchangeInfo"
        response = self.session.get(endpoint, timeout=30)
        response.raise_for_status()
        data = response.json()

        symbols = []
        for s in data.get('symbols', []):
            if s['quoteAsset'] == quote_asset and s['status'] == 'TRADING':
                symbols.append(s['symbol'])

        return sorted(symbols)

    def get_multi_timeframe(
        self,
        symbol: str,
        primary_tf: str,
        higher_tf: Optional[str] = None,
        candles: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """Fetch primary + higher timeframe for context."""
        result = {
            'primary': self.get_klines(symbol, primary_tf, candles)
        }

        if higher_tf:
            result['higher'] = self.get_klines(symbol, higher_tf, candles)

        return result


# Quick test
if __name__ == '__main__':
    fetcher = BinanceDataFetcher()

    print("Fetching BTCUSDT 1h...")
    df = fetcher.get_klines('BTCUSDT', '1h', 10)
    print(df.tail())

    print("\nCurrent price:")
    price = fetcher.get_price('BTCUSDT')
    print(f"BTCUSDT: ${price:,.2f}")
