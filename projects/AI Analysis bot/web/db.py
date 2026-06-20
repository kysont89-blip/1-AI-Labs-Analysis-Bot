"""1% AI Lab — async Postgres wrapper for the web layer.

This module deliberately mirrors the surface area of `bots/database.py`
(the SQLite path the Telegram bot still uses) so Phase 2's auth routes
can call the same `get_or_create_user`, `update_user_settings`,
`submit_verification` etc. — but against Neon.

bots/ is read-only. The Telegram bot keeps using users.db through the
cutover window. Once Phase 1 row-counts match, the next phases will
swap the bot over to this module without touching its call sites.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Optional

# Windows: psycopg async only works on SelectorEventLoop. We set the
# policy at import time so scripts (e.g. migrate_from_sqlite) get the
# right loop without ceremony. Under uvicorn this is a no-op.
if sys.platform == "win32":
    try:
        import asyncio
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy(),
        )
    except (AttributeError, DeprecationWarning):
        pass

import psycopg
from psycopg.rows import dict_row


# ── Tier enum (mirrors bots/database.py) ─────────────────────────

class UserTier(Enum):
    FREE  = "free"
    TIER1 = "tier1"   # $100-499 deposit
    TIER2 = "tier2"   # $500-1999
    TIER3 = "tier3"   # $2000+
    VIP   = "vip"     # $5000+ ShelbyMarket


TIER_LIMITS = {
    UserTier.FREE:  3,
    UserTier.TIER1: 20,
    UserTier.TIER2: 50,
    UserTier.TIER3: 999999,
    UserTier.VIP:   999999,
}


# ── User dataclass (mirrors bots/database.py) ─────────────────────

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
    account_balance: float = 10000.0
    risk_percent: float = 2.0
    leverage: float = 20.0
    leverage_crypto: float = 20.0
    leverage_mt5: float = 500.0
    default_timeframe: str = "H1"
    trading_style: str = "auto"


# ── Connection management ─────────────────────────────────────────

def _dsn() -> str:
    """DATABASE_URL from env. No fallback — fail loud."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy web/.env.example to web/.env "
            "and fill in the Neon connection string."
        )
    return url


@asynccontextmanager
async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """Yield an async connection. Caller commits or rolls back."""
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
        yield conn


@asynccontextmanager
async def get_cursor(commit: bool = True) -> AsyncIterator[psycopg.AsyncCursor]:
    """Yield a cursor with automatic commit on success."""
    async with get_conn() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            yield cur
            if commit:
                await conn.commit()


# ── User-facing API (mirrors bots/database.py) ────────────────────

