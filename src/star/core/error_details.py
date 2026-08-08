"""Safe public error-detail sanitization helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

SAFE_ERROR_DETAIL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "action_id",
        "file_id",
        "param",
        "allowed",
        "requires",
        "reason",
        "errors",
        "extension",
        "detected_mime",
        "algorithm",
        "expected",
        "actual",
    }
)
SAFE_VALIDATION_ERROR_KEYS: Final[frozenset[str]] = frozenset({"type", "loc", "msg"})
MAX_ERROR_DETAIL_STRING_LENGTH: Final[int] = 256
MAX_ERROR_DETAIL_LIST_ITEMS: Final[int] = 10
MAX_ERROR_DETAIL_DEPTH: Final[int] = 3
MAX_ERROR_DETAIL_SERIALIZED_BYTES: Final[int] = 4096
INTERNAL_ERROR_CODE: Final[str] = "INTERNAL_ERROR"


class _DropValue:
    """Sentinel for values that must not be exposed in public error details."""


_DROP: Final[_DropValue] = _DropValue()


def sanitize_error_details(
    details: Mapping[str, Any] | None,
    *,
    error_code: str,
) -> dict[str, Any] | None:
    """Return a public-safe copy of structured error details.

    Args:
        details: Candidate public details mapping.
        error_code: Stable STAR error code for context-aware redaction.

    Returns:
        Sanitized details preserving only reviewed keys and bounded values. A
        `None` input remains `None`; an empty or fully omitted mapping returns
        an empty dictionary.
    """

    if details is None:
        return None

    sanitized = _sanitize_mapping(
        details,
        error_code=error_code,
        depth=0,
    )
    if isinstance(sanitized, _DropValue):
        return {}

    bounded: dict[str, Any] = {}
    for key, value in sanitized.items():
        candidate = {**bounded, key: value}
        if _serialized_size(candidate) <= MAX_ERROR_DETAIL_SERIALIZED_BYTES:
            bounded[key] = value

    return bounded


def _sanitize_mapping(
    value: Mapping[str, Any],
    *,
    error_code: str,
    depth: int,
) -> dict[str, Any] | _DropValue:
    """Sanitize an error-detail mapping recursively."""

    if depth > MAX_ERROR_DETAIL_DEPTH:
        return _DROP

    sanitized: dict[str, Any] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or key not in SAFE_ERROR_DETAIL_KEYS:
            continue
        if key == "reason" and error_code == INTERNAL_ERROR_CODE:
            continue

        if key == "errors":
            safe_value = _sanitize_validation_errors(raw_value, error_code=error_code)
        else:
            safe_value = _sanitize_value(
                raw_value,
                error_code=error_code,
                depth=depth + 1,
            )
        if safe_value is not _DROP:
            sanitized[key] = safe_value

    return sanitized


def _sanitize_validation_errors(
    value: Any,
    *,
    error_code: str,
) -> list[dict[str, Any]] | _DropValue:
    """Sanitize Pydantic-style validation error items."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return _DROP

    errors: list[dict[str, Any]] = []
    for raw_item in value[:MAX_ERROR_DETAIL_LIST_ITEMS]:
        if not isinstance(raw_item, Mapping):
            continue

        item: dict[str, Any] = {}
        for key, raw_field in raw_item.items():
            if key not in SAFE_VALIDATION_ERROR_KEYS:
                continue

            if key == "loc":
                safe_field = _sanitize_location(raw_field)
            else:
                safe_field = _sanitize_value(
                    raw_field,
                    error_code=error_code,
                    depth=1,
                )
            if safe_field is not _DROP:
                item[key] = safe_field

        if item:
            errors.append(item)

    return errors


def _sanitize_location(value: Any) -> list[str | int] | _DropValue:
    """Normalize a validation error location to safe path components."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return _DROP

    location: list[str | int] = []
    for part in value[:MAX_ERROR_DETAIL_LIST_ITEMS]:
        if isinstance(part, str):
            location.append(_truncate_string(part))
        elif isinstance(part, int) and not isinstance(part, bool):
            location.append(part)

    return location


def _sanitize_value(
    value: Any,
    *,
    error_code: str,
    depth: int,
) -> Any | _DropValue:
    """Sanitize a JSON-compatible error-detail value."""

    if depth > MAX_ERROR_DETAIL_DEPTH:
        return _DROP

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, error_code=error_code, depth=depth)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _sanitize_sequence(value, error_code=error_code, depth=depth)

    return _DROP


def _sanitize_sequence(
    value: Sequence[Any],
    *,
    error_code: str,
    depth: int,
) -> list[Any]:
    """Sanitize a bounded public error-detail list."""

    sanitized: list[Any] = []
    for item in value[:MAX_ERROR_DETAIL_LIST_ITEMS]:
        safe_item = _sanitize_value(
            item,
            error_code=error_code,
            depth=depth + 1,
        )
        if safe_item is not _DROP:
            sanitized.append(safe_item)
    return sanitized


def _truncate_string(value: str) -> str:
    """Bound a public error-detail string."""

    if len(value) <= MAX_ERROR_DETAIL_STRING_LENGTH:
        return value
    return f"{value[: MAX_ERROR_DETAIL_STRING_LENGTH - 3]}..."


def _serialized_size(value: Mapping[str, Any]) -> int:
    """Return compact UTF-8 JSON size for a sanitized details mapping."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(serialized.encode("utf-8"))


__all__ = [
    "INTERNAL_ERROR_CODE",
    "MAX_ERROR_DETAIL_DEPTH",
    "MAX_ERROR_DETAIL_LIST_ITEMS",
    "MAX_ERROR_DETAIL_SERIALIZED_BYTES",
    "MAX_ERROR_DETAIL_STRING_LENGTH",
    "SAFE_ERROR_DETAIL_KEYS",
    "SAFE_VALIDATION_ERROR_KEYS",
    "sanitize_error_details",
]
