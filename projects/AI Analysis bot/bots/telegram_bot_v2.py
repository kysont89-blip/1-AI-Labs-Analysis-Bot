"""
XOX Analysis Bot v2 - AI-Powered Analysis

PRIMARY: AI Analysis (Ollama qwen3.5:397b-cloud) - Text-based for speed
BACKUP: Code Analysis (if AI offline)

Features:
- AI receives all technical data as structured text
- AI generates: signal, confidence, reasoning
- Code provides: indicators, patterns, levels
- Chart generated for user visual reference
- MTF confluence check
- Fundamental context
- Recent pairs quick-access

Commands:
  /start     - Welcome
  /analyze   - Begin analysis flow
  /quick     - Quick analyze (power user)
  /status    - Your stats
  /upgrade   - Deposit tiers
  /help      - Help

Report Formats (choose during /analyze):
  - Full Report (Recommended) - Complete: signal + levels + indicators
    + patterns + Order Flow + Fundamental + Divergence
    + Position sizing + AI reasoning
  - Simple Report - Quick: signal + entry/SL/TP + key levels
    + Order Flow (crypto only). Faster, less detail.

Signal Display:
  🟢 STRONG BUY  — Only when trend ≥+80 with confluence
  🔴 STRONG SELL — Only when trend ≤-80 with confluence
  ⚪ NO TRADE    — Weak or conflicting setups

Deposit Tiers:
  Free: $0 = 3/day
  Silver: $100-500 = 20/day
  Gold: $501-1000 = 50/day
  Unlimited: $1001+ = Unlimited
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

import logging
import asyncio
from typing import Dict, Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)

from database import UserDatabase, UserTier, TIER_NAMES, TIER_LIMITS
from indicators import IndicatorCalculator
from pattern_detector import PatternDetector
from chart_generator import ChartGenerator
from report_builder import ReportBuilder, SignalStrength, resolve_plan
from fundamental_analysis import FundamentalAnalyzer
from unified_market_data import UnifiedDataFetcher
from ai_analyzer import AIAnalyzer
from ollama_health import is_ollama_running_sync
from multi_timeframe import MultiTimeframeAnalyzer
from position_sizer import PositionSizer
from divergence_detector import DivergenceDetector
from regime_detector import RegimeDetector
from session_analyzer import SessionAnalyzer
from economic_calendar import EconomicCalendar
from orderflow import OrderFlowAnalyzer
from news_fetcher import NewsFetcher, format_headlines

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

REFERRAL_LINKS = {
    'xox': 'https://app.xox.exchange/en/referral/ZAR26K'
}

# Components
db = UserDatabase()
fetcher = UnifiedDataFetcher()
chart_gen = ChartGenerator()
report_builder = ReportBuilder()
fundamental_analyzer = FundamentalAnalyzer()
ai_analyzer = AIAnalyzer()
mtf_analyzer = MultiTimeframeAnalyzer(fetcher)

# NEW: Critical Gap Components
calendar = EconomicCalendar()
news_fetcher = NewsFetcher()  # RSS-based real-time news (BTC/XAU/macro)
regime_detector = None  # Created per-analysis
session_analyzer = None  # Created per-analysis
divergence_detector = None  # Created per-analysis
# position_sizer now created per-analysis in run_analysis_direct

STEP_PAIR, STEP_TIMEFRAME, STEP_TYPE, STEP_CONFIRM, STEP_SETTINGS = range(5)
STEP_VERIFY_WALLET, STEP_VERIFY_PHOTO = range(5, 7)
user_sessions: Dict[int, Dict] = {}
user_recent_pairs: Dict[int, List[str]] = {}
verify_sessions: Dict[int, Dict] = {}  # user_id -> {step: 'wallet'|'photo', wallet_address: str, broker_uid: str}

# Ask AI session state — keys are user_id. Each value holds the active report
# context, the built system prompt, rolling message history, and the last
# Ollama call timestamp for throttling. Cleared on /start, /cancel, and on
# a new /analyze. The report context itself is cached on user_sessions
# (under 'ai_context') so the post-report button can wire it up.
ask_ai_sessions: Dict[int, Dict] = {}
MAX_AI_HISTORY = 20  # 10 user + 10 assistant turns
AI_CONTEXT_TTL_MINUTES = 60  # report context expires 1h after the analysis ran
AI_THROTTLE_SECONDS = 3.0  # minimum seconds between AI calls per user
AI_MAX_INPUT_CHARS = 4000  # Telegram limit is 4096; leave a small safety margin

# Supported trading pairs — keep this list in sync across the UI, /quick validation,
# and the recent-pairs filter. Updated 2026-06-12: removed GBP/USD, USD/JPY, SOL/USDT, AVAX/USDT.
SUPPORTED_PAIRS = ('BTCUSDT', 'ETHUSDT', 'XAUUSD', 'EURUSD')

# Short disclaimer appended to every analysis report and surfaced in /start, /help.
# This bot is a trading-support tool, NOT a financial advisor. Not financial advice.
DISCLAIMER_SHORT = (
    "⚠️ Not financial advice. This is a trading-support AI tool, not a financial advisor. "
    "Always do your own research and manage your risk."
)


def get_pair_keyboard(user_id: int):
    keyboard = [
        [InlineKeyboardButton("₿ BTC/USDT", callback_data='pair_BTCUSDT'),
         InlineKeyboardButton("Ξ ETH/USDT", callback_data='pair_ETHUSDT')],
        [InlineKeyboardButton("🥇 XAU/USD", callback_data='pair_XAUUSD'),
         InlineKeyboardButton("💵 EUR/USD", callback_data='pair_EURUSD')],
    ]
    # Recent pairs — filter to currently-supported pairs so old favorites (e.g. SOL)
    # don't appear as broken buttons.
    recent = [r for r in user_recent_pairs.get(user_id, []) if r in SUPPORTED_PAIRS]
    if recent:
        recent_buttons = [InlineKeyboardButton(f"🔄 {r}", callback_data=f'pair_{r}') for r in recent[:3]]
        keyboard.insert(0, recent_buttons)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='back_start')])
    return InlineKeyboardMarkup(keyboard)


def get_timeframe_keyboard():
    keyboard = [
        [InlineKeyboardButton("⏱ Scalping (M5)", callback_data='tf_M5'),
         InlineKeyboardButton("📊 Short Term (M15)", callback_data='tf_M15')],
        [InlineKeyboardButton("📈 Swing (H1)", callback_data='tf_H1'),
         InlineKeyboardButton("📉 Position (H4)", callback_data='tf_H4')],
        [InlineKeyboardButton("🗓 Daily (D1)", callback_data='tf_D1')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_pair')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_default_timeframe_keyboard():
    """Keyboard for selecting default timeframe in settings."""
    keyboard = [
        [InlineKeyboardButton("⏱ M5", callback_data='dtf_M5'),
         InlineKeyboardButton("📊 M15", callback_data='dtf_M15')],
        [InlineKeyboardButton("📈 H1", callback_data='dtf_H1'),
         InlineKeyboardButton("📉 H4", callback_data='dtf_H4')],
        [InlineKeyboardButton("🗓 D1", callback_data='dtf_D1')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_settings')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_report_type_keyboard():
    """Simplified menu: just choose report detail level.
    
    AI Full analysis always runs in background.
    User only chooses how much detail to see.
    """
    keyboard = [
        [InlineKeyboardButton("📊 Full Report (Recommended)", callback_data='report_full')],
        [InlineKeyboardButton("📱 Simple Report", callback_data='report_simple')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_timeframe')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_confirm_keyboard(symbol: str, tf: str, report_type: str):
    type_names = {
        'report_full': '📊 Full Report',
        'report_simple': '📱 Simple Report',
    }
    keyboard = [
        [InlineKeyboardButton(f"🚀 Run {type_names.get(report_type, report_type)}", callback_data='run_analysis')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_type')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_upgrade_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Register on XOX", url=REFERRAL_LINKS['xox'])],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_start')]
    ])


def get_main_menu_keyboard(tier: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Start Analysis", callback_data='menu_analyze')],
        [InlineKeyboardButton("📰 News", callback_data='menu_news'),
         InlineKeyboardButton("📅 Events", callback_data='menu_events')],
        [InlineKeyboardButton("🤖 Ask AI", callback_data='menu_ask_ai')],
        [InlineKeyboardButton("📊 My Stats", callback_data='menu_status'),
         InlineKeyboardButton("⚙️ Settings", callback_data='menu_settings')],
        [InlineKeyboardButton("💎 Upgrade", callback_data='menu_upgrade'),
         InlineKeyboardButton("❓ Help", callback_data='menu_help')]
    ])


def get_post_report_keyboard(analysis_id: int):
    """Keyboard shown after a report finishes.

    Combines the Ask AI entry (wired to the just-sent report context) with
    news/events shortcuts and the main menu so the user can either drill
    into Q&A, scan today's headlines, check the economic calendar, or move
    on. The analysis_id is embedded in the callback so the handler can
    verify the context, but the actual context is cached in
    user_sessions['ai_context'].
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Ask AI about this report", callback_data=f'ask_about_{analysis_id}')],
        [InlineKeyboardButton("📰 News", callback_data='menu_news'),
         InlineKeyboardButton("📅 Events", callback_data='menu_events')],
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_start')],
    ])


def get_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Account Balance", callback_data='set_balance')],
        [InlineKeyboardButton("⚖️ Risk Percentage", callback_data='set_risk')],
        [InlineKeyboardButton("🔧 Leverage (Crypto)", callback_data='set_lev_crypto'),
         InlineKeyboardButton("🔧 Leverage (MT5)", callback_data='set_lev_mt5')],
        [InlineKeyboardButton("📊 Default Timeframe", callback_data='set_timeframe')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_start')]
    ])


def get_leverage_crypto_keyboard():
    """Leverage options for Crypto (Binance)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("20x", callback_data='lev_20'),
         InlineKeyboardButton("50x", callback_data='lev_50')],
        [InlineKeyboardButton("100x", callback_data='lev_100'),
         InlineKeyboardButton("125x", callback_data='lev_125')],
        [InlineKeyboardButton("✏️ Custom x", callback_data='lev_custom')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_settings')]
    ])


def get_leverage_mt5_keyboard():
    """Leverage options for MT5 (Forex/Gold) — offshore broker range."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("100x", callback_data='lev_100'),
         InlineKeyboardButton("200x", callback_data='lev_200')],
        [InlineKeyboardButton("500x", callback_data='lev_500'),
         InlineKeyboardButton("1000x", callback_data='lev_1000')],
        [InlineKeyboardButton("✏️ Custom x", callback_data='lev_custom')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_settings')]
    ])


def get_risk_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1%", callback_data='risk_1'),
         InlineKeyboardButton("2%", callback_data='risk_2')],
        [InlineKeyboardButton("3%", callback_data='risk_3'),
         InlineKeyboardButton("5%", callback_data='risk_5')],
        [InlineKeyboardButton("✏️ Custom %", callback_data='risk_custom')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_settings')]
    ])


# ═══════════════════════════════════════════════
# FREE TIERS
# ═══════════════════════════════════════════════

def get_free_tiers_text():
    """Single source of truth for the upgrade message shown everywhere
    (💎 Upgrade button, daily-limit blocks, /upgrade command)."""
    return """💎 **Upgrade to Unlock More Analyses**

**Tiers:**
🔹 **Free** — $0 = 3 analyses/day
🔹 **Silver** — $100-500 deposit = 20/day
🔹 **Gold** — $501-1000 deposit = 50/day
🔹 **Unlimited** — $1001+ deposit = no daily limit

**How to upgrade (4 steps):**
1️⃣ Tap **Register on XOX** below — opens the sign-up page
2️⃣ Create your XOX account & complete a deposit of $100+
   (use the same link so your registration is tracked)
3️⃣ Copy your **XOX wallet address** (Profile → Wallet)
4️⃣ Come back here and send **/verify** — paste your wallet
   address, then upload a screenshot of your deposit

After verification your tier activates within a few hours.

📞 Questions: @kysont89
"""


