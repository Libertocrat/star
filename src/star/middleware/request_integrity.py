"""
RequestIntegrityMiddleware

Structural hygiene enforcement layer (NOT a WAF).

Responsibilities:
- Reject malformed / structurally invalid requests as early as possible.
- Always return the standard error envelope (never raw HTTPException).
- Always include X-Request-Id (preserve if present, otherwise generate).
- Enforce:
  - Path sanity (NUL, backslash, control chars <0x20 except TAB)
  - Header integrity via raw headers (duplicate Authorization, whitespace in name,
    control chars in name/value)
  - Content-Type for routes with declared content-type policies
  - Body size enforcement using a default limit plus explicit route policies:
    - Strict Content-Length parsing when present
    - Streaming enforcement when Content-Length is absent
  - Rejection of request bodies on methods that STAR treats as bodyless
"""

from __future__ import annotations

import logging
import uuid

from prometheus_client import Counter
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from star.core.errors import (
    INVALID_REQUEST,
    REQUEST_BODY_TOO_LARGE,
    ErrorDef,
)
from star.core.responses import error_json_response
from star.core.security.headers import find_header_integrity_violation
from star.core.security.http_body import (
    BODY_NOT_ALLOWED_MESSAGE,
    inspect_bodyless_request,
    method_allows_request_body,
    wrap_receive_with_body_limit,
)
from star.core.security.http_policies import (
    BodyLimitPolicy,
    ContentTypePolicy,
    ResolvedBodyLimit,
    index_body_limit_policies,
    index_content_type_policies,
    resolve_body_limit_policy,
    resolve_content_type_policy,
    resolve_default_body_limit,
)
from star.core.security.http_validation import (
    normalize_content_type,
    parse_content_length_strict,
    path_has_disallowed_characters,
)
from star.core.utils.http import normalize_metric_path

logger = logging.getLogger("star.middleware.request_integrity")

REQUEST_INTEGRITY_REJECTIONS_TOTAL = Counter(
    "star_request_integrity_rejections_total",
    "Total requests rejected by request-integrity middleware.",
    labelnames=("path", "method", "reason"),
)


