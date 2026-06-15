"""
User Database for XOX Analysis Bot
SQLite-based user management with tier tracking + user settings.
"""

import aiosqlite
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass
from enum import Enum
import json


class UserTier(Enum):
    FREE = "free"
    TIER1 = "tier1"  # $100-499 deposit
    TIER2 = "tier2"  # $500-1999
    TIER3 = "tier3"  # $2000+
    VIP = "vip"      # $5000+ ShelbyMarket


TIER_LIMITS = {
    UserTier.FREE: 3,       # 3 analyses/day (was 1)
    UserTier.TIER1: 20,     # $100-500 deposit
    UserTier.TIER2: 50,     # $501-1000 deposit
    UserTier.TIER3: 999999, # $1001+ deposit (unlimited)
    UserTier.VIP: 999999    # $5000+ ShelbyMarket
}

TIER_NAMES = {
    UserTier.FREE: "Free",
    UserTier.TIER1: "Silver",
    UserTier.TIER2: "Gold",
    UserTier.TIER3: "Unlimited",
    UserTier.VIP: "VIP"
}

TIER_DEPOSIT_RANGES = {
    UserTier.FREE: (0, 0),
    UserTier.TIER1: (100, 500),
    UserTier.TIER2: (501, 1000),
    UserTier.TIER3: (1001, None),  # No upper limit
    UserTier.VIP: (5000, None)
}


@dataclass
class User:
    user_id: int
    username: Optional[str]
    first_name: str
    last_name: Optional[str]
    tier: UserTier
    language: str
    daily_used: int
    daily_reset: datetime
    broker: Optional[str] = None
    broker_uid: Optional[str] = None
    verified: bool = False
    created_at: Optional[datetime] = None
    # NEW: User settings
    account_balance: float = 10000.0
    risk_percent: float = 2.0
    leverage: float = 20.0
    leverage_crypto: float = 20.0
    leverage_mt5: float = 500.0
    default_timeframe: str = "H1"
    trading_style: str = "auto"  # 'auto' or 'scalp'/'day'/'swing'/'position'