# ═══════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await db.get_user(user.id)
    if not db_user:
        db_user = await db.create_user(user.id, user.username, user.first_name, user.last_name)
    
    # Ensure settings are initialized
    if db_user.account_balance is None:
        await db.update_user_settings(user.id, account_balance=10000.0, risk_percent=2.0, leverage=20.0, default_timeframe='H1')

    tier_name = TIER_NAMES.get(db_user.tier, "Free")
    limit = TIER_LIMITS.get(db_user.tier, 1)
    limit_text = "Unlimited" if limit > 99999 else str(limit)

    welcome = f"""🎯 Welcome to AI Analysis Bot!

Hi {user.first_name}!

🤖 **AI-Powered Analysis**
Your personal AI analyst reads the chart and ALL market data to generate signals.

**What I do:**
• 🤖 AI-powered signal generation from chart analysis
• 📊 Technical analysis with multiple confirmation layers
• 🎯 Pattern recognition across price action
• 📊 Market sentiment & macro context
• 🔀 Multi-timeframe alignment verification

━━━━━━━━━━━━━━━━━━━━━━
📊 Your Status:
Tier: {tier_name}
Daily: {limit_text} analysis{'es' if isinstance(limit, int) and limit > 1 else ''}/day
━━━━━━━━━━━━━━━━━━━━━━

**How to use:**
1️⃣ Click "Start Analysis"
2️⃣ Choose pair (BTC, ETH, XAU, etc.)
3️⃣ Choose timeframe (M5, H1, H4, D1)
4️⃣ Choose analysis depth
5️⃣ Get AI-generated report with chart

💡 Free: 3/day | 💎 Upgrade for unlimited

━━━━━━━━━━━━━━━━━━━━━━
{DISCLAIMER_SHORT}
"""
    if update.message:
        await update.message.reply_text(welcome, reply_markup=get_main_menu_keyboard(db_user.tier.value))
    else:
        await update.callback_query.edit_message_text(welcome, reply_markup=get_main_menu_keyboard(db_user.tier.value))

    # /start always clears any in-flight Ask AI session and stale context
    ask_ai_sessions.pop(user.id, None)
    sess = user_sessions.get(user.id)
    if sess:
        sess.pop('ai_context', None)


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    can, msg = await db.can_analyze(user.id)
    if not can:
        text = f"⛔ {msg}\n\n{get_free_tiers_text()}"
        kb = get_upgrade_keyboard()
        if query:
            await query.edit_message_text(text, reply_markup=kb)
        else:
            await update.message.reply_text(text, reply_markup=kb)
        return

    user_sessions[user.id] = {'symbol': None, 'timeframe': None, 'report_type': None}

    # A new analysis invalidates the cached report context and any in-flight
    # Ask AI session — the post-report button on the *new* report will
    # re-populate ai_context. This prevents /askai mid-flow from answering
    # about the previous report.
    ask_ai_sessions.pop(user.id, None)
    user_sessions[user.id].pop('ai_context', None)

    text = """🔍 **Step 1/3: Choose Trading Pair**

Select the pair:

💡 Supported:
• BTC/USDT - Bitcoin
• ETH/USDT - Ethereum
• XAU/USD - Gold
• EUR/USD - Forex
"""
    if query:
        await query.edit_message_text(text, reply_markup=get_pair_keyboard(user.id))
    else:
        await update.message.reply_text(text, reply_markup=get_pair_keyboard(user.id))
    return STEP_PAIR


async def quick_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    can, msg = await db.can_analyze(user.id)
    if not can:
        await update.message.reply_text(f"⛔ {msg}\n\n{get_free_tiers_text()}", reply_markup=get_upgrade_keyboard())
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /quick SYMBOL TF [TYPE]\n\n"
            "Examples:\n"
            "  /quick BTCUSDT H1\n"
            "  /quick XAUUSD H4 report_full\n"
            "  /quick ETHUSDT M15 report_simple\n\n"
            "Pairs: " + ", ".join(SUPPORTED_PAIRS) + "\n"
            "Tip: H1 is our most reliable timeframe (Swing).\n"
            "Types: report_full (default), report_simple"
        )
        return

    symbol = args[0].upper()
    tf = args[1].upper()
    report_type = args[2].lower() if len(args) > 2 else 'report_full'

    valid_tfs = {'M5', 'M15', 'H1', 'H4', 'D1'}
    valid_types = {'report_full', 'report_simple'}

    if symbol not in SUPPORTED_PAIRS:
        await update.message.reply_text(
            f"❌ Unsupported pair. Supported: {', '.join(SUPPORTED_PAIRS)}"
        )
        return
    if tf not in valid_tfs:
        await update.message.reply_text(f"❌ Invalid timeframe. Use: {', '.join(valid_tfs)}")
        return
    if report_type not in valid_types:
        await update.message.reply_text(f"❌ Invalid type. Use: {', '.join(valid_types)}")
        return

    user_sessions[user.id] = {
        'symbol': symbol,
        'timeframe': tf,
        'report_type': report_type
    }

    await run_analysis_direct(update, context, user.id, symbol, tf, report_type)


