"""Ollama ngrok tunnel launcher — Phase 5 of the web dashboard plan.

Run this on the workstation that hosts the local Ollama instance. It
starts an ngrok tunnel in front of Ollama's port 11434 and prints the
public URL so we can wire it into Render's OLLAMA_BASE_URL env var.

Why: Render (and any cloud host) cannot reach 127.0.0.1:11434 from
behind the home NAT. ngrok bridges that gap. Free tier is enough —
Ollama requests come in at human pacing (a handful per minute at
peak), not at cloud rate.

Setup:
  1. Install ngrok: https://ngrok.com/download
  2. Sign up, copy your authtoken, run `ngrok config add-authtoken <token>`
  3. Set NGROK_AUTHTOKEN in your shell (or .env) — DO NOT commit it.

Usage:
  python scripts/ollama_tunnel.py
  # then in another shell:
  curl https://<random>.ngrok-free.app/api/generate -d '{"model":"qwen3.5:397b-cloud","prompt":"hi"}'

The script also writes the public URL to .ollama_url so the bot and
the web app can pick it up without copy-paste.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NGROK_API = "http://127.0.0.1:4040/api/tunnels"  # ngrok local API
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", "11434"))
URL_OUT = Path(__file__).resolve().parent.parent / ".ollama_url"


def _have_ngrok() -> bool:
    """True iff the ngrok binary is on PATH."""
    from shutil import which
    return which("ngrok") is not None


def _have_authtoken() -> bool:
    """True iff ngrok already has an authtoken configured.

    We check the ngrok config file rather than env to avoid coupling
    this script to a specific env var name. Users can either run
    `ngrok config add-authtoken ...` once or set NGROK_AUTHTOKEN.
    """
    if os.environ.get("NGROK_AUTHTOKEN"):
        return True
    # ngrok config path on Windows: %LOCALAPPDATA%\ngrok\ngrok.yml
    # (older installs may have used %APPDATA%; check both).
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "ngrok" / "ngrok.yml",
        Path(os.environ.get("APPDATA", "")) / "ngrok" / "ngrok.yml",
    ]
    return any(p.exists() for p in candidates)


def _start_ngrok() -> subprocess.Popen:
    """Spawn the ngrok process. Returns the Popen handle."""
    if not _have_ngrok():
        sys.exit("ngrok not found on PATH. Install from https://ngrok.com/download")
    if not _have_authtoken():
        sys.exit(
            "ngrok has no authtoken. Run `ngrok config add-authtoken <token>` "
            "or set NGROK_AUTHTOKEN in your environment."
        )

    # If the caller passed NGROK_AUTHTOKEN via env, ngrok picks it up
    # automatically — no need to write to its config file.
    cmd = ["ngrok", "http", str(OLLAMA_PORT), "--log", "stdout"]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )


def _wait_for_tunnel(timeout: float = 30.0) -> str:
    """Poll ngrok's local API until a tunnel is up. Returns public URL."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(NGROK_API, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
            for t in data.get("tunnels", []):
                if t.get("proto") == "https":
                    return t["public_url"]
        except Exception:
            pass
        time.sleep(0.5)
    sys.exit(f"ngrok did not come up within {timeout}s — check 'ngrok' output.")


def main() -> None:
    print(f"Starting ngrok tunnel to Ollama on :{OLLAMA_PORT} ...")
    proc = _start_ngrok()

    # On Ctrl-C, kill ngrok too.
    def _shutdown(*_args):
        print("\nShutting down ngrok ...")
        proc.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)

    url = _wait_for_tunnel()
    print(f"\nPublic Ollama URL: {url}")
    print(f"(Written to {URL_OUT})")
    URL_OUT.write_text(url, encoding="utf-8")

    print("\nNext steps:")
    print("  1. Visit this URL ONCE in a browser to activate the tunnel:")
    print(f"     {url}")
    print("     (ngrok free-tier requires a browser visit before API")
    print("      calls from non-browser clients work. Without it you'll")
    print("      get HTTP 403 from programmatic callers like Render.)")
    print("  2. Set OLLAMA_BASE_URL in your Render env vars to this URL.")
    print("  3. In .env / web/.env, set OLLAMA_BASE_URL too — so the bot")
    print("     running locally can use the same tunnel.")
    print("\nKeep this script running. Closing it kills the tunnel.")


if __name__ == "__main__":
    main()
