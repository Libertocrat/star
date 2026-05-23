"""Bearer-token authentication middleware for protected STAR endpoints."""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.types import ASGIApp

from star.core.schemas.envelope import ResponseEnvelope


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

        The middleware exempts the `/health` endpoint. If authentication
        fails, a 401 response is returned. When available, the request id
        stored on `request.state.request_id` is included in error responses
        for correlation.

        Args:
            request: Incoming Starlette Request.
            call_next: Callable to invoke the next app/middleware and obtain
                a Response.

        Returns:
            A Starlette Response from downstream or a 401 JSONResponse when
            authentication fails.
        """
        # Exempt health and metrics endpoints from auth. Use a list so
        # additional prefixes can be appended without causing mypy/type
        # incompatibilities when the collection grows.
        exempt_prefixes: list[str] = ["/health", "/metrics"]
        # Exempt docs endpoints when enabled in settings
        if (
            getattr(request.app.state, "settings", None)
            and request.app.state.settings.star_enable_docs
        ):
            exempt_prefixes.extend(["/openapi.json", "/docs", "/redoc"])

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
        payload = ResponseEnvelope.failure(
            code="UNAUTHORIZED", message=detail
        ).model_dump()
        return JSONResponse(
            status_code=HTTP_401_UNAUTHORIZED, content=payload, headers=headers
        )
