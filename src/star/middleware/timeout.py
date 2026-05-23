"""Request timeout middleware for STAR HTTP traffic."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from star.core.errors import TIMEOUT, StarError
from star.core.schemas.envelope import ResponseEnvelope
from star.core.utils.http import normalize_metric_path

logger = logging.getLogger("star.middleware.timeout")

TIMEOUTS_TOTAL = Counter(
    "star_timeouts_total",
    "Total requests timed out in timeout middleware.",
    labelnames=("path", "method"),
)


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a hard request execution timeout.

    The middleware wraps downstream request processing in
    `asyncio.wait_for(...)` and converts timeout-like failures into
    a standardized timeout envelope. The `/metrics` and `/health`
    endpoints are exempted to prevent observability traffic from
    triggering signals that would mask healthy scraping behavior.

    Args:
        app: ASGI application to wrap.
        timeout_ms: Optional explicit timeout override in milliseconds.
            When omitted, the value is resolved from `app.state.settings`.

    Attributes:
        _MIN_TIMEOUT_MS: Minimum accepted timeout value in milliseconds.
    """

    _MIN_TIMEOUT_MS = 100

    def __init__(self, app: ASGIApp, timeout_ms: int | None = None) -> None:
        """Initialize timeout configuration and precomputed seconds value."""

        super().__init__(app)
        self._timeout_ms = self._resolve_timeout_ms(app, timeout_ms)
        self._timeout_seconds = max(0.1, self._timeout_ms / 1000.0)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Execute downstream request handling with timeout enforcement.

        Args:
            request: Incoming HTTP request.
            call_next: Callable that invokes the next middleware/app.

        Returns:
            Downstream response when it completes on time, otherwise a
            structured timeout response.

        Raises:
            StarError: Propagated unchanged to avoid masking domain errors.
        """

        if self._is_exempt_path(request):
            return await call_next(request)

        start = time.monotonic()

        try:
            return await asyncio.wait_for(
                call_next(request),
                timeout=self._timeout_seconds,
            )
        except StarError:
            raise
        except asyncio.TimeoutError:
            elapsed_ms = self._elapsed_ms_since(start)
            return self._build_timeout_response(
                request=request,
                elapsed_ms=elapsed_ms,
                reason="wait_for_timeout",
            )
        except asyncio.CancelledError:
            # Treat cancellation as a timeout signal for consistent
            # observability in the middleware layer.
            elapsed_ms = self._elapsed_ms_since(start)
            return self._build_timeout_response(
                request=request,
                elapsed_ms=elapsed_ms,
                reason="cancelled",
            )

    def _build_timeout_response(
        self,
        request: Request,
        elapsed_ms: int,
        reason: str,
    ) -> JSONResponse:
        """Create timeout telemetry and return a standardized 504 envelope.

        Args:
            request: Incoming HTTP request.
            elapsed_ms: Elapsed request time in milliseconds.
            reason: Observability reason describing timeout condition.

        Returns:
            JSONResponse with timeout error envelope.
        """

        request_id = getattr(request.state, "request_id", None)
        client_host = request.client.host if request.client else "unknown"
        normalized_path = normalize_metric_path(request.url.path)

        # Increment timeout Prometheus metric
        TIMEOUTS_TOTAL.labels(path=normalized_path, method=request.method).inc()

        logger.warning(
            "Request timed out",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "client_host": client_host,
                "elapsed_ms": elapsed_ms,
                "reason": reason,
            },
        )

        headers: dict[str, str] = {"X-Request-Id": request_id} if request_id else {}
        payload = ResponseEnvelope.failure(
            code=TIMEOUT.code,
            message=TIMEOUT.default_message,
        ).model_dump()

        return JSONResponse(
            status_code=TIMEOUT.http_status,
            content=payload,
            headers=headers,
        )

    def _is_exempt_path(self, request: Request) -> bool:
        """Return True when the request should skip timeout enforcement."""

        path = request.url.path
        for prefix in ("/metrics", "/health"):
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return False

    @classmethod
    def _resolve_timeout_ms(cls, app: ASGIApp, override: int | None) -> int:
        """Resolve timeout configuration with minimum clamping.

        Args:
            app: ASGI application that may contain `app.state.settings`.
            override: Optional explicit timeout in milliseconds.

        Returns:
            Timeout value in milliseconds, clamped to a sensible minimum.
        """

        if isinstance(override, int) and override > 0:
            return max(cls._MIN_TIMEOUT_MS, override)

        settings = getattr(getattr(app, "state", None), "settings", None)
        configured = getattr(settings, "star_timeout_ms", None)

        if isinstance(configured, int) and configured > 0:
            return max(cls._MIN_TIMEOUT_MS, configured)

        return cls._MIN_TIMEOUT_MS

    @staticmethod
    def _elapsed_ms_since(start: float) -> int:
        """Calculate elapsed milliseconds since monotonic start time.

        Args:
            start: Monotonic start timestamp.

        Returns:
            Non-negative elapsed milliseconds.
        """

        return max(0, int((time.monotonic() - start) * 1000))
