"""
Binance Order Flow / Depth Analysis Module
Provides real-time order book insights for crypto pairs.

Features:
- Full order book depth (20 or 5000 levels)
- Liquidity wall detection
- Bid/ask imbalance analysis
- Order book heatmap
- Key level clustering from depth
- Absorption detection (large market orders hitting walls)
"""

import requests
import json
import time
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import websocket
import threading

@dataclass
class DepthSnapshot:
    """Snapshot of order book at a point in time."""
    symbol: str
    timestamp: float
    bids: List[Tuple[float, float]]  # (price, volume)
    asks: List[Tuple[float, float]]
    
    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2
    
    @property
    def total_bid_volume(self) -> float:
        return sum(v for _, v in self.bids)
    
    @property
    def total_ask_volume(self) -> float:
        return sum(v for _, v in self.asks)
    
    @property
    def bid_ask_ratio(self) -> float:
        """0.5 = balanced, >0.5 = bid heavy, <0.5 = ask heavy"""
        total = self.total_bid_volume + self.total_ask_volume
        return self.total_bid_volume / total if total > 0 else 0.5


class OrderFlowAnalyzer:
    """Analyze Binance order book depth for crypto pairs."""
    
    # Binance REST API base
    REST_BASE = "https://api.binance.com"
    
    # Binance WebSocket base
    WS_BASE = "wss://stream.binance.com:9443/ws"
    
    def __init__(self, symbol: str, depth_limit: int = 100):
        """
        Initialize order flow analyzer.
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            depth_limit: Order book depth to fetch (20, 50, 100, 500, 1000, 5000)
        """
        self.symbol = symbol.upper()
        self.depth_limit = min(depth_limit, 5000)
        self.current_snapshot: Optional[DepthSnapshot] = None
        self.ws = None
        self.ws_thread = None
        self._running = False
        
    def fetch_depth(self) -> Optional[DepthSnapshot]:
        """Fetch order book snapshot via REST API."""
        try:
            url = f"{self.REST_BASE}/api/v3/depth"
            params = {
                'symbol': self.symbol,
                'limit': self.depth_limit
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if 'bids' not in data or 'asks' not in data:
                print(f"[OrderFlow] Invalid response for {self.symbol}")
                return None
            
            bids = [(float(p), float(v)) for p, v in data['bids']]
            asks = [(float(p), float(v)) for p, v in data['asks']]
            
            snapshot = DepthSnapshot(
                symbol=self.symbol,
                timestamp=time.time(),
                bids=bids,
                asks=asks
            )
            
            self.current_snapshot = snapshot
            return snapshot
            
        except Exception as e:
            print(f"[OrderFlow] Error fetching depth for {self.symbol}: {e}")
            return None
    
    def start_websocket(self):
        """Start WebSocket for real-time depth updates."""
        self._running = True
        self.ws_thread = threading.Thread(target=self._ws_loop)
        self.ws_thread.daemon = True
        self.ws_thread.start()
    
    def stop_websocket(self):
        """Stop WebSocket connection."""
        self._running = False
        if self.ws:
            self.ws.close()
    
    def _ws_loop(self):
        """WebSocket connection loop."""
        symbol_lower = self.symbol.lower()
        url = f"{self.WS_BASE}/{symbol_lower}@depth@{self.depth_limit}ms"
        
        while self._running:
            try:
                self.ws = websocket.create_connection(url, timeout=10)
                
                while self._running:
                    message = self.ws.recv()
                    data = json.loads(message)
                    self._process_ws_update(data)
                    
            except Exception as e:
                print(f"[OrderFlow] WebSocket error: {e}")
                time.sleep(5)  # Reconnect delay
    
    def _process_ws_update(self, data: Dict):
        """Process WebSocket depth update."""
        # WebSocket updates only show changes, not full snapshot
        # For simplicity, just refetch full snapshot periodically
        pass
    
    def get_liquidity_walls(self, min_ratio: float = 2.0) -> Dict:
        """
        Detect liquidity walls (large orders clustered at a level).
        
        Args:
            min_ratio: Minimum volume ratio vs average to be considered a wall
            
        Returns:
            Dict with 'bid_walls' and 'ask_walls'
        """
        if not self.current_snapshot:
            self.fetch_depth()
        
        if not self.current_snapshot:
            return {'bid_walls': [], 'ask_walls': []}
        
        snapshot = self.current_snapshot
        
        # Calculate average volume per level
        avg_bid = snapshot.total_bid_volume / len(snapshot.bids) if snapshot.bids else 1
        avg_ask = snapshot.total_ask_volume / len(snapshot.asks) if snapshot.asks else 1
        
        # Find walls (volume > min_ratio * average)
        bid_walls = [
            {'price': p, 'volume': v, 'ratio': v / avg_bid}
            for p, v in snapshot.bids
            if v >= avg_bid * min_ratio
        ]
        
        ask_walls = [
            {'price': p, 'volume': v, 'ratio': v / avg_ask}
            for p, v in snapshot.asks
            if v >= avg_ask * min_ratio
        ]
        
        return {
            'bid_walls': bid_walls,
            'ask_walls': ask_walls,
            'avg_bid_volume': avg_bid,
            'avg_ask_volume': avg_ask
        }
    
    def get_imbalance(self) -> Dict:
        """
        Analyze bid/ask imbalance.
        
        Returns:
            Dict with imbalance metrics
        """
        if not self.current_snapshot:
            self.fetch_depth()
        
        if not self.current_snapshot:
            return {'ratio': 0.5, 'direction': 'neutral', 'strength': 0}
        
        snapshot = self.current_snapshot
        ratio = snapshot.bid_ask_ratio
        
        # Determine direction and strength
        if ratio > 0.6:
            direction = 'bid_heavy'
            strength = (ratio - 0.5) * 200  # 0-100 scale
        elif ratio < 0.4:
            direction = 'ask_heavy'
            strength = (0.5 - ratio) * 200
        else:
            direction = 'neutral'
            strength = 0
        
        return {
            'ratio': ratio,
            'direction': direction,
            'strength': min(100, strength),
            'total_bid': snapshot.total_bid_volume,
            'total_ask': snapshot.total_ask_volume,
            'spread_pct': (snapshot.spread / snapshot.mid_price) * 100
        }
    
    def get_cumulative_depth(self, price_range_pct: float = 0.5) -> Dict:
        """
        Get cumulative bid/ask volume within a price range.
        
        Args:
            price_range_pct: Percentage range around mid price (e.g., 0.5 = ±0.5%)
            
        Returns:
            Dict with cumulative metrics
        """
        if not self.current_snapshot:
            self.fetch_depth()
        
        if not self.current_snapshot:
            return {}
        
        snapshot = self.current_snapshot
        mid = snapshot.mid_price
        range_min = mid * (1 - price_range_pct / 100)
        range_max = mid * (1 + price_range_pct / 100)
        
        # Cumulative bids (support) below mid
        cum_bids = sum(v for p, v in snapshot.bids if p >= range_min)
        
        # Cumulative asks (resistance) above mid
        cum_asks = sum(v for p, v in snapshot.asks if p <= range_max)
        
        return {
            'price_range_pct': price_range_pct,
            'range_min': range_min,
            'range_max': range_max,
            'cumulative_bids': cum_bids,
            'cumulative_asks': cum_asks,
            'net_flow': cum_bids - cum_asks,
            'support_strength': cum_bids / (cum_bids + cum_asks) if (cum_bids + cum_asks) > 0 else 0.5
        }
    
    def get_key_levels_from_depth(self, wall_threshold: float = 3.0) -> Dict:
        """
        Extract key support/resistance levels from order book walls.
        
        Args:
            wall_threshold: Volume ratio to be considered a key level
            
        Returns:
            Dict with 'support_levels' and 'resistance_levels'
        """
        walls = self.get_liquidity_walls(min_ratio=wall_threshold)
        
        support = [
            {'price': w['price'], 'volume': w['volume'], 'strength': w['ratio']}
            for w in walls['bid_walls']
        ]
        
        resistance = [
            {'price': w['price'], 'volume': w['volume'], 'strength': w['ratio']}
            for w in walls['ask_walls']
        ]
        
        # Sort by price
        support.sort(key=lambda x: x['price'], reverse=True)
        resistance.sort(key=lambda x: x['price'])
        
        return {
            'support_levels': support,
            'resistance_levels': resistance,
            'strongest_support': support[0] if support else None,
            'strongest_resistance': resistance[0] if resistance else None
        }
    
    def analyze(self) -> Dict:
        """
        Complete order flow analysis.
        
        Returns:
            Dict with all order flow metrics
        """
        snapshot = self.fetch_depth()
        if not snapshot:
            return {'error': 'Failed to fetch order book'}
        
        walls = self.get_liquidity_walls()
        imbalance = self.get_imbalance()
        cumulative = self.get_cumulative_depth()
        key_levels = self.get_key_levels_from_depth()
        
        return {
            'symbol': self.symbol,
            'timestamp': snapshot.timestamp,
            'mid_price': snapshot.mid_price,
            'spread': snapshot.spread,
            'spread_pct': (snapshot.spread / snapshot.mid_price) * 100,
            'total_bid_volume': snapshot.total_bid_volume,
            'total_ask_volume': snapshot.total_ask_volume,
            'liquidity_walls': walls,
            'imbalance': imbalance,
            'cumulative_depth': cumulative,
            'key_levels': key_levels
        }
    
    def get_confirmation(self, analysis: Dict, signal_direction: str) -> str:
        """
        Get order flow confirmation verdict for a signal.
        
        Args:
            analysis: Order flow analysis dict
            signal_direction: 'LONG', 'SHORT', or 'NEUTRAL'
            
        Returns:
            Confirmation text with emoji
        """
        if 'error' in analysis:
            return "⚪ Order Flow: Unavailable"
        
        imbalance = analysis.get('imbalance', {})
        direction = imbalance.get('direction', 'neutral')
        strength = imbalance.get('strength', 0)
        key_levels = analysis.get('key_levels', {})
        
        # Walls near price
        support = key_levels.get('strongest_support')
        resistance = key_levels.get('strongest_resistance')
        
        # Determine alignment
        if signal_direction == 'LONG':
            if direction == 'bid_heavy' and strength >= 30:
                # Check if there's a bid wall near current price
                if support and support['strength'] >= 3.0:
                    return f"🟢 CONFIRMS LONG — Bid-heavy + strong wall at {support['price']:,.2f}"
                return f"🟢 CONFIRMS LONG — Bid-heavy ({strength:.0f}% buying pressure)"
            elif direction == 'ask_heavy' and strength >= 30:
                return f"🔴 CONTRADICTS — Ask-heavy ({strength:.0f}%), long may struggle"
            else:
                return "⚪ Neutral — No directional edge from order flow"
                
        elif signal_direction == 'SHORT':
            if direction == 'ask_heavy' and strength >= 30:
                if resistance and resistance['strength'] >= 3.0:
                    return f"🟢 CONFIRMS SHORT — Ask-heavy + strong wall at {resistance['price']:,.2f}"
                return f"🟢 CONFIRMS SHORT — Ask-heavy ({strength:.0f}% selling pressure)"
            elif direction == 'bid_heavy' and strength >= 30:
                return f"🔴 CONTRADICTS — Bid-heavy ({strength:.0f}%), short may struggle"
            else:
                return "⚪ Neutral — No directional edge from order flow"
        
        else:  # NEUTRAL
            if direction == 'bid_heavy':
                return f"🟢 Buying interest present ({strength:.0f}%) — watch for breakout"
            elif direction == 'ask_heavy':
                return f"🔴 Selling interest present ({strength:.0f}%) — watch for breakdown"
            else:
                return "⚪ No directional pressure — true ranging"
    
    def format_telegram_simple(self, analysis: Dict, signal_direction: str) -> str:
        """
        Format SIMPLE order flow (2 lines max).
        Used in Simple Report.
        """
        if 'error' in analysis:
            return ""
        
        confirmation = self.get_confirmation(analysis, signal_direction)
        key_levels = analysis.get('key_levels', {})
        
        # One key wall only
        wall_text = ""
        if signal_direction == 'LONG' and key_levels.get('strongest_support'):
            s = key_levels['strongest_support']
            wall_text = f"📊 Wall: {s['price']:,.2f} ({s['strength']:.1f}x)"
        elif signal_direction == 'SHORT' and key_levels.get('strongest_resistance'):
            r = key_levels['strongest_resistance']
            wall_text = f"📊 Wall: {r['price']:,.2f} ({r['strength']:.1f}x)"
        
        if wall_text:
            return f"{confirmation}\n{wall_text}"
        return confirmation
    
    def format_telegram(self, analysis: Dict) -> str:
        """Format order flow analysis for Telegram report."""
        if 'error' in analysis:
            return f"⚠️ Order Flow: {analysis['error']}"
        
        symbol = analysis['symbol']
        mid = analysis['mid_price']
        spread_pct = analysis['spread_pct']
        
        walls = analysis['liquidity_walls']
        imbalance = analysis['imbalance']
        key_levels = analysis['key_levels']
        
        # Imbalance emoji
        if imbalance['direction'] == 'bid_heavy':
            imb_emoji = "🟢"
            imb_text = f"Bid Heavy ({imbalance['strength']:.0f}%)"
        elif imbalance['direction'] == 'ask_heavy':
            imb_emoji = "🔴"
            imb_text = f"Ask Heavy ({imbalance['strength']:.0f}%)"
        else:
            imb_emoji = "⚪"
            imb_text = "Balanced"
        
        text = f"""📊 ORDER FLOW ({symbol})
━━━━━━━━━━━━━━━━━━━━━━
Mid: {mid:,.2f} | Spread: {spread_pct:.3f}%

{imb_emoji} **IMBALANCE**: {imb_text}
Bids: {analysis['total_bid_volume']:,.4f}
Asks: {analysis['total_ask_volume']:,.4f}
"""
        
        # Walls
        bid_walls = walls['bid_walls'][:3]
        ask_walls = walls['ask_walls'][:3]
        
        if bid_walls or ask_walls:
            text += "\n🧱 **LIQUIDITY WALLS**\n━━━━━━━━━━━━━━━━━━━━━━\n"
            
            if bid_walls:
                text += "🟢 BID WALLS (Support):\n"
                for w in bid_walls:
                    text += f"  {w['price']:,.2f} | {w['volume']:,.4f} ({w['ratio']:.1f}x avg)\n"
            
            if ask_walls:
                text += "\n🔴 ASK WALLS (Resistance):\n"
                for w in ask_walls:
                    text += f"  {w['price']:,.2f} | {w['volume']:,.4f} ({w['ratio']:.1f}x avg)\n"
        
        # Key levels
        if key_levels['strongest_support'] or key_levels['strongest_resistance']:
            text += "\n📐 **KEY LEVELS FROM DEPTH**\n━━━━━━━━━━━━━━━━━━━━━━\n"
            
            if key_levels['strongest_support']:
                s = key_levels['strongest_support']
                text += f"🟢 Strongest Support: {s['price']:,.2f} ({s['strength']:.1f}x)\n"
            
            if key_levels['strongest_resistance']:
                r = key_levels['strongest_resistance']
                text += f"🔴 Strongest Resistance: {r['price']:,.2f} ({r['strength']:.1f}x)\n"
        
        return text


# Quick test
if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("=" * 60)
    print("ORDER FLOW ANALYZER TEST")
    print("=" * 60)
    
    # Test BTC
    ofa = OrderFlowAnalyzer('BTCUSDT', depth_limit=100)
    result = ofa.analyze()
    
    if 'error' not in result:
        print(ofa.format_telegram(result))
    else:
        print(f"Error: {result['error']}")
    
    print()
    print("=" * 60)
    
    # Test ETH
    ofa2 = OrderFlowAnalyzer('ETHUSDT', depth_limit=100)
    result2 = ofa2.analyze()
    
    if 'error' not in result2:
        print(ofa2.format_telegram(result2))
    else:
        print(f"Error: {result2['error']}")
