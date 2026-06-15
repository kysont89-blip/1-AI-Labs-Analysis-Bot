"""
Economic Calendar / News Event Filter
Prevents trading during high-impact news events.

Data sources:
- Embedded known events (FOMC, NFP, CPI, ECB, BoE, BoJ)
- User can add custom events via Telegram
- Free tier: no external API needed
- Premium tier: can integrate Investing.com/ForexFactory scraper
"""

import json
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from pathlib import Path


@dataclass
class EconomicEvent:
    """Single economic event."""
    name: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM (GMT)
    impact: str  # 'high', 'medium', 'low'
    currency: str  # 'USD', 'EUR', 'GBP', 'JPY', 'ALL'
    description: str
    source: str = "builtin"
    # 1-5 star rating for XAU / BTC impact (0 = not rated yet; only
    # populated for high-impact events). Reason is a short line shown
    # under the event so the user can see *why* this rating.
    impact_stars: int = 0
    impact_reason: str = ""


# Star rating table for XAU / BTC trading impact. 1-5 stars.
# Keyed on a case-insensitive substring match against the event name,
# so a future "FOMC Press Conference" would also be rated if added.
# 5★ = top-tier market mover (FOMC, NFP, CPI), 1★ = barely moves price.
# Tuned for XAU / BTC: a BoE rate decision is 4★ for GBP but only ~2-3★
# for XAU/BTC, so the rating below reflects *spillover to USD-locked
# gold and BTC liquidity*, not the local-currency impact.
#
# Format: (match_substring_lowercase, stars, reason)
_STAR_RATINGS = [
    # ---- 5★ — Top tier ----
    ('fomc',            5, "Top macro indicator; sets the rate curve."),
    ('non-farm',        5, "Top jobs indicator; Fed-watchers key off it."),
    ('nfp',             5, "Top jobs indicator; Fed-watchers key off it."),
    ('cpi inflation',   5, "Top inflation gauge; Fed pivots on it."),
    ('core cpi',        5, "Top inflation gauge; Fed pivots on it."),

    # ---- 4★ — Very important ----
    ('us gdp',          4, "Big headline print, but backward-looking."),
    ('ecb ',            4, "Top EUR mover; spills to USD via DXY."),
    ('ecb interest',    4, "Top EUR mover; spills to USD via DXY."),
    ('boe ',            4, "Top GBP mover; small USD spillover."),
    ('boe interest',    4, "Top GBP mover; small USD spillover."),
    ('boj ',            4, "Top JPY mover; USD/JPY spills to DXY."),
    ('boj interest',    4, "Top JPY mover; USD/JPY spills to DXY."),

    # ---- 3★ — Important but not market-defining ----
    ('ppi inflation',   3, "Pre-CPI proxy; rarely moves on its own."),
    ('pce',             3, "Fed's preferred inflation gauge; smaller move than CPI."),
    ('jolts',           3, "Jobs demand indicator; secondary to NFP."),
    ('ism manufacturing', 3, "Factory health snapshot; cyclical signal."),

    # ---- 2★ — Moderate ----
    ('retail sales',    2, "Consumer spending; moderate market reaction."),
    ('unemployment',    2, "Released alongside NFP; overshadowed by payrolls."),
    ('building permits',2, "Housing leading indicator; modest impact."),

    # ---- 1★ — Minor ----
    ('consumer confidence', 1, "Soft sentiment indicator; rarely market-moving."),
    ('trade balance',   1, "Low-volatility release; not a market driver."),
]


def _lookup_star_rating(event_name: str) -> tuple:
    """Return (stars, reason) for an event name, or (0, '') if not rated."""
    if not event_name:
        return 0, ""
    n = event_name.lower()
    for substr, stars, reason in _STAR_RATINGS:
        if substr in n:
            return stars, reason
    return 0, ""


def _format_stars(n: int) -> str:
    """Render an integer 1-5 as a row of filled stars (e.g. '★★★★☆')."""
    if n <= 0:
        return ""
    if n > 5:
        n = 5
    return "★" * n + "☆" * (5 - n)


