from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


settings = get_settings()
request_windows: dict[str, deque[datetime]] = defaultdict(deque)


async def enforce_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = datetime.utcnow()
    window = request_windows[client]
    threshold = now - timedelta(minutes=1)

    while window and window[0] < threshold:
        window.popleft()

    if len(window) >= settings.api_rate_limit_per_minute:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded.")

    window.append(now)