class UserDatabase:
    """SQLite database for user management."""

    def __init__(self, db_path: str = "users.db"):
        self.db_path = db_path

    async def init(self):
        """Create tables if not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            # Users table with settings
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    tier TEXT DEFAULT 'free',
                    language TEXT DEFAULT 'en',
                    daily_used INTEGER DEFAULT 0,
                    daily_reset TEXT,
                    broker TEXT,
                    broker_uid TEXT,
                    verified INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    account_balance REAL DEFAULT 10000.0,
                    risk_percent REAL DEFAULT 2.0,
                    leverage REAL DEFAULT 20.0,
                    leverage_crypto REAL DEFAULT 20.0,
                    leverage_mt5 REAL DEFAULT 500.0,
                    default_timeframe TEXT DEFAULT 'H1',
                    trading_style TEXT DEFAULT 'auto'
                )
            """)

            # Migrate existing users.db files that pre-date trading_style.
            # SQLite raises OperationalError if the column already exists;
            # we swallow that to keep the migration idempotent.
            try:
                await db.execute(
                    "ALTER TABLE users ADD COLUMN trading_style TEXT DEFAULT 'auto'"
                )
            except Exception:
                pass

            # Analysis history (expanded)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS analysis_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    symbol TEXT,
                    timeframe TEXT,
                    signal TEXT,
                    confidence REAL,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    outcome TEXT DEFAULT 'pending',
                    pnl REAL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Signal tracking (for accuracy)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS signal_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    analysis_id INTEGER,
                    symbol TEXT,
                    signal TEXT,
                    entry_price REAL,
                    tp_price REAL,
                    sl_price REAL,
                    hit_tp INTEGER DEFAULT 0,
                    hit_sl INTEGER DEFAULT 0,
                    current_price REAL,
                    pnl_percent REAL,
                    status TEXT DEFAULT 'open',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (analysis_id) REFERENCES analysis_history(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS verification_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    broker TEXT,
                    broker_uid TEXT,
                    wallet_address TEXT,
                    deposit_proof TEXT,
                    status TEXT DEFAULT 'pending',
                    requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    verified_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Self-heal: add missing columns to verification_requests for upgrades
            cur = await db.execute("PRAGMA table_info(verification_requests)")
            existing_cols = {row[1] for row in await cur.fetchall()}
            if 'wallet_address' not in existing_cols:
                await db.execute("ALTER TABLE verification_requests ADD COLUMN wallet_address TEXT")
            if 'deposit_proof' not in existing_cols:
                await db.execute("ALTER TABLE verification_requests ADD COLUMN deposit_proof TEXT")

            await db.commit()

    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by Telegram ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                return User(
                    user_id=row['user_id'],
                    username=row['username'],
                    first_name=row['first_name'],
                    last_name=row['last_name'],
                    tier=UserTier(row['tier']),
                    language=row['language'],
                    daily_used=row['daily_used'],
                    daily_reset=datetime.fromisoformat(row['daily_reset']) if row['daily_reset'] else datetime.now(),
                    broker=row['broker'],
                    broker_uid=row['broker_uid'],
                    verified=bool(row['verified']),
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(),
                    account_balance=row['account_balance'] if row['account_balance'] is not None else 10000.0,
                    risk_percent=row['risk_percent'] if row['risk_percent'] is not None else 2.0,
                    leverage=row['leverage'] if row['leverage'] is not None else 20.0,
                    leverage_crypto=row['leverage_crypto'] if row['leverage_crypto'] is not None else 20.0,
                    leverage_mt5=row['leverage_mt5'] if row['leverage_mt5'] is not None else 500.0,
                    default_timeframe=row['default_timeframe'] if row['default_timeframe'] is not None else 'H1',
                    trading_style=row['trading_style'] if row['trading_style'] is not None else 'auto'
                )

    async def create_user(self, user_id: int, username: Optional[str],
                          first_name: str, last_name: Optional[str]):
        """Create new user."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO users
                (user_id, username, first_name, last_name, daily_reset,
                 account_balance, risk_percent, leverage, leverage_crypto, leverage_mt5, default_timeframe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, first_name, last_name, now,
                 10000.0, 2.0, 20.0, 20.0, 500.0, 'H1')
            )
            await db.commit()

    async def get_or_create_user(self, user_id: int, username: Optional[str],
                                   first_name: str, last_name: Optional[str]) -> User:
        """Get existing user or create new."""
        user = await self.get_user(user_id)
        if user:
            return user

        await self.create_user(user_id, username, first_name, last_name)
        return await self.get_user(user_id)

    async def update_user_settings(self, user_id: int, **settings) -> bool:
        """Update user settings (balance, risk, leverage, timeframe)."""
        valid_fields = {'account_balance', 'risk_percent', 'leverage', 'leverage_crypto', 'leverage_mt5', 'default_timeframe', 'trading_style'}
        filtered = {k: v for k, v in settings.items() if k in valid_fields}

        if not filtered:
            return False

        async with aiosqlite.connect(self.db_path) as db:
            set_clause = ", ".join(f"{k} = ?" for k in filtered.keys())
            values = list(filtered.values()) + [user_id]
            await db.execute(
                f"UPDATE users SET {set_clause} WHERE user_id = ?",
                values
            )
            await db.commit()
        return True

    async def can_analyze(self, user_id: int) -> tuple:
        """Check if user can perform analysis."""
        user = await self.get_user(user_id)
        if not user:
            return False, "User not found"

        # Reset daily counter if needed
        now = datetime.now()
        if now.date() > user.daily_reset.date():
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE users SET daily_used = 0, daily_reset = ? WHERE user_id = ?",
                    (now.isoformat(), user_id)
                )
                await db.commit()
            user.daily_used = 0

        limit = TIER_LIMITS[user.tier]
        if user.daily_used >= limit:
            return False, f"Daily limit reached ({limit}/day)"

        return True, "OK"

    async def increment_usage(self, user_id: int):
        """Increment daily usage counter."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET daily_used = daily_used + 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()

    async def record_analysis(self, user_id: int, symbol: str, timeframe: str,
                               signal: str, confidence: float,
                               entry_price: float, stop_loss: float,
                               take_profit: float) -> int:
        """Record analysis and return ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO analysis_history
                (user_id, symbol, timeframe, signal, confidence,
                 entry_price, stop_loss, take_profit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, symbol, timeframe, signal, confidence,
                 entry_price, stop_loss, take_profit)
            )
            await db.commit()
            return cursor.lastrowid

    async def create_signal_tracker(self, user_id: int, analysis_id: int,
                                     symbol: str, signal: str,
                                     entry_price: float, tp_price: float,
                                     sl_price: float):
        """Create signal tracking entry for accuracy monitoring."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO signal_tracking
                (user_id, analysis_id, symbol, signal, entry_price, tp_price, sl_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, analysis_id, symbol, signal, entry_price, tp_price, sl_price)
            )
            await db.commit()

    async def get_signal_stats(self, user_id: int) -> Dict:
        """Get signal accuracy statistics."""
        async with aiosqlite.connect(self.db_path) as db:
            # Total signals
            async with db.execute(
                "SELECT COUNT(*) FROM signal_tracking WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                total = (await cursor.fetchone())[0]

            # Resolved signals
            async with db.execute(
                "SELECT COUNT(*) FROM signal_tracking WHERE user_id = ? AND status = 'resolved'",
                (user_id,)
            ) as cursor:
                resolved = (await cursor.fetchone())[0]

            # TP hits
            async with db.execute(
                "SELECT COUNT(*) FROM signal_tracking WHERE user_id = ? AND hit_tp = 1",
                (user_id,)
            ) as cursor:
                tp_hits = (await cursor.fetchone())[0]

            # SL hits
            async with db.execute(
                "SELECT COUNT(*) FROM signal_tracking WHERE user_id = ? AND hit_sl = 1",
                (user_id,)
            ) as cursor:
                sl_hits = (await cursor.fetchone())[0]

            # Average P&L
            async with db.execute(
                "SELECT AVG(pnl_percent) FROM signal_tracking WHERE user_id = ? AND status = 'resolved'",
                (user_id,)
            ) as cursor:
                avg_pnl = (await cursor.fetchone())[0] or 0

        accuracy = (tp_hits / resolved * 100) if resolved > 0 else 0
        win_rate = (tp_hits / (tp_hits + sl_hits) * 100) if (tp_hits + sl_hits) > 0 else 0

        return {
            'total_signals': total,
            'resolved': resolved,
            'tp_hits': tp_hits,
            'sl_hits': sl_hits,
            'accuracy_percent': round(accuracy, 1),
            'win_rate': round(win_rate, 1),
            'avg_pnl_percent': round(avg_pnl, 2)
        }

    async def use_analysis(self, user_id: int):
        """Use an analysis credit (increment usage)."""
        await self.increment_usage(user_id)

    async def get_stats(self, user_id: int) -> Dict:
        """Get user stats."""
        user = await self.get_user(user_id)
        if not user:
            return {}

        signal_stats = await self.get_signal_stats(user_id)

        tier_name = TIER_NAMES.get(user.tier, "Unknown")
        limit = TIER_LIMITS.get(user.tier, 0)
        remaining = limit - user.daily_used

        return {
            'tier': tier_name,
            'tier_code': user.tier.value,
            'daily_used': user.daily_used,
            'daily_limit': limit,
            'daily_remaining': remaining,
            'account_balance': user.account_balance,
            'risk_percent': user.risk_percent,
            'leverage': user.leverage,
            'default_timeframe': user.default_timeframe,
            'signal_stats': signal_stats
        }

    # ═══════════════════════════════════════════════
    # VERIFICATION & TIER MANAGEMENT
    # ═══════════════════════════════════════════════

    async def submit_verification(self, user_id: int, broker: str, broker_uid: str, wallet_address: str = None) -> bool:
        """Submit a new verification request."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Check if already pending
            async with db.execute(
                "SELECT id FROM verification_requests WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    return False  # Already pending
            
            await db.execute(
                """INSERT INTO verification_requests 
                (user_id, broker, broker_uid, wallet_address, deposit_proof, status, requested_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (user_id, broker, broker_uid, wallet_address, None, now)
            )
            await db.commit()
        return True
    
    async def update_verification_proof(self, user_id: int, deposit_proof_path: str) -> bool:
        """Update deposit proof image path for pending request."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE verification_requests
                SET deposit_proof = ?
                WHERE user_id = ? AND status = 'pending'""",
                (deposit_proof_path, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_pending_verifications(self) -> List[Dict]:
        """Get all pending verification requests (for admin)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT v.*, u.username, u.first_name, u.last_name, u.tier
                FROM verification_requests v
                JOIN users u ON v.user_id = u.user_id
                WHERE v.status = 'pending'
                ORDER BY v.requested_at"""
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def approve_verification(self, user_id: int, new_tier: str) -> bool:
        """Approve verification and upgrade tier."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Update verification request
            await db.execute(
                """UPDATE verification_requests 
                SET status = 'approved', verified_at = ?
                WHERE user_id = ? AND status = 'pending'""",
                (now, user_id)
            )
            # Update user tier and verified flag
            await db.execute(
                "UPDATE users SET tier = ?, verified = 1 WHERE user_id = ?",
                (new_tier, user_id)
            )
            await db.commit()
        return True

    async def reject_verification(self, user_id: int) -> bool:
        """Reject verification request."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE verification_requests 
                SET status = 'rejected', verified_at = ?
                WHERE user_id = ? AND status = 'pending'""",
                (now, user_id)
            )
            await db.commit()
        return True

    async def get_user_verification_status(self, user_id: int) -> Optional[Dict]:
        """Get latest verification request for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM verification_requests 
                WHERE user_id = ? ORDER BY requested_at DESC LIMIT 1""",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
