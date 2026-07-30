"""Bearer-token authentication middleware for protected STAR endpoints."""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.types import ASGIApp

from star.core.config import Settings
from star.core.errors import UNAUTHORIZED
from star.core.responses import error_json_response


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate protected HTTP requests using a shared bearer token."""

    def __init__(self, app: ASGIApp, api_token: str) -> None:
        """Create a new AuthMiddleware instance.

        Args:
            app: The ASGI application to wrap.
            api_token: The expected bearer token value used for simple
                authentication. Leading/trailing whitespace is stripped.
        """
        super().__init__(app)
        self.api_token = api_token.strip()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Handle an incoming request, enforcing Bearer token auth.

        The middleware exempts `/health` unconditionally. It exempts
        `/metrics` and documentation endpoints only when runtime settings
        explicitly make those surfaces public. If authentication fails, a 401
        response is returned. When available, the
        request id stored on `request.state.request_id` is included in error
        responses for correlation.

        Args:
            request: Incoming Starlette Request.
            call_next: Callable to invoke the next app/middleware and obtain
                a Response.

        Returns:
            A Starlette Response from downstream or a 401 JSONResponse when
            authentication fails.
        """
        settings = getattr(request.app.state, "settings", None)
        exempt_prefixes = self._exempt_prefixes(settings)

        # Use prefix matching to allow for subpaths (e.g. /health/ready)
        if any(
            request.url.path == p or request.url.path.startswith(p + "/")
            for p in exempt_prefixes
        ):
            return await call_next(request)
        # request_id middleware runs before this, so state.request_id should be present
        rid = getattr(request.state, "request_id", None)

        auth = request.headers.get("authorization")

        if not auth:
            return self._unauthorized("Missing Authorization header", rid)

        scheme, _, token = auth.partition(" ")

        if scheme.lower() != "bearer" or not token:
            return self._unauthorized("Invalid authorization scheme", rid)

        token = token.strip()

        if not hmac.compare_digest(token, self.api_token):
            return self._unauthorized("Invalid token", rid)

        return await call_next(request)

    def _unauthorized(self, detail: str, request_id: str | None = None) -> JSONResponse:
        """Return a 401 Unauthorized JSONResponse.

        Args:
            detail: Error message to include in the response body.
            request_id: Optional request identifier to add to the
                `X-Request-Id` response header for correlation.

        Returns:
            A configured `JSONResponse` with status 401 and appropriate
            headers.
        """
        headers = {"WWW-Authenticate": "Bearer"}
        if request_id:
            headers["X-Request-Id"] = request_id
        return error_json_response(
            UNAUTHORIZED,
            message=detail,
            headers=headers,
            status_code=HTTP_401_UNAUTHORIZED,
        )

    @staticmethod
    def _exempt_prefixes(settings: object) -> tuple[str, ...]:
        """Return auth-exempt prefixes for the active runtime settings.

        Args:
            settings: Runtime settings object stored on application state.

        Returns:
            Tuple of path prefixes that bypass Bearer authentication.
        """

        # Fail closed for docs and metrics if settings are unavailable or have
        # an unexpected type; only health remains unconditionally public.
        if not isinstance(settings, Settings):
            return ("/health",)

        prefixes = ["/health"]
        if not settings.star_metrics_require_auth:
            prefixes.append("/metrics")
        if settings.star_enable_docs and not settings.star_docs_require_auth:
            prefixes.extend(["/openapi.json", "/docs", "/redoc"])

        return tuple(prefixes)