async def run_analysis_direct(update, context, user_id, symbol, tf, analysis_type):
    msg = await context.bot.send_message(chat_id=user_id, text=f"🔍 Analyzing **{symbol}** on **{tf}**...")

    try:
        # Dynamic candle count based on timeframe
        CANDLE_LIMITS = {
            'M5': 500,
            'M15': 300,
            'H1': 150,
            'H4': 150,
            'D1': 100,
        }
        candle_limit = CANDLE_LIMITS.get(tf, 150)

        df = fetcher.get_klines(symbol, tf, limit=candle_limit)
        if df.empty:
            # Send error (ignore if message expired)
            try:
                await context.bot.edit_message_text(chat_id=user_id, message_id=msg.message_id,
                    text=f"❌ No data for {symbol} on {tf}.", reply_markup=get_main_menu_keyboard('free'))
            except Exception:
                await context.bot.send_message(chat_id=user_id,
                    text=f"❌ No data for {symbol} on {tf}.", reply_markup=get_main_menu_keyboard('free'))
            return

        current_price = df['close'].iloc[-1]

        # Load user settings for position sizing
        user_obj = await db.get_user(user_id)
        account_balance = getattr(user_obj, 'account_balance', 10000) if user_obj else 10000
        risk_percent = getattr(user_obj, 'risk_percent', 2) if user_obj else 2
        position_sizer = PositionSizer(account_balance=account_balance, risk_percent=risk_percent)

        calc = IndicatorCalculator(df)
        indicators = calc.calculate_all()
        detector = PatternDetector(df)
        patterns = detector.detect_all()

        indicator_dict = {
            'ema': indicators.ema,
            'vwap': indicators.vwap,
            'poc': indicators.volume_profile.get('poc', current_price),
            'vah': indicators.volume_profile.get('vah', current_price),
            'val': indicators.volume_profile.get('val', current_price),
            'rsi': indicators.rsi
        }
        levels = {
            'support': indicators.support_levels,
            'resistance': indicators.resistance_levels
        }
        chart_bytes = chart_gen.generate(df, symbol, tf, indicator_dict, patterns, levels)

        # ═══════════════════════════════════════════════
        # ORDER FLOW (Crypto only)
        # ═══════════════════════════════════════════════
        order_flow_data = None
        if symbol in UnifiedDataFetcher.CRYPTO_PAIRS:
            try:
                of_analyzer = OrderFlowAnalyzer(symbol, depth_limit=100)
                order_flow_data = of_analyzer.analyze()
                logger.info(f"[OrderFlow] {symbol}: {order_flow_data.get('imbalance', {}).get('direction', 'N/A')}")
            except Exception as e:
                logger.warning(f"[OrderFlow] Failed for {symbol}: {e}")

        # ═══════════════════════════════════════════════
        # FULL ANALYSIS (background) — only show essentials
        # ═══════════════════════════════════════════════
        is_simple_view = analysis_type in ('report_simple', 'simple')

        # Compute all data (background) — may use later
        # News, regime, session, divergence, position sizing computed but not displayed
        # to keep reports clean and actionable

        # Fundamental — always compute
        fundamental = fundamental_analyzer.analyze(symbol)
        fundamental_data = fundamental.to_dict()

        # MTF — always compute
        include_mtf = True

        # Divergence — always compute
        div_detector = DivergenceDetector(df)
        div_signals = div_detector.detect_all(indicators.rsi)

        # ── News + Events (premium report context) ──
        # Fetched early so the event-block override can be applied inside
        # report_builder.build() — that keeps the recorded signal, the
        # displayed signal, and the position-sizer all in sync.
        # Failures are isolated: an offline RSS feed or empty calendar
        # must not blank the analysis.
        news_items: List = []
        upcoming_events: List = []
        news_block: Optional[Dict] = None
        try:
            _gathered = await asyncio.gather(
                news_fetcher.get_headlines(topics=None, limit=8),
                asyncio.to_thread(calendar.get_upcoming_events, 3),
                asyncio.to_thread(calendar.check_signal_blocked, symbol),
                return_exceptions=True,
            )
            _news_raw, _events_raw, _block_raw = _gathered
        except Exception as e:
            logger.warning(f"[News/Calendar] concurrent fetch failed: {e}")
            _news_raw = _events_raw = _block_raw = None
        # Defensive: gather() can return exception objects per task
        if isinstance(_news_raw, list):
            news_items = _news_raw
        elif isinstance(_news_raw, Exception):
            logger.warning(f"[News] fetch failed: {_news_raw}")
        if isinstance(_events_raw, list):
            upcoming_events = _events_raw
        elif isinstance(_events_raw, Exception):
            logger.warning(f"[Calendar] get_upcoming_events failed: {_events_raw}")
        if isinstance(_block_raw, dict):
            news_block = _block_raw
        elif isinstance(_block_raw, Exception):
            logger.warning(f"[Calendar] check_signal_blocked failed: {_block_raw}")
        # Re-filter events to the symbol's currency so the embedded
        # UPCOMING EVENTS block matches the /events command's UX.
        try:
            if upcoming_events:
                upcoming_events = calendar.get_events_for_symbol(
                    symbol, days_ahead=3
                ) or upcoming_events[:3]
        except Exception as e:
            logger.warning(f"[Calendar] get_events_for_symbol failed: {e}")

        # Build report
        report = report_builder.build(
            symbol=symbol, timeframe=tf, price=current_price,
            indicators=indicators.to_dict(), patterns=patterns,
            vision=None, fundamental=fundamental_data,
            order_flow=order_flow_data,
            divergence=div_signals,
            tier='premium',
            trading_style=getattr(user_obj, 'trading_style', None) or 'auto',
            news_items=news_items,
            upcoming_events=upcoming_events,
            event_blocked=news_block,
        )
        code_confidence = report.signal_confidence

        # AI — always try if available
        ai_result = None
        if is_ollama_running_sync():
            try:
                # Update status (ignore if message expired)
                await context.bot.edit_message_text(
                    chat_id=user_id, message_id=msg.message_id,
                    text=f"🔍 Analyzing **{symbol}** on **{tf}**...\n\n🤖 AI quick check..."
                )
            except Exception:
                pass  # Message expired, continue silently
            
            try:
                ai_result = await ai_analyzer.analyze(
                    symbol, tf, current_price,
                    indicators.to_dict(), patterns, fundamental_data
                )
                # Only use AI if confident AND agrees with code (or code is neutral)
                ai_confident = ai_result and ai_result.confidence >= 0.6
                ai_agrees = ai_result and (
                    (ai_result.signal == 'LONG' and report.trend_score > 0) or
                    (ai_result.signal == 'SHORT' and report.trend_score < 0) or
                    (ai_result.signal == 'NEUTRAL')
                )
                # Event-block guard: if a high-impact economic event is in
                # the buffer window, build() already forced the signal to
                # NEUTRAL. We must NOT let the AI undo that and push the
                # signal back to BUY/SELL. Block = no trade, full stop.
                event_block_active = bool(
                    news_block and news_block.get('blocked')
                    and news_block.get('impact') == 'high'
                )
                if ai_confident and ai_agrees and not event_block_active:
                    signal_map = {
                        'LONG': SignalStrength.BUY if ai_result.confidence < 0.8 else SignalStrength.STRONG_BUY,
                        'SHORT': SignalStrength.SELL if ai_result.confidence < 0.8 else SignalStrength.STRONG_SELL,
                        'NEUTRAL': SignalStrength.NEUTRAL
                    }
                    report.overall_signal = signal_map.get(ai_result.signal, SignalStrength.NEUTRAL)
                    report.signal_confidence = ai_result.confidence
                    logger.info(f"AI override: {ai_result.signal} ({ai_result.confidence:.0%})")
                elif event_block_active and ai_confident and ai_agrees:
                    logger.info(
                        f"AI suggested {ai_result.signal} but event-blocked "
                        f"({news_block.get('event')}) — keeping NEUTRAL"
                    )
                else:
                    # AI returned but low confidence or disagrees - keep code signal
                    if ai_result:
                        logger.info(f"AI disagrees/low conf: {ai_result.signal} ({ai_result.confidence:.0%}) - keeping code signal")
                    else:
                        logger.info("AI disagrees/low conf: no result - keeping code signal")
            except Exception as e:
                logger.error(f"AI quick check failed: {e}")
                ai_result = None

        # MTF Confluence
        mtf_check = None
        if include_mtf:
            mtf_check = mtf_analyzer.analyze_confluence(symbol, tf, report._direction_text())

        # Use credit
        await db.use_analysis(user_id)

        # Track recent
        recent = user_recent_pairs.get(user_id, [])
        if symbol not in recent:
            recent.insert(0, symbol)
            user_recent_pairs[user_id] = recent[:5]

        # GAP 2: NEWS / EVENT FILTER is fetched earlier in the flow
        # (just before report_builder.build) so the event-block override
        # can be applied inside build(). news_block / news_items /
        # upcoming_events are already populated by this point.

        # ═══════════════════════════════════════════════
        # GAP 4: REGIME DETECTION
        # ═══════════════════════════════════════════════
        regime = RegimeDetector(df).detect()
        
        # ═══════════════════════════════════════════════
        # GAP 5: SESSION ANALYSIS
        # ═══════════════════════════════════════════════
        session = SessionAnalyzer(df, symbol).analyze()

        # div_signals was already computed once before the report build (line ~485);
        # reuse it here for the user-facing divergence section.

        # ═══════════════════════════════════════════════
        # GAP 1: POSITION SIZING
        # ═══════════════════════════════════════════════
        # Use entry midpoint for calculation
        entry_mid = (report.entry_zone[0] + report.entry_zone[1]) / 2
        
        # Determine correct leverage based on instrument type.
        # Prefer the position_sizer spec when present, but fall back to the same
        # crypto/forex heuristic UnifiedDataFetcher uses so that any USDT pair
        # (e.g. DOGEUSDT) still gets the right leverage even if not in the spec.
        spec = position_sizer.INSTRUMENTS.get(symbol, {})
        spec_type = spec.get('type', '')
        if spec_type in ('crypto', 'gold', 'fx'):
            instrument_type = spec_type
        elif fetcher.is_crypto(symbol):
            instrument_type = 'crypto'
        else:
            instrument_type = 'fx'
        if instrument_type == 'crypto':
            leverage = getattr(user_obj, 'leverage_crypto', 20)
        else:
            leverage = getattr(user_obj, 'leverage_mt5', 500)
        
        sizing = position_sizer.calculate(symbol, entry_mid, report.stop_loss, leverage=leverage)
        
        # Build report text — SIMPLE or FULL view
        if is_simple_view:
            # === SIMPLE VIEW ===
            
            # Ranging market guidance
            if report.trend == "ranging" or report.overall_signal.value == "NEUTRAL":
                full_text = f"""📊 {report.symbol} | {report.timeframe} | {report.timestamp}

{DISCLAIMER_SHORT}

🎯 QUICK ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━

⚪ NO TRADE
━━━━━━━━━━━━━━━━━━━━━━

📈 **SIGNAL**: NO TRADE
Confidence: {report.signal_confidence:.0%}
Trend: RANGING (Score {report.trend_score:+.0f}/100)

⚠️ Price is in the middle of the range. No clear edge.

📐 **KEY LEVELS** (Watch These)
━━━━━━━━━━━━━━━━━━━━━━
Resistance: {', '.join(f'{r:,.2f}' for r in report.resistance[:3]) if report.resistance else 'N/A'}
Support:    {', '.join(f'{s:,.2f}' for s in report.support[:3]) if report.support else 'N/A'}

💡 WAIT for price to reach a key level, then analyze again.
"""
            # === SIMPLE VIEW (trending) ===
                # Sanitized simple view — no exact indicator values, no pattern names
                direction_emoji = report._direction_emoji()
                direction_text = report._direction_text()
                
                # Generic trend description
                trend_generic = report._generic_trend_description()
                momentum_generic = report._generic_momentum_description()
                vol_generic = report._generic_volatility_description()
                
                # Pattern summary only (no names)
                bullish_count = sum(1 for p in report.patterns_detected if p.get('direction') == 'bullish')
                bearish_count = sum(1 for p in report.patterns_detected if p.get('direction') == 'bearish')
                total_patterns = len(report.patterns_detected)
                if total_patterns > 0:
                    if bullish_count > bearish_count:
                        pattern_summary = f"{total_patterns} bullish patterns forming"
                    elif bearish_count > bullish_count:
                        pattern_summary = f"{total_patterns} bearish patterns forming"
                    else:
                        pattern_summary = f"{total_patterns} mixed patterns forming"
                else:
                    pattern_summary = "No significant patterns"
                
                full_text = f"""📊 {report.symbol} | {report.timeframe} | {report.timestamp}

{DISCLAIMER_SHORT}

🎯 QUICK ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━

{direction_emoji}
━━━━━━━━━━━━━━━━━━━━━━

📈 **SIGNAL**: {direction_text}
Confidence: {report.signal_confidence:.0%}
{trend_generic}

📈 **TRADE LEVELS**
━━━━━━━━━━━━━━━━━━━━━━
Entry Zone: {report.entry_zone[0]:,.2f} - {report.entry_zone[1]:,.2f}
Stop Loss:  {report.stop_loss:,.2f}
Take Profit: {report.take_profit:,.2f}
Risk:Reward ≈ 1:{report.risk_reward:.1f}

📐 **KEY ZONES**
Resistance: {', '.join(f'${r:,.0f}' for r in report.resistance[:3]) if report.resistance else 'N/A'}
Support:    {', '.join(f'${s:,.0f}' for s in report.support[:3]) if report.support else 'N/A'}

📊 **MARKET CONDITION**
• Momentum: {momentum_generic}
• Volatility: {vol_generic}
• {pattern_summary}
"""
                
                # Order Flow confirmation (crypto only) — generic
                if report.order_flow and 'error' not in report.order_flow:
                    ofa = OrderFlowAnalyzer(symbol, depth_limit=100)
                    raw_signal = direction_text
                    if 'STRONG BUY' in raw_signal or 'BUY' in raw_signal:
                        signal_dir = 'LONG'
                    elif 'STRONG SELL' in raw_signal or 'SELL' in raw_signal:
                        signal_dir = 'SHORT'
                    else:
                        signal_dir = 'NEUTRAL'
                    of_text = ofa.get_confirmation(report.order_flow, signal_dir)
                    # Sanitize order flow text to remove exact numbers
                    full_text += f"\n📊 Order Flow: {of_text}\n"
                
                # Check for contradictions — generic language only
                contradictions = []
                if report.order_flow:
                    of_imb = report.order_flow.get('imbalance', {}).get('direction', '')
                    if report.overall_signal.value == 'STRONG_SELL' and of_imb == 'bid_heavy':
                        contradictions.append("Order Flow shows buying interest")
                    elif report.overall_signal.value == 'STRONG_BUY' and of_imb == 'ask_heavy':
                        contradictions.append("Order Flow shows selling interest")
                
                if contradictions:
                    full_text += f"\n⚠️ **CONFLUENCE CHECK**\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    for c in contradictions:
                        full_text += f"• {c}\n"
                    full_text += "\n💡 Consider waiting for additional confirmation\n"
                
                if mtf_check:
                    # Generic MTF only
                    higher_tf = mtf_check.higher_tf
                    alignment = mtf_check.alignment.upper()
                    if alignment == "ALIGNED":
                        mtf_text = f"Higher timeframe {higher_tf} confirms the signal"
                    elif alignment == "CONFLICTING":
                        mtf_text = f"Higher timeframe {higher_tf} disagrees — caution advised"
                    else:
                        mtf_text = f"Higher timeframe {higher_tf} is mixed"
                    full_text += f"\n📊 {mtf_text}"
                    if mtf_check.warning:
                        full_text += f"\n⚠️ {mtf_check.warning}"
        else:
            # === FULL VIEW (PREMIUM) ===
            # Uses sanitized premium report builder — no exact internals leaked
            full_text = f"{DISCLAIMER_SHORT}\n\n" + report.to_telegram_text(tier='premium')
            full_text += "\n\n"
            
            # Position sizing — instrument-native units, anchored to
            # current price. sizing is a PositionSizeResult dataclass
            # (see position_sizer.py:16). The display distinguishes:
            #   - what you risk (2% of account, the max loss if SL hits)
            #   - what you control (position in BTC / lots / ounces)
            #   - what you put up (margin = position / leverage)
            sym = sizing.instrument
            # Instrument-native stop distance + position units.
            # Crypto: $ distance, units = coins, position in BTC.
            # XAUUSD: $ distance, units = ounces (1 lot = 100 oz).
            # FX: pip distance, units = base currency, lots = standard lots.
            if 'JPY' in sym:
                pip_size = 0.01
                pip_value_per_lot = 1000.0 / sizing.entry_price  # matches _calc_fx
                stop_pips = sizing.stop_distance / pip_size
                units_str = f"{sizing.lot_size:.2f} lots ({sizing.units:,.0f} base)"
                pos_value_str = f"${sizing.total_value_at_risk:,.0f} notional"
                stop_str = f"{stop_pips:.0f} pips (${sizing.stop_distance:.2f})"
            elif sym in ('EURUSD', 'GBPUSD', 'AUDUSD', 'USDCAD', 'NZDUSD'):
                # Non-JPY FX pairs: pip = 0.0001, ~$10 per pip per lot.
                pip_size = 0.0001
                stop_pips = sizing.stop_distance / pip_size
                units_str = f"{sizing.lot_size:.2f} lots ({sizing.units:,.0f} base)"
                pos_value_str = f"${sizing.total_value_at_risk:,.0f} notional"
                stop_str = f"{stop_pips:.1f} pips (${sizing.stop_distance:.5f})"
            elif sym == 'XAUUSD':
                stop_dollars = sizing.stop_distance
                oz_per_lot = 100
                stop_pips_xau = stop_dollars / 0.01  # 1 pip = 0.01 for XAU
                units_str = f"{sizing.lot_size:.2f} lots ({sizing.units:.1f} oz)"
                pos_value_str = f"${sizing.total_value_at_risk:,.0f} notional"
                stop_str = (f"${stop_dollars:.2f} ({stop_pips_xau:.0f} pips, "
                            f"{abs(stop_dollars / sizing.entry_price) * 100:.2f}%)")
            elif sym.endswith('USDT') or sym in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT',
                                                  'AVAXUSDT', 'BNBUSDT', 'ADAUSDT',
                                                  'DOTUSDT', 'MATICUSDT', 'LINKUSDT',
                                                  'LTCUSDT', 'BCHUSDT', 'XRPUSDT'):
                stop_pct = (sizing.stop_distance / sizing.entry_price) * 100
                # Asset symbol = strip 'USDT' suffix (BTCUSDT -> BTC, etc.)
                asset = sym.replace('USDT', '')
                units_str = f"{sizing.units:.4f} {asset}"
                pos_value_str = f"${sizing.total_value_at_risk:,.0f} notional"
                stop_str = (f"${sizing.stop_distance:,.2f} "
                            f"({stop_pct:.2f}% of ${sizing.entry_price:,.2f})")
            else:
                # Other / unknown — fall back to dollar display
                units_str = f"{sizing.units:,.4f} units"
                pos_value_str = f"${sizing.total_value_at_risk:,.0f} notional"
                stop_str = f"${sizing.stop_distance:,.2f}"
            full_text += f"""💰 **POSITION SIZING** ({sym})
━━━━━━━━━━━━━━━━━━━━━━
Account: ${sizing.account_balance:,.0f}
Risk: {sizing.risk_percent:.0f}% = ${sizing.risk_amount:,.0f} (max loss if SL hits)

📊 **TRADE LEVELS**
Entry: ${sizing.entry_price:,.5f}
SL:    ${sizing.stop_loss:,.5f}
Stop distance: {stop_str}

🎯 **POSITION**
Size: {units_str}
Position: {pos_value_str}
Leverage: {sizing.leverage_used:.0f}x
Margin Required: ${sizing.margin_required:,.0f}
"""
            # Leverage / stop warnings from the sizer (already instrument-
            # specific, see _calc_crypto/_calc_gold/_calc_fx in
            # position_sizer.py). Skip the platform_note footer — it
            # duplicates the max_leverage display, and the user already
            # sees "Leverage: Nx" above.
            if sizing.warning:
                # Strip the trailing [platform: ...] line if present
                warn_text = sizing.warning.split('\n[')[0]
                if warn_text.strip():
                    full_text += f"⚠️ {warn_text.strip()}\n"
            full_text += "\n"
            
            # Gap 2: AUTO-BLOCKED callout. The full news + events list
            # is now embedded in the report body itself (📰 TOP HEADLINES
            # and 📅 UPCOMING EVENTS, rendered by report_builder).
            # Here we just point the user to it and explain why the
            # signal was forced to NO TRADE. Only shows when the block
            # actually fired (i.e. high-impact event in the buffer).
            if news_block and news_block.get('blocked'):
                full_text += (
                    f"🚫 **AUTO-BLOCKED BY EVENT**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ {news_block['event']} in "
                    f"{news_block['minutes_until']} min "
                    f"({news_block['impact'].upper()} impact)\n"
                    f"Signal forced to NO TRADE. "
                    f"See 📅 UPCOMING EVENTS below.\n\n"
                )
            
            # Gap 3: Divergence — generic only
            # div is a DivergenceSignal dataclass (see divergence_detector.py:29).
            # Direction is encoded in div.type (DivergenceType enum: regular_bullish,
            # regular_bearish, hidden_bullish, hidden_bearish).
            if div_signals:
                div_lines = []
                for div in div_signals[:3]:  # max 3
                    dir_str = div.type.value.upper()  # e.g. "REGULAR_BULLISH"
                    if 'BULLISH' in dir_str:
                        div_lines.append("• Bullish divergence forming")
                    elif 'BEARISH' in dir_str:
                        div_lines.append("• Bearish divergence forming")
                    else:
                        div_lines.append("• Divergence signal forming")
                full_text += f"""🔄 **DIVERGENCE**
━━━━━━━━━━━━━━━━━━━━━━
{"\n".join(div_lines)}

"""
            
            # Gap 4: Regime — generic description only
            # regime is a RegimeResult dataclass (see regime_detector.py:29).
            # Fields used: regime (MarketRegime enum), structure ("HH_HL"/"LH_LL"/"MIXED").
            if regime:
                regime_name = regime.regime.value.replace('_', ' ').title()
                structure_label = regime.structure.replace('_', ' ').title()
                full_text += f"""🏛️ **MARKET REGIME**
━━━━━━━━━━━━━━━━━━━━━━
Environment: {regime_name}
Structure: {structure_label}

"""
            
            # Gap 5: Session — generic only
            # session is a SessionResult dataclass (see session_analyzer.py:24).
            # Fields used: session_name (already formatted e.g. "LONDON-NY OVERLAP"),
            # volatility_quality ("high"/"normal"/"low"/"unknown").
            if session:
                session_name = session.session_name
                vol_profile = session.volatility_quality.title() if session.volatility_quality else ''
                if vol_profile and vol_profile != 'Unknown':
                    session_text = f"{session_name} session, {vol_profile.lower()} volatility expected"
                else:
                    session_text = f"{session_name} session"
                full_text += f"""⏰ **SESSION CONTEXT**
━━━━━━━━━━━━━━━━━━━━━━
{session_text}

"""
            
            # Add AI reasoning if available — sanitize if too technical
            if ai_result and ai_result.reasoning:
                # Truncate to avoid revealing too much
                reasoning_short = ai_result.reasoning[:400]
                if len(ai_result.reasoning) > 400:
                    reasoning_short += "..."
                full_text += f"""🧠 **AI CONTEXT**
━━━━━━━━━━━━━━━━━━━━━━
{reasoning_short}

"""
            
            # Add MTF — generic only
            if mtf_check:
                higher_tf = mtf_check.higher_tf
                alignment = mtf_check.alignment.upper()
                if alignment == "ALIGNED":
                    mtf_text = f"Higher timeframe {higher_tf} confirms this direction"
                elif alignment == "CONFLICTING":
                    mtf_text = f"Higher timeframe {higher_tf} disagrees — reduce size or wait"
                else:
                    mtf_text = f"Higher timeframe {higher_tf} is mixed — partial confirmation"
                full_text += f"📊 **MULTI-TIMEFRAME VIEW**\n━━━━━━━━━━━━━━━━━━━━━━\n{mtf_text}\n"
                if mtf_check.warning:
                    full_text += f"⚠️ {mtf_check.warning}\n"

            # Truncate the Full view to stay under Telegram's 4096-char
            # message limit. The Premium report + news + events + position
            # sizing + divergence/regime/session/AI/MTF can easily exceed
            # 4096 chars on a busy day. Mirrors the truncation in
            # news_command (line ~2029) and events_command (line ~2080).
            if len(full_text) > 4000:
                full_text = full_text[:3990] + "…\n\n" + DISCLAIMER_SHORT

        # Record analysis in database (for signal tracking).
        # analysis_id is hoisted out of the try so we can wire it into the
        # post-report keyboard (Ask AI button) and cache the report context
        # for the Ask AI Q&A session.
        analysis_id = None
        try:
            analysis_id = await db.record_analysis(
                user_id=user_id,
                symbol=symbol,
                timeframe=tf,
                signal=report.overall_signal.value,
                confidence=report.signal_confidence,
                entry_price=entry_mid,
                stop_loss=report.stop_loss,
                take_profit=report.take_profit
            )

            # Create signal tracker for accuracy monitoring
            if report.overall_signal.value in ('STRONG_BUY', 'BUY', 'STRONG_SELL', 'SELL'):
                await db.create_signal_tracker(
                    user_id=user_id,
                    analysis_id=analysis_id,
                    symbol=symbol,
                    signal=report.overall_signal.value,
                    entry_price=entry_mid,
                    tp_price=report.take_profit,
                    sl_price=report.stop_loss
                )
        except Exception as e:
            logger.warning(f"Failed to record analysis: {e}")

        # Cache report context for Ask AI Q&A. The button on the post-report
        # message will read from this cache. Invalidate any prior context so
        # a stale "Ask AI about <old report>" button can't dangle.
        from datetime import datetime, timezone
        try:
            user_sessions.setdefault(user_id, {})
            user_sessions[user_id]['ai_context'] = {
                'analysis_id': analysis_id,
                'symbol': symbol,
                'timeframe': tf,
                'price': entry_mid,
                'signal': report.overall_signal.value,
                'confidence': report.signal_confidence,
                'entry_zone': report.entry_zone,
                'stop_loss': report.stop_loss,
                'take_profit': report.take_profit,
                'risk_reward': report.risk_reward,
                'support': (report.support or [])[:3],
                'resistance': (report.resistance or [])[:3],
                'report_excerpt': (full_text or '')[:4000],
                'created_at': datetime.now(timezone.utc),
            }
            # New report → drop any in-flight AI session for this user.
            ask_ai_sessions.pop(user_id, None)
        except Exception as e:
            logger.warning(f"Failed to cache ai_context: {e}")

        # Send chart first (don't fail if original message can't be deleted)
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg.message_id)
        except Exception as e:
            logger.warning(f"Could not delete original message: {e}")
        
        try:
            await context.bot.send_photo(
                chat_id=user_id, 
                photo=chart_bytes, 
                caption=f"📊 {symbol} | {tf} | {report.overall_signal.value}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"Markdown caption failed: {e}")
            await context.bot.send_photo(
                chat_id=user_id, 
                photo=chart_bytes, 
                caption=f"📊 {symbol} | {tf} | {report.overall_signal.value}"
            )
        
        # Split long report into chunks < 3500 chars to stay well under 4096 limit
        MAX_CHUNK = 3500
        chunks = []
        current_chunk = ""
        
        for line in full_text.split('\n'):
            if len(current_chunk) + len(line) + 1 > MAX_CHUNK:
                chunks.append(current_chunk)
                current_chunk = line + '\n'
            else:
                current_chunk += line + '\n'
        if current_chunk:
            chunks.append(current_chunk)
        
        # Send each chunk
        for i, chunk in enumerate(chunks):
            try:
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=chunk, 
                    parse_mode='Markdown'
                )
            except Exception:
                # Fallback: no markdown
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=chunk
                )

        # Trading Plan for user. Attach an Ask AI inline button so the user
        # can pivot to Q&A right after reading the plan (the most engaged
        # moment in the flow). The actual report context is in
        # user_sessions[uid]['ai_context']; the button is just an entry.
        trading_plan = build_trading_plan(report, sizing, user_id)
        ask_ai_btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 Ask AI about this report", callback_data=f'ask_about_{analysis_id or 0}')
        ]])
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=trading_plan,
                parse_mode='Markdown',
                reply_markup=ask_ai_btn,
            )
        except Exception:
            # Markdown fallback if the report contains chars that break parse
            await context.bot.send_message(
                chat_id=user_id,
                text=trading_plan,
                reply_markup=ask_ai_btn,
            )

        # Stats
        stats = await db.get_stats(user_id)
        limit = stats.get('daily_limit', 1)
        is_unlimited = isinstance(limit, str) or (isinstance(limit, int) and limit > 99999)
        if is_unlimited:
            usage_text = "\n━━━━━━━━━━━━━━━━━━━━━━\n📊 Usage: Unlimited\n✅ Unlimited tier\n━━━━━━━━━━━━━━━━━━━━━━"
        else:
            used = stats.get('daily_used', 0)
            remaining = max(0, limit - used)
            usage_text = f"\n━━━━━━━━━━━━━━━━━━━━━━\n📊 Usage: {used}/{limit} today\n⏳ {remaining} remaining\n━━━━━━━━━━━━━━━━━━━━━━"

        # Post-report keyboard: Ask AI (wired to this report) + Top News
        # + main menu. This is the last message in the analysis flow and
        # the cleanest place for the user to act on the report.
        post_kb = get_post_report_keyboard(analysis_id) if analysis_id else get_main_menu_keyboard('free')
        await context.bot.send_message(chat_id=user_id, text=usage_text, reply_markup=post_kb)

    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        try:
            await context.bot.edit_message_text(chat_id=user_id, message_id=msg.message_id,
                text=f"❌ Analysis failed: {str(e)[:200]}\nPlease try again.", reply_markup=get_main_menu_keyboard('free'))
        except Exception:
            await context.bot.send_message(chat_id=user_id,
                text=f"❌ Analysis failed. Please try again.", reply_markup=get_main_menu_keyboard('free'))


