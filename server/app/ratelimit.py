"""In-memory rate limiter for the free tier.

v1: per-IP sliding window. Future: per-API-key with Redis backend.
"""

import time
from collections import defaultdict
from fastapi import Request, HTTPException


# Sliding window: track request timestamps per IP
_windows: dict[str, list[float]] = defaultdict(list)

# Free tier: 60 requests per minute
RATE_LIMIT = 60
WINDOW_SECONDS = 60


def check_rate_limit(request: Request) -> None:
    """Raise 429 if the client has exceeded the rate limit."""
    # Use X-Forwarded-For if behind proxy, otherwise client host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    now = time.monotonic()
    window = _windows[client_ip]

    # Prune old entries
    cutoff = now - WINDOW_SECONDS
    _windows[client_ip] = [t for t in window if t > cutoff]
    window = _windows[client_ip]

    if len(window) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "RATE_LIMITED",
                "message": f"Free tier limit: {RATE_LIMIT} requests per minute",
                "retryAfterSeconds": int(WINDOW_SECONDS - (now - window[0])) + 1,
            },
        )

    window.append(now)
