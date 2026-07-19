"""Shared request/response envelope schemas used by STAR."""

from __future__ import annotations

from typing import Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel, Field
from pydantic.generics import GenericModel

T = TypeVar("T")


class ErrorInfo(BaseModel):
    """Standard error payload returned on failed requests.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable error message.
        details: Optional structured details payload.
    """

    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")
    details: Optional[dict[str, Any]] = Field(
        default=None, description="Optional details."
    )


class ResponseEnvelope(GenericModel, Generic[T]):
    """Standard HTTP response envelope used across services.

    Fields follow the README contract: `success`, `data`, and `error`.
    Request correlation is performed via the `X-Request-Id` header injected
    by middleware; the JSON body deliberately omits `request_id`.

    Attributes:
        success: Whether the request completed successfully.
        data: Success payload when available.
        error: Structured error payload when request fails.
    """

    success: bool = Field(..., description="Success flag.")
    data: Optional[T] = Field(default=None, description="Result payload on success.")
    error: Optional[ErrorInfo] = Field(
        default=None, description="Error payload on failure."
    )

    @classmethod
    def from_success(cls, data: T) -> "ResponseEnvelope[T]":
        """Build a success envelope.

        Args:
            data: Result payload to return to the caller.

        Returns:
            A success response envelope containing the provided payload.
        """

        return cls(success=True, data=data, error=None)

    @classmethod
    def from_error(
        cls,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> "ResponseEnvelope[Any]":
        """Build an error envelope.

        Args:
            code: Stable machine-readable error code.
            message: Human-readable error message.
            details: Optional structured error details.

        Returns:
            An error response envelope with populated error information.
        """

        return cls(
            success=False,
            data=None,
            error=ErrorInfo(code=code, message=message, details=details),
        )