# ═══════════════════════════════════════════════
# TRADING PLAN BUILDER
# ═══════════════════════════════════════════════

def build_trading_plan(report, sizing, user_id: int) -> str:
    """Build actionable trading plan for the user.

    Uses the report's plan metadata (plan_style, time_in_trade_minutes,
    partial_tp_prices) to render plan-specific management rules.
    """

    signal = report.overall_signal.value
    direction = "LONG" if signal in ('STRONG_BUY', 'BUY') else "SHORT" if signal in ('STRONG_SELL', 'SELL') else "NEUTRAL"

    if direction == "NEUTRAL":
        return """📋 **TRADING PLAN**
━━━━━━━━━━━━━━━━━━━━━━

⚪ Signal: NEUTRAL

No trade recommended at this time.

💡 ACTION:
• Wait for price to reach support/resistance
• Re-analyze when trend becomes clearer
• Consider reducing position size if trading anyway

━━━━━━━━━━━━━━━━━━━━━━
"""

    entry_zone = f"{report.entry_zone[0]:,.2f} - {report.entry_zone[1]:,.2f}"
    sl = report.stop_loss
    tp = report.take_profit
    rr = report.risk_reward

    # Resolve the plan object from the report's stored plan_style + timeframe.
    # Falls back to swing if plan_style is unknown (defensive against older
    # reports that pre-date the plan metadata).
    plan = resolve_plan(getattr(report, 'plan_style', None), report.timeframe)
    time_exit_label = _format_time_in_trade(plan.time_in_trade_minutes)

    # Build the management rules block dynamically.
    # Always: move SL to breakeven at +1R + time-based exit.
    # One extra bullet per partial-TP level (50% size at each R-multiple).
    mgmt_lines = [
        "1. Move SL to breakeven after +1R profit",
    ]
    for i, _ in enumerate(plan.partial_tp_r, start=2):
        mgmt_lines.append(f"{i}. Take 50% partial at +{plan.partial_tp_r[i-2]:.1f}R")
    next_rule_num = len(mgmt_lines) + 1
    mgmt_lines.append(f"{next_rule_num}. Trail remaining with ATR-based stop")
    mgmt_lines.append(f"{next_rule_num + 1}. Exit by time: {time_exit_label}")
    management_rules = "\n".join(mgmt_lines)

    # Build the partial-TP price bullets, if the plan has any
    partial_tp_lines = ""
    if getattr(report, 'partial_tp_prices', ()):
        tp_bullets = "\n".join(
            f"   • TP{i+1} (+{r:.1f}R): {price:,.2f}"
            for i, (r, price) in enumerate(zip(plan.partial_tp_r, report.partial_tp_prices))
        )
        partial_tp_lines = f"\n**Partial Take-Profits:**\n{tp_bullets}\n"

    # Trade setup
    setup = f"""📋 **TRADING PLAN** ({plan.name})
━━━━━━━━━━━━━━━━━━━━━━

📊 {report.symbol} | {report.timeframe} | {direction}

🎯 **ENTRY STRATEGY**
━━━━━━━━━━━━━━━━━━━━━━
• Entry Zone: {entry_zone}
• Entry Type: Limit order at zone
• If price breaks through: Wait for pullback

🛑 **RISK MANAGEMENT**
━━━━━━━━━━━━━━━━━━━━━━
• Stop Loss: {sl:,.2f}
• Position Size: {sizing.lot_size:.4f} units
• Margin Required: ${sizing.margin_required:,.2f}
• Leverage: {sizing.leverage_used}x
• Risk: ${sizing.risk_amount:,.2f} ({sizing.risk_percent}%)

🎯 **EXIT STRATEGY**
━━━━━━━━━━━━━━━━━━━━━━
• Take Profit: {tp:,.2f}
• Risk:Reward = 1:{rr:.1f}
• Breakeven: {sizing.breakeven_price:,.2f}{partial_tp_lines}
📊 **MANAGEMENT RULES**
━━━━━━━━━━━━━━━━━━━━━━
{management_rules}

⚠️ **RISK WARNINGS**
━━━━━━━━━━━━━━━━━━━━━━
"""

    # Add warnings
    if sizing.warning:
        setup += f"• {sizing.warning}\n"
    if report.risk_warning:
        setup += f"• {report.risk_warning}\n"

    # Add contradiction warning
    if report.order_flow and 'error' not in report.order_flow:
        ofa = OrderFlowAnalyzer(report.symbol, depth_limit=100)
        of_text = ofa.get_confirmation(report.order_flow, direction)
        if 'CONTRADICTS' in of_text:
            setup += f"• {of_text}\n"

    setup += f"""
━━━━━━━━━━━━━━━━━━━━━━
💡 Remember: Never risk more than you can afford to lose

{DISCLAIMER_SHORT}
"""

    return setup


