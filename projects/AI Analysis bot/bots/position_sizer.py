"""
Position Sizing Engine
Professional risk management for crypto, XAU, and FX.

Instrument-specific calculations:
- Crypto (BTC, ETH, SOL, AVAX): units per USDT
- XAUUSD: ounces per USD
- FX (EURUSD, GBPUSD, USDJPY): standard lots/micro lots
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np


@dataclass
class PositionSizeResult:
    """Position sizing calculation result."""
    instrument: str
    account_balance: float
    risk_percent: float
    risk_amount: float
    
    entry_price: float
    stop_loss: float
    stop_distance: float
    
    lot_size: float
    units: float
    margin_required: float
    leverage_used: float
    
    pip_value: float
    total_value_at_risk: float
    
    max_drawdown_percent: float
    breakeven_price: float
    
    # Validation
    is_valid: bool
    warning: Optional[str] = None


class PositionSizer:
    """Professional position sizing engine."""
    
    # Instrument specs
    INSTRUMENTS = {
        # Crypto: contract size = 1 unit per USDT
        'BTCUSDT': {'type': 'crypto', 'tick_size': 0.01, 'contract_size': 1, 'min_lot': 0.001, 'max_leverage': 125},
        'ETHUSDT': {'type': 'crypto', 'tick_size': 0.01, 'contract_size': 1, 'min_lot': 0.001, 'max_leverage': 100},
        'SOLUSDT': {'type': 'crypto', 'tick_size': 0.001, 'contract_size': 1, 'min_lot': 0.01, 'max_leverage': 50},
        'AVAXUSDT': {'type': 'crypto', 'tick_size': 0.001, 'contract_size': 1, 'min_lot': 0.01, 'max_leverage': 50},
        # Additional crypto pairs (Binance, per unified_market_data.CRYPTO_PAIRS)
        'BNBUSDT':  {'type': 'crypto', 'tick_size': 0.01,  'contract_size': 1, 'min_lot': 0.001, 'max_leverage': 75},
        'ADAUSDT':  {'type': 'crypto', 'tick_size': 0.0001, 'contract_size': 1, 'min_lot': 0.01,  'max_leverage': 50},
        'DOTUSDT':  {'type': 'crypto', 'tick_size': 0.001, 'contract_size': 1, 'min_lot': 0.01,  'max_leverage': 50},
        'MATICUSDT':{'type': 'crypto', 'tick_size': 0.0001, 'contract_size': 1, 'min_lot': 0.01,  'max_leverage': 50},
        'LINKUSDT': {'type': 'crypto', 'tick_size': 0.001, 'contract_size': 1, 'min_lot': 0.01,  'max_leverage': 50},
        'LTCUSDT':  {'type': 'crypto', 'tick_size': 0.01,  'contract_size': 1, 'min_lot': 0.001, 'max_leverage': 75},
        'BCHUSDT':  {'type': 'crypto', 'tick_size': 0.01,  'contract_size': 1, 'min_lot': 0.001, 'max_leverage': 75},
        'XRPUSDT':  {'type': 'crypto', 'tick_size': 0.0001, 'contract_size': 1, 'min_lot': 0.01,  'max_leverage': 75},

        # XAUUSD: 1 standard lot = 100 troy ounces
        'XAUUSD': {'type': 'gold', 'tick_size': 0.01, 'contract_size': 100, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 1.0},

        # FX: 1 standard lot = 100,000 base currency
        'EURUSD': {'type': 'fx', 'tick_size': 0.00001, 'contract_size': 100000, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 10.0},
        'GBPUSD': {'type': 'fx', 'tick_size': 0.00001, 'contract_size': 100000, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 10.0},
        'USDJPY': {'type': 'fx', 'tick_size': 0.001, 'contract_size': 100000, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 1000.0 / 151.5},  # Approximate
        'AUDUSD': {'type': 'fx', 'tick_size': 0.00001, 'contract_size': 100000, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 10.0},
        # USDCAD: Yahoo returns it inverted (CAD=X), so we treat the lot math
        # the same as other FX; pricing will arrive inverted in the data feed.
        'USDCAD': {'type': 'fx', 'tick_size': 0.00001, 'contract_size': 100000, 'min_lot': 0.01, 'max_leverage': 500, 'pip_value': 10.0},
    }
    
    def __init__(self, account_balance: float = 10000.0, risk_percent: float = 2.0):
        self.account_balance = account_balance
        self.risk_percent = risk_percent
    
    def calculate(self, symbol: str, entry: float, stop_loss: float, 
                  leverage: Optional[float] = None) -> PositionSizeResult:
        """Calculate position size for any instrument."""
        
        spec = self.INSTRUMENTS.get(symbol, {
            'type': 'unknown', 'tick_size': 0.01, 'contract_size': 1,
            'min_lot': 0.01, 'max_leverage': 100
        })
        
        risk_amount = self.account_balance * (self.risk_percent / 100)
        stop_distance = abs(entry - stop_loss)
        
        if stop_distance == 0:
            return PositionSizeResult(
                instrument=symbol, account_balance=self.account_balance,
                risk_percent=self.risk_percent, risk_amount=risk_amount,
                entry_price=entry, stop_loss=stop_loss, stop_distance=0,
                lot_size=0, units=0, margin_required=0, leverage_used=0,
                pip_value=0, total_value_at_risk=0, max_drawdown_percent=0,
                breakeven_price=entry, is_valid=False,
                warning="Stop loss equals entry price"
            )
        
        # Calculate based on instrument type
        if spec['type'] == 'crypto':
            result = self._calc_crypto(symbol, entry, stop_loss, risk_amount, spec, leverage)
        elif spec['type'] == 'gold':
            result = self._calc_gold(symbol, entry, stop_loss, risk_amount, spec, leverage)
        elif spec['type'] == 'fx':
            result = self._calc_fx(symbol, entry, stop_loss, risk_amount, spec, leverage)
        else:
            result = self._calc_generic(symbol, entry, stop_loss, risk_amount, spec, leverage)
        
        return result
    
    def _calc_crypto(self, symbol: str, entry: float, stop_loss: float,
                     risk_amount: float, spec: dict, leverage: Optional[float]) -> PositionSizeResult:
        """Calculate position for crypto pairs.
        
        Formula: units = risk_amount / (stop_distance / entry)
        Because: PnL = units × (exit - entry)
        Risk = units × |stop - entry|
        → units = risk_amount / |stop - entry|
        
        But in crypto perps, PnL is in USDT:
        PnL = (exit - entry) / entry × position_value
        Risk = |stop - entry| / entry × position_value
        → position_value = risk_amount × entry / |stop - entry|
        → units = position_value / entry
        """
        stop_distance = abs(entry - stop_loss)
        
        # Position value in USDT
        position_value = risk_amount * entry / stop_distance
        
        # Units (coins)
        units = position_value / entry
        lot_size = units  # In crypto, lot = units
        
        # Leverage
        max_lev = spec['max_leverage']
        if leverage is None:
            leverage = min(max_lev, 20)  # Default 20x
        leverage = min(leverage, max_lev)
        
        # Margin required
        margin_required = position_value / leverage
        
        # For perp contracts, pip/tick value
        tick_value = spec['tick_size']  # Value per tick in USDT
        
        # Breakeven (include fees)
        fee_rate = 0.0005  # 0.05% taker fee
        breakeven = entry * (1 + fee_rate * 2) if entry > stop_loss else entry * (1 - fee_rate * 2)
        
        # Validation
        warning = None
        if margin_required > self.account_balance * 0.5:
            warning = f"Margin ${margin_required:.2f} > 50% of account"
        if leverage > 10:
            warning = f"High leverage {leverage}x — use caution"
        
        # Instrument-specific note
        instrument_type = spec.get('type', 'unknown')
        if instrument_type == 'crypto':
            platform_note = f"[Crypto: max {spec['max_leverage']}x available]"
        elif instrument_type == 'gold':
            platform_note = f"[XAU/MT5: max {spec['max_leverage']}x available]"
        elif instrument_type == 'fx':
            platform_note = f"[Forex/MT5: max {spec['max_leverage']}x available]"
        else:
            platform_note = ""
        
        if warning:
            warning += f"\n{platform_note}"
        else:
            warning = platform_note
        
        return PositionSizeResult(
            instrument=symbol, account_balance=self.account_balance,
            risk_percent=self.risk_percent, risk_amount=risk_amount,
            entry_price=entry, stop_loss=stop_loss, stop_distance=stop_distance,
            lot_size=round(lot_size, 4), units=round(units, 4),
            margin_required=round(margin_required, 2), leverage_used=leverage,
            pip_value=tick_value, total_value_at_risk=round(position_value, 2),
            max_drawdown_percent=round((risk_amount / self.account_balance) * 100, 2),
            breakeven_price=round(breakeven, 2), is_valid=True, warning=warning
        )
    
    def _calc_gold(self, symbol: str, entry: float, stop_loss: float,
                   risk_amount: float, spec: dict, leverage: Optional[float]) -> PositionSizeResult:
        """Calculate position for XAUUSD.
        
        XAUUSD: 1 standard lot = 100 troy ounces
        1 pip = 0.01 USD per ounce
        1 pip value per lot = $1.00 (0.01 × 100 oz)
        
        Stop in pips = |entry - stop| / 0.01
        Lot size = risk_amount / (stop_pips × pip_value)
        """
        stop_distance = abs(entry - stop_loss)
        pip_size = 0.01  # 1 pip = 1 cent for gold
        stop_pips = stop_distance / pip_size
        pip_value_per_lot = 1.0  # $1 per pip per standard lot
        
        # Standard lots
        lots = risk_amount / (stop_pips * pip_value_per_lot)
        
        # Leverage (default 100x for gold)
        max_lev = spec['max_leverage']
        if leverage is None:
            leverage = min(max_lev, 100)
        leverage = min(leverage, max_lev)
        
        # Contract value
        contract_value = lots * 100 * entry  # lots × 100 oz × price
        margin_required = contract_value / leverage
        
        # Units (ounces)
        units = lots * 100
        
        # Breakeven with spread
        spread = 0.26  # Vantage gold spread in pips
        breakeven = entry + (spread * pip_size) if entry > stop_loss else entry - (spread * pip_size)
        
        warning = None
        if lots < 0.01:
            warning = "Lot size below broker minimum (0.01)"
        if stop_pips < 100:
            warning = f"Tight stop ({stop_pips:.0f} pips) — gold needs wider stops"
        
        return PositionSizeResult(
            instrument=symbol, account_balance=self.account_balance,
            risk_percent=self.risk_percent, risk_amount=risk_amount,
            entry_price=entry, stop_loss=stop_loss, stop_distance=stop_distance,
            lot_size=round(lots, 2), units=round(units, 2),
            margin_required=round(margin_required, 2), leverage_used=leverage,
            pip_value=pip_value_per_lot, total_value_at_risk=round(contract_value, 2),
            max_drawdown_percent=round((risk_amount / self.account_balance) * 100, 2),
            breakeven_price=round(breakeven, 2), is_valid=True, warning=warning
        )
    
    def _calc_fx(self, symbol: str, entry: float, stop_loss: float,
                 risk_amount: float, spec: dict, leverage: Optional[float]) -> PositionSizeResult:
        """Calculate position for FX pairs.
        
        FX: 1 standard lot = 100,000 base currency
        EURUSD: 1 pip = 0.0001 = $10 per standard lot
        GBPUSD: 1 pip = 0.0001 = $10 per standard lot
        USDJPY: 1 pip = 0.01 ≈ $6.60 per standard lot (at 151.50)
        
        For JPY pairs: pip = 0.01 (3rd decimal place)
        """
        stop_distance = abs(entry - stop_loss)
        if 'JPY' in symbol:
            pip_size = 0.01
            pip_value = 1000.0 / entry  # Approximate
        else:
            pip_size = 0.0001
            pip_value = 10.0  # $10 per pip per lot
        
        stop_pips = stop_distance / pip_size
        lots = risk_amount / (stop_pips * pip_value)
        
        # Leverage (default 50x for FX)
        max_lev = spec['max_leverage']
        if leverage is None:
            leverage = min(max_lev, 50)
        leverage = min(leverage, max_lev)
        
        # Contract value
        contract_value = lots * 100000 * entry  # For XXXUSD pairs
        if symbol.startswith('USD'):
            contract_value = lots * 100000  # USD is base
        margin_required = contract_value / leverage
        
        # Units (base currency)
        units = lots * 100000
        
        # Breakeven with spread
        spread = 1.5 * pip_size  # Typical spread
        breakeven = entry + spread if entry > stop_loss else entry - spread
        
        warning = None
        if lots < 0.01:
            warning = "Lot size below broker minimum (0.01)"
        
        return PositionSizeResult(
            instrument=symbol, account_balance=self.account_balance,
            risk_percent=self.risk_percent, risk_amount=risk_amount,
            entry_price=entry, stop_loss=stop_loss, stop_distance=stop_distance,
            lot_size=round(lots, 2), units=round(units, 0),
            margin_required=round(margin_required, 2), leverage_used=leverage,
            pip_value=pip_value, total_value_at_risk=round(contract_value, 2),
            max_drawdown_percent=round((risk_amount / self.account_balance) * 100, 2),
            breakeven_price=round(breakeven, 5), is_valid=True, warning=warning
        )
    
    def _calc_generic(self, symbol: str, entry: float, stop_loss: float,
                      risk_amount: float, spec: dict, leverage: Optional[float]) -> PositionSizeResult:
        """Fallback for unknown instruments."""
        stop_distance = abs(entry - stop_loss)
        position_value = risk_amount * entry / stop_distance
        units = position_value / entry
        margin = position_value / (leverage or 10)
        
        return PositionSizeResult(
            instrument=symbol, account_balance=self.account_balance,
            risk_percent=self.risk_percent, risk_amount=risk_amount,
            entry_price=entry, stop_loss=stop_loss, stop_distance=stop_distance,
            lot_size=round(units, 4), units=round(units, 4),
            margin_required=round(margin, 2), leverage_used=leverage or 10,
            pip_value=0.01, total_value_at_risk=round(position_value, 2),
            max_drawdown_percent=round((risk_amount / self.account_balance) * 100, 2),
            breakeven_price=entry, is_valid=True,
            warning="Unknown instrument — using generic calculation"
        )
    
    def format_report(self, result: PositionSizeResult) -> str:
        """Format position size for Telegram report."""
        
        symbol = result.instrument
        emoji = "🟢" if result.is_valid else "🔴"
        
        text = f"""{emoji} **POSITION SIZE ({symbol})**
