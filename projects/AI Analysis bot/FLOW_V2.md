# XOX Analysis Bot v2 — Interactive Flow

## User Journey Step-by-Step

### Step 0: Start the Bot
**User action:** Click bot link or send `/start`

**Bot response:**
```
🎯 Welcome to AI Analysis Bot!

Hi {name}!

🤖 AI-Powered Analysis
Your personal AI analyst reads the chart and ALL market data to generate signals.

What I do:
• AI-powered signal generation from chart analysis
• Technical analysis with multiple confirmation layers
• Pattern recognition across price action
• Market sentiment & macro context
• Multi-timeframe alignment verification

━━━━━━━━━━━━━━━━━━━━━━
📊 Your Status:
Tier: {tier_name}
Daily: {N} analyses/day
━━━━━━━━━━━━━━━━━━━━━━

How to use:
1. Click "Start Analysis"
2. Choose pair (BTC, ETH, XAU, etc.)
3. Choose timeframe (M5, H1, H4, D1)
4. Choose report depth (Simple or Full)
5. Get AI-generated report with chart

💡 Free: 3/day | 💎 Upgrade for unlimited
```

Free users get 3 analyses/day; Silver (XOX $100-500) gets 20/day; Gold ($501-1000) gets 50/day; Unlimited ($1001+) has no cap. Pair list and tier copy is centralized in `telegram_bot_v2.py` (`get_free_tiers_text`).

---

### Step 1: Choose Trading Pair
**User action:** Click "Start Analysis"

**Bot checks:** Rate limit (free = 3/day, etc.). If exceeded, show upgrade options.

**Bot shows:**
```
🔍 Step 1/3: Choose Trading Pair

Select the pair you want to analyze:

[₿ BTC/USDT] [Ξ ETH/USDT]
[🥇 XAU/USD] [💵 EUR/USD]
[💷 GBP/USD] [💴 USD/JPY]
[💰 SOL/USDT] [🔺 AVAX/USDT]
[⬅️ Back]
```

---

### Step 2: Choose Timeframe
**User action:** Click a pair (e.g., BTC/USDT)

**Bot shows:**
```
✅ Pair Selected: BTC/USDT

🔍 Step 2/3: Choose Timeframe

Select your trading style:

[⏱ Scalping (M5)] [📊 Short Term (M15)]
[📈 Swing (H1)]    [📉 Position (H4)]
[🗓 Daily (D1)]
[⬅️ Back]
```

---

### Step 3: Choose Report Depth
**User action:** Click a timeframe (e.g., H1)

**Bot shows:**
```
✅ Pair: BTC/USDT
✅ Timeframe: H1

🔍 Step 3/3: Choose Report Type

Which report do you want?

[🎯 Quick Analysis (simple)]
[🔥 Full Analysis (premium)]
[⬅️ Back]
```

Report types:
- **Quick (simple)** — short, sanitized text: signal, key levels, generic trend/momentum/volatility description. No raw indicator numbers.
- **Full (premium)** — full premium report plus position sizing, market calendar, divergence, regime, session, AI context, MTF. All sanitized — no exact RSI/ADX/ATR values or pattern names.

The premium report is the same body for all users; the only gating is the daily analysis count.

---

### Step 4: Run Analysis
**User action:** Click "Quick" or "Full"

**Bot shows:**
```
🔍 Analyzing BTC/USDT on H1...

⏳ Fetching market data...
📊 Calculating indicators...
🎯 Detecting patterns...
🤖 AI quick check...
```

---

### Step 5: Results
**Bot sends:**
1. Chart image (dark theme, no user_id watermark)
2. Report text (sanitized)

A free user sees the full premium report text, but each analysis consumes one of their 3 daily credits. There are no "two tiers of report content" — just one report, gated by usage.

---

### Step 6: Free User Limit Reached
**If free user used all 3 analyses today:**
```
💎 Want more analyses?

You've used all 3 of your free analyses today.
Upgrade to unlock:
• Silver ($100-500): 20 analyses/day
• Gold ($501-1000): 50 analyses/day
• Unlimited ($1001+): No limits!

[💎 Register at XOX]
```

---

## Deposit Tier System

| Tier | Deposit | Daily Analyses | Best For |
|------|---------|---------------|----------|
| Free | $0 | 3 | Testing |
| Silver | $100-500 | 20 | Casual traders |
| Gold | $501-1000 | 50 | Active traders |
| Unlimited | $1001+ | Unlimited | Professionals |

## Rate Limit Logic

1. User clicks "Start Analysis"
2. Bot checks `daily_used` vs `daily_limit`
3. If limit reached → Show upgrade options
4. If under limit → Proceed to Step 1
5. After analysis → Increment `daily_used` (one increment per analysis)
6. Reset counter at midnight

## Referral Links

- **XOX (Crypto)**: https://app.xox.exchange/en/referral/ZAR26K