class Database:
    """Async Postgres-backed user store.

    Method names + signatures mirror bots.database.UserDatabase so the
    two can be swapped by import without changing call sites.
    """

    # ── read ─────────────────────────────────────────────────────

    async def get_user(self, user_id: int) -> Optional[User]:
        async with get_cursor(commit=False) as cur:
            await cur.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
            return _row_to_user(row) if row else None

    # ── write ────────────────────────────────────────────────────

    async def create_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        last_name: Optional[str],
    ) -> None:
        async with get_cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users (
                    user_id, username, first_name, last_name,
                    daily_reset, account_balance, risk_percent,
                    leverage, leverage_crypto, leverage_mt5,
                    default_timeframe
                )
                VALUES (%s, %s, %s, %s, NOW(), 10000.0, 2.0,
                        20.0, 20.0, 500.0, 'H1')
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id, username, first_name, last_name),
            )

    async def get_or_create_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        last_name: Optional[str],
    ) -> User:
        user = await self.get_user(user_id)
        if user:
            return user
        await self.create_user(user_id, username, first_name, last_name)
        return await self.get_user(user_id)

    async def update_user_settings(self, user_id: int, **settings) -> bool:
        """Update user settings. Mirrors bots/database.py:241."""
        valid_fields = {
            "account_balance", "risk_percent", "leverage",
            "leverage_crypto", "leverage_mt5", "default_timeframe",
            "trading_style",
        }
        filtered = {k: v for k, v in settings.items() if k in valid_fields}
        if not filtered:
            return False

        set_clause = ", ".join(f"{k} = %s" for k in filtered)
        values = list(filtered.values()) + [user_id]
        async with get_cursor() as cur:
            await cur.execute(
                f"UPDATE users SET {set_clause} WHERE user_id = %s",
                values,
            )
            return cur.rowcount > 0

    # ── usage counter (Phase 4 uses this) ────────────────────────

    async def increment_usage(self, user_id: int) -> None:
        async with get_cursor() as cur:
            await cur.execute(
                "UPDATE users SET daily_used = daily_used + 1 "
                "WHERE user_id = %s",
                (user_id,),
            )

    async def can_analyze(self, user_id: int) -> tuple[bool, str]:
        user = await self.get_user(user_id)
        if not user:
            return False, "User not found"

        now = datetime.utcnow()
        if user.daily_reset is None or now.date() > user.daily_reset.date():
            async with get_cursor() as cur:
                await cur.execute(
                    "UPDATE users SET daily_used = 0, daily_reset = NOW() "
                    "WHERE user_id = %s",
                    (user_id,),
                )
            user.daily_used = 0

        limit = TIER_LIMITS.get(user.tier, 0)
        if user.daily_used >= limit:
            return False, f"Daily limit reached ({limit}/day)"
        return True, "OK"

    # ── verification (Phase 2 uses this) ─────────────────────────

    async def submit_verification(
        self,
        user_id: int,
        broker: str,
        broker_uid: str,
        wallet_address: Optional[str] = None,
    ) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                "SELECT id FROM verification_requests "
                "WHERE user_id = %s AND status = 'pending'",
                (user_id,),
            )
            if await cur.fetchone():
                return False
            await cur.execute(
                """
                INSERT INTO verification_requests
                    (user_id, broker, broker_uid, wallet_address,
                     deposit_proof, status, requested_at)
                VALUES (%s, %s, %s, %s, NULL, 'pending', NOW())
                """,
                (user_id, broker, broker_uid, wallet_address),
            )
        return True

    async def update_verification_proof(
        self, user_id: int, deposit_proof_path: str,
    ) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                "UPDATE verification_requests "
                "SET deposit_proof = %s "
                "WHERE user_id = %s AND status = 'pending'",
                (deposit_proof_path, user_id),
            )
            return cur.rowcount > 0

    async def get_user_verification_status(
        self, user_id: int,
    ) -> Optional[dict]:
        async with get_cursor(commit=False) as cur:
            await cur.execute(
                "SELECT * FROM verification_requests "
                "WHERE user_id = %s "
                "ORDER BY requested_at DESC LIMIT 1",
                (user_id,),
            )
            return await cur.fetchone()

    async def get_pending_verifications(self) -> list[dict]:
        async with get_cursor(commit=False) as cur:
            await cur.execute(
                """
                SELECT v.*, u.username, u.first_name, u.last_name, u.tier
                FROM verification_requests v
                JOIN users u ON v.user_id = u.user_id
                WHERE v.status = 'pending'
                ORDER BY v.requested_at
                """,
            )
            return await cur.fetchall()

    async def approve_verification(self, user_id: int, new_tier: str) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                "UPDATE verification_requests "
                "SET status = 'approved', verified_at = NOW() "
                "WHERE user_id = %s AND status = 'pending'",
                (user_id,),
            )
            await cur.execute(
                "UPDATE users SET tier = %s, verified = TRUE "
                "WHERE user_id = %s",
                (new_tier, user_id),
            )
        return True

    async def reject_verification(self, user_id: int) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                "UPDATE verification_requests "
                "SET status = 'rejected', verified_at = NOW() "
                "WHERE user_id = %s AND status = 'pending'",
                (user_id,),
            )
        return True


# ── Row mapper ─────────────────────────────────────────────────────

def _row_to_user(row: dict) -> User:
    return User(
        user_id=row["user_id"],
        username=row.get("username"),
        first_name=row.get("first_name") or "",
        last_name=row.get("last_name"),
        tier=UserTier(row["tier"]) if row.get("tier") else UserTier.FREE,
        language=row.get("language") or "en",
        daily_used=row.get("daily_used") or 0,
        daily_reset=row["daily_reset"] if row.get("daily_reset") else datetime.utcnow(),
        broker=row.get("broker"),
        broker_uid=row.get("broker_uid"),
        verified=bool(row.get("verified")),
        created_at=row.get("created_at"),
        account_balance=row.get("account_balance") or 10000.0,
        risk_percent=row.get("risk_percent") or 2.0,
        leverage=row.get("leverage") or 20.0,
        leverage_crypto=row.get("leverage_crypto") or 20.0,
        leverage_mt5=row.get("leverage_mt5") or 500.0,
        default_timeframe=row.get("default_timeframe") or "H1",
        trading_style=row.get("trading_style") or "auto",
    )


# Singleton — Phase 2+ will reuse this in `deps.py`.
db = Database()
