"""Centralized HTTP error definitions shared across STAR."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StarError(Exception):
    """
    Canonical structured STAR application exception.

    This exception represents a normalized, transport-ready error raised
    by STAR handlers and consumed by route layers to produce HTTP responses.

    Design principles:
    - Built from a centralized ErrorDef (single source of truth)
    - Allows optional overrides (message, details)
    - Normalizes all fields to stable, non-optional types
    - Safe to serialize into API responses via ResponseEnvelope

    Attributes:
        error: Error definition used as canonical source for code and status.
        message: Final normalized message exposed to the client.
        details: Optional structured metadata safe for client exposure.

    Notes:
        - Helpers must NOT raise StarError (only standard exceptions).
        - Routes are responsible for mapping this exception to HTTP responses.
        - `details` must never contain sensitive or internal-only information.
            - `code` and `http_status` are derived runtime attributes copied from
                `error` during initialization.
    """

    error: ErrorDef
    message: str = field(init=False)
    details: dict[str, Any] = field(init=False)

    def __init__(
        self,
        error: ErrorDef,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Build a normalized runtime StarError instance.

        Args:
            error: Canonical error definition.
            message: Optional message override.
            details: Optional structured error metadata.
        """
        self.error = error
        self.code = error.code
        self.http_status = error.http_status

        self.message = message or error.default_message
        self.details = dict(details or {})

        Exception.__init__(self, self.message)


@dataclass(frozen=True, slots=True)
class ErrorDef:
    """Definition of a stable STAR error contract.

    Attributes:
        code: Stable machine-readable error code.
        http_status: Canonical HTTP status for this error.
        default_message: Default client-facing message.
        category: Error family label used for grouping.
    """

    code: str
    http_status: int
    default_message: str
    category: str = "domain"


BAD_REQUEST = ErrorDef(
    code="BAD_REQUEST",
    http_status=400,
    default_message="Bad request.",
)

INVALID_PARAMS = ErrorDef(
    code="INVALID_PARAMS",
    http_status=400,
    default_message="Invalid params for action.",
)

INVALID_REQUEST = ErrorDef(
    code="INVALID_REQUEST",
    http_status=400,
    default_message="Invalid request.",
)

INVALID_ALGORITHM = ErrorDef(
    code="INVALID_ALGORITHM",
    http_status=400,
    default_message="Unsupported checksum algorithm.",
)

FILE_EXTENSION_MISSING = ErrorDef(
    code="FILE_EXTENSION_MISSING",
    http_status=400,
    default_message="Cannot infer MIME type because file has no extension.",
)

MIME_MAPPING_NOT_DEFINED = ErrorDef(
    code="MIME_MAPPING_NOT_DEFINED",
    http_status=400,
    default_message="No MIME mapping defined for file extension.",
)

UNAUTHORIZED = ErrorDef(
    code="UNAUTHORIZED",
    http_status=401,
    default_message="Authentication required or invalid token.",
)

PATH_NOT_ALLOWED = ErrorDef(
    code="PATH_NOT_ALLOWED",
    http_status=403,
    default_message="Path not allowed.",
)

PERMISSION_DENIED = ErrorDef(
    code="PERMISSION_DENIED",
    http_status=403,
    default_message="Permission denied.",
)

RESOURCE_NOT_FOUND = ErrorDef(
    code="RESOURCE_NOT_FOUND",
    http_status=404,
    default_message="Resource not found.",
)

ACTION_NOT_FOUND = ErrorDef(
    code="ACTION_NOT_FOUND",
    http_status=404,
    default_message="Unsupported action.",
)

FILE_NOT_FOUND = ErrorDef(
    code="FILE_NOT_FOUND",
    http_status=404,
    default_message="File not found.",
)

METHOD_NOT_ALLOWED = ErrorDef(
    code="METHOD_NOT_ALLOWED",
    http_status=405,
    default_message="HTTP method not allowed for this path.",
)

CONFLICT = ErrorDef(
    code="CONFLICT",
    http_status=409,
    default_message="Resource conflict.",
)

FILE_TOO_LARGE = ErrorDef(
    code="FILE_TOO_LARGE",
    http_status=413,
    default_message="File exceeds maximum allowed size.",
)

UNSUPPORTED_MEDIA_TYPE = ErrorDef(
    code="UNSUPPORTED_MEDIA_TYPE",
    http_status=415,
    default_message="Unsupported media type.",
)

UNPROCESSABLE_ENTITY = ErrorDef(
    code="UNPROCESSABLE_ENTITY",
    http_status=422,
    default_message="Unprocessable entity.",
)

RATE_LIMITED = ErrorDef(
    code="RATE_LIMITED",
    http_status=429,
    default_message="Rate limit exceeded.",
)

INVALID_RESULT = ErrorDef(
    code="INVALID_RESULT",
    http_status=500,
    default_message="Handler returned invalid result.",
)

INTERNAL_ERROR = ErrorDef(
    code="INTERNAL_ERROR",
    http_status=500,
    default_message="Unhandled error while executing action.",
)

TIMEOUT = ErrorDef(
    code="TIMEOUT",
    http_status=504,
    default_message="Operation timed out.",
)

PUBLIC_HTTP_ERRORS = [
    BAD_REQUEST,
    INVALID_PARAMS,
    INVALID_REQUEST,
    INVALID_ALGORITHM,
    FILE_EXTENSION_MISSING,
    MIME_MAPPING_NOT_DEFINED,
    UNAUTHORIZED,
    PATH_NOT_ALLOWED,
    PERMISSION_DENIED,
    RESOURCE_NOT_FOUND,
    ACTION_NOT_FOUND,
    FILE_NOT_FOUND,
    METHOD_NOT_ALLOWED,
    CONFLICT,
    FILE_TOO_LARGE,
    UNSUPPORTED_MEDIA_TYPE,
    UNPROCESSABLE_ENTITY,
    RATE_LIMITED,
    INVALID_RESULT,
    INTERNAL_ERROR,
    TIMEOUT,
]

# Errors flagged as public are included in the OpenAPI schema and used by
# FastAPI/Starlette handlers when mapping raw HTTP codes to STAR's stable
# machine-readable responses.

__all__ = [
    "ErrorDef",
    "StarError",
    "PUBLIC_HTTP_ERRORS",
    "BAD_REQUEST",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "INVALID_ALGORITHM",
    "FILE_EXTENSION_MISSING",
    "MIME_MAPPING_NOT_DEFINED",
    "UNAUTHORIZED",
    "PATH_NOT_ALLOWED",
    "PERMISSION_DENIED",
    "RESOURCE_NOT_FOUND",
    "ACTION_NOT_FOUND",
    "FILE_NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "CONFLICT",
    "FILE_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "UNPROCESSABLE_ENTITY",
    "RATE_LIMITED",
    "INVALID_RESULT",
    "INTERNAL_ERROR",
    "TIMEOUT",
]
