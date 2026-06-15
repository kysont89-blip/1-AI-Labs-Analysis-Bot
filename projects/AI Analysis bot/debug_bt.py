import sys, os
sys.path.insert(0, 'bots')
from backtest_engine import fetch_data, calculate_indicators_window, detect_patterns_window, build_signal

# Fetch data
df = fetch_data('BTCUSDT', '1h', days=90)
print(f'Total rows: {len(df)}')

# Test one window
i = 200
window = df.iloc[i - 200:i].copy()
current_price = df.iloc[i]['close']

print(f'Window shape: {window.shape}')
print(f'Current price: {current_price}')

# Test indicators
try:
    ind = calculate_indicators_window(window)
    print(f"Indicators: trend={ind['trend_score']}, rsi={ind['rsi']}, adx={ind['adx']}, atr={ind['atr']}")
except Exception as e:
    print(f'Indicator error: {e}')
    import traceback
    traceback.print_exc()

# Test patterns
try:
    patterns = detect_patterns_window(window)
    print(f'Patterns: {len(patterns)}')
    for p in patterns[:3]:
        print(f'  - {p}')
except Exception as e:
    print(f'Pattern error: {e}')
    import traceback
    traceback.print_exc()

# Test signal build
try:
    sig, entry, sl, tp, conf, confidence = build_signal(current_price, ind, patterns)
    print(f'Signal: {sig}, confluence: {conf}, confidence: {confidence}')
except Exception as e:
    print(f'Signal error: {e}')
    import traceback
    traceback.print_exc()
