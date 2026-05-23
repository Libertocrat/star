"""
RequestIntegrityMiddleware

Structural hygiene enforcement layer (NOT a WAF).

Responsibilities:
- Reject malformed / structurally invalid requests as early as possible.
- Always return ResponseEnvelope.failure (never raw HTTPException).
- Always include X-Request-Id (preserve if present, otherwise generate).
- Enforce:
  - Path sanity (NUL, backslash, control chars <0x20 except TAB)
  - Header integrity via raw headers (duplicate Authorization, whitespace in name,
    control chars in name/value)
   - Content-Type for POST /v1/actions/{action_id} (application/json base type required)
  - Body size enforcement using star_max_file_bytes:
    - Strict Content-Length parsing when present
    - Streaming enforcement when Content-Length is absent
"""

from __future__ import annotations

import logging
import uuid
from typing import Final

from prometheus_client import Counter
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from star.core.errors import FILE_TOO_LARGE, INVALID_REQUEST, ErrorDef
from star.core.schemas.envelope import ResponseEnvelope
from star.core.security.headers import find_header_integrity_violation
from star.core.security.http_validation import (
    normalize_content_type,
    parse_content_length_strict,
    path_has_disallowed_characters,
)
from star.core.utils.http import normalize_metric_path
from star.middleware.schemas import ContentTypePolicy

logger = logging.getLogger("star.middleware.request_integrity")

REQUEST_INTEGRITY_REJECTIONS_TOTAL = Counter(
    "star_request_integrity_rejections_total",
    "Total requests rejected by request-integrity middleware.",
    labelnames=("path", "method", "reason"),
)

_BODY_METHODS: Final[set[str]] = {"POST", "PUT", "PATCH", "DELETE"}


class _BodyTooLargeError(Exception):
    """Internal sentinel used to short-circuit downstream processing."""