def _format_time_in_trade(minutes: int) -> str:
    """Format a time-in-trade value (in minutes) for display.

    Examples:
        60    -> "1 hour"
        90    -> "1.5 hours"
        360   -> "6 hours"
        1440  -> "1 day"
        4320  -> "3 days"
        20160 -> "14 days"
    """
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 1440:
        hours = minutes / 60
        if hours == int(hours):
            return f"{int(hours)} hour{'s' if hours != 1 else ''}"
        return f"{hours:.1f} hours"
    days = minutes / 1440
    if days == int(days):
        return f"{int(days)} day{'s' if days != 1 else ''}"
    return f"{days:.1f} days"


# (build_ai_report removed — the user-facing report path is
# AnalysisReport.to_telegram_text(tier='premium') in report_builder.py)


# ═══════════════════════════════════════════════
# CONVERSATION HANDLERS
# ═══════════════════════════════════════════════

async def handle_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    if data == 'back_start':
        await start(update, context)
        return ConversationHandler.END

    symbol = data.replace('pair_', '')
    user_sessions[user.id]['symbol'] = symbol
    display_symbol = symbol.replace('USDT', '/USDT').replace('USD', '/USD')

    text = f"""✅ Pair Selected: **{display_symbol}**

🔍 **Step 2/3: Choose Timeframe**

📌 **Recommended: H1 (Swing trading)**
   H1 is our most reliable timeframe — best balance of signal quality and trade frequency.

Select your trading style:

⏱ **M5** - Scalping (5 min candles)
📊 **M15** - Short term (15 min candles)
📈 **H1** - Swing trading (1 hour candles) ⭐ Recommended
📉 **H4** - Position trading (4 hour candles)
🗓 **D1** - Daily analysis

Tip: Higher timeframes = more reliable signals
"""
    await query.edit_message_text(text, reply_markup=get_timeframe_keyboard())
    return STEP_TIMEFRAME


async def handle_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    if data == 'back_pair':
        text = "🔍 **Step 1/3: Choose Trading Pair**\n\nSelect the pair you want to analyze:"
        await query.edit_message_text(text, reply_markup=get_pair_keyboard(user.id))
        return STEP_PAIR
    if data == 'back_start':
        await start(update, context)
        return ConversationHandler.END

    tf = data.replace('tf_', '')
    user_sessions[user.id]['timeframe'] = tf
    symbol = user_sessions[user.id]['symbol']
    display_symbol = symbol.replace('USDT', '/USDT').replace('USD', '/USD')

    tf_names = {'M5': '5 Minute', 'M15': '15 Minute', 'H1': '1 Hour', 'H4': '4 Hour', 'D1': 'Daily'}

    ollama_online = is_ollama_running_sync()

    text = f"""✅ Pair: **{display_symbol}**
✅ Timeframe: **{tf_names.get(tf, tf)}**

🔍 **Step 3/3: Choose Report Format**

How much detail do you want?

"""
    if ollama_online:
        text += """📊 **Full Report (Recommended)**
   -> Complete: signal + levels + indicators + patterns
   -> Order Flow (crypto only) + Fundamental + Divergence
   -> Position sizing + AI reasoning + risk assessment
   -> Best for making informed decisions

📱 **Simple Report**
   -> Quick: signal + entry/SL/TP + key levels
   -> Order Flow confirmation (crypto only)
   -> Fast — ideal for quick decisions

💡 Recommended: **Full Report** for best insights
"""
    else:
        text += """⚠️ **AI temporarily offline**
   -> Ollama is offline
   -> Code analysis running at full speed

📊 **Full Report**
   -> Complete technical analysis
   -> Order Flow + Fundamental context

📱 **Simple Report**
   -> Quick signal + levels
   -> Order Flow confirmation (crypto only)
"""

    await query.edit_message_text(text, reply_markup=get_report_type_keyboard())
    return STEP_TYPE


async def handle_analysis_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    if data == 'back_timeframe':
        symbol = user_sessions[user.id]['symbol']
        display_symbol = symbol.replace('USDT', '/USDT').replace('USD', '/USD')
        text = f"""✅ Pair Selected: **{display_symbol}**\n\n🔍 **Step 2/3: Choose Timeframe**"""
        await query.edit_message_text(text, reply_markup=get_timeframe_keyboard())
        return STEP_TIMEFRAME

    if data == 'type_code_fallback':
        await query.answer("AI is offline. Starting Ollama enables AI analysis.", show_alert=True)
        return STEP_TYPE

    analysis_type = data.replace('type_', '')
    user_sessions[user.id]['analysis_type'] = analysis_type

    symbol = user_sessions[user.id]['symbol']
    tf = user_sessions[user.id]['timeframe']
    display_symbol = symbol.replace('USDT', '/USDT').replace('USD', '/USD')

    type_names = {
        'report_full': '📊 Full Report',
        'report_simple': '📱 Simple Report',
    }

    text = f"""📋 **Analysis Summary**
━━━━━━━━━━━━━━━━━━━━━━

Pair: **{display_symbol}**
Timeframe: **{tf}**
Report: **{type_names.get(analysis_type, analysis_type)}**

Ready to run analysis?

Click **Run Analysis** below 👇
"""
    await query.edit_message_text(text, reply_markup=get_confirm_keyboard(symbol, tf, analysis_type))
    return STEP_CONFIRM


async def run_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔍 Analyzing...", show_alert=False)

    user = update.effective_user
    session = user_sessions.get(user.id, {})

    symbol = session.get('symbol', 'BTCUSDT')
    tf = session.get('timeframe', 'H1')
    analysis_type = session.get('analysis_type', 'ai_full')

    can, msg = await db.can_analyze(user.id)
    if not can:
        await query.edit_message_text(f"⛔ {msg}\n\n{get_free_tiers_text()}", reply_markup=get_upgrade_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(f"🔍 Analyzing **{symbol}** on **{tf}**...")
    await run_analysis_direct(update, context, user.id, symbol, tf, analysis_type)
    return ConversationHandler.END


# ═══════════════════════════════════════════════
# MENU HANDLERS
# ═══════════════════════════════════════════════

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == 'menu_analyze':
        return await analyze_command(update, context)

    elif data == 'menu_status':
        stats = await db.get_stats(user.id)
        if not stats:
            await query.edit_message_text("Please start with /start first.", reply_markup=get_main_menu_keyboard('free'))
            return

        tier = stats.get('tier', 'Free')
        used = stats.get('daily_used', 0)
        limit = stats.get('daily_limit', 1)
        total = stats.get('total_analyses', 0)
        resets = stats.get('resets_in', 'soon')
        is_unlimited = isinstance(limit, str) or (isinstance(limit, int) and limit > 99999)

        text = f"""📊 Your Statistics

Tier: {tier}
━━━━━━━━━━━━━━━━━━━━━━
Daily Used: {used}
{'✅ Unlimited access!' if is_unlimited else f'Remaining: {max(0, limit - used)}'}
{'Resets in: ' + resets if not is_unlimited else 'Never resets — unlimited!'}
━━━━━━━━━━━━━━━━━━━━━━
Total Analyses: {total}
━━━━━━━━━━━━━━━━━━━━━━
"""
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard('free'))

    elif data == 'menu_settings':
        return await settings_command(update, context)

    elif data == 'menu_upgrade':
        await query.edit_message_text(get_free_tiers_text(), reply_markup=get_upgrade_keyboard())

    elif data == 'menu_news':
        # Real-time headlines from RSS feeds (BTC + XAU + macro mixed).
        # For the upcoming-events calendar, see menu_events.
        try:
            items = await news_fetcher.get_headlines(limit=12)
        except Exception as e:
            logger.error(f"menu_news: fetch failed: {e}")
            await query.edit_message_text(
                "⚠️ News feed unavailable. Try again in a moment.",
                reply_markup=get_main_menu_keyboard('free'),
            )
            return
        if not items:
            await query.edit_message_text(
                "ℹ️ No headlines right now. Try again in a few minutes.",
                reply_markup=get_main_menu_keyboard('free'),
            )
            return
        body = format_headlines(items)
        text = f"{body}\n\n{DISCLAIMER_SHORT}"
        if len(text) > 4000:
            text = text[:3990] + "…\n\n" + DISCLAIMER_SHORT
        await query.edit_message_text(
            text,
            reply_markup=get_main_menu_keyboard('free'),
        )

    elif data == 'menu_events':
        # Upcoming high-impact events (CPI, NFP, FOMC, ECB) for all currencies.
        # For real-time headlines, see menu_news.
        try:
            body = calendar.format_upcoming(symbol=None, days_ahead=7)
        except Exception as e:
            logger.error(f"menu_events: calendar failed: {e}")
            await query.edit_message_text(
                "⚠️ Could not load events right now. Try again in a moment.",
                reply_markup=get_main_menu_keyboard('free'),
            )
            return
        text = f"📅 **UPCOMING EVENTS**\n\n{body}\n\n{DISCLAIMER_SHORT}"
        if len(text) > 4000:
            text = text[:3990] + "…\n\n" + DISCLAIMER_SHORT
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard('free'),
        )

    elif data == 'menu_ask_ai':
        # Shortcut: same flow as /askai, but launched from the main menu.
        # Educational when no recent report exists.
        ctx = user_sessions.get(user.id, {}).get('ai_context')
        if not ctx or not _ai_context_is_fresh(ctx):
            await query.edit_message_text(
                "ℹ️ No recent report. Run /analyze first, then tap Ask AI on the result "
                "to ask follow-up questions about it.",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard('free'),
            )
            return
        _start_ask_ai_session(user.id, ctx)
        await query.edit_message_text(
            f"🤖 **Ask AI** — type your question about {ctx['symbol']} {ctx['timeframe']}.\n"
            "Send /cancel to exit.\n\n"
            f"{DISCLAIMER_SHORT}",
            parse_mode='Markdown',
        )

    elif data == 'menu_help':
        text = """🎯 XOX AI Analysis Bot - Help

**Commands:**
/start - Start the bot
/analyze - Begin analysis flow
/quick SYMBOL TF [TYPE] - Quick analyze
/status - Check your usage
/upgrade - View deposit tiers
/news [btc|xau|macro|all] - Real headlines filtered for price impact
                       (add `all` to see every headline as scraped)
/events [SYMBOL] - Upcoming high-impact events (CPI, NFP, FOMC)
/askai - Ask follow-up questions about your last report
/help - Show this help

**Report Formats (chosen during /analyze):**
📊 Full Report (Recommended) - Complete analysis with everything
📱 Simple Report - Quick signal + key levels only

**Deposit Tiers:**
🔹 Free: $0 = 3/day
🔹 Silver: $100-500 = 20/day
🔹 Gold: $501-1000 = 50/day
🔹 Unlimited: $1001+ = No limit

**Supported Pairs:**
Crypto: BTC/USDT, ETH/USDT
Forex: EUR/USD
Commodity: XAU/USD (Gold)

**Recommended Timeframe:** H1 (Swing) — our most reliable setting.

**News vs Events:**
• 📰 /news — Real headlines (RSS), filtered for price impact by default.
  Use to see what's moving BTC/XAU *right now*.
  Examples: /news btc, /news xau, /news macro
  Add `all` (/news all) to see every headline as scraped.
• 📅 /events — Scheduled high-impact events (calendar). Use to see when the
  next CPI / NFP / FOMC drops.
  Examples: /events, /events EURUSD

**New Features:**
• 🤖 Ask AI — After a report, tap "Ask AI" to ask follow-up questions
  about entry, stop, take-profit, levels, R:R, or trade management.
  Or use /askai anytime (requires a recent /analyze).

**Q&A scope (Ask AI):**
• Your report's entry, stop, take-profit, levels, R:R
• Current market context for BTC, ETH, XAU, EUR/USD
• Trading concepts and trade management

**Features:**
• 🤖 AI-powered primary analysis
• 📊 Technical confirmation layers
• 🔀 Multi-timeframe alignment
• 📊 Market sentiment context
• 🔄 Recent pairs quick-access

📞 Support: @kysont89

{DISCLAIMER_SHORT}
"""
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard('free'))


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_obj = await db.get_user(user.id)
    
    if not user_obj:
        await update.message.reply_text("Please /start first.")
        return
    
    text = f"""⚙️ **Settings**

💰 Account Balance: ${user_obj.account_balance:,.2f}
⚖️ Risk per Trade: {user_obj.risk_percent}%
🔧 Leverage (Crypto): {user_obj.leverage_crypto}x
🔧 Leverage (MT5/XAU/FX): {user_obj.leverage_mt5}x
📊 Default Timeframe: {user_obj.default_timeframe}

Click below to change settings:"""
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=get_settings_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=get_settings_keyboard())


