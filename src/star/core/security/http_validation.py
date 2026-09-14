"""
HTTP validation helpers for structural request hygiene.

These helpers are pure (no Starlette/FastAPI dependency) and are intended
to be used by middleware and other HTTP-facing modules.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Literal, TypeAlias

QueryParameterShapeReason: TypeAlias = Literal["unknown", "repeated", "empty"]
"""Finite reasons for a structurally invalid decoded HTTP query."""


class QueryParameterShapeError(ValueError):
    """Raised when decoded HTTP query pairs have an unsafe shape.

    Attributes:
        reason: Low-cardinality structural reason that never includes raw query
            names or values.
    """

    def __init__(self, reason: QueryParameterShapeReason):
        """Initialize the query-shape failure.

        Args:
            reason: Canonical structural query violation.
        """

        self.reason = reason
        super().__init__(reason)


def is_supported_json_content_type(content_type: str | None) -> bool:
    """Return whether `content_type` carries application/json.

    Args:
        content_type: Raw `Content-Type` header value.

    Returns:
        True when the normalized base media type is `application/json`.
    """
    if not content_type:
        return False

    base = content_type.split(";", 1)[0].strip().lower()
    return base == "application/json"


def parse_content_length_strict(value: str) -> int:
    """Parse `Content-Length` strictly as a non-negative integer.

    Args:
        value: Raw `Content-Length` header value.

    Returns:
        Parsed integer when `value` is digits only and non-negative.

    Raises:
        ValueError: When `value` is empty, contains non-digit characters, or
            represents a negative number.
    """
    if not value or not value.isdigit():
        raise ValueError("Content-Length must be digits only")

    parsed = int(value)
    if parsed < 0:
        raise ValueError("Content-Length cannot be negative")
    return parsed


def path_has_disallowed_characters(path: str) -> bool:
    """Return whether `path` contains disallowed characters.

    Args:
        path: Request path to validate.

    Returns:
        True when `path` contains a NUL byte, backslash, or control
        character (< 0x20) other than TAB.
    """
    if "\x00" in path or "\\" in path:
        return True

    for ch in path:
        code = ord(ch)
        if code < 0x20 and code != 0x09:
            return True
    return False


def normalize_content_type(value: str | None) -> str | None:
    """Normalize a `Content-Type` value to its base media type.

    Args:
        value: Raw header value.

    Returns:
        Lowercase media type without parameters, or None when no value is
        provided.
    """

    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def validate_query_parameter_shape(
    pairs: Iterable[tuple[str, str]],
    *,
    allowed_names: Collection[str],
) -> None:
    """Reject unknown, repeated, empty, or whitespace-only query parameters.

    Args:
        pairs: Decoded HTTP query pairs in their original multiplicity.
        allowed_names: Parameter names accepted by the owning HTTP contract.

    Raises:
        QueryParameterShapeError: If a pair is unknown, repeated, empty, or
            whitespace-only.
    """

    seen: set[str] = set()
    for name, value in pairs:
        if name not in allowed_names:
            raise QueryParameterShapeError("unknown")
        if name in seen:
            raise QueryParameterShapeError("repeated")
        if not value or not value.strip():
            raise QueryParameterShapeError("empty")
        seen.add(name)


__all__ = [
    "is_supported_json_content_type",
    "parse_content_length_strict",
    "path_has_disallowed_characters",
    "normalize_content_type",
    "QueryParameterShapeError",
    "validate_query_parameter_shape",
]
