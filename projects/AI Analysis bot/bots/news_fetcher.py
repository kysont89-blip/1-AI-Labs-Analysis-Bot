"""
Real-time news fetcher.

Pulls BTC/XAU/macro headlines from public RSS feeds. No API key, no new deps
beyond httpx (already used elsewhere in the project) and the stdlib
xml.etree for parsing.

Cache: 15 min in-process. A single asyncio.Lock serializes fetches so a
/news burst doesn't hammer the upstream feeds.

Per-feed failures are silent — one dead feed must not blank the response.
"""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx


logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """A single headline."""
    title: str
    link: str
    published: str       # raw pubDate string from the feed
    source: str          # display name, e.g. "CoinDesk"
    age_minutes: int     # minutes since pubDate (approx)
    tags: List[str] = field(default_factory=list)
    epoch: float = 0.0   # numeric timestamp for sorting; 0 if unparseable
    relevance_drivers: List[str] = field(default_factory=list)
    # ↑ list of matched price-impact drivers, e.g. ['fed', 'etf'].
    # Empty list = headline does not plausibly move XAU or BTC.
    impact_stars: int = 0
    # ↑ 1-5 star rating of how strongly the headline is likely to move
    # XAU or BTC price (absolute market impact, ForexFactory style).
    # 0 = not rated (no driver matched). 5 = top market-mover.


# (url, display name, topic tags).
# Topics are used by /news [topic] filtering. Tags overlap intentionally
# so a "btc" query also returns crypto-tagged items.
FEEDS = [
    ('https://www.coindesk.com/arc/outboundfeeds/rss/',         'CoinDesk',        ['btc', 'crypto']),
    ('https://cointelegraph.com/rss',                           'Cointelegraph',   ['btc', 'crypto']),
    ('https://www.investing.com/rss/news_1.rss',                'Investing.com',   ['xau', 'forex']),
    ('https://www.forexlive.com/feed/',                         'ForexLive',       ['macro', 'forex']),
    ('https://www.cnbc.com/id/100003114/device/rss/rss.html',   'CNBC',            ['macro', 'geopolitics']),
]

CACHE_TTL_SECONDS = 900   # 15 min
HTTP_TIMEOUT = 8.0
MAX_PER_FEED = 8          # cap per source so one chatty feed doesn't dominate
MAX_ITEMS = 50            # absolute cap on the merged cache


