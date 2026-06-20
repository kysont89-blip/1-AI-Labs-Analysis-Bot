"""1% AI Lab — Web Dashboard entry point.

Run:
    uvicorn web.main:app --reload --port 8000

The bot's analysis pipeline (`bots/`) is read-only from here — web is a
second surface backed by the same modules. Phase 2 adds auth and the
XOX verify flow; later phases add the dashboard, analyze, and settings.
"""
from __future__ import annotations

# psycopg async on Windows only works on SelectorEventLoop. Uvicorn's
# default ProactorEventLoop rejects the connection at handshake time.
# Install the policy at the very top of main.py so it's active before
# uvicorn constructs the loop. On non-Windows and under the Linux-only
# Render deploy this is a no-op.
import sys

if sys.platform == "win32":
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except (AttributeError, DeprecationWarning):
        pass

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .deps import current_user
from .routes import analyze, auth, dashboard, market, settings, verify


WEB_ROOT = Path(__file__).resolve().parent
STATIC_DIR = WEB_ROOT / "static"
TEMPLATES_DIR = WEB_ROOT / "templates"

app = FastAPI(
    title="1% AI Lab",
    version="0.2.0",
    docs_url=None,           # No public Swagger yet
    redoc_url=None,
)

# Static assets (logo, compiled CSS) — mounted at /static
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Expose templates on app.state so route modules can fetch the same
# instance without re-importing jinja2 directly. This keeps config in
# one place — main.py owns the templates dir.
app.state.templates = templates


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(current_user)) -> HTMLResponse:
    """Public landing page. Shows 'Open dashboard' if signed in,
    'Sign in / Create account' if not."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "brand": {
                "name": "1% AI Lab",
                "tagline": "AI-powered trading analysis, in your browser.",
                "subtitle": (
                    "Same engine as the XOX Telegram bot — now with "
                    "TradingView-style charts, a real dashboard, and a "
                    "professional surface."
                ),
            },
            "user": user,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Tiny liveness probe Render will hit during deploys."""
    return {"status": "ok", "version": app.version}


# ── Phase 6 — error pages + meta ────────────────────────────────
_BRAND = {
    "name": "1% AI Lab",
    "tagline": "AI-powered trading analysis, in your browser.",
    "subtitle": (
        "Same engine as the XOX Telegram bot — now with "
        "TradingView-style charts, a real dashboard, and a "
        "professional surface."
    ),
}


@app.exception_handler(404)
async def not_found(request: Request, exc) -> HTMLResponse:
    if request.url.path.startswith("/api/") or request.url.path.startswith("/analyze") \
            or request.url.path.startswith("/market/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return templates.TemplateResponse(
        request, "404.html", {"brand": _BRAND}, status_code=404,
    )


@app.exception_handler(500)
async def server_error(request: Request, exc) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "500.html", {"brand": _BRAND}, status_code=500,
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    """Phase 6: standard SEO. Disallow /app, /verify, /admin so search
    engines index only the marketing surface."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /app\n"
        "Disallow: /app/\n"
        "Disallow: /verify\n"
        "Disallow: /admin\n"
        "Disallow: /auth/\n"
        "Disallow: /api/\n"
        "Disallow: /market/\n"
        "Disallow: /analyze\n"
        "Disallow: /healthz\n"
        "\n"
        "Sitemap: /sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap() -> HTMLResponse:
    """Phase 6: minimal sitemap. Only public marketing routes."""
    base = os.environ.get("PUBLIC_BASE_URL", "https://onepercent-ai-lab.onrender.com")
    urls = [
        f"{base}/",
        f"{base}/auth/login",
        f"{base}/auth/register",
    ]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(
            f"  <url><loc>{u}</loc><changefreq>weekly</changefreq></url>" for u in urls
        )
        + "\n</urlset>\n"
    )
    return HTMLResponse(content=body, media_type="application/xml")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "privacy.html", {"brand": _BRAND})


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms.html", {"brand": _BRAND})


# ── Routers ──────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(verify.router)
app.include_router(dashboard.router)
app.include_router(market.router)
app.include_router(analyze.router)
app.include_router(settings.router)


if __name__ == "__main__":
    # Windows: psycopg async requires SelectorEventLoop. Uvicorn 0.45+
    # ignores set_event_loop_policy and creates a Proactor loop by
    # default. Pass a loop factory via the `loop` argument so the
    # running loop is right. On non-Windows leave the default.
    import asyncio
    import selectors
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    if sys.platform == "win32":
        loop = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    else:
        loop = "asyncio"

    uvicorn.run("web.main:app", host="0.0.0.0", port=port, loop=loop)
