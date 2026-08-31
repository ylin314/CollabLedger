from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass(frozen=True)
class LimitRule:
    max_requests: int
    window_seconds: float


class SlidingWindowLimiter:
    """Small process-local limiter; suitable for one-worker deployments and dev.

    For multi-worker or multi-instance production, place an equivalent policy at the
    reverse proxy or replace the store with Redis.
    """

    def __init__(self, rules: Optional[dict[str, LimitRule]] = None, *, max_keys: int = 10000):
        self.rules = rules or {}
        self.max_keys = max_keys
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _rule_for(self, path: str) -> Optional[LimitRule]:
        if path == "/api/agent/chat" or "/agent" in path:
            return self.rules.get("@agent")
        for prefix, rule in self.rules.items():
            if prefix.startswith("@"):
                continue
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return rule
        return None

    def check(self, key: str, path: str, now: Optional[float] = None) -> tuple[bool, int, Optional[int]]:
        rule = self._rule_for(path)
        if rule is None:
            return True, 0, None
        current = time.monotonic() if now is None else now
        bucket_key = (key, path)
        with self._lock:
            bucket = self._events[bucket_key]
            cutoff = current - rule.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= rule.max_requests:
                retry_after = max(1, int(bucket[0] + rule.window_seconds - current + 0.999))
                return False, len(bucket), retry_after
            bucket.append(current)
            if len(self._events) > self.max_keys:
                stale = [k for k, events in self._events.items() if not events or events[-1] <= current - 3600]
                for stale_key in stale[: max(1, len(stale) // 2)]:
                    self._events.pop(stale_key, None)
            return True, len(bucket), None


DEFAULT_RULES = {
    "/api/auth/login": LimitRule(10, 60),
    "/api/auth/register": LimitRule(5, 300),
    "/api/auth/accept-invitation": LimitRule(20, 60),
    "/api/invitations": LimitRule(20, 60),
    "@agent": LimitRule(30, 60),
    "/api/": LimitRule(300, 60),
}


def _env_disabled() -> bool:
    return (os.getenv("COLLAB_RATE_LIMIT_DISABLED") or "").strip().lower() in {"1", "true", "yes"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, limiter: Optional[SlidingWindowLimiter] = None, trusted_proxy: bool = False):
        super().__init__(app)
        self.disabled = _env_disabled()
        self.limiter = limiter or SlidingWindowLimiter(DEFAULT_RULES)
        self.trusted_proxy = trusted_proxy

    def _client_key(self, request: Request) -> str:
        if self.trusted_proxy:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # COLLAB_RATE_LIMIT_DISABLED=1 仅用于本地 E2E/演示环境批量造号；生产保持默认限流。
        if self.disabled:
            return await call_next(request)
        # Starlette TestClient shares one application-level limiter across tests;
        # unit tests exercise the limiter directly instead of consuming production quotas.
        if request.client and request.client.host == "testclient":
            return await call_next(request)
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        allowed, _, retry_after = self.limiter.check(self._client_key(request), request.url.path)
        if not allowed:
            return JSONResponse(
                {"error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}},
                status_code=429,
                headers={"Retry-After": str(retry_after or 1)},
            )
        return await call_next(request)
