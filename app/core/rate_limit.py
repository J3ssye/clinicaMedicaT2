from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


settings = get_settings()
_request_windows: dict[str, deque[datetime]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def enforce_rate_limit(request: Request) -> None:
    client = _client_key(request)
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(minutes=1)
    window = _request_windows[client]

    while window and window[0] < threshold:
        window.popleft()

    if len(window) >= settings.api_rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limit_exceeded",
        )

    window.append(now)
