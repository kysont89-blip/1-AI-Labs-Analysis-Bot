"""
Ollama Health Check Utility
Quick check if Ollama is running and responsive.
"""

import httpx
import time

_ollama_status = None
_last_check = 0
CACHE_TTL = 30  # Check every 30 seconds


async def is_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama is running and responsive."""
    global _ollama_status, _last_check

    now = time.time()
    if _ollama_status is not None and (now - _last_check) < CACHE_TTL:
        return _ollama_status

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            _ollama_status = response.status_code == 200
    except Exception:
        _ollama_status = False

    _last_check = now
    return _ollama_status


def is_ollama_running_sync(base_url: str = "http://localhost:11434") -> bool:
    """Synchronous check if Ollama is running."""
    global _ollama_status, _last_check

    now = time.time()
    if _ollama_status is not None and (now - _last_check) < CACHE_TTL:
        return _ollama_status

    try:
        import requests
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=2.0)
        _ollama_status = response.status_code == 200
    except Exception:
        _ollama_status = False

    _last_check = now
    return _ollama_status


def reset_ollama_status():
    """Reset cached status (call after bot restart)."""
    global _ollama_status, _last_check
    _ollama_status = None
    _last_check = 0


if __name__ == '__main__':
    import asyncio
    status = asyncio.run(is_ollama_running())
    print(f"Ollama running: {status}")
