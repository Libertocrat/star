"""Process-local per-client rate-limiting middleware for STAR HTTP traffic."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Awaitable, Callable

from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from star.core.errors import RATE_LIMITED
from star.core.responses import error_json_response
from star.core.utils.http import normalize_metric_path

logger = logging.getLogger("star.middleware.rate_limit")

RATE_LIMITED_TOTAL = Counter(
    "star_rate_limited_total",
    "Total requests rejected by the rate-limit middleware.",
    labelnames=("path", "method", "reason"),
)


class _TokenBucket:
    """In-memory token bucket with async-safe state transitions.

    Args:
        capacity: Maximum number of tokens the bucket can store.
        refill_rate: Number of tokens replenished per second.
        clock: Monotonic clock callable used for refill calculations.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the token bucket state and async lock."""

        self._capacity = float(capacity)
        self._refill_rate = refill_rate
        self._tokens = float(capacity)
        self._clock = clock
        self._updated_at = self._clock()
        self._lock = asyncio.Lock()

    async def try_consume(self) -> bool:
        """Attempt to consume one token.

        Returns:
            `True` if one token was consumed and request can proceed,
            otherwise `False`.
        """

        async with self._lock:
            self._refill_locked()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            return False

    async def time_until_next_token(self) -> float:
        """Estimate how much time remains until at least one token is available."""

        async with self._lock:
            self._refill_locked()
            if self._tokens >= 1.0:
                return 0.0
            return (1.0 - self._tokens) / self._refill_rate

    def _refill_locked(self) -> None:
        """Refill tokens based on elapsed monotonic time."""

        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        if elapsed <= 0.0:
            return

        replenished = elapsed * self._refill_rate
        self._tokens = min(self._capacity, self._tokens + replenished)
        self._updated_at = now


class _ClientBucketStore:
    """Process-local bucket registry keyed by client identity.

    Args:
        capacity: Maximum number of tokens each client bucket can store.
        refill_rate: Number of tokens replenished per second per client.
        ttl_seconds: Inactivity window after which client buckets are removed.
        clock: Monotonic clock callable used for cleanup and bucket refill.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_rate: float,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the per-client bucket registry."""

        self._capacity = capacity
        self._refill_rate = refill_rate
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._buckets: dict[str, _TokenBucket] = {}
        self._last_seen_at: dict[str, float] = {}
        self._last_cleanup_at = self._clock()
        self._lock = asyncio.Lock()

    async def bucket_for(self, client_id: str) -> _TokenBucket:
        """Return the bucket owned by a client identity.

        Args:
            client_id: Stable client identity for rate limiting.

        Returns:
            Token bucket associated with the client identity.
        """

        async with self._lock:
            now = self._clock()
            if now - self._last_cleanup_at >= self._ttl_seconds:
                self._cleanup_expired_locked(now)

            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = _TokenBucket(
                    capacity=self._capacity,
                    refill_rate=self._refill_rate,
                    clock=self._clock,
                )
                self._buckets[client_id] = bucket

            self._last_seen_at[client_id] = now
            return bucket

    def _cleanup_expired_locked(self, now: float) -> None:
        """Remove buckets inactive longer than the configured TTL."""

        expired_client_ids = [
            client_id
            for client_id, last_seen_at in self._last_seen_at.items()
            if now - last_seen_at >= self._ttl_seconds
        ]
        for client_id in expired_client_ids:
            self._buckets.pop(client_id, None)
            self._last_seen_at.pop(client_id, None)
        self._last_cleanup_at = now


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client token-bucket rate limiter for STAR HTTP traffic.

    The middleware enforces a process-local requests-per-second cap per client
    host and returns a structured 429 envelope when the client's bucket is
    exhausted.

    Args:
        app: ASGI application to wrap.
        rate_limit_rps: Optional explicit requests-per-second override. When
            omitted, the value is resolved from `app.state.settings`.
    """

    def __init__(self, app: ASGIApp, rate_limit_rps: int | None = None) -> None:
        """Create the middleware and provision the client bucket store."""

        super().__init__(app)
        self._rate_limit_rps = self._resolve_rate_limit_rps(app, rate_limit_rps)
        self._buckets = _ClientBucketStore(
            capacity=self._rate_limit_rps,
            refill_rate=float(self._rate_limit_rps),
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Handle a request and enforce per-client rate limiting."""

        if self._is_exempt_path(request):
            return await call_next(request)

        bucket = await self._buckets.bucket_for(self._client_identity(request))
        if await bucket.try_consume():
            return await call_next(request)

        return await self._build_rate_limited_response(request, bucket)

    @staticmethod
    def _client_identity(request: Request) -> str:
        """Return the client identity used for rate limiting."""

        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def _is_exempt_path(self, request: Request) -> bool:
        """Return `True` when the request path must skip rate limiting."""

        path = request.url.path
        for prefix in self._exempt_prefixes(request):
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return False

    def _exempt_prefixes(self, request: Request) -> tuple[str, ...]:
        """
        Build the list of endpoint prefixes exempt from rate limiting.
        If documentation endpoints are enabled, they are also exempted.
        """

        prefixes = ["/metrics"]

        settings = getattr(getattr(request.app, "state", None), "settings", None)
        if settings and getattr(settings, "star_enable_docs", False):
            prefixes.extend(["/openapi.json", "/docs", "/redoc"])

        return tuple(prefixes)

    async def _build_rate_limited_response(
        self,
        request: Request,
        bucket: _TokenBucket,
    ) -> JSONResponse:
        """Create the standardized 429 response envelope and telemetry."""

        reason = "token_bucket_exhausted"
        request_id = getattr(request.state, "request_id", None)
        client_host = request.client.host if request.client else "unknown"
        retry_after_seconds = await bucket.time_until_next_token()
        normalized_path = normalize_metric_path(request.url.path)

        RATE_LIMITED_TOTAL.labels(
            path=normalized_path,
            method=request.method,
            reason=reason,
        ).inc()

        logger.info(
            "Rate-limited request",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "client_host": client_host,
                "reason": reason,
            },
        )

        headers: dict[str, str] = {"X-Request-Id": request_id} if request_id else {}
        if retry_after_seconds > 0:
            headers["Retry-After"] = str(math.ceil(retry_after_seconds))
        return error_json_response(RATE_LIMITED, headers=headers)

    @staticmethod
    def _resolve_rate_limit_rps(app: ASGIApp, override: int | None) -> int:
        """Resolve middleware RPS setting."""

        if override is not None and override > 0:
            return override

        settings = getattr(getattr(app, "state", None), "settings", None)
        configured = getattr(settings, "star_rate_limit_rps", None)
        if isinstance(configured, int) and configured > 0:
            return configured

        return 10
