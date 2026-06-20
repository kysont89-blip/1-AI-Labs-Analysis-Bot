# 1% AI Lab — Web Dashboard

A second surface for the XOX AI Analysis Bot. Same analysis engine,
professional web shell, TradingView-style charts.

## Run locally

```bash
# from the project root
python -m venv .venv-web
.venv-web\Scripts\activate          # Windows
pip install -r requirements-web.txt
uvicorn web.main:app --reload --port 8000
```

Open <http://localhost:8000/>.

## Layout

```
web/
├── main.py               # FastAPI app — Phase 0 entry point
├── templates/
│   ├── base.html         # Brand shell
│   └── index.html        # Landing page (Phase 0 surface)
├── static/
│   ├── css/
│   │   ├── tokens.css    # Design tokens (colors, spacing, type)
│   │   └── app.css       # Base styles
│   └── img/              # Logo assets (Phase 0: rendered via CSS wordmark)
├── routes/               # Added in Phase 2+
├── db.py                 # Added in Phase 1
└── migrations/           # Added in Phase 1
```

## Phases

See `dapper-mapping-bonbon.md` for the full phased plan.

| Phase | Status | Surface |
|---|---|---|
| 0 | ✅ Shipped | Brand shell + landing page |
| 1 | ✅ Shipped | Neon Postgres + SQLite migration |
| 2 | ✅ Shipped | Auth + XOX register/login (web mirror of /verify) |
| 3 | ✅ Shipped | Dashboard + TradingView chart |
| 4 | ✅ Shipped | `/analyze` web flow + settings UI |
| 5 | 🚧 In progress | Render deploy + Ollama ngrok tunnel |
| 6 | ⏳ Pending | Polish (OG image, 404/500, lighthouse) |

## Phase 5 — Render deploy

`render.yaml` at the project root describes the Web Service. Required
env vars (set in Render dashboard, never committed):

- `DATABASE_URL` — Neon Postgres connection string
- `SESSION_SECRET` — Render generates this on first deploy
- `ADMIN_EMAIL` — the user allowed to approve /verify requests
- `OLLAMA_BASE_URL` — ngrok URL for the local Ollama instance (see
  `scripts/ollama_tunnel.py`)
- `RESEND_API_KEY` — for password reset emails

### Ollama tunnel

The local Ollama instance is the LLM backend. Render (and any cloud
host) cannot reach `127.0.0.1:11434` from behind the home NAT, so we
front it with ngrok. The workstation running Ollama must also run
`scripts/ollama_tunnel.py` — it spawns ngrok, polls its local API for
the public URL, and writes that URL to `.ollama_url` and stdout.

```bash
# one-time
ngrok config add-authtoken <your-token>     # or export NGROK_AUTHTOKEN

# every time you want the tunnel up
python scripts/ollama_tunnel.py
# → Public Ollama URL: https://<random>.ngrok-free.app
# → Written to .ollama_url
```

Copy the URL into:
- `.env` (so the local Telegram bot can find Ollama)
- Render env var `OLLAMA_BASE_URL` (so the web app on Render can find it)

## Design notes

- Pure black/white, no color accent — reserves green/red for the
  BUY/SELL signal pills only.
- Inter (UI), JetBrains Mono (numbers).
- 4px spacing scale.
- Light hand-rolled CSS instead of Tailwind in Phase 0 — faster to
  ship, smaller deploy, matches the minimal brand. Tailwind comes
  back in if/when we need it.

## Boundaries

`bots/` is read-only from this app. The web layer calls into the
existing analysis pipeline (`bots/report_builder.build`, etc.) but
never mutates bot-side code. Both surfaces share the same Neon DB
row from Phase 1 onward.