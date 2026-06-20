-- 1% AI Lab — Phase 1: Neon Postgres schema (initial migration)
--
-- Ported from bots/database.py:82-171 (SQLite). Column-for-column, with
-- type translations:
--   INTEGER PRIMARY KEY AUTOINCREMENT  ->  BIGSERIAL PRIMARY KEY
--   TEXT                               ->  TEXT
--   REAL                               ->  DOUBLE PRECISION
--   INTEGER 0/1 (boolean)              ->  BOOLEAN
--   TEXT DEFAULT CURRENT_TIMESTAMP     ->  TIMESTAMPTZ DEFAULT NOW()
--
-- Decisions locked with user 2026-06-17:
--   * `verified` -> BOOLEAN
--   * `tier`     -> VARCHAR(16)  (not ENUM — easier to alter later)
--
-- bots/ stays untouched. The Telegram bot still uses users.db (SQLite)
-- during the cutover window; once Phase 1 verifies row counts match,
-- Phase 5 flips the bot to Neon too.

BEGIN;

-- ── users ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id          BIGSERIAL PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    last_name        TEXT,
    tier             VARCHAR(16)  DEFAULT 'free',
    language         TEXT         DEFAULT 'en',
    daily_used       INTEGER      DEFAULT 0,
    daily_reset      TIMESTAMPTZ,
    broker           TEXT,
    broker_uid       TEXT,
    verified         BOOLEAN      DEFAULT FALSE,
    created_at       TIMESTAMPTZ  DEFAULT NOW(),
    account_balance  DOUBLE PRECISION DEFAULT 10000.0,
    risk_percent     DOUBLE PRECISION DEFAULT 2.0,
    leverage         DOUBLE PRECISION DEFAULT 20.0,
    leverage_crypto  DOUBLE PRECISION DEFAULT 20.0,
    leverage_mt5     DOUBLE PRECISION DEFAULT 500.0,
    default_timeframe TEXT        DEFAULT 'H1',
    trading_style    TEXT         DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_users_tier       ON users (tier);
CREATE INDEX IF NOT EXISTS idx_users_verified   ON users (verified);

-- ── analysis_history ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_history (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT,
    symbol       TEXT,
    timeframe    TEXT,
    signal       TEXT,
    confidence   DOUBLE PRECISION,
    entry_price  DOUBLE PRECISION,
    stop_loss    DOUBLE PRECISION,
    take_profit  DOUBLE PRECISION,
    outcome      TEXT         DEFAULT 'pending',
    pnl          DOUBLE PRECISION,
    timestamp    TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT fk_analysis_history_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analysis_history_user_ts
    ON analysis_history (user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_history_symbol
    ON analysis_history (symbol, timestamp DESC);

-- ── signal_tracking ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_tracking (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT,
    analysis_id    BIGINT,
    symbol         TEXT,
    signal         TEXT,
    entry_price    DOUBLE PRECISION,
    tp_price       DOUBLE PRECISION,
    sl_price       DOUBLE PRECISION,
    hit_tp         BOOLEAN      DEFAULT FALSE,
    hit_sl         BOOLEAN      DEFAULT FALSE,
    current_price  DOUBLE PRECISION,
    pnl_percent    DOUBLE PRECISION,
    status         TEXT         DEFAULT 'open',
    created_at     TIMESTAMPTZ  DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ,
    CONSTRAINT fk_signal_tracking_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_signal_tracking_analysis
        FOREIGN KEY (analysis_id) REFERENCES analysis_history (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_tracking_user_status
    ON signal_tracking (user_id, status);
CREATE INDEX IF NOT EXISTS idx_signal_tracking_symbol
    ON signal_tracking (symbol, created_at DESC);

-- ── verification_requests ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS verification_requests (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT,
    broker          TEXT,
    broker_uid      TEXT,
    wallet_address  TEXT,
    deposit_proof   TEXT,
    status          TEXT         DEFAULT 'pending',
    requested_at    TIMESTAMPTZ  DEFAULT NOW(),
    verified_at     TIMESTAMPTZ,
    CONSTRAINT fk_verification_requests_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_verification_requests_status
    ON verification_requests (status, requested_at);

-- ── web_users ─────────────────────────────────────────────────────
-- Separate table for web signups. `web_user_id` is the integer we mint
-- at signup (>= 10^15, see web/deps.py) and it IS the primary key for
-- the web account. `neon_user_id` is the link into the `users` table
-- the Telegram bot also writes to — null until Phase 5 wires the merge.
-- Email is the login identifier and is unique.
CREATE TABLE IF NOT EXISTS web_users (
    web_user_id     BIGINT       PRIMARY KEY,
    email           TEXT         NOT NULL UNIQUE,
    password_hash   TEXT         NOT NULL,
    is_admin        BOOLEAN      DEFAULT FALSE,
    neon_user_id    BIGINT,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    last_login      TIMESTAMPTZ,
    CONSTRAINT fk_web_users_neon_user
        FOREIGN KEY (neon_user_id) REFERENCES users (user_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_web_users_neon_user
    ON web_users (neon_user_id);

COMMIT;