━━━━━━━━━━━━━━━━━━━━━━
💰 Account: ${result.account_balance:,.2f}
⚖️ Risk: {result.risk_percent:.0f}% = ${result.risk_amount:,.2f}

📊 **TRADE PARAMETERS**
Entry: ${result.entry_price:,.5f}
SL: ${result.stop_loss:,.5f}

🎯 **CALCULATED SIZE**
Lot Size: {result.lot_size}
Leverage: {result.leverage_used:.0f}x

💵 **MARGIN & RISK**
Margin Required: ${result.margin_required:,.2f}
Breakeven: ${result.breakeven_price:,.5f}
"""
        
        if result.warning:
            text += f"\n⚠️ {result.warning}\n"
        
        return text


# Quick test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("=== POSITION SIZING TEST ===\n")
    
    # Test Crypto
    sizer = PositionSizer(account_balance=10000, risk_percent=2)
    result = sizer.calculate('BTCUSDT', entry=64000, stop_loss=63000)
    print(sizer.format_report(result))
    
    # Test Gold
    result = sizer.calculate('XAUUSD', entry=3345.00, stop_loss=3340.00)
    print(sizer.format_report(result))
    
    # Test FX
    result = sizer.calculate('EURUSD', entry=1.0850, stop_loss=1.0800)
    print(sizer.format_report(result))
    
    result = sizer.calculate('USDJPY', entry=151.50, stop_loss=151.00)
    print(sizer.format_report(result))
