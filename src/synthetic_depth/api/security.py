"""API Security, Key Authentication, and In-Memory Rate Limiting."""

import time
from collections import defaultdict, deque
from typing import Dict, Optional
from fastapi import Header, HTTPException, Security, status

from synthetic_depth.config import get_settings


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter per API key.

    Note for Production: In a multi-worker / multi-instance deployment, replace this
    in-memory structure with a Redis-backed token bucket.
    """

    def __init__(self, requests_per_minute: int = 120):
        """Initialize RateLimiter.

        Args:
            requests_per_minute: Max allowed requests per 60-second window.
        """
        self.requests_per_minute = requests_per_minute
        self._history: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, api_key: str) -> bool:
        """Check if request is within rate limit and record timestamp."""
        now = time.time()
        window_start = now - 60.0
        timestamps = self._history[api_key]

        # Purge timestamps outside the 60-second window
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()

        if len(timestamps) >= self.requests_per_minute:
            return False

        timestamps.append(now)
        return True

    def reset(self) -> None:
        """Reset rate limiter state (useful for tests)."""
        self._history.clear()


# Global singleton rate limiter instance
_rate_limiter = InMemoryRateLimiter(requests_per_minute=get_settings().rate_limit_per_minute)


def get_rate_limiter() -> InMemoryRateLimiter:
    """Retrieve singleton rate limiter."""
    return _rate_limiter


def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", description="API Access Key"),
) -> str:
    """Validate API key from request header and apply rate limiting.

    Args:
        x_api_key: API key provided in X-API-Key header.

    Returns:
        Validated API key string.

    Raises:
        HTTPException 401: If API key is missing or not recognized.
        HTTPException 429: If API key has exceeded request rate limit.
    """
    settings = get_settings()
    allowed_keys = settings.api_keys

    if not x_api_key or x_api_key.strip() not in allowed_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Provide a valid 'X-API-Key' header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    clean_key = x_api_key.strip()
    limiter = get_rate_limiter()
    if not limiter.is_allowed(clean_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API rate limit exceeded. Max 120 requests per minute.",
            headers={"Retry-After": "60"},
        )

    return clean_key
