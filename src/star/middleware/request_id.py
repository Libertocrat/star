"""Request identifier middleware that propagates `X-Request-Id`."""

import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that ensures every request has a stable request identifier.

    Behavior:
    - Accepts an optional client-supplied `X-Request-Id` header. If present and a
      valid UUID, the value is preserved. If absent or invalid, a new UUID4 is
      generated.
    - Stores the resulting request id on `request.state.request_id` for
      downstream consumers (other middleware and route handlers).
    - Adds the header `X-Request-Id` to the response.

    This middleware is intentionally small and synchronous. It expects to be
    registered at last so it runs before any middleware that depends on
    `request.state.request_id` (see module-level comments about order).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Dispatch the middleware for a single request.

        Args:
            request: The incoming Starlette/FastAPI request.
            call_next: Callable that runs the next app/middleware and returns
                a Response.

        Returns:
            The response from downstream, with `X-Request-Id` header added.
        """

        # Header names are case-insensitive; Starlette normalizes them to lower-case.
        raw = request.headers.get("x-request-id")

        try:
            if raw and raw.strip():
                rid = str(uuid.UUID(raw.strip()))
            else:
                rid = str(uuid.uuid4())
        except (ValueError, TypeError):
            # If a client supplied an invalid UUID, fall back to a generated one.
            rid = str(uuid.uuid4())

        request.state.request_id = rid

        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response
