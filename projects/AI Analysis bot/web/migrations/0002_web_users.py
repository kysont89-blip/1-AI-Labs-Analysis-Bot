"""Add the web_users table to Neon.

Phase 2 introduces web-only signups. web_users is a sibling of the
existing `users` table — it carries email + password_hash + a link
(neon_user_id) to the `users` row once a Telegram and web account
get merged in Phase 5.

Run once:
    set DATABASE_URL=postgresql://...
    python web/migrations/0002_web_users.py
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

import psycopg

DDL = """
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
"""


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        return 1
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        # verify
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = 'web_users' "
                "ORDER BY ordinal_position"
            )
            rows = cur.fetchall()
    print("web_users columns:")
    for r in rows:
        print(f"  {r[0]:20s} {r[1]}")
    print("OK web_users applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
