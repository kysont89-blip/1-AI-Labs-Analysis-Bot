"""Generate a marketing card (PNG) from backtest results.

Usage: python make_backtest_card.py
Output: backtest_card.png in the project root.

The card is sized for a 1.91:1 social-media preview (1200x628) with:
- Brand name + tagline
- Headline metrics (Net R, PF, WR)
- Equity-curve sparkline (same shape as the /backtest command)
- Disclaimer footer

Re-run this whenever backtest_results_v2.json changes to refresh the
marketing asset. No arguments needed.
"""
import json
import math
import os
import sys
from typing import List, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow not installed. Run: pip install Pillow")
    sys.exit(1)


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(PROJECT_ROOT, "backtest_results_v2.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "backtest_card.png")

# 1.91:1 social media preview dimensions
CARD_W = 1200
CARD_H = 628

# Colors — dark theme, professional.
BG_TOP = (15, 23, 42)        # slate-900
BG_BOT = (30, 41, 59)        # slate-800
ACCENT_GREEN = (34, 197, 94) # green-500
ACCENT_BLUE = (96, 165, 250) # blue-400
TEXT_PRIMARY = (248, 250, 252)   # slate-50
TEXT_SECONDARY = (203, 213, 225) # slate-300
TEXT_MUTED = (148, 163, 184)     # slate-400
GRID = (51, 65, 85)              # slate-700


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Try to find a usable system font. Fall back to default if missing."""
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf",  # Windows Segoe UI Bold
        r"C:\Windows\Fonts\segoeui.ttf",   # Windows Segoe UI
        r"C:\Windows\Fonts\arialbd.ttf",   # Arial Bold
        r"C:\Windows\Fonts\arial.ttf",     # Arial
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _sparkline_points(net_r: float, max_dd_r: float, n: int = 60) -> List[Tuple[float, float]]:
    """Return (x, y) coords for the equity-curve sparkline.

    The shape mirrors the /backtest command's schematic: linear ramp
    from 0R to net_r with a Gaussian dip of magnitude max_dd_r near
    the 30% mark. Returns normalized (x, y) values where x is 0..1
    and y is 0..1 with 0 at the bottom.
    """
    dd_center = 0.30
    dd_width = 0.12
    dip_amp = max(max_dd_r, net_r * 0.4)
    raw: List[float] = []
    for i in range(n):
        progress = (i + 0.5) / n
        ramp = progress * net_r
        dip = -dip_amp * math.exp(
            -((progress - dd_center) ** 2) / (2 * dd_width ** 2)
        )
        raw.append(ramp + dip)
    lo, hi = min(raw), max(raw)
    span = hi - lo if hi > lo else 1
    return [
        ((i + 0.5) / n, (v - lo) / span)
        for i, v in enumerate(raw)
    ]


def _draw_vertical_gradient(img: Image.Image, top: Tuple[int, int, int],
                              bottom: Tuple[int, int, int]) -> None:
    """Fill img with a vertical gradient from top color to bottom color."""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for y in range(h):
        # Linear interpolation top → bottom
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def _draw_sparkline(draw: ImageDraw.ImageDraw, points: List[Tuple[float, float]],
                    x0: int, y0: int, w: int, h: int) -> None:
    """Draw a smooth equity curve in the rect (x0, y0, w, h)."""
    if not points or w < 4 or h < 4:
        return
    # Subtle grid line at midpoint
    mid_y = y0 + h // 2
    draw.line([(x0, mid_y), (x0 + w, mid_y)], fill=GRID, width=1)
    # Curve as connected line segments
    poly: List[Tuple[int, int]] = []
    for px, py in points:
        sx = int(x0 + px * w)
        # Invert y so 0 is at the bottom of the rect
        sy = int(y0 + h - py * h)
        poly.append((sx, sy))
    # Glow underlay
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        glow = [(x + dx, y + dy) for x, y in poly]
        draw.line(glow, fill=(*ACCENT_GREEN, 80), width=5)
    draw.line(poly, fill=ACCENT_GREEN, width=4)
    # End-point dot
    end_x, end_y = poly[-1]
    draw.ellipse(
        [end_x - 6, end_y - 6, end_x + 6, end_y + 6],
        fill=ACCENT_GREEN,
    )


def _draw_text_centered(draw: ImageDraw.ImageDraw, text: str,
                        font: ImageFont.ImageFont, y: int,
                        fill: Tuple[int, int, int], w: int = CARD_W) -> None:
    """Draw text horizontally centered on the canvas."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (w - tw) // 2
    draw.text((x, y), text, font=font, fill=fill)


def main() -> int:
    if not os.path.exists(JSON_PATH):
        print(f"ERROR: backtest results not found at {JSON_PATH}")
        print("Run `python backtest_v2.py` first, then re-run this script.")
        return 1

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    combos = data.get("combos") or []
    if not combos:
        print("ERROR: no combos in backtest JSON")
        return 1
    c = combos[0]
    symbol = c.get("symbol", "?")
    timeframe = c.get("timeframe", "?")
    plan = c.get("plan", "?")
    win_rate = c.get("win_rate", 0.0)
    pf = c.get("profit_factor", 0.0)
    net_r = c.get("net_pnl_r", 0.0)
    max_dd = c.get("max_drawdown_r", 0.0)
    n_trades = c.get("n_tradable", 0)

    # Translate R-multiples into % return. 1% risk per trade is the
    # industry-standard "1% rule" — this is what most bot users will
    # actually use. Sized differently, the % scales linearly: at 0.5%
    # risk you see half the return and half the drawdown.
    risk_pct = 1.0
    return_90d_pct = net_r * risk_pct
    max_dd_pct = max_dd * risk_pct

    img = Image.new("RGB", (CARD_W, CARD_H), BG_TOP)
    _draw_vertical_gradient(img, BG_TOP, BG_BOT)
    draw = ImageDraw.Draw(img, "RGBA")

    # ── Header (brand + tagline) ──
    f_brand = _font(28)
    f_tag = _font(18)
    _draw_text_centered(draw, "XOX AI Analysis Bot", f_brand, 30, ACCENT_BLUE)
    _draw_text_centered(
        draw, "90-day walk-forward backtest on production strategy",
        f_tag, 65, TEXT_SECONDARY,
    )

    # ── Symbol tag ──
    f_sym = _font(20)
    sym_text = f"{symbol}  ·  {timeframe}  ·  {plan}  ·  90 days"
    _draw_text_centered(draw, sym_text, f_sym, 105, TEXT_MUTED)

    # ── Headline number — 90-day return in % ──
    f_huge = _font(96)
    f_label = _font(20)
    head_text = f"+{return_90d_pct:,.0f}%"
    _draw_text_centered(draw, head_text, f_huge, 145, ACCENT_GREEN)
    _draw_text_centered(
        draw,
        f"in 90 days at 1% risk per trade · {n_trades} trades",
        f_label, 260, TEXT_SECONDARY,
    )

    # ── Sub-metrics row ──
    f_metric_v = _font(44)
    f_metric_l = _font(15)
    metric_y_v = 310
    metric_y_l = 365
    col_w = CARD_W // 3
    metrics = [
        (f"{pf:.2f}", "Profit Factor"),
        (f"{win_rate:.0f}%", "Win %"),
        (f"−{max_dd_pct:,.0f}%", "Max Drawdown"),
    ]
    for i, (val, label) in enumerate(metrics):
        cx = col_w * i + col_w // 2
        bbox = draw.textbbox((0, 0), val, font=f_metric_v)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, metric_y_v), val, font=f_metric_v, fill=TEXT_PRIMARY)
        bbox = draw.textbbox((0, 0), label, font=f_metric_l)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, metric_y_l), label, font=f_metric_l, fill=TEXT_MUTED)

    # ── Sparkline ──
    spark_x0, spark_y0, spark_w, spark_h = 120, 410, 960, 130
    points = _sparkline_points(net_r, max_dd, n=80)
    _draw_sparkline(draw, points, spark_x0, spark_y0, spark_w, spark_h)

    # ── Footer ──
    f_foot = _font(13)
    _draw_text_centered(
        draw,
        "Past performance does not guarantee future results. "
        "Not financial advice — for educational use only.",
        f_foot, 580, TEXT_MUTED,
    )

    img.save(OUTPUT_PATH, "PNG", optimize=True)
    print(f"✓ Wrote {OUTPUT_PATH} ({CARD_W}×{CARD_H})")
    print(f"  Headline: +{return_90d_pct:,.0f}% in 90 days · "
          f"PF {pf:.2f} · {win_rate:.0f}% Win · -{max_dd_pct:,.0f}% DD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
