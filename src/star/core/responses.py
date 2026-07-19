"""HTTP JSON response helpers for STAR error envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse

from star.core.errors import ErrorDef, StarError
from star.core.schemas.envelope import ResponseEnvelope


def error_json_response(
    error: ErrorDef,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    status_code: int | None = None,
) -> JSONResponse:
    """Build a STAR error envelope as an HTTP JSON response.

    Args:
        error: Stable STAR error definition that provides the default code,
            message, and HTTP status.
        message: Optional client-facing message override.
        details: Optional structured details that have already been reviewed
            as safe for public responses.
        headers: Optional HTTP headers to attach to the response. The mapping
            is copied so callers retain ownership.
        status_code: Optional HTTP status override for framework exceptions
            whose status must be preserved while using a mapped STAR error.

    Returns:
        A JSONResponse containing the standard STAR error envelope.
    """

    payload = ResponseEnvelope.from_error(
        code=error.code,
        message=message or error.default_message,
        details=details,
    )
    return JSONResponse(
        status_code=status_code if status_code is not None else error.http_status,
        content=payload.model_dump(),
        headers=dict(headers or {}),
    )


def star_error_json_response(
    exc: StarError,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an HTTP JSON response from a transport-ready StarError.

    Args:
        exc: Transport-ready STAR application error raised by a handler.
        headers: Optional HTTP headers to attach to the response. The mapping
            is copied so callers retain ownership.

    Returns:
        A JSONResponse preserving the StarError status, message, and details.
    """

    return error_json_response(
        exc.error,
        message=exc.message,
        details=exc.details,
        headers=headers,
        status_code=exc.http_status,
    )