async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data
    
    if data == 'set_balance':
        await query.edit_message_text("💰 Enter your account balance in USD:\n\n(e.g., 5000 or 10000)")
        return STEP_SETTINGS
    elif data == 'set_risk':
        await query.edit_message_text("⚖️ Select risk percentage:", reply_markup=get_risk_keyboard())
        return STEP_SETTINGS
    elif data == 'set_leverage':
        await query.edit_message_text("🔧 Select leverage:", reply_markup=get_leverage_crypto_keyboard())
        return STEP_SETTINGS
    elif data == 'back_settings':
        return await settings_command(update, context)
    elif data.startswith('lev_'):
        lev = int(data.replace('lev_', ''))
        await db.update_user_settings(user.id, leverage=lev)
        await query.edit_message_text(f"✅ Leverage set to {lev}x", reply_markup=get_settings_keyboard())
        return STEP_SETTINGS
    elif data.startswith('risk_'):
        risk = int(data.replace('risk_', ''))
        await db.update_user_settings(user.id, risk_percent=risk)
        await query.edit_message_text(f"✅ Risk set to {risk}%", reply_markup=get_settings_keyboard())
        return STEP_SETTINGS


async def handle_settings_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    
    if text.isdigit():
        balance = int(text)
        await db.update_user_settings(user.id, account_balance=balance)
        await update.message.reply_text(f"✅ Account balance set to ${balance:,.2f}", reply_markup=get_settings_keyboard())
        return STEP_SETTINGS
    else:
        await update.message.reply_text("❌ Please enter a valid number (e.g., 5000 or 10000).")
        return STEP_SETTINGS


async def handle_settings_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global settings handler - works outside conversation."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data
    
    if data == 'back_settings':
        return await settings_command(update, context)
    elif data.startswith('lev_'):
        lev = data.replace('lev_', '')
        if lev == 'custom':
            # Check which leverage we're setting
            session = user_sessions.get(user.id, {})
            lev_type = session.get('setting_leverage', 'crypto')
            label = 'Crypto' if lev_type == 'crypto' else 'MT5'
            await query.edit_message_text(f"🔧 Enter custom {label} leverage:\n\n(e.g., 3, 15, 75, 125)")
            return
        lev = int(lev)
        # Check which leverage type
        session = user_sessions.get(user.id, {})
        lev_type = session.get('setting_leverage', 'crypto')
        if lev_type == 'mt5':
            await db.update_user_settings(user.id, leverage_mt5=lev)
            await query.edit_message_text(f"✅ MT5 Leverage set to {lev}x", reply_markup=get_settings_keyboard())
        else:
            await db.update_user_settings(user.id, leverage_crypto=lev)
            await query.edit_message_text(f"✅ Crypto Leverage set to {lev}x", reply_markup=get_settings_keyboard())
    elif data.startswith('risk_'):
        risk = data.replace('risk_', '')
        if risk == 'custom':
            await query.edit_message_text("⚖️ Enter custom risk percentage:\n\n(e.g., 0.5, 1.5, 4)")
            return
        risk = float(risk)
        await db.update_user_settings(user.id, risk_percent=risk)
        await query.edit_message_text(f"✅ Risk set to {risk}%", reply_markup=get_settings_keyboard())
    elif data == 'set_balance':
        await query.edit_message_text("💰 Enter your account balance in USD:\n\n(e.g., 5000 or 10000)")
    elif data == 'set_risk':
        await query.edit_message_text("⚖️ Select risk percentage:", reply_markup=get_risk_keyboard())
    elif data == 'set_lev_crypto':
        user_sessions[user.id] = user_sessions.get(user.id, {})
        user_sessions[user.id]['setting_leverage'] = 'crypto'
        await query.edit_message_text("🔧 Select Crypto leverage (Binance):", reply_markup=get_leverage_crypto_keyboard())
    elif data == 'set_lev_mt5':
        user_sessions[user.id] = user_sessions.get(user.id, {})
        user_sessions[user.id]['setting_leverage'] = 'mt5'
        await query.edit_message_text("🔧 Select MT5 leverage (Forex/Gold):", reply_markup=get_leverage_mt5_keyboard())
    elif data == 'set_timeframe':
        await query.edit_message_text("📊 Select your default timeframe:\n\nThis will be pre-selected when you start analysis.", reply_markup=get_default_timeframe_keyboard())
    elif data.startswith('dtf_'):
        tf = data.replace('dtf_', '')
        await db.update_user_settings(user.id, default_timeframe=tf)
        await query.edit_message_text(f"✅ Default timeframe set to {tf}", reply_markup=get_settings_keyboard())


async def handle_balance_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global text handler for balance, risk, leverage, verification, and Ask AI."""
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else None

    # Ask AI takes precedence over other text flows — if the user is in an
    # active AI session, every text message is a question to the model.
    if user.id in ask_ai_sessions:
        await handle_ai_question(update, context)
        return

    # Check if user is in verification flow
    v_session = verify_sessions.get(user.id)
    if v_session and v_session.get('step') == 'wallet':
        await handle_verify_input(update, context)
        return

    # Check if user was setting leverage (crypto or mt5)
    session = user_sessions.get(user.id, {})
    setting_lev = session.get('setting_leverage')
    
    if not text:
        return
    
    try:
        # Remove common suffixes
        clean_text = text.replace('%', '').replace('x', '').replace('X', '').replace(',', '').strip()
        value = float(clean_text)
        
        # If we know they were setting leverage, set the right field
        if setting_lev == 'crypto':
            await db.update_user_settings(user.id, leverage_crypto=value)
            await update.message.reply_text(
                f"✅ Crypto Leverage set to {value}x",
                reply_markup=get_settings_keyboard()
            )
            session.pop('setting_leverage', None)
            return
        elif setting_lev == 'mt5':
            await db.update_user_settings(user.id, leverage_mt5=value)
            await update.message.reply_text(
                f"✅ MT5 Leverage set to {value}x",
                reply_markup=get_settings_keyboard()
            )
            session.pop('setting_leverage', None)
            return
        
        # Determine what the user is setting based on value range
        if value >= 100:
            # Likely balance (e.g., 5000, 10000)
            await db.update_user_settings(user.id, account_balance=value)
            await update.message.reply_text(
                f"✅ Account balance set to ${value:,.2f}",
                reply_markup=get_settings_keyboard()
            )
        elif value > 0 and value <= 10:
            # Risk % (0.5-10)
            await db.update_user_settings(user.id, risk_percent=value)
            await update.message.reply_text(
                f"✅ Risk per trade set to {value}%",
                reply_markup=get_settings_keyboard()
            )
        else:
            # Leverage value but we don't know which type - ask
            await update.message.reply_text(
                f"❓ {value}x - Is this for Crypto or MT5?\nUse Settings buttons to set leverage.",
                reply_markup=get_settings_keyboard()
            )
    except ValueError:
        # Not a number - show main menu
        await update.message.reply_text(
            "Send /start to begin or click a button above.",
            reply_markup=get_main_menu_keyboard('free')
        )


async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'back_start':
        await start(update, context)


# ═══════════════════════════════════════════════
# OTHER COMMANDS
# ═══════════════════════════════════════════════

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    stats = await db.get_stats(user.id)
    if not stats:
        await update.message.reply_text("Please start with /start first.")
        return

    tier = stats.get('tier', 'Free')
    used = stats.get('daily_used', 0)
    limit = stats.get('daily_limit', 1)
    total = stats.get('total_analyses', 0)
    resets = stats.get('resets_in', 'soon')
    is_unlimited = isinstance(limit, str) or (isinstance(limit, int) and limit > 99999)

    text = f"""📊 Your Status

Tier: {tier}
━━━━━━━━━━━━━━━━━━━━━━
Daily: {used}/{limit if not is_unlimited else 'Unlimited'} used
{'✅ Unlimited!' if is_unlimited else f'Remaining: {max(0, limit - used)}'}
Resets in: {resets}
━━━━━━━━━━━━━━━━━━━━━━
Total Analyses: {total}
━━━━━━━━━━━━━━━━━━━━━━

