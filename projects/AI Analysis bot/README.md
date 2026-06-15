# AI Analysis Bot — Clean Project

## 📁 Folder Structure (Cleaned)

```
AI Analysis bot/
├── bots/                          # All Python code
│   ├── main.py                    # Entry point (API / bot / test)
│   ├── telegram_bot_v2.py         # 🚀 MAIN TELEGRAM BOT (what you run)
│   ├── report_builder.py          # Report formatting (sanitized for users)
│   ├── database.py                # SQLite user database + tiers
│   ├── indicators.py              # Technical indicators
│   ├── pattern_detector.py        # Pattern detection engine
│   ├── ai_analyzer.py             # Ollama AI integration
│   ├── chart_generator.py         # Chart image generation
│   ├── position_sizer.py          # Position sizing logic
│   ├── orderflow.py               # Order flow analysis
│   ├── fundamental_analysis.py    # Fear & Greed, funding, DXY
│   ├── multi_timeframe.py         # MTF confluence check
│   ├── divergence_detector.py     # RSI/MACD divergence
│   ├── regime_detector.py         # Market regime (trend/range/volatility)
│   ├── session_analyzer.py        # Trading session context
│   ├── economic_calendar.py       # News event filter
│   ├── market_data.py             # Binance data fetcher
│   ├── unified_market_data.py     # Multi-source data
│   ├── vision_analyzer.py         # Ollama vision (chart analysis)
│   └── ollama_health.py           # Ollama status check
│
├── users.db                       # SQLite database (preserved)
├── .env                           # API keys + config
├── .env.example                   # Template for .env
├── requirements.txt               # Python dependencies
│
├── start.bat                      # 🖥️ Interactive launcher (choose what to run)
├── restart_bot.bat                # 🔄 Clean restart (kill + wait + restart)
│
├── FLOW_V2.md                     # Bot flow documentation
├── RESEARCH.md                    # Research + design decisions
└── README.md                        # This file
```

---

## 🚀 How to Start the Bot

### Option 1: Interactive Launcher
```
Double-click: start.bat
```
Choose:
- `[1]` Run Telegram Bot v2 (what you want)
- `[2]` Run API Server only
- `[3]` Run Bot + API
- `[4]` Test analysis engine

### Option 2: Direct Run
```
cd C:\Users\User\.openclaw\workspace\projects\AI Analysis bot
python bots\telegram_bot_v2.py
```

### Option 3: Clean Restart (after crashes/conflicts)
```
Double-click: restart_bot.bat
```
This will:
1. Kill old bot instances
2. Wait 60s for Telegram API to clear
3. Clear old logs
4. Start fresh

---

## 🛡️ What's Sanitized (Competitor Protection)

| Users See | What's Hidden |
|-----------|---------------|
| Entry Zone, SL, TP, Confidence | Exact indicator values (RSI, ADX, ATR) |
| Generic trend descriptions | EMA alignment details |
| "3 patterns detected, mostly bullish" | Pattern names (Bull Flag, Engulfing, etc.) |
| "Trend strength is moderate" | ADX threshold numbers |
| "Momentum is neutral" | Exact RSI values |
| Position sizing recommendation | Calculation formula |

**Internal admin report** has everything raw — never sent to users.

---

## 👤 User Tiers

| Tier | Deposit | Daily Analyses |
|------|---------|---------------|
| Free | $0 | 3/day |
| Silver | $100-500 | 20/day |
| Gold | $501-1000 | 50/day |
| Unlimited | $1001+ | Unlimited |

---

## 🔧 Latest Fixes (2026-06-10)

1. ✅ Sanitized reports — no leaked thresholds/indicators
2. ✅ Fixed `get_free_tiers_text()` — upgrade button works
3. ✅ Fixed duplicate text "Free: Free:" across bot
4. ✅ Free tier = 3/day consistently everywhere
5. ✅ Cleaned up old/broken files

---

## 📞 Bot Username

Find your bot on Telegram: `@YourBotUsername`

Commands:
- `/start` — Welcome + main menu
- `/analyze` — Begin analysis flow
- `/quick BTCUSDT H1` — Quick analysis
- `/quick BTCUSDT H1 report_simple` — Simple view
- `/quick BTCUSDT H1 report_full` — Full view
- `/status` — Your usage stats
- `/upgrade` — View tiers + referral link
- `/help` — Help text

---

## ⚠️ Important Notes

- **Only ONE bot instance can run at a time** (Telegram API conflict)
- If bot crashes, use `restart_bot.bat` or wait 60s before restarting
- `users.db` contains all user data — **do not delete**
- `.env` contains API keys — **do not share**

---

*Last updated: 2026-06-10*
*Status: Cleaned + Ready*
