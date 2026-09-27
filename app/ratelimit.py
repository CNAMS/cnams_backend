"""Simple in-memory rate limiter — 200 req/min per IP, 20/min on /auth/*."""
import time
from collections import defaultdict, deque

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

LIMITS = (("/auth/", 20, 60), ("/", 200, 60))


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        ip = request.client.host if request.client else "?"
        path = request.url.path
        limit, window = next((l, w) for prefix, l, w in LIMITS if path.startswith(prefix + "v1/auth") or (prefix == "/" and True))
        # resolve: auth rule first
        if path.startswith("/v1/auth"):
            limit, window = 20, 60
        else:
            limit, window = 200, 60
        now = time.monotonic()
        q = self.hits[f"{ip}:{limit}"]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return JSONResponse({"detail": "rate_limited", "code": 429}, status_code=429,
                                headers={"Retry-After": str(int(window - (now - q[0])))})
        q.append(now)
        return await call_next(request)
