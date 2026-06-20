"""One-shot SQLite → Neon migration for Phase 1.

Copies users.db (used by the Telegram bot) into a fresh Neon Postgres
instance. Run AFTER 0001_init.sql has been applied to Neon.

Usage:
    set DATABASE_URL=postgresql://...
    python web/migrate_from_sqlite.py [--sqlite-path users.db]

The script:
  1. Connects to SQLite + Neon.
  2. Reads each table in dependency order (parents first).
  3. Coerces SQLite types to Postgres types (0/1 -> bool, ISO strings
     -> timestamptz, REAL stays the same, TEXT stays the same).
  4. Inserts via COPY-equivalent INSERT ... ON CONFLICT DO NOTHING so
     re-running is safe.
  5. Prints a row-count parity table at the end.

bots/ is untouched. After migration, the Telegram bot still uses
users.db. Phase 5 will flip the bot to Neon once parity is confirmed.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdio on Windows so the parity table renders cleanly
# even when PowerShell's default cp1252 is in play.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

import psycopg


# ── Column type coercions ─────────────────────────────────────────
# Maps SQLite column names to a coercion function. Anything not in
# this map passes through as a string.

def _to_bool(v):
    if v is None:
        return None
    return bool(v)


def _to_ts(v):
    """SQLite stores TIMESTAMPTZ as ISO strings (or NULL). Parse them."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    # SQLite CURRENT_TIMESTAMP -> "YYYY-MM-DD HH:MM:SS"
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return v  # let psycopg try its own parsing as a last resort


# Per-table column coercion specs. Keys = column name, value = fn.
COERCIONS: dict[str, dict[str, callable]] = {
    "users": {
        "verified": _to_bool,
        "daily_reset": _to_ts,
        "created_at": _to_ts,
    },
    "analysis_history": {
        "timestamp": _to_ts,
    },
    "signal_tracking": {
        "hit_tp": _to_bool,
        "hit_sl": _to_bool,
        "created_at": _to_ts,
        "resolved_at": _to_ts,
    },
    "verification_requests": {
        "requested_at": _to_ts,
        "verified_at": _to_ts,
    },
}


# ── Helpers ────────────────────────────────────────────────────────

def _coerce_row(table: str, row: tuple, columns: list[str]) -> tuple:
    spec = COERCIONS.get(table, {})
    out = []
    for col, val in zip(columns, row):
        fn = spec.get(col)
        out.append(fn(val) if fn else val)
    return tuple(out)


def _copy_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection,
    table: str,
    *,
    skip_orphans_for: tuple[str, ...] = (),
) -> tuple[int, int, int]:
    """Copy one table. Returns (sqlite_count, pg_count, skipped).

    `skip_orphans_for` = column names whose values are FK references to
    other tables. If a row's FK value is not present in the destination
    table, the row is logged and skipped (not inserted) instead of
    failing the whole migration. This handles SQLite data the bot
    accumulated without FK enforcement.
    """
    scur = sqlite_conn.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in scur.description]
    rows = scur.fetchall()
    scur.close()
    sqlite_count = len(rows)

    if not rows:
        print(f"  {table}: 0 rows in SQLite — skipping")
        return 0, 0, 0

    # Pre-fetch existing FK targets for orphan checks
    fk_existing: dict[str, set] = {}
    for fk_col in skip_orphans_for:
        # `fk_col` is e.g. "user_id" -> parents table is "users"
        with pg_conn.cursor() as cur:
            cur.execute(f"SELECT {fk_col} FROM users")
            fk_existing[fk_col] = {r[0] for r in cur.fetchall()}

    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )

    inserted = 0
    skipped = 0
    with pg_conn.cursor() as cur:
        for row in rows:
            row_dict = dict(zip(columns, row))
            # Orphan check
            is_orphan = False
            for fk_col in skip_orphans_for:
                val = row_dict.get(fk_col)
                if val is not None and val not in fk_existing[fk_col]:
                    print(
                        f"    skip {table} id={row_dict.get('id', '?')}: "
                        f"orphan {fk_col}={val}",
                    )
                    is_orphan = True
                    break
            if is_orphan:
                skipped += 1
                continue

            coerced = _coerce_row(table, row, columns)
            cur.execute(sql, coerced)
            inserted += 1
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = cur.fetchone()[0]

    pg_conn.commit()
    if skipped:
        print(
            f"  {table}: copied {inserted} rows, "
            f"skipped {skipped} orphans (now {pg_count} in Neon)",
        )
    else:
        print(f"  {table}: copied {inserted} rows (now {pg_count} in Neon)")
    return sqlite_count, pg_count, skipped


def _sqlite_count(sqlite_conn: sqlite3.Connection, table: str) -> int:
    return sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ── Main ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite-path",
        default="users.db",
        help="Path to the source SQLite file (default: ./users.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect and count, but do not write.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        print("  export DATABASE_URL=postgresql://...", file=sys.stderr)
        return 1

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1

    print(f"Source SQLite: {sqlite_path.resolve()}")
    print(f"Target Neon:   {db_url.split('@')[-1]}")
    print()

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row

    # Sanity check tables exist in source
    src_tables = {
        r[0]
        for r in sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    needed = {"users", "analysis_history", "signal_tracking", "verification_requests"}
    missing = needed - src_tables
    if missing:
        print(f"ERROR: missing tables in SQLite: {missing}", file=sys.stderr)
        sqlite_conn.close()
        return 1

    pg_conn = psycopg.connect(db_url)

    if args.dry_run:
        print("DRY RUN — counts only:")
        for t in needed:
            print(f"  {t}: sqlite={_sqlite_count(sqlite_conn, t)}")
        pg_conn.close()
        sqlite_conn.close()
        return 0

    # Copy in dependency order
    try:
        print("Copying tables (dependency order):")
        results = {}
        # Tables whose `user_id` is a FK to users — orphan-check
        orphan_check_tables = {
            "analysis_history": ("user_id",),
            "signal_tracking": ("user_id",),
            "verification_requests": ("user_id",),
        }
        for table in ("users", "analysis_history",
                      "signal_tracking", "verification_requests"):
            sqlite_count, pg_count, skipped = _copy_table(
                sqlite_conn, pg_conn, table,
                skip_orphans_for=orphan_check_tables.get(table, ()),
            )
            results[table] = (sqlite_count, pg_count, skipped)
    finally:
        pg_conn.close()
        sqlite_conn.close()

    # Parity report
    print()
    print("Row-count parity:")
    print(f"  {'table':<24} {'sqlite':>8} {'neon':>8} {'skipped':>9} {'delta':>8}")
    print(f"  {'-'*24} {'-'*8} {'-'*8} {'-'*9} {'-'*8}")
    for t, (s, p, sk) in results.items():
        # Delta = rows in Neon that came from THIS migration.
        # For the first table, pg_count includes everything in Neon.
        # For dependent tables, expected delta is (sqlite - skipped) unless
        # there's pre-existing data. We surface both numbers, no judgment.
        delta = p - s
        flag = "" if delta <= 0 else f"  ⚠ pre-existing data on Neon"
        print(f"  {t:<24} {s:>8} {p:>8} {sk:>9} {delta:>8}{flag}")

    print()
    print("OK Migration complete. bots/ unchanged -- Telegram bot still uses SQLite.")
    print("   Phase 5 will switch the bot to Neon once you've verified parity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