class EconomicCalendar:
    """News event filter for trading signals."""
    
    # Known 2025-2026 high-impact events (embedded)
    BUILTIN_EVENTS = [
        # FOMC meetings 2025
        EconomicEvent("FOMC Interest Rate Decision", "2025-01-29", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-03-19", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-05-07", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-06-18", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-07-30", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-09-17", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-10-29", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2025-12-10", "19:00", "high", "USD", "Federal Reserve policy decision"),
        
        # NFP 2025 (first Friday of each month)
        EconomicEvent("Non-Farm Payrolls", "2025-01-10", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-02-07", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-03-07", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-04-04", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-05-02", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-06-06", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-07-03", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-08-01", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-09-05", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-10-03", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-11-07", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2025-12-05", "13:30", "high", "USD", "US employment report"),
        
        # CPI 2025 (monthly, mid-month)
        EconomicEvent("CPI Inflation Report", "2025-01-15", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-02-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-03-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-04-10", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-05-13", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-06-11", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-07-15", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-08-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-09-11", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-10-14", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-11-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2025-12-11", "13:30", "high", "USD", "US consumer price index"),
        
        # ECB meetings 2025
        EconomicEvent("ECB Interest Rate Decision", "2025-01-30", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-03-06", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-04-17", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-06-05", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-07-24", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-09-11", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-10-23", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2025-12-11", "12:15", "high", "EUR", "European Central Bank"),
        
        # BoE meetings 2025
        EconomicEvent("BoE Interest Rate Decision", "2025-02-06", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-03-20", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-05-08", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-06-19", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-08-07", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-09-18", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-11-06", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2025-12-18", "12:00", "high", "GBP", "Bank of England"),
        
        # BoJ meetings 2025
        EconomicEvent("BoJ Interest Rate Decision", "2025-01-24", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-03-19", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-04-30", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-06-16", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-07-31", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-09-19", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-10-31", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2025-12-19", "03:00", "high", "JPY", "Bank of Japan"),
        
        # GDP releases (quarterly)
        EconomicEvent("US GDP Annualized", "2025-01-30", "13:30", "high", "USD", "US economic growth"),
        EconomicEvent("US GDP Annualized", "2025-04-30", "13:30", "high", "USD", "US economic growth"),
        EconomicEvent("US GDP Annualized", "2025-07-30", "13:30", "high", "USD", "US economic growth"),
        EconomicEvent("US GDP Annualized", "2025-10-30", "13:30", "high", "USD", "US economic growth"),
        
        # Retail Sales (monthly)
        EconomicEvent("US Retail Sales", "2025-01-16", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-02-14", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-03-17", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-04-15", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-05-15", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-06-17", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-07-16", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-08-15", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-09-16", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-10-16", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-11-14", "13:30", "medium", "USD", "Consumer spending"),
        EconomicEvent("US Retail Sales", "2025-12-16", "13:30", "medium", "USD", "Consumer spending"),
        
        # ISM Manufacturing (monthly, first business day)
        EconomicEvent("ISM Manufacturing PMI", "2025-01-03", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-02-03", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-03-03", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-04-01", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-05-01", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-06-02", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-07-01", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-08-01", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-09-02", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-10-01", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-11-03", "15:00", "medium", "USD", "Factory activity"),
        EconomicEvent("ISM Manufacturing PMI", "2025-12-01", "15:00", "medium", "USD", "Factory activity"),
        
        # Unemployment Rate (with NFP)
        # Already covered by NFP dates
        
        # PPI (monthly, mid-month)
        EconomicEvent("PPI Inflation Report", "2025-01-14", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-02-13", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-03-13", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-04-11", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-05-13", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-06-12", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-07-14", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-08-13", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-09-11", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-10-15", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-11-13", "13:30", "high", "USD", "Producer prices"),
        EconomicEvent("PPI Inflation Report", "2025-12-12", "13:30", "high", "USD", "Producer prices"),
        
        # 2026 FOMC (known dates)
        EconomicEvent("FOMC Interest Rate Decision", "2026-01-28", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-03-18", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-04-29", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-06-17", "19:00", "high", "USD", "Federal Reserve policy decision"),
        
        # 2026 NFP (first Friday)
        EconomicEvent("Non-Farm Payrolls", "2026-01-09", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-02-06", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-03-06", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-04-03", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-05-08", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-06-05", "13:30", "high", "USD", "US employment report"),
        
        # 2026 CPI
        EconomicEvent("CPI Inflation Report", "2026-01-14", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-02-11", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-03-11", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-04-15", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-05-13", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-06-10", "13:30", "high", "USD", "US consumer price index"),

        # 2026 H2 FOMC (4 more meetings: Jul, Sep, Oct, Dec)
        EconomicEvent("FOMC Interest Rate Decision", "2026-07-29", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-09-16", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-10-28", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2026-12-16", "19:00", "high", "USD", "Federal Reserve policy decision"),

        # 2026 H2 NFP (first Friday)
        EconomicEvent("Non-Farm Payrolls", "2026-07-02", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-08-07", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-09-04", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-10-02", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-11-06", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2026-12-04", "13:30", "high", "USD", "US employment report"),

        # 2026 H2 CPI
        EconomicEvent("CPI Inflation Report", "2026-07-14", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-08-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-09-15", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-10-13", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-11-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2026-12-10", "13:30", "high", "USD", "US consumer price index"),

        # 2026 H2 ECB (~6 per year)
        EconomicEvent("ECB Interest Rate Decision", "2026-07-30", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2026-09-10", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2026-10-29", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2026-12-10", "12:15", "high", "EUR", "European Central Bank"),

        # 2026 H2 BoE (~8 per year)
        EconomicEvent("BoE Interest Rate Decision", "2026-08-06", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2026-09-17", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2026-11-05", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2026-12-17", "12:00", "high", "GBP", "Bank of England"),

        # 2026 H2 BoJ (~8 per year)
        EconomicEvent("BoJ Interest Rate Decision", "2026-07-30", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2026-09-18", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2026-10-30", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2026-12-18", "03:00", "high", "JPY", "Bank of Japan"),

        # 2026 H2 GDP
        EconomicEvent("US GDP Annualized", "2026-07-30", "13:30", "high", "USD", "US economic growth"),
        EconomicEvent("US GDP Annualized", "2026-10-29", "13:30", "high", "USD", "US economic growth"),

        # 2027 H1 FOMC
        EconomicEvent("FOMC Interest Rate Decision", "2027-01-27", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2027-03-17", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2027-04-28", "19:00", "high", "USD", "Federal Reserve policy decision"),
        EconomicEvent("FOMC Interest Rate Decision", "2027-06-16", "19:00", "high", "USD", "Federal Reserve policy decision"),

        # 2027 H1 NFP
        EconomicEvent("Non-Farm Payrolls", "2027-01-08", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2027-02-05", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2027-03-05", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2027-04-02", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2027-05-07", "13:30", "high", "USD", "US employment report"),
        EconomicEvent("Non-Farm Payrolls", "2027-06-04", "13:30", "high", "USD", "US employment report"),

        # 2027 H1 CPI
        EconomicEvent("CPI Inflation Report", "2027-01-13", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2027-02-10", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2027-03-11", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2027-04-13", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2027-05-12", "13:30", "high", "USD", "US consumer price index"),
        EconomicEvent("CPI Inflation Report", "2027-06-10", "13:30", "high", "USD", "US consumer price index"),

        # 2027 H1 ECB
        EconomicEvent("ECB Interest Rate Decision", "2027-01-28", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2027-03-11", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2027-04-22", "12:15", "high", "EUR", "European Central Bank"),
        EconomicEvent("ECB Interest Rate Decision", "2027-06-03", "12:15", "high", "EUR", "European Central Bank"),

        # 2027 H1 BoE
        EconomicEvent("BoE Interest Rate Decision", "2027-02-04", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2027-03-18", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2027-05-06", "12:00", "high", "GBP", "Bank of England"),
        EconomicEvent("BoE Interest Rate Decision", "2027-06-17", "12:00", "high", "GBP", "Bank of England"),

        # 2027 H1 BoJ
        EconomicEvent("BoJ Interest Rate Decision", "2027-01-28", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2027-03-19", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2027-04-30", "03:00", "high", "JPY", "Bank of Japan"),
        EconomicEvent("BoJ Interest Rate Decision", "2027-06-18", "03:00", "high", "JPY", "Bank of Japan"),
    ]
    
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = data_dir
        self.custom_events_path = os.path.join(data_dir, "custom_events.json")
        self.events: List[EconomicEvent] = []
        self._load_events()
    
    def _load_events(self):
        """Load builtin + custom events. Applies star ratings to any
        high-impact event that doesn't have them set yet (covers
        legacy/custom events on first run)."""
        self.events = list(self.BUILTIN_EVENTS)

        # Apply star rating if not already set. Only high-impact events
        # get a star rating (per the product decision: stars add value
        # for top-tier events, not for medium/low noise).
        for e in self.events:
            if e.impact == 'high' and e.impact_stars == 0:
                stars, reason = _lookup_star_rating(e.name)
                e.impact_stars = stars
                e.impact_reason = reason

        # Load custom events
        if os.path.exists(self.custom_events_path):
            try:
                with open(self.custom_events_path, 'r') as f:
                    custom = json.load(f)
                    for e in custom:
                        self.events.append(EconomicEvent(**e))
            except:
                pass
    
    def save_custom_events(self):
        """Save custom events to file."""
        custom = [asdict(e) for e in self.events if e.source == "custom"]
        with open(self.custom_events_path, 'w') as f:
            json.dump(custom, f, indent=2)
    
    def add_custom_event(self, name: str, date: str, time: str, 
                         impact: str, currency: str, description: str = ""):
        """Add a custom event."""
        event = EconomicEvent(
            name=name, date=date, time=time, impact=impact,
            currency=currency, description=description, source="custom"
        )
        self.events.append(event)
        self.save_custom_events()
    
    def get_upcoming_events(self, days_ahead: int = 7) -> List[EconomicEvent]:
        """Get events in the next N days."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)
        
        upcoming = []
        for e in self.events:
            event_dt = datetime.strptime(f"{e.date} {e.time}", "%Y-%m-%d %H:%M")
            event_dt = event_dt.replace(tzinfo=timezone.utc)
            if now <= event_dt <= cutoff:
                upcoming.append(e)
        
        upcoming.sort(key=lambda x: datetime.strptime(f"{x.date} {x.time}", "%Y-%m-%d %H:%M"))
        return upcoming
    
    def check_signal_blocked(self, symbol: str, check_time: datetime = None,
                             buffer_minutes: int = 30) -> Optional[Dict]:
        """
        Check if a trading signal should be blocked due to upcoming news.
        
        Returns None if safe to trade, or dict with warning info.
        """
        if check_time is None:
            check_time = datetime.now(timezone.utc)
        
        # Determine currency from symbol
        currency_map = {
            'BTCUSDT': 'USD', 'ETHUSDT': 'USD', 'SOLUSDT': 'USD', 'AVAXUSDT': 'USD',
            'XAUUSD': 'USD', 'EURUSD': 'EUR', 'GBPUSD': 'GBP', 'USDJPY': 'JPY'
        }
        target_currency = currency_map.get(symbol, 'USD')
        
        # Check all high-impact events within buffer window
        buffer = timedelta(minutes=buffer_minutes)
        
        for e in self.events:
            if e.impact not in ['high', 'medium']:
                continue
            
            # Match currency: event currency matches symbol OR event is 'ALL'
            if e.currency not in [target_currency, 'ALL', 'USD']:
                continue
            
            event_dt = datetime.strptime(f"{e.date} {e.time}", "%Y-%m-%d %H:%M")
            event_dt = event_dt.replace(tzinfo=timezone.utc)
            
            # Check if signal falls within buffer window
            if abs((event_dt - check_time).total_seconds()) <= buffer.total_seconds():
                minutes_until = int((event_dt - check_time).total_seconds() / 60)
                return {
                    'blocked': True,
                    'event': e.name,
                    'time': f"{e.date} {e.time} GMT",
                    'impact': e.impact,
                    'currency': e.currency,
                    'description': e.description,
                    'minutes_until': minutes_until,
                    'minutes_after': -minutes_until if minutes_until < 0 else 0,
                    'recommendation': 'AVOID' if e.impact == 'high' else 'CAUTION'
                }
        
        return None
    
    def get_events_for_symbol(self, symbol: str, days_ahead: int = 7) -> List[EconomicEvent]:
        """Get relevant events for a specific symbol."""
        currency_map = {
            'BTCUSDT': 'USD', 'ETHUSDT': 'USD', 'SOLUSDT': 'USD', 'AVAXUSDT': 'USD',
            'XAUUSD': 'USD', 'EURUSD': 'EUR', 'GBPUSD': 'GBP', 'USDJPY': 'JPY'
        }
        target_currency = currency_map.get(symbol, 'USD')
        
        upcoming = self.get_upcoming_events(days_ahead)
        return [e for e in upcoming if e.currency in [target_currency, 'ALL', 'USD']]
    
    def format_upcoming(self, symbol: str = None, days_ahead: int = 3) -> str:
        """Format upcoming events for Telegram.

        For high-impact events, each line includes a 1-5 star rating
        and a one-line reason (e.g. 'FOMC ⭐⭐⭐⭐⭐ — Top macro
        indicator; sets the rate curve.'). For medium / low impact
        events, the original 🟡 / 🟢 emoji is kept — stars would be
        noise on releases that don't move XAU or BTC.
        """
        if symbol:
            events = self.get_events_for_symbol(symbol, days_ahead)
            header = f"📅 UPCOMING EVENTS ({symbol})\n"
        else:
            events = self.get_upcoming_events(days_ahead)
            header = "📅 UPCOMING ECONOMIC EVENTS\n"

        if not events:
            return header + f"No major events in the next {days_ahead} days.\n"

        text = header + "━━━━━━━━━━━━━━━━━━━━━━\n"
        for e in events[:10]:  # Show max 10
            if e.impact == 'high':
                # Use the star rating as the primary visual cue.
                impact_emoji = "🔴"
                stars = _format_stars(e.impact_stars) if e.impact_stars else ""
                rating_line = f"   {impact_emoji} {stars} {e.date} {e.time} GMT\n"
                text += rating_line
                text += f"   {e.name}\n"
                if e.impact_reason:
                    text += f"   💡 {e.impact_reason}\n"
            else:
                # Medium / low — keep the original 3-bucket display,
                # no stars (would be misleading: a 1★ medium-impact
                # event would still be more volatile than a no-star
                # medium-impact event).
                impact_emoji = "🟡" if e.impact == 'medium' else "🟢"
                text += f"   {impact_emoji} {e.date} {e.time} GMT\n"
                text += f"   {e.name}\n"
            text += f"   Impact: {e.impact.upper()} | Currency: {e.currency}\n\n"

        return text


# Quick test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # Unit-check the star rating lookup. No network / no clock needed.
    print("== _lookup_star_rating unit checks ==")
    _UNIT = [
        ("FOMC Interest Rate Decision",                 (5, "Top macro indicator; sets the rate curve.")),
        ("Non-Farm Payrolls",                           (5, "Top jobs indicator; Fed-watchers key off it.")),
        ("CPI Inflation Report",                        (5, "Top inflation gauge; Fed pivots on it.")),
        ("US GDP Annualized",                           (4, "Big headline print, but backward-looking.")),
        ("ECB Interest Rate Decision",                  (4, "Top EUR mover; spills to USD via DXY.")),
        ("BoE Interest Rate Decision",                  (4, "Top GBP mover; small USD spillover.")),
        ("BoJ Interest Rate Decision",                  (4, "Top JPY mover; USD/JPY spills to DXY.")),
        ("PPI Inflation Report",                        (3, "Pre-CPI proxy; rarely moves on its own.")),
        ("Retail Sales",                                (2, "Consumer spending; moderate market reaction.")),
        ("ISM Manufacturing PMI",                       (3, "Factory health snapshot; cyclical signal.")),
    ]
    fail = 0
    for name, (exp_stars, exp_reason) in _UNIT:
        got_stars, got_reason = _lookup_star_rating(name)
        ok = "OK " if (got_stars, got_reason) == (exp_stars, exp_reason) else "FAIL"
        if (got_stars, got_reason) != (exp_stars, exp_reason):
            fail += 1
        print(f"  {ok} | {name!r:42s} -> ({got_stars}, {got_reason!r})")
    print(f"  {len(_UNIT) - fail}/{len(_UNIT)} passed\n")

    # Visual check on _format_stars
    print("== _format_stars visual check ==")
    for n in range(0, 6):
        print(f"  {n} -> {_format_stars(n)!r}")
    print()

    calendar = EconomicCalendar()

    print("=== NEWS FILTER TEST (XAUUSD, 30d window) ===\n")
    print(calendar.format_upcoming(symbol='XAUUSD', days_ahead=30))

    print("\n=== SIGNAL BLOCKING TEST ===")
    test_times = [
        datetime(2025, 6, 6, 13, 0, tzinfo=timezone.utc),   # 30 min before NFP
        datetime(2025, 6, 6, 14, 0, tzinfo=timezone.utc),   # 30 min after NFP
        datetime(2025, 6, 10, 12, 0, tzinfo=timezone.utc),  # Random time
    ]
    for t in test_times:
        result = calendar.check_signal_blocked('XAUUSD', t, buffer_minutes=60)
        status = "🟢 SAFE" if result is None else f"🔴 BLOCKED: {result['event']}"
        print(f"{t.strftime('%Y-%m-%d %H:%M')} UTC: {status}")