class RequestIntegrityMiddleware:
    """ASGI middleware enforcing structural request integrity constraints.

    Args:
        app: The ASGI application to wrap.
        max_body_bytes: Optional explicit body size limit (falls back to
            `star_max_file_bytes`).
        content_type_policies: Optional collection of policies that restrict
            the allowed content types per method/path.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int | None = None,
        content_type_policies: list[ContentTypePolicy] | None = None,
    ) -> None:
        """Configure the middleware body limit and content-type policies."""

        self.app = app
        self._max_body_bytes = self._resolve_max_body_bytes(app, max_body_bytes)
        self._content_type_policies = self._index_policies(content_type_policies or [])

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

        # Ensure request id is always available for any rejection
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
        # 3) CL + TE smuggling mitigation
        # ------------------------------------------------------------------
        has_content_length = (
            self._get_header_value(raw_headers, b"content-length") is not None
        )
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

        # ------------------------------------------------------------------
        # 4) Content-Type enforcement (policy-driven)
        # ------------------------------------------------------------------
        policy = self._resolve_content_type_policy(method, path)
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
        # 5) Body size enforcement
        # ------------------------------------------------------------------
        content_length = self._get_header_value(raw_headers, b"content-length")
        if content_length is not None:
            try:
                declared_size = parse_content_length_strict(content_length)
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

            if declared_size > self._max_body_bytes:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=FILE_TOO_LARGE,
                    message=FILE_TOO_LARGE.default_message,
                    reason="content_length_exceeds_limit",
                )
                return

            # Declared size is within limit: proceed normally
            await self.app(scope, receive, send)
            return

        # No Content-Length: enforce streaming size limit for body-capable methods.
        if method in _BODY_METHODS:
            limited_receive = self._wrap_receive_with_body_limit(
                receive=receive,
                max_bytes=self._max_body_bytes,
            )
            try:
                await self.app(scope, limited_receive, send)
            except _BodyTooLargeError:
                await self._send_rejection(
                    scope=scope,
                    receive=receive,
                    send=send,
                    request_id=request_id,
                    error=FILE_TOO_LARGE,
                    message=FILE_TOO_LARGE.default_message,
                    reason="body_exceeds_limit",
                )
            return

        # Methods without body (or we don't care): proceed
        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _index_policies(
        policies: list[ContentTypePolicy],
    ) -> list[tuple[str, str, frozenset[str]]]:
        """Normalize content type policies for lookup.

        Args:
            policies: List of `ContentTypePolicy` instances.

        Returns:
            List of `(method, path, allowlist)` tuples.
        """

        indexed: list[tuple[str, str, frozenset[str]]] = []

        for p in policies:
            indexed.append(
                (
                    p.method.upper(),
                    p.path,
                    frozenset(ct.lower() for ct in p.allowed),
                )
            )

        return indexed

    @staticmethod
    def _path_matches_policy_path(policy_path: str, request_path: str) -> bool:
        """Return True when a request path matches a configured policy path.

        A policy path can be either an exact literal path or a template path
        containing placeholders such as `/v1/actions/{action_id}`.
        """

        if policy_path == request_path:
            return True

        if "{" not in policy_path or "}" not in policy_path:
            return False

        policy_segments = policy_path.strip("/").split("/")
        request_segments = request_path.strip("/").split("/")
        if len(policy_segments) != len(request_segments):
            return False

        for policy_segment, request_segment in zip(
            policy_segments,
            request_segments,
            strict=True,
        ):
            if (
                policy_segment.startswith("{")
                and policy_segment.endswith("}")
                and len(policy_segment) > 2
            ):
                if not request_segment:
                    return False
                continue
            if policy_segment != request_segment:
                return False

        return True

    def _resolve_content_type_policy(
        self,
        method: str,
        path: str,
    ) -> frozenset[str] | None:
        """Resolve the allowlist matching a method/path pair.

        Args:
            method: Incoming HTTP method.
            path: Incoming request path.

        Returns:
            Matched content-type allowlist or None when no policy applies.
        """

        method_upper = method.upper()
        for policy_method, policy_path, allowed in self._content_type_policies:
            if policy_method != method_upper:
                continue
            if self._path_matches_policy_path(policy_path, path):
                return allowed

        return None

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
                # Use latin-1 for safe round-trip of arbitrary bytes
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

    @classmethod
    def _wrap_receive_with_body_limit(cls, receive: Receive, max_bytes: int) -> Receive:
        """Wrap `receive` to enforce a total body byte limit.

        Args:
            receive: Original ASGI receive callable.
            max_bytes: Maximum number of allowed body bytes.

        Returns:
            Wrapped receive callable that raises `_BodyTooLargeError`
            when the limit is exceeded.
        """

        total = 0

        async def limited_receive() -> Message:
            """Track streamed request bytes and fail once the limit is exceeded."""

            nonlocal total
            message = await receive()

            if message.get("type") != "http.request":
                return message

            body = message.get("body", b"")
            if body:
                total += len(body)
                if total > max_bytes:
                    raise _BodyTooLargeError()

            return message

        return limited_receive

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

        payload = ResponseEnvelope.failure(
            code=error.code, message=message
        ).model_dump()
        response = JSONResponse(
            status_code=error.http_status,
            content=payload,
            headers={"X-Request-Id": request_id},
        )
        await response(scope, receive, send)

    @staticmethod
    def _resolve_max_body_bytes(app: ASGIApp, override: int | None) -> int:
        """Determine the allowed body size limit.

        Args:
            app: ASGI application whose settings may contain `star_max_file_bytes`.
            override: Optional explicit override.

        Returns:
            Resolved byte limit (minimum safe default when unset).
        """

        if isinstance(override, int) and override > 0:
            return override

        settings = getattr(getattr(app, "state", None), "settings", None)
        configured = getattr(settings, "star_max_file_bytes", None)
        if isinstance(configured, int) and configured > 0:
            return configured

        # Safe fallback (should not normally be hit)
        return 1024 * 1024


__all__ = [
    "REQUEST_INTEGRITY_REJECTIONS_TOTAL",
    "RequestIntegrityMiddleware",
]