# (driver_name, list of substring keywords that trigger it, reason line).
# Substring match is case-insensitive on the lowercased title. Multi-word
# phrases match as substrings ("rate decision" matches "interest-rate
# decision" too). The reason is a single short line — appended to the
# matched item so the user can see *why* the headline might move price.
# Tune by editing the list; no LLM call per headline (cheaper, faster,
# and the matched: line lets the user sanity-check the filter).
RELEVANCE_DRIVERS = [
    # ---- XAU (gold) drivers ----
    ('fed', [
        'fed ', "fed'", 'fomc', 'powell', 'rate decision', 'rate cut',
        'rate hike', 'interest rate', 'basis points', ' bps', 'fed chair',
        'fomc minutes', 'dot plot', 'monetary policy', 'rate path',
        # bare "Fed," / "Fed." at start of word (no space needed)
        'fed,', 'fed.', 'fed:', 'fed/', 'fed-',
    ],
     "Fed / monetary policy moves the dollar; stronger dollar = "
     "weaker gold. BTC also trades on liquidity expectations."),
    ('inflation', [
        'cpi', 'pce', 'ppi', 'inflation', 'consumer price', 'core inflation',
        'hot inflation', 'cool inflation',
    ],
     "Inflation prints move real-yield expectations; the #1 driver "
     "of gold and a top-3 driver of BTC."),
    ('jobs', [
        'nfp', 'nonfarm', 'payroll', 'unemployment', 'jolts', 'jobs report',
        'adp employment', 'wage growth', 'initial claims',
    ],
     "Jobs data shapes Fed-cut / hike expectations, which move both "
     "gold and BTC."),
    ('geopolitics', [
        'iran', 'israel', 'russia', 'ukraine', 'taiwan', 'sanction',
        'strikes on', 'strike on', ' war ', 'military', 'lebanon', 'gaza',
        'korea', 'nuclear', 'hamas', 'hezbollah', 'kremlin',
    ],
     "Safe-haven demand: wars / instability push investors into gold; "
     "risk-off can hit BTC short-term, then rally on liquidity hopes."),
    ('dollar', [
        'dxy', 'dollar index', 'usd strengthens', 'usd weakens', 'greenback',
        'dollar rally', 'dollar slump', 'us dollar',
    ],
     "DXY is roughly inverse to gold; BTC also trades vs. the dollar."),
    ('real_yields', [
        'real yield', 'tips yield', '10-year real', 'real interest rate',
    ],
     "Real yields = main competitor to gold. Rising real yields = "
     "bearish gold; BTC is more loosely correlated."),
    ('central_bank', [
        'ecb ', 'boe ', 'boj ', 'pboc', "people's bank", 'central bank',
        'lagarde', 'rba ', 'snb ', 'bailey', 'ueda',
    ],
     "Central-bank rate decisions and gold buying both move gold."),
    ('etf_flows', [
        'gld', 'slv', 'gold etf', 'gold outflow', 'gold inflow',
        'gold demand', 'gold buying',
    ],
     "ETF flows are a real-time gauge of institutional gold demand."),

    # ---- BTC (bitcoin / crypto) drivers ----
    ('sec', [
        'sec ', "sec'", 'sec,', 'sec.', 'sec:', 'sec/', 'sec-', 'gensler',
        'sec chair', 'sec ruling', 'securities and exchange', 'sec charges',
        'sec sues', 'sec vs',
    ],
     "SEC actions on tokens / ETFs directly drive BTC regulatory "
     "risk premium."),
    ('etf', [
        'btc etf', 'bitcoin etf', 'spot etf', 'ethereum etf', 'eth etf',
        'etf approval', 'etf approval', 'etf outflow', 'etf inflow',
        'etf launch', 'etf filing',
    ],
     "Spot BTC / ETH ETF flows are the single biggest institutional "
     "BTC driver."),
    ('regulation', [
        'crypto bill', 'crypto regulation', 'senator ', ' congress ',
        'white house crypto', 'executive order crypto', 'crypto ban',
        'crypto crackdown', 'sec vs',
    ],
     "Regulatory shifts change the institutional-friendliness of BTC."),
    ('hack', [
        'hack', 'exploit', 'stolen', 'drained', 'bridge exploit', ' breach',
        'cyberattack', 'rug pull', 'rugpull',
    ],
     "Large exchange / protocol hacks cause forced selling and trust loss."),
    ('stablecoin', [
        'usdt', 'usdc', 'tether', 'circle ', 'stablecoin depeg',
        'stable depeg', 'dai', 'stablecoin',
    ],
     "Stablecoin depegs = liquidity stress, almost always hits BTC first."),
    ('mining', [
        'hashrate', 'miner capitulation', 'mining ban', 'mining regulation',
        'bitcoin miner', 'crypto miner', 'mining difficulty',
    ],
     "Hashrate crashes or mining bans signal miner stress = sell pressure."),
    ('whale', [
        'whale', 'large transfer', 'wallet moves', 'whale alert',
        'coinex',
    ],
     "Large on-chain moves to exchanges precede sell pressure."),
]


# Map driver name -> reason text, for O(1) lookup in format_headlines.
_DRIVER_REASON = {name: reason for name, _, reason in RELEVANCE_DRIVERS}


def _classify_relevance(title: str) -> List[str]:
    """Return the list of price-impact driver names matched by a title.

    Empty list means the headline does not plausibly move XAU or BTC
    and should be filtered out of the default /news output. Substring
    match is case-insensitive. Order follows RELEVANCE_DRIVERS, which
    is XAU-first then BTC — so when multiple drivers match, the first
    one is the lead reason we show in the output.
    """
    if not title:
        return []
    t = title.lower()
    matched: List[str] = []
    for name, kws, _reason in RELEVANCE_DRIVERS:
        for kw in kws:
            if kw in t:
                matched.append(name)
                break   # one match per driver is enough
    return matched