💎 Want more? Use /upgrade
"""
    await update.message.reply_text(text)


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Single /upgrade entry point — uses the same shared text and keyboard
    as the 💎 Upgrade button and the daily-limit messages."""
    await update.message.reply_text(
        get_free_tiers_text(),
        reply_markup=get_upgrade_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """🎯 XOX AI Analysis Bot - Help

**Commands:**
/start - Start the bot
/analyze - Begin analysis flow
/quick SYMBOL TF [TYPE] - Quick analyze
   Examples:
   /quick BTCUSDT H1
   /quick XAUUSD H4 report_full
   /quick ETHUSDT M15 report_simple

   Types: report_full (default), report_simple

/status - Check your usage
/upgrade - View deposit tiers
/news [btc|xau|macro|all] - Real headlines filtered for price impact
                       (add `all` to see every headline as scraped)
/events [SYMBOL] - Upcoming high-impact events (CPI, NFP, FOMC)
/askai - Ask follow-up questions about your last report
/help - Show this help

**Report Formats (chosen during /analyze):**
📊 Full Report (Recommended) - Complete analysis with everything
📱 Simple Report - Quick signal + key levels only

**Signal Display:**
🟢 STRONG BUY — Only shown when trend +80 and confluence confirmed
🔴 STRONG SELL — Only shown when trend -80 and confluence confirmed
⚪ NO TRADE — When setup is weak or conflicting

**Deposit Tiers:**
🔹 Free: $0 = 3/day
🔹 Silver: $100-500 = 20/day
🔹 Gold: $501-1000 = 50/day
🔹 Unlimited: $1001+ = No limit

**Supported Pairs:**
Crypto: BTC/USDT, ETH/USDT
Forex: EUR/USD
Commodity: XAU/USD (Gold)

**Recommended Timeframe:** H1 (Swing) — our most reliable setting.

**News vs Events:**
• 📰 /news — Real headlines (RSS), filtered for price impact by default.
  Use to see what's moving BTC/XAU *right now*.
  Examples: /news btc, /news xau, /news macro
  Add `all` (/news all) to see every headline as scraped.
• 📅 /events — Scheduled high-impact events (calendar). Use to see when the
  next CPI / NFP / FOMC drops.
  Examples: /events, /events EURUSD

**New Features:**
• 🤖 Ask AI — After a report, tap "Ask AI" to ask follow-up questions
  about entry, stop, take-profit, levels, R:R, or trade management.
  Or use /askai anytime (requires a recent /analyze).

**Q&A scope (Ask AI):**
• Your report's entry, stop, take-profit, levels, R:R
• Current market context for BTC, ETH, XAU, EUR/USD
• Trading concepts and trade management

**How it works:**
1. Code calculates all indicators (instant)
2. AI adds reasoning bonus (~20-25s)
3. Only STRONG signals shown — reduces false entries

📞 Support: @kysont89

{DISCLAIMER_SHORT}
"""
    await update.message.reply_text(text, reply_markup=get_main_menu_keyboard('free'))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any in-flight flow — analysis conversation OR Ask AI session."""
    user = update.effective_user
    if user:
        ask_ai_sessions.pop(user.id, None)
        sess = user_sessions.get(user.id)
        if sess:
            sess.pop('ai_context', None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=get_main_menu_keyboard('free'))
    return ConversationHandler.END


# ═══════════════════════════════════════════════
# TOP NEWS / ASK AI FEATURES
# ═══════════════════════════════════════════════

def _ai_context_is_fresh(ctx: Dict) -> bool:
    """True if the cached report context is still within the TTL window."""
    from datetime import datetime, timezone, timedelta
    created = ctx.get('created_at')
    if not created:
        return False
    age = datetime.now(timezone.utc) - created
    return age <= timedelta(minutes=AI_CONTEXT_TTL_MINUTES)


def _build_ai_system_prompt(ctx: Dict) -> str:
    """Build the system prompt for Ask AI, embedding the report context."""
    return (
        "You are a trading-support assistant for the XOX AI Analysis Bot. "
        "Answer a follow-up question from a user about a specific analysis report.\n\n"
        f"Report context:\n"
        f"- Symbol: {ctx['symbol']}\n"
        f"- Timeframe: {ctx['timeframe']}\n"
        f"- Current price: {ctx['price']:.2f}\n"
        f"- Signal: {ctx['signal']} (confidence {ctx['confidence']:.0%})\n"
        f"- Entry zone: {ctx['entry_zone'][0]:,.2f} - {ctx['entry_zone'][1]:,.2f}\n"
        f"- Stop loss: {ctx['stop_loss']:,.2f}\n"
        f"- Take profit: {ctx['take_profit']:,.2f}\n"
        f"- Risk:Reward ≈ 1:{ctx['risk_reward']:.1f}\n"
        f"- Support: {', '.join(f'{s:,.2f}' for s in ctx.get('support', [])[:3]) or 'N/A'}\n"
        f"- Resistance: {', '.join(f'{r:,.2f}' for r in ctx.get('resistance', [])[:3]) or 'N/A'}\n\n"
        "Allowed scope:\n"
        f"- Questions about THIS {ctx['symbol']} report (entry, SL, TP, levels, R:R, management)\n"
        f"- General questions about the {ctx['symbol']} market or trading sessions\n"
        "- Trading concepts and education (R:R, position sizing, leverage, candles, patterns)\n"
        "- Trade management (breakeven, trailing stops, partials)\n\n"
        "Out of scope (politely redirect):\n"
        "- Other symbols not in the supported list\n"
        "- Specific tax, legal, or jurisdiction questions\n"
        "- Personal financial advice\n"
        "- Predictions of exact future prices\n\n"
        "Style: concise, Telegram-friendly. 2-5 short paragraphs max. "
        "Use *bold* and _italic_ only. Always end with a one-line reminder that "
        "this is educational, not financial advice."
    )


def _start_ask_ai_session(user_id: int, ctx: Dict) -> None:
    """Build system prompt and initialize ask_ai_sessions[uid]."""
    ask_ai_sessions[user_id] = {
        'context': ctx,
        'system': _build_ai_system_prompt(ctx),
        'history': [],
        'last_call': 0.0,
    }


# Topic argument aliases for /news [topic]
# `None` = don't topic-filter (return all sources). `all` is a special
# user-facing arg that means "show every headline" (skip the relevance
# filter as well).
_NEWS_TOPIC_ALIASES = {
    'btc':   ['btc', 'crypto'],
    'crypto':['btc', 'crypto'],
    'xau':   ['xau'],
    'gold':  ['xau'],
    'macro': ['macro'],
    'fed':   ['macro'],
    'forex': ['forex'],
    'all':   None,   # no topic filter; handled specially below
}


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/news [btc|xau|macro|forex|all] — real headlines from RSS feeds.

    WITHOUT a topic, shows a filtered, price-impact-relevant mix
    (BTC + XAU + macro, newest first) — headlines that don't plausibly
    move XAU or BTC are dropped.

    WITH a topic (btc, xau, gold, macro, fed, forex), filters to that
    topic and keeps the relevance filter on.

    With `all` (or no topic is fine too), shows every headline as
    scraped; relevant ones get a `matched:` and `💡` line, irrelevant
    ones are shown with no annotation.

    /news is for the news *right now* — geopolitical events, ETF flows,
    Fed speeches, dollar moves, etc. For the upcoming CPI/NFP/FOMC
    calendar, use /events instead.
    """
    if not update.message:
        return
    topics = None
    topic_label = None
    include_unfiltered = False
    if context.args:
        arg = context.args[0].lower()
        if arg not in _NEWS_TOPIC_ALIASES:
            await update.message.reply_text(
                "Usage: `/news [btc|xau|macro|forex|all]`\n\n"
                "Examples:\n"
                "• `/news` — price-impact headlines (default)\n"
                "• `/news btc` — Bitcoin/crypto headlines\n"
                "• `/news xau` — Gold headlines\n"
                "• `/news macro` — Fed / central bank / macro\n"
                "• `/news all` — every headline as scraped (no filter)",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard('free'),
            )
            return
        if arg == 'all':
            include_unfiltered = True
            topic_label = "all"
        else:
            topics = _NEWS_TOPIC_ALIASES[arg]
            topic_label = arg.upper()

    try:
        items = await news_fetcher.get_headlines(
            topics=topics, limit=12, include_unfiltered=include_unfiltered,
        )
    except Exception as e:
        logger.error(f"news_command: fetch failed: {e}")
        await update.message.reply_text(
            "⚠️ News feed unavailable. Try again in a moment.",
            reply_markup=get_main_menu_keyboard('free'),
        )
        return

    if not items:
        if include_unfiltered:
            # Should not happen — unfiltered mode returns everything in cache.
            await update.message.reply_text(
                "ℹ️ No headlines right now. Try again in a few minutes.",
                reply_markup=get_main_menu_keyboard('free'),
            )
        else:
            # Filtered mode found nothing price-impact-relevant.
            await update.message.reply_text(
                "ℹ️ No market-moving headlines right now. "
                "Use /news all to see every headline as scraped.",
                reply_markup=get_main_menu_keyboard('free'),
            )
        return

    # Build plain-text output (avoids Markdown parse errors on feeds with
    # weird characters in titles). 12 items * ~3 lines = ~36 lines; well
    # under Telegram's 4096 limit.
    body = format_headlines(
        items, topic_label=topic_label, show_unfiltered=include_unfiltered,
    )
    text = f"{body}\n\n{DISCLAIMER_SHORT}"
    if len(text) > 4000:
        text = text[:3990] + "…\n\n" + DISCLAIMER_SHORT

    # Send with no parse_mode to keep the output safe from any odd chars in
    # the titles. Bot users on phones get a clean, predictable display.
    await update.message.reply_text(
        text,
        reply_markup=get_main_menu_keyboard('free'),
    )


async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/events [SYMBOL] — upcoming CPI, NFP, FOMC, ECB and other calendar events.

    WITHOUT a symbol, shows all upcoming events. WITH a symbol, scopes to
    the currency that pair maps to (BTC/ETH/XAU -> USD, EURUSD -> EUR).

    /events is the *calendar* — scheduled high-impact events with date and
    time. For real-time headlines (what's moving the market *right now*),
    use /news instead.
    """
    if not update.message:
        return
    symbol: Optional[str] = None
    if context.args:
        symbol = context.args[0].upper()
        if symbol not in SUPPORTED_PAIRS:
            await update.message.reply_text(
                f"❌ Unsupported pair. Supported: {', '.join(SUPPORTED_PAIRS)}",
                reply_markup=get_main_menu_keyboard('free'),
            )
            return

    try:
        body = calendar.format_upcoming(symbol=symbol, days_ahead=7)
    except Exception as e:
        logger.error(f"events_command: calendar failed: {e}")
        await update.message.reply_text(
            "⚠️ Could not load events right now. Try again in a moment.",
            reply_markup=get_main_menu_keyboard('free'),
        )
        return

    header = (
        "📅 **UPCOMING EVENTS**"
        + (f" for `{symbol}`" if symbol else " — all currencies")
        + "\n\n"
    )
    text = f"{header}{body}\n\n{DISCLAIMER_SHORT}"

    # Stay under Telegram's 4096-char limit on long event lists
    if len(text) > 4000:
        text = text[:3990] + "…\n\n" + DISCLAIMER_SHORT

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard('free'),
    )


async def askai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/askai — Enter Ask AI mode. Requires a recent report context."""
    user = update.effective_user
    if not user or not update.message:
        return
    ctx = user_sessions.get(user.id, {}).get('ai_context')
    if not ctx or not _ai_context_is_fresh(ctx):
        await update.message.reply_text(
            "ℹ️ No recent report (or it's over 1h old). Run /analyze first, "
            "then I can answer questions about it.",
            reply_markup=get_main_menu_keyboard('free'),
        )
        return
    _start_ask_ai_session(user.id, ctx)
    await update.message.reply_text(
        "🤖 **Ask AI** — type your question about your report, the current "
        "market, or trading concepts.\n\n"
        "*Examples:*\n"
        "• What's my risk on this trade?\n"
        "• Why is the stop loss where it is?\n"
        "• Explain R:R in simple terms\n\n"
        "Send /cancel to exit.\n\n"
        f"{DISCLAIMER_SHORT}",
        parse_mode='Markdown',
    )


async def ask_ai_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback 'ask_about_<id>' from the post-report button."""
    query = update.callback_query
    await query.answer()
    if not query:
        return
    user = query.from_user
    ctx = user_sessions.get(user.id, {}).get('ai_context')
    if not ctx or not _ai_context_is_fresh(ctx):
        try:
            await query.edit_message_text(
                "ℹ️ Your report is over 1 hour old. Run /analyze for a fresh one.",
                reply_markup=get_main_menu_keyboard('free'),
            )
        except Exception:
            pass
        return
    if user.id in ask_ai_sessions:
        try:
            await query.edit_message_text(
                "🤖 Already in Ask AI mode — type your next question here.\n\n"
                f"{DISCLAIMER_SHORT}",
                parse_mode='Markdown',
            )
        except Exception:
            pass
        return
    _start_ask_ai_session(user.id, ctx)
    try:
        await query.edit_message_text(
            f"🤖 **Ask AI** — type your question about {ctx['symbol']} {ctx['timeframe']}.\n"
            "Send /cancel to exit.\n\n"
            f"{DISCLAIMER_SHORT}",
            parse_mode='Markdown',
        )
    except Exception:
        pass


async def handle_ai_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text input handler for Ask AI mode. Called from the global text dispatch
    when the user has an active ask_ai_sessions[uid] entry."""
    user = update.effective_user
    if not user or not update.message:
        return
    sess = ask_ai_sessions.get(user.id)
    if not sess:
        return  # not in AI mode — let other handlers process

    text = (update.message.text or '').strip()
    if not text:
        return

    # 4000-char cap (Telegram hard limit is 4096)
    if len(text) > AI_MAX_INPUT_CHARS:
        await update.message.reply_text(
            f"❌ Question too long (max {AI_MAX_INPUT_CHARS} chars). Try again."
        )
        return

    # Per-user throttle — protects Ollama from being hammered.
    now = asyncio.get_event_loop().time()
    if now - sess['last_call'] < AI_THROTTLE_SECONDS:
        await update.message.reply_text("⏳ Slow down, please wait a moment.")
        return
    sess['last_call'] = now

    # Append user turn, trim from the front to keep the last N messages.
    sess['history'].append({"role": "user", "content": text})
    if len(sess['history']) > MAX_AI_HISTORY:
        sess['history'] = sess['history'][-MAX_AI_HISTORY:]

    # Show typing indicator so the user knows the bot is working.
    try:
        await context.bot.send_chat_action(chat_id=user.id, action="typing")
    except Exception:
        pass

    reply = await ai_analyzer.ask(
        messages=sess['history'],
        system=sess['system'],
    )

    sess['history'].append({"role": "assistant", "content": reply})
    if len(sess['history']) > MAX_AI_HISTORY:
        sess['history'] = sess['history'][-MAX_AI_HISTORY:]

    # Send. Wrap with disclaimer. If the AI said it's offline, don't pretend.
    if "AI is offline" in reply:
        body = f"⚠️ {reply}\n\n{DISCLAIMER_SHORT}"
    else:
        body = f"{reply}\n\n{DISCLAIMER_SHORT}"

    # Stay under 4096 chars
    if len(body) > 4000:
        body = body[:3990] + "…\n\n" + DISCLAIMER_SHORT

    try:
        await update.message.reply_text(body, parse_mode='Markdown')
    except Exception:
        # Markdown fallback (model sometimes emits chars that break parse)
        await update.message.reply_text(body)


# ═══════════════════════════════════════════════
# VERIFICATION & ADMIN COMMANDS
# ═══════════════════════════════════════════════

# Kysont's Telegram ID (admin)
ADMIN_ID = 1701985687
VERIFY_DIR = r"C:\Users\User\.openclaw\workspace\projects\AI Analysis bot\verify_uploads"
os.makedirs(VERIFY_DIR, exist_ok=True)

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start verification flow: Wallet Address -> Deposit Proof Photo."""
    user = update.effective_user
    args = context.args
    
    # If user provided args directly, use as wallet address
    if len(args) >= 1:
        wallet_address = args[0]
        verify_sessions[user.id] = {'step': 'photo', 'broker_uid': wallet_address, 'wallet_address': wallet_address, 'broker': 'xox'}
        await update.message.reply_text(
            f"✅ Wallet address received: `{wallet_address[:10]}...{wallet_address[-6:]}`\n\n"
            f"📋 **Step 2/2: Deposit Proof**\n\n"
            f"Please **upload a screenshot** showing your deposit confirmation:\n"
            f"📸 Tap the attachment icon and send photo\n"
            f"• Must show deposit amount\n"
            f"• Must show your wallet address\n"
            f"• Screenshot from XOX app or email confirmation\n\n"
            f"Upload your screenshot below 👇",
            reply_markup=get_main_menu_keyboard('free'),
            parse_mode='Markdown'
        )
        return
    
    # Start fresh verification
    verify_sessions[user.id] = {'step': 'wallet', 'broker': 'xox'}
    await update.message.reply_text(
        "📋 **Verification for VIP Access**\n\n"
        "To unlock unlimited analyses, complete 2 steps:\n\n"
        "**Step 1/2: Wallet Address**\n"
        "Send your **XOX wallet address** used for deposit:\n"
        "(This proves you registered and deposited)\n\n"
        "Send your wallet address below 👇",
        reply_markup=get_main_menu_keyboard('free'),
        parse_mode='Markdown'
    )

async def handle_verify_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle verification step inputs (wallet address and deposit proof photo)."""
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else None
    
    session = verify_sessions.get(user.id)
    if not session:
        return  # Not in verification flow
    
    step = session.get('step')
    
    # Step 1: Receive Wallet Address (this IS their XOX account ID)
    if step == 'wallet':
        wallet_address = text
        if not wallet_address or len(wallet_address) < 10:
            await update.message.reply_text("❌ Invalid wallet address. Please send a valid XOX wallet address (usually 20+ characters).")
            return
        
        session['wallet_address'] = wallet_address
        session['broker_uid'] = wallet_address  # Use wallet as account ID
        session['step'] = 'photo'
        verify_sessions[user.id] = session
        
        await update.message.reply_text(
            f"✅ Wallet address received: `{wallet_address[:10]}...{wallet_address[-6:]}`\n\n"
            f"📋 **Step 2/2: Deposit Proof**\n\n"
            f"Please **upload a screenshot** showing your deposit confirmation:\n"
            f"📸 Tap the attachment icon and send photo\n"
            f"• Must show deposit amount\n"
            f"• Must show your wallet address\n"
            f"• Screenshot from XOX app or email confirmation\n\n"
            f"Upload your screenshot below 👇",
            parse_mode='Markdown'
        )
        return

async def handle_verify_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit proof photo upload."""
    user = update.effective_user
    photo = update.message.photo[-1] if update.message.photo else None
    
    session = verify_sessions.get(user.id)
    if not session or session.get('step') != 'photo':
        return  # Not expecting photo
    
    if not photo:
        await update.message.reply_text("❌ Please upload a photo (screenshot of deposit proof).")
        return
    
    # Download photo
    broker_uid = session.get('broker_uid', 'unknown')
    wallet_address = session.get('wallet_address', 'unknown')
    file_name = f"verify_{user.id}_{broker_uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    file_path = os.path.join(VERIFY_DIR, file_name)
    
    try:
        file = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(file_path)
        
        # Submit to database
        success = await db.submit_verification(user.id, 'xox', broker_uid, wallet_address)
        if success:
            await db.update_verification_proof(user.id, file_path)
            
            await update.message.reply_text(
                f"✅ **Verification Submitted!**\n\n"
                f"📊 Summary:\n"
                f"• Broker: XOX\n"
                f"• Wallet (Account): `{wallet_address[:10]}...{wallet_address[-6:]}`\n"
                f"• Deposit proof: Uploaded ✅\n\n"
                f"⏳ Status: **Pending Review**\n"
                f"You'll be notified once approved.",
                reply_markup=get_main_menu_keyboard('free'),
                parse_mode='Markdown'
            )
            
            # Notify admin
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🔔 **New Verification Request**\n\n"
                         f"User: {user.first_name} (@{user.username or 'N/A'})\n"
                         f"ID: `{user.id}`\n"
                         f"Broker: XOX\n"
                         f"Wallet (Account): `{wallet_address}`\n\n"
                         f"Use `/admin` to review."
                )
            except Exception:
                pass
            
            # Clear session
            verify_sessions.pop(user.id, None)
        else:
            await update.message.reply_text(
                "⏳ You already have a pending verification request.\n"
                "Please wait for approval."
            )
            verify_sessions.pop(user.id, None)
            
    except Exception as e:
        logger.error(f"[VerifyPhoto] Failed: {e}")
        await update.message.reply_text("❌ Failed to upload photo. Please try again with /verify")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: View pending verifications."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    
    pending = await db.get_pending_verifications()
    if not pending:
        await update.message.reply_text("✅ No pending verification requests.")
        return
    
    for req in pending:
        # Build per-request message
        wallet_display = req.get('wallet_address', 'N/A') or 'N/A'
        if wallet_display != 'N/A' and len(wallet_display) > 20:
            wallet_display = f"{wallet_display[:12]}...{wallet_display[-8:]}"
        
        text = (
            f"📋 **Verification Request #{req['id']}**\n\n"
            f"👤 User: {req['first_name']} (@{req['username'] or 'N/A'})\n"
            f"🆔 ID: `{req['user_id']}`\n"
            f"📊 Current Tier: {req['tier']}\n"
            f"⏰ Submitted: {req['requested_at'][:19]}\n\n"
            f"🏦 Broker: {req['broker'].upper()}\n"
            f"💳 Wallet (Account): `{wallet_display}`\n\n"
            f"**Approve:**\n"
            f"`/approve {req['user_id']} tier1` — Silver\n"
            f"`/approve {req['user_id']} tier2` — Gold\n"
            f"`/approve {req['user_id']} tier3` — Unlimited\n"
            f"`/reject {req['user_id']}` — Reject"
        )
        
        # Send text
        await update.message.reply_text(text, parse_mode='Markdown')
        
        # Send deposit proof photo if exists
        deposit_proof = req.get('deposit_proof')
        if deposit_proof and os.path.exists(deposit_proof):
            try:
                with open(deposit_proof, 'rb') as f:
                    await context.bot.send_photo(
                        chat_id=user.id,
                        photo=f,
                        caption=f"📸 Deposit Proof for User {req['user_id']}"
                    )
            except Exception as e:
                await update.message.reply_text(f"⚠️ Could not load photo: {e}")
        else:
            await update.message.reply_text("⚠️ No deposit proof photo uploaded yet.")
        
        # Separator
        await update.message.reply_text("━━━━━━━━━━━━━━━━━━━━━━")


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Approve user verification and set tier."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/approve USER_ID TIER`\n\n"
            "Tiers: free, tier1 (Silver), tier2 (Gold), tier3 (Unlimited), vip"
        )
        return
    
    try:
        target_id = int(args[0])
        new_tier = args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    
    valid_tiers = ['free', 'tier1', 'tier2', 'tier3', 'vip']
    if new_tier not in valid_tiers:
        await update.message.reply_text(f"❌ Invalid tier. Use: {', '.join(valid_tiers)}")
        return
    
    success = await db.approve_verification(target_id, new_tier)
    if success:
        tier_display = TIER_NAMES.get(UserTier(new_tier), new_tier.upper())
        await update.message.reply_text(
            f"✅ **Approved!**\n\n"
            f"User ID: `{target_id}`\n"
            f"New Tier: **{tier_display}**\n\n"
            f"User has been notified."
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 **Verification Approved!**\n\n"
                     f"Your account has been upgraded to **{tier_display}**.\n\n"
                     f"Daily limit: {TIER_LIMITS[UserTier(new_tier)] if UserTier(new_tier) in TIER_LIMITS else 'Unlimited'}\n\n"
                     f"Enjoy your upgraded analysis! 🚀",
                reply_markup=get_main_menu_keyboard(new_tier)
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("❌ Failed to approve. User may not have a pending request.")


async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Reject user verification."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/reject USER_ID`")
        return
    
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    
    success = await db.reject_verification(target_id)
    if success:
        await update.message.reply_text(f"❌ Rejected user `{target_id}`.")
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="❌ **Verification Rejected**\n\n"
                     "Your verification request was not approved.\n\n"
                     "Please ensure:\n"
                     "• You registered via the referral link\n"
                     "• You deposited the required amount\n"
                     "• Your Broker UID is correct\n\n"
                     "Contact @kysont89 for help."
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("❌ Failed to reject.")


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════

async def post_init(application: Application):
    await db.init()
    ollama_status = is_ollama_running_sync()
    logger.info(f"Database initialized")
    logger.info(f"Ollama status: {'Online' if ollama_status else 'Offline'}")


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return

    application = Application.builder().token(token).post_init(post_init).build()

    application.add_handler(CallbackQueryHandler(handle_back, pattern='^back_start$'))

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(analyze_command, pattern='^menu_analyze$'),
            CommandHandler('analyze', lambda u, c: analyze_command(u, c) or STEP_PAIR)
        ],
        states={
            STEP_PAIR: [CallbackQueryHandler(handle_pair, pattern='^pair_|back_start$')],
            STEP_TIMEFRAME: [CallbackQueryHandler(handle_timeframe, pattern='^tf_|back_pair$')],
            STEP_TYPE: [CallbackQueryHandler(handle_analysis_type, pattern='^report_|back_timeframe$')],
            STEP_CONFIRM: [
                CallbackQueryHandler(run_analysis, pattern='^run_analysis$'),
                CallbackQueryHandler(handle_analysis_type, pattern='^back_type$')
            ],
            STEP_SETTINGS: [
                CallbackQueryHandler(handle_settings, pattern='^set_|^lev_|^risk_|back_settings$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_text)
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CallbackQueryHandler(handle_back, pattern='^back_')
        ],
        per_message=False
    )

    # Settings handlers (outside conversation)
    application.add_handler(CallbackQueryHandler(handle_settings_global, pattern='^set_|^lev_|^risk_|back_settings$'))
    
    # Global text handler for balance input and verification text (outside conversation)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_balance_input))
    
    # Verification photo handler
    application.add_handler(MessageHandler(filters.PHOTO, handle_verify_photo))
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("upgrade", upgrade_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("quick", quick_analyze))

    # Top News + Ask AI commands / callbacks (added 2026-06-12, split 2026-06-15)
    application.add_handler(CommandHandler("news", news_command))
    application.add_handler(CommandHandler("events", events_command))
    application.add_handler(CommandHandler("askai", askai_command))
    application.add_handler(CallbackQueryHandler(ask_ai_entry, pattern='^ask_about_'))

    # Admin commands
    application.add_handler(CommandHandler("verify", verify_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("approve", approve_command))
    application.add_handler(CommandHandler("reject", reject_command))

    application.add_handler(CallbackQueryHandler(handle_menu, pattern='^menu_'))

    logger.info("Starting AI Analysis Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, poll_interval=1, timeout=30)


if __name__ == '__main__':
    main()