class RequestIntegrityMiddleware:
    """ASGI middleware enforcing structural request integrity constraints.

    Args:
        app: The ASGI application to wrap.
        max_body_bytes: Optional explicit default body size limit. This limit
            applies to non-upload request bodies.
        content_type_policies: Optional collection of policies that restrict
            the allowed content types per method/path.
        body_limit_policies: Optional collection of method/path-specific body
            limits for routes that need an explicit override, such as multipart
            upload endpoints.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int | None = None,
        content_type_policies: list[ContentTypePolicy] | None = None,
        body_limit_policies: list[BodyLimitPolicy] | None = None,
    ) -> None:
        """Configure body limits and content-type policies."""

        self.app = app
        self._default_body_limit = ResolvedBodyLimit(
            max_bytes=resolve_default_body_limit(app, max_body_bytes),
            error=REQUEST_BODY_TOO_LARGE,
        )
        self._content_type_policies = index_content_type_policies(
            content_type_policies or []
        )
        self._body_limit_policies = index_body_limit_policies(body_limit_policies or [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an incoming ASGI request with hygiene checks.

        Args:
            scope: Incoming ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = (scope.get("method") or "").upper()
        path = scope.get("path") or ""

        # Ensure request id is always available for any rejection.
        request_id = self._get_or_create_request_id(scope)

        # ------------------------------------------------------------------
        # 1) Path sanity (all requests)
        # ------------------------------------------------------------------
        if path_has_disallowed_characters(path):
            await self._send_rejection(
                scope=scope,
                receive=receive,
                send=send,
                request_id=request_id,
                error=INVALID_REQUEST,
                message="Malformed request path",
                reason="invalid_path",
            )
            return

        # ------------------------------------------------------------------
        # 2) Header integrity (all requests)
        # ------------------------------------------------------------------
        raw_headers = self._get_raw_headers(scope)
        header_violation = find_header_integrity_violation(raw_headers)
        if header_violation is not None:
            duplicate_msg = "Duplicate Authorization headers are not allowed"
            message_by_reason: dict[str, str] = {
                "duplicate_authorization": duplicate_msg,
                "header_name_whitespace": "Malformed request headers",
                "header_name_control_char": "Malformed request headers",
                "header_value_control_char": "Malformed request headers",
            }
            await self._send_rejection(
                scope=scope,
                receive=receive,
                send=send,
                request_id=request_id,
                error=INVALID_REQUEST,
                message=message_by_reason.get(
                    header_violation, INVALID_REQUEST.default_message
                ),
                reason=header_violation,
            )
            return

        # ------------------------------------------------------------------
        # 3) CL + TE smuggling mitigation and Content-Length parsing
        # ------------------------------------------------------------------
        content_length_value = self._get_header_value(raw_headers, b"content-length")
        has_content_length = content_length_value is not None
        has_transfer_encoding = (
            self._get_header_value(raw_headers, b"transfer-encoding") is not None
        )

        if has_content_length and has_transfer_encoding:
            await self._send_rejection(
                scope=scope,
                receive=receive,
                send=send,
                request_id=request_id,
                error=INVALID_REQUEST,
                message="Conflicting Content-Length and Transfer-Encoding headers",
                reason="conflicting_cl_te",
            )
            return

        declared_size: int | None = None
        if content_length_value is not None:
            try:
                declared_size = parse_content_length_strict(content_length_value)
            except ValueError:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=INVALID_REQUEST,
                    message="Invalid Content-Length header",
                    reason="invalid_content_length",
                )
                return

        # ------------------------------------------------------------------
        # 4) Reject bodies on methods that STAR treats as bodyless
        # ------------------------------------------------------------------
        if not method_allows_request_body(method):
            bodyless_result = await inspect_bodyless_request(
                receive=receive,
                declared_size=declared_size,
                has_transfer_encoding=has_transfer_encoding,
            )
            if bodyless_result.body_detected:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=INVALID_REQUEST,
                    message=BODY_NOT_ALLOWED_MESSAGE,
                    reason="body_not_allowed",
                )
                return

            await self.app(scope, bodyless_result.receive, send)
            return

        # ------------------------------------------------------------------
        # 5) Content-Type enforcement (policy-driven)
        # ------------------------------------------------------------------
        policy = resolve_content_type_policy(
            self._content_type_policies,
            method=method,
            path=path,
        )
        if policy:
            raw_ct = self._get_header_value(raw_headers, b"content-type")
            base_ct = normalize_content_type(raw_ct)

            if base_ct not in policy:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=INVALID_REQUEST,
                    message="Unsupported content type",
                    reason="unsupported_content_type",
                )
                return

        # ------------------------------------------------------------------
        # 6) Body size enforcement
        # ------------------------------------------------------------------
        body_limit = resolve_body_limit_policy(
            self._body_limit_policies,
            default_limit=self._default_body_limit,
            method=method,
            path=path,
        )
        if declared_size is not None:
            if declared_size > body_limit.max_bytes:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=body_limit.error,
                    message=body_limit.error.default_message,
                    reason="content_length_exceeds_limit",
                )
                return

            # Declared size is within limit: proceed normally.
            await self.app(scope, receive, send)
            return

        body_limited_receive = wrap_receive_with_body_limit(
            receive,
            body_limit.max_bytes,
        )
        response_started = False

        async def guarded_send(message: Message) -> None:
            """Suppress downstream responses after the body limit is exceeded."""

            nonlocal response_started
            if body_limited_receive.limit_exceeded():
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, body_limited_receive.receive, guarded_send)
        except Exception:
            if not body_limited_receive.limit_exceeded():
                raise

        if body_limited_receive.limit_exceeded() and not response_started:
            await self._send_rejection(
                scope=scope,
                receive=receive,
                send=send,
                request_id=request_id,
                error=body_limit.error,
                message=body_limit.error.default_message,
                reason="body_exceeds_limit",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_raw_headers(scope: Scope) -> list[tuple[bytes, bytes]]:
        """Return the raw headers list from the ASGI scope.

        Args:
            scope: ASGI request scope.

        Returns:
            A list of raw `(name, value)` header tuples.
        """

        raw = scope.get("raw_headers")
        if isinstance(raw, list):
            return raw
        hdrs = scope.get("headers")
        if isinstance(hdrs, list):
            return hdrs
        return []

    @staticmethod
    def _get_header_value(
        raw_headers: list[tuple[bytes, bytes]], name: bytes
    ) -> str | None:
        """Return the decoded header value matching `name`, if present.

        Args:
            raw_headers: List of raw headers.
            name: Header name to search for.

        Returns:
            Header value decoded via Latin-1 when found, otherwise None.
        """

        needle = name.lower()
        for k, v in raw_headers:
            if k.lower() == needle:
                # Use latin-1 for safe round-trip of arbitrary bytes.
                return v.decode("latin-1")
        return None

    @staticmethod
    def _get_or_create_request_id(scope: Scope) -> str:
        """Return an existing request ID or generate a new one.

        Args:
            scope: ASGI scope where request state is stored.

        Returns:
            Request identifier string (canonical uuid4) stored on the scope.
        """

        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state

        existing = state.get("request_id")
        if isinstance(existing, str) and existing:
            return existing

        rid = str(uuid.uuid4())
        state["request_id"] = rid
        return rid

    async def _send_rejection(
        self,
        *,
        scope: Scope,
        receive: Receive,
        send: Send,
        request_id: str,
        error: ErrorDef,
        message: str,
        reason: str,
    ) -> None:
        """Emit metrics/logs and send a rejection response.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
            request_id: Request identifier for correlation.
            error: Error definition describing HTTP status and code.
            message: Human-readable explanation.
            reason: Machine-readable reason for observability/metrics.
        """

        method = (scope.get("method") or "").upper()
        path = scope.get("path") or ""
        normalized_path = normalize_metric_path(path)

        REQUEST_INTEGRITY_REJECTIONS_TOTAL.labels(
            path=normalized_path,
            method=method,
            reason=reason,
        ).inc()

        client = scope.get("client")
        client_host = "unknown"
        if isinstance(client, (list, tuple)) and client:
            client_host = str(client[0])

        logger.info(
            "Request rejected by request-integrity middleware",
            extra={
                "request_id": request_id,
                "path": path,
                "method": method,
                "client_host": client_host,
                "reason": reason,
            },
        )

        response = error_json_response(
            error,
            message=message,
            headers={"X-Request-Id": request_id},
        )
        await response(scope, receive, send)


__all__ = [
    "REQUEST_INTEGRITY_REJECTIONS_TOTAL",
    "RequestIntegrityMiddleware",
]