# Base star rating per driver (XAU / BTC tuned, ForexFactory-style).
# This is the *starting* score for an item; modifiers in the title
# can bump it up or down. 5★ = top market-mover, 1★ = barely moves
# price. Same XAU/BTC perspective as the /events star table.
_DRIVER_BASE_STARS = {
    'fed':           4,   # can move 0.5-1% on surprise, sometimes a non-event
    'inflation':     4,   # same
    'jobs':          4,   # same
    'geopolitics':   3,   # wide variance: background tension -> active strike
    'dollar':        3,   # mostly a reaction to other drivers
    'real_yields':   3,   # slow signal
    'central_bank':  3,   # top for local currency, less for XAU/BTC
    'etf_flows':     3,   # real-time institutional demand
    'sec':           4,   # regulatory shocks move BTC hard
    'etf':           4,   # same
    'regulation':    3,   # slow burn
    'hack':          5,   # any major hack is 5★ for BTC
    'stablecoin':    5,   # depeg = liquidity crisis
    'mining':        3,   # slow signal
    'whale':         2,   # single transfer rarely moves market
}


# Positive modifiers — each +1 to the base star (capped at +2 total).
# These signal a clear, immediate catalyst: action verbs, market-moving
# words. Substring match is case-insensitive on the lowercased title.
_POSITIVE_MODIFIERS = [
    # Crisis / emergency language
    'depeg', 'halt', 'halted', 'outage', 'outflow', 'drained', 'exploit',
    'ban', 'banned', 'crackdown', 'sanction', 'sanctioned', 'sanctions',
    'strikes on', 'strike on', 'attack on', 'attacks on', 'nuclear strike',
    ' war ', 'emergency', 'default', 'hacked', 'cyberattack',
    # Market-moving action verbs
    'plunge', 'plunges', 'plunged', 'surge', 'surges', 'surged', 'soar',
    'soars', 'soared', 'crash', 'crashes', 'crashed', 'tumble', 'tumbles',
    'tumbled', 'shatters', 'record high', 'record low', 'all-time high',
    'all-time low',
    # Regulatory green light
    'approves', 'approved', 'approval', 'launches', 'launched', 'launch',
]


# Negative modifiers — each -1 (capped at -2 total). These signal a
# hedge, a forecast, or background tension rather than an active
# catalyst. "may", "could", "might" especially.
_NEGATIVE_MODIFIERS = [
    'may ', 'might ', 'could ', 'weighs', 'considers', 'talks', 'talk of',
    'negotiate', 'negotiating', 'peace deal', 'ceasefire', 'agreement',
    'expected to', 'expected ', 'unchanged', 'steady', 'outlook',
    'forecast', 'preview', 'week in focus', 'wrap-up', 'weekly',
    'roundup', 'recap', 'analysis', 'opinion',
]


def _score_impact(title: str, drivers: List[str]) -> int:
    """Return a 1-5 star rating for a headline based on its drivers
    and positive / negative modifiers in the title.

    Scoring:
        base  = _DRIVER_BASE_STARS[drivers[0]]  (the first/strongest)
        +1 for each positive modifier matched (capped at +2)
        -1 for each negative modifier matched (capped at -2)
        +1 bonus if 2+ drivers matched  (reinforcing signal)
        clamp to [1, 5]
    """
    if not drivers:
        return 0
    base = _DRIVER_BASE_STARS.get(drivers[0], 3)
    t = title.lower()
    pos = sum(1 for m in _POSITIVE_MODIFIERS if m in t)
    neg = sum(1 for m in _NEGATIVE_MODIFIERS if m in t)
    pos = min(pos, 2)        # cap +2
    neg = min(neg, 2)        # cap -2
    multi = 1 if len(drivers) >= 2 else 0
    score = base + pos - neg + multi
    if score < 1:
        return 1
    if score > 5:
        return 5
    return score


def _format_stars(n: int) -> str:
    """Render an integer 0-5 as a row of stars. 0 -> '' (no prefix).

    Examples:
        0 -> ''
        1 -> '★☆☆☆☆'
        3 -> '★★★☆☆'
        5 -> '★★★★★'
    """
    if n <= 0:
        return ""
    if n > 5:
        n = 5
    return "★" * n + "☆" * (5 - n)


def _parse_pubdate(raw: str) -> float:
    """Best-effort parse of an RSS pubDate into a UTC epoch.

    Returns 0.0 on failure (sort puts unparseable items at the bottom).
    """
    if not raw:
        return 0.0
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return 0.0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _format_age(age_min: int) -> str:
    """Friendly '12m ago' / '3h ago' / '2d ago' string."""
    if age_min < 1:
        return "just now"
    if age_min < 60:
        return f"{age_min}m ago"
    if age_min < 60 * 24:
        return f"{age_min // 60}h ago"
    return f"{age_min // (60 * 24)}d ago"


