"""Middleware that captures STAR HTTP telemetry without altering requests."""

from __future__ import annotations

import logging
import time
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from star.core.utils.http import normalize_metric_path, status_class_from_code

logger = logging.getLogger("star.middleware.observability")

HTTP_REQUESTS_TOTAL = Counter(
    "star_http_requests_total",
    "Total number of HTTP requests processed by STAR.",
    labelnames=("method", "path", "status_code"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "star_http_request_duration_seconds",
    "End-to-end HTTP request duration in seconds.",
    labelnames=("method", "path", "status_class"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

HTTP_INFLIGHT_REQUESTS = Gauge(
    "star_http_inflight_requests",
    "Number of HTTP requests currently being processed.",
)

HTTP_ERRORS_TOTAL = Counter(
    "star_http_errors_total",
    "Total number of HTTP responses classified as 4xx or 5xx.",
    labelnames=("status_class",),
)


class ObservabilityMiddleware:
    """ASGI middleware that captures structural HTTP telemetry.

    This middleware is intentionally passive: it records metrics and never
    modifies requests, responses, or control flow.

    Args:
        app: ASGI application to wrap.
        excluded_path_prefixes: Path prefixes excluded from instrumentation.
            Prefix matching is applied to support endpoint subpaths.
    """

    def __init__(
        self,
        app: ASGIApp,
        excluded_path_prefixes: tuple[str, ...] = ("/metrics",),
    ) -> None:
        """Store the wrapped application and excluded metric paths."""

        self.app = app
        self._excluded_path_prefixes = excluded_path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI scope and record HTTP telemetry when applicable.

        Args:
            scope: Incoming ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = (scope.get("method") or "").upper()
        path = scope.get("path") or "/"
        normalized_path = normalize_metric_path(path)

        if self._is_excluded_path(normalized_path):
            await self.app(scope, receive, send)
            return

        request_id = self._extract_request_id(scope)
        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            """Capture the first response status code before forwarding."""

            nonlocal status_code
            if message.get("type") == "http.response.start" and status_code is None:
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status_code = raw_status
            await send(message)

        start_time = time.perf_counter()
        inflight_incremented = self._safe_metric_operation(
            operation=lambda: HTTP_INFLIGHT_REQUESTS.inc(),
            request_id=request_id,
            method=method,
            normalized_path=normalized_path,
        )

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if status_code is None:
                status_code = 500
            raise
        finally:
            elapsed_seconds = max(0.0, time.perf_counter() - start_time)
            status_class = status_class_from_code(status_code)
            status_code_label = (
                str(status_code) if status_code is not None else "unknown"
            )

            if inflight_incremented:
                self._safe_metric_operation(
                    operation=lambda: HTTP_INFLIGHT_REQUESTS.dec(),
                    request_id=request_id,
                    method=method,
                    normalized_path=normalized_path,
                )

            self._safe_metric_operation(
                operation=lambda: HTTP_REQUESTS_TOTAL.labels(
                    method=method,
                    path=normalized_path,
                    status_code=status_code_label,
                ).inc(),
                request_id=request_id,
                method=method,
                normalized_path=normalized_path,
            )

            self._safe_metric_operation(
                operation=lambda: HTTP_REQUEST_DURATION_SECONDS.labels(
                    method=method,
                    path=normalized_path,
                    status_class=status_class,
                ).observe(elapsed_seconds),
                request_id=request_id,
                method=method,
                normalized_path=normalized_path,
            )

            if status_class in {"4xx", "5xx"}:
                self._safe_metric_operation(
                    operation=lambda: HTTP_ERRORS_TOTAL.labels(
                        status_class=status_class
                    ).inc(),
                    request_id=request_id,
                    method=method,
                    normalized_path=normalized_path,
                )

    def _is_excluded_path(self, normalized_path: str) -> bool:
        """Return `True` when path instrumentation should be skipped.

        Args:
            normalized_path: Canonicalized request path.

        Returns:
            Boolean indicating exclusion.
        """

        for prefix in self._excluded_path_prefixes:
            if normalized_path == prefix or normalized_path.startswith(prefix + "/"):
                return True
        return False

    def _safe_metric_operation(
        self,
        *,
        operation: Callable[[], None],
        request_id: str | None,
        method: str,
        normalized_path: str,
    ) -> bool:
        """Run a metric update operation without impacting traffic.

        Args:
            operation: Zero-argument callable that mutates a metric.
            request_id: Correlation ID from request context when available.
            method: HTTP method used for structured logging.
            normalized_path: Canonical path used for metric labels.

        Returns:
            `True` when operation succeeds, otherwise `False`.
        """

        try:
            operation()
            return True
        except Exception as exc:
            logger.warning(
                "Metric update failed in observability middleware",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": normalized_path,
                    "exception_type": type(exc).__name__,
                },
            )
            return False

    @staticmethod
    def _extract_request_id(scope: Scope) -> str | None:
        """Extract request identifier from ASGI scope state when present.

        Args:
            scope: ASGI scope.

        Returns:
            Request ID string when available, otherwise `None`.
        """

        state = scope.get("state")
        if isinstance(state, dict):
            rid = state.get("request_id")
            if isinstance(rid, str) and rid:
                return rid
        return None


__all__ = [
    "HTTP_ERRORS_TOTAL",
    "HTTP_INFLIGHT_REQUESTS",
    "HTTP_REQUEST_DURATION_SECONDS",
    "HTTP_REQUESTS_TOTAL",
    "ObservabilityMiddleware",
]
