"""Exception handlers and utilities for the STAR application.

This module exposes two handlers used by the FastAPI app:
- `http_exception_handler`: formats Starlette HTTP exceptions as JSON and
    preserves the request id when available.
- `generic_exception_handler`: logs unhandled exceptions and returns a
    generic 500 response while including request id for correlation.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, cast

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from star.core.errors import (
    BAD_REQUEST,
    FILE_TOO_LARGE,
    INTERNAL_ERROR,
    METHOD_NOT_ALLOWED,
    PATH_NOT_ALLOWED,
    RATE_LIMITED,
    RESOURCE_NOT_FOUND,
    UNAUTHORIZED,
    UNPROCESSABLE_ENTITY,
    UNSUPPORTED_MEDIA_TYPE,
    ErrorDef,
)
from star.core.responses import error_json_response

logger = logging.getLogger("star.exceptions")


# Starlette http exceptions mapping from centralized error definitions.
STARLETTE_HTTP_STATUS_MAP: dict[int, ErrorDef] = {
    400: BAD_REQUEST,
    401: UNAUTHORIZED,
    403: PATH_NOT_ALLOWED,
    404: RESOURCE_NOT_FOUND,
    405: METHOD_NOT_ALLOWED,
    413: FILE_TOO_LARGE,
    415: UNSUPPORTED_MEDIA_TYPE,
    422: UNPROCESSABLE_ENTITY,
    429: RATE_LIMITED,
}

for status, err in STARLETTE_HTTP_STATUS_MAP.items():
    assert err.http_status == status, (
        f"ErrorDef {err.code} has http_status={err.http_status}, " f"expected {status}"
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle Starlette HTTP exceptions and include request id header.

    Args:
        request: The incoming FastAPI request.
        exc: The Starlette HTTPException being handled.

    Returns:
        A JSONResponse with the original status code and a minimal payload
        containing `detail`. If a `request_id` is available on the
        request state it is added to the `X-Request-Id` response header.
    """

    rid = getattr(request.state, "request_id", None)
    headers = {"X-Request-Id": rid} if rid else {}

    err_def = STARLETTE_HTTP_STATUS_MAP.get(exc.status_code, INTERNAL_ERROR)
    logger.debug(
        "HTTP exception detail (request_id=%s, status=%s): %s",
        rid,
        exc.status_code,
        exc.detail,
    )
    return error_json_response(
        err_def,
        headers=headers,
        status_code=exc.status_code,
    )


# `add_exception_handler` has a broader expected type (it accepts handlers for
# `Exception`), so expose a typed alias that satisfies that API while keeping
# the runtime handler signature narrow for clarity and static reasoning.
http_exception_handler: Callable[
    [Request, Exception], Response | Awaitable[Response]
] = cast(
    Callable[[Request, Exception], Response | Awaitable[Response]],
    _http_exception_handler,
)


async def generic_exception_handler(request: Request, exc: Exception):
    """Generic exception handler for unhandled exceptions.

    This handler logs the exception (including the `request_id` when
    available) and returns a 500 Internal Server Error response with a
    minimal payload. The handler guarantees that `X-Request-Id` is present
    in the response when a request id was assigned earlier in the request
    lifecycle.

    Args:
        request: The incoming FastAPI request.
        exc: The exception that was raised.

    Returns:
        A JSONResponse with HTTP 500 and a minimal error payload.
    """

    rid = getattr(request.state, "request_id", None)
    headers = {"X-Request-Id": rid} if rid else {}
    logger.exception("Unhandled exception (request_id=%s): %s", rid, exc)
    return error_json_response(
        INTERNAL_ERROR,
        message="Internal Server Error",
        headers=headers,
    )