def parse_rss(xml_bytes: bytes, source: str, tags: List[str]) -> List[NewsItem]:
    """Parse RSS 2.0 <item> elements into NewsItem list. Skips bad items.

    Also tolerates Atom <entry> under the standard atom namespace — if a
    feed ever switches, we still get items.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        logger.debug(f"parse_rss({source}): XML parse failed: {e}")
        return []

    items: List[NewsItem] = []

    # RSS 2.0 path
    for it in root.findall('.//item'):
        title = (it.findtext('title') or '').strip()
        link = (it.findtext('link') or '').strip()
        pub = (it.findtext('pubDate') or '').strip()
        if not title or not link:
            continue
        epoch = _parse_pubdate(pub)
        age_min = max(0, int((time.time() - epoch) / 60)) if epoch else 9999
        drivers = _classify_relevance(title)
        items.append(NewsItem(
            title=title, link=link, published=pub, source=source,
            age_minutes=age_min, tags=list(tags), epoch=epoch,
            relevance_drivers=drivers,
            impact_stars=_score_impact(title, drivers),
        ))

    # Atom path (fallback — none of our current feeds use it)
    if not items:
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('.//atom:entry', ns):
            title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
            link_el = entry.find('atom:link', ns)
            link = (link_el.get('href') if link_el is not None else '') or ''
            pub = (entry.findtext('atom:published', namespaces=ns) or '').strip()
            if not title or not link:
                continue
            epoch = _parse_pubdate(pub)
            age_min = max(0, int((time.time() - epoch) / 60)) if epoch else 9999
            drivers = _classify_relevance(title)
            items.append(NewsItem(
                title=title, link=link, published=pub, source=source,
                age_minutes=age_min, tags=list(tags), epoch=epoch,
                relevance_drivers=drivers,
                impact_stars=_score_impact(title, drivers),
            ))

    return items[:MAX_PER_FEED]


def format_headlines(
    items: List[NewsItem],
    topic_label: Optional[str] = None,
    show_unfiltered: bool = False,
) -> str:
    """Format a list of headlines for Telegram. Plain text, no markdown.

    Each item line starts with a 1-5 star rating prefix (e.g.
    "⭐⭐⭐⭐ ") so the user can see impact at a glance. Items with
    no driver match get no star prefix (only seen in /news all).

    When `show_unfiltered` is False (default), items with no matched
    price-impact driver are dropped from the output entirely. When
    True, all items are shown but un-matched ones get no annotation.

    Each kept item with matched drivers gets two extra lines:
        ↳ <source> · matched: <driver-list>
        💡 <first matched driver's reason>
    """
    header = "📰 NEWS"
    if topic_label:
        header += f" — {topic_label}"
    else:
        header += " — BTC, XAU & macro"
    if show_unfiltered:
        header += "\n   showing every headline as scraped (sorted by impact)"
    else:
        header += "\n   filtered for price impact — /news all to see every headline"

    lines = [header, "━━━━━━━━━━━━━━━━━━━━━━"]
    n = 0
    for it in items:
        # Drop items with no matched driver when not in unfiltered mode
        if not it.relevance_drivers and not show_unfiltered:
            continue
        n += 1
        # Trim long titles so the message stays compact
        title = it.title if len(it.title) <= 140 else it.title[:137].rstrip() + "..."
        # Collapse internal newlines so each item is one tight block
        title = " ".join(title.split())
        age = _format_age(it.age_minutes)
        # Star prefix — empty string when no driver matched (noise
        # items in /news all mode). Always aligned at the same column
        # so the eye can scan the impact column.
        stars = _format_stars(it.impact_stars) if it.relevance_drivers else ""
        prefix = f"{stars} " if stars else ""
        lines.append(f"{n}. {prefix}[{age}] {title}")
        if it.relevance_drivers:
            lines.append(f"   ↳ {it.source} · matched: {', '.join(it.relevance_drivers)}")
            # Use the first matched driver's reason as the lead reason.
            # Reasons are tuned to read sensibly even if the match is loose.
            reason = _DRIVER_REASON.get(it.relevance_drivers[0], "")
            if reason:
                lines.append(f"   💡 {reason}")
        else:
            # Unfiltered mode: noise item, no driver matched
            lines.append(f"   ↳ {it.source}")
        lines.append("")
    return "\n".join(lines).rstrip()


class NewsFetcher:
    """Async RSS fetcher with a 15-min in-process cache."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: List[NewsItem] = []
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _client_lazy(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={'User-Agent': 'XOX-AI-Analysis-Bot/2.0 (+news)'},
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    async def get_headlines(
        self,
        topics: Optional[List[str]] = None,
        limit: int = 12,
        include_unfiltered: bool = False,
    ) -> List[NewsItem]:
        """Return cached or freshly-fetched headlines.

        `topics` is a list of tags to filter on (e.g. ['btc'], ['xau'],
        ['macro']). Items are returned sorted by impact descending
        (5★ first, then 4★, etc.) with newest-first as a tie-breaker.

        `include_unfiltered` controls whether headlines with no matched
        price-impact driver (False: drop) or below the 3★ threshold
        (False: drop) are kept. When True, all items are kept regardless
        of star rating, but they are still sorted by impact first.
        """
        now = time.time()
        async with self._lock:
            if not self._cache or (now - self._cached_at) > CACHE_TTL_SECONDS:
                self._cache = await self._fetch_all()
                self._cached_at = now
            items = list(self._cache)

        if topics:
            tag_set = {t.lower() for t in topics}
            items = [i for i in items if any(t in tag_set for t in i.tags)]

        if not include_unfiltered:
            # Default mode: only items with a matched driver AND
            # 3★+ rating. The star threshold keeps the list focused
            # on real catalysts (3★ = "likely to move price 0.3% or
            # more" — see _DRIVER_BASE_STARS / _score_impact).
            items = [i for i in items if i.relevance_drivers and i.impact_stars >= 3]

        # Highest impact first; break ties with newest-first.
        # Items with impact_stars=0 sink to the bottom (only visible
        # in /news all mode).
        items.sort(key=lambda i: (i.impact_stars, i.epoch), reverse=True)

        return items[:limit]

    async def _fetch_all(self) -> List[NewsItem]:
        client = await self._client_lazy()
        results = await asyncio.gather(
            *[self._fetch_one(client, url, name, tags) for url, name, tags in FEEDS],
            return_exceptions=True,
        )
        merged: List[NewsItem] = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
            # Exceptions are already swallowed in _fetch_one; anything else
            # would be a bug — log and continue.
            elif isinstance(r, Exception):
                logger.debug(f"_fetch_all: unexpected exception: {r}")
        # Newest first; unparseable dates sink to the bottom (epoch=0)
        merged.sort(key=lambda x: x.epoch, reverse=True)
        return merged[:MAX_ITEMS]

    async def _fetch_one(
        self, client: httpx.AsyncClient, url: str, source: str, tags: List[str]
    ) -> List[NewsItem]:
        try:
            r = await client.get(url)
            r.raise_for_status()
            return parse_rss(r.content, source=source, tags=tags)
        except Exception as e:
            logger.debug(f"_fetch_one({source}): {type(e).__name__}: {e}")
            return []

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


# Quick manual test
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # Unit-check the relevance classifier on representative titles.
    # No network needed; pure function.
    _UNIT = [
        ("U.S. peace deal with Iran in question as Israel strikes Lebanon, "
         "Trump warns not to 'blow it'",     ['geopolitics']),
        ("SEC's big swing to clear tokenization path",                ['sec']),
        ("Newsquawk Week in Focus: Fed, BoJ, RBA, BoE, SNB, US Retail "
         "Sales, and Japan CPI",               ['fed', 'inflation']),
        # "tokenized treasury markets" is a sector trend story, not an
        # ETF-event story. No driver should fire. (Drivers are for direct
        # price catalysts, not adjacent coverage.)
        ("Wall Street and crypto are crashing into each other as "
         "tokenized treasury markets hit $14.6 billion",              []),
        ("Aerodrome is turning liquidity into a prediction market",   []),
        ("Elon Musk drifted from Larry Page over a decade ago",       []),
        ("JetBlue bets big on Fort Lauderdale, from a new airport "
         "lounge to an international gateway",                         []),
        ("Bitcoin ETF inflows hit $1B as CPI cools",                  ['inflation', 'etf']),
        ("ETH/BTC hack: $50M drained from major exchange",            ['hack']),
        ("Tether USDT depegs to $0.95 in liquidity crunch",           ['stablecoin']),
        ("Iran sanctions tighten as oil prices surge on Hormuz "
         "blockade",                                                   ['geopolitics']),
        ("FOMC minutes signal more rate cuts to come",                ['fed']),
    ]
    print("== _classify_relevance unit checks ==")
    fail = 0
    for title, expected in _UNIT:
        got = _classify_relevance(title)
        ok = "OK " if got == expected else "FAIL"
        if got != expected:
            fail += 1
        print(f"  {ok} | expected={expected} got={got}")
        print(f"      title: {title[:80]}...")
    print(f"  {len(_UNIT) - fail}/{len(_UNIT)} passed\n")

    # Unit-check the impact star score. Pure function. Expected values
    # are hand-derived from the base + modifier + multi-driver rules
    # in _score_impact. Re-tune _DRIVER_BASE_STARS / modifier lists
    # and these will need to be updated.
    _STARS = [
        # ---- 5★ (top market movers) ----
        ("Tether USDT depegs to $0.95 in liquidity crunch",
         ['stablecoin'],                                5),  # 5 base
        ("Major exchange hacked, $100M drained",
         ['hack'],                                       5),  # 5 base
        ("FOMC signals rate cut on soft CPI print",
         ['fed', 'inflation'],                           5),  # 4 base + 1 multi = 5
        ("Israel strikes on Iran, emergency declared",
         ['geopolitics'],                                5),  # 3 base + 2 pos = 5
        # ---- 4★ (very important) ----
        ("FOMC minutes signal more rate cuts to come",
         ['fed'],                                        4),  # 4 base
        ("SEC sues Coinbase over securities violations",
         ['sec'],                                        4),  # 4 base
        # ---- 3★ (important but not market-defining) ----
        # "Iran sanctions tighten" — 'sanction' is a positive modifier
        # (an active tightening, not a hedge), so 3 base + 1 = 4★
        ("Iran sanctions tighten on oil prices",
         ['geopolitics'],                                4),  # 3 base + 1 pos
        ("U.S. peace deal with Iran in question as Israel strikes Lebanon",
         ['geopolitics'],                                3),  # 3 base, no modifiers
        # ---- 2★ (low impact but still relevant) ----
        ("Bitcoin whale alert: 50,000 BTC moved to exchange",
         ['whale'],                                      2),  # 2 base
        # ---- Negative modifier brings score down ----
        ("Fed may consider rate talks this week, expected to remain steady",
         ['fed'],                                        2),  # 4 base - 2 neg = 2
    ]
    print("== _score_impact unit checks ==")
    fail = 0
    for title, drivers, expected in _STARS:
        got = _score_impact(title, drivers)
        ok = "OK " if got == expected else "FAIL"
        if got != expected:
            fail += 1
        print(f"  {ok} | {got}★ (expected {expected}★) | {title[:60]!r}")
    print(f"  {len(_STARS) - fail}/{len(_STARS)} passed\n")

    # Visual check on _format_stars
    print("== _format_stars visual check ==")
    for n in range(0, 6):
        print(f"  {n} -> {_format_stars(n)!r}")
    print()

    async def _main():
        nf = NewsFetcher()
        print("== Default /news (filtered, 3★+, sorted by impact) ==")
        filtered = await nf.get_headlines(limit=12)
        print(f"Got {len(filtered)} filtered items")
        if filtered:
            print()
            print(format_headlines(filtered))
        print()
        print("---")
        print("== /news all (unfiltered, 1-5★ all visible) ==")
        all_items = await nf.get_headlines(limit=12, include_unfiltered=True)
        print(f"Got {len(all_items)} total items")
        if all_items:
            print()
            print(format_headlines(all_items, show_unfiltered=True))
        print()
        print("---")
        print("== /news btc (filtered BTC only) ==")
        btc = await nf.get_headlines(topics=['btc'], limit=5)
        print(f"Got {len(btc)} filtered BTC items")
        if btc:
            print(format_headlines(btc, topic_label="BTC"))
        await nf.close()

    asyncio.run(_main())
