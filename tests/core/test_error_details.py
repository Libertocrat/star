"""Tests for public STAR error-detail sanitization."""

from __future__ import annotations

import json

from star.core.error_details import (
    MAX_ERROR_DETAIL_LIST_ITEMS,
    MAX_ERROR_DETAIL_SERIALIZED_BYTES,
    MAX_ERROR_DETAIL_STRING_LENGTH,
    sanitize_error_details,
)


def _serialized_size(value: dict) -> int:
    """Return compact UTF-8 JSON size for test assertions."""

    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def test_sanitize_error_details_keeps_allowlisted_keys_only():
    """
    GIVEN public error details with reviewed and unreviewed keys
    WHEN details are sanitized for a client-visible error
    THEN only allowlisted keys remain in the public details payload
    """
    details = {
        "action_id": "test_runtime.ping",
        "param": "count",
        "secret": "do-not-expose",
        "path": "/internal/path",
    }

    sanitized = sanitize_error_details(details, error_code="INVALID_PARAMS")

    assert sanitized == {
        "action_id": "test_runtime.ping",
        "param": "count",
    }


def test_sanitize_error_details_bounds_strings_lists_and_total_size():
    """
    GIVEN oversized string and list values in public error details
    WHEN details are sanitized for response serialization
    THEN values and the final serialized payload are bounded
    """
    oversized = "x" * (MAX_ERROR_DETAIL_STRING_LENGTH + 20)
    large_list = ["y" * MAX_ERROR_DETAIL_STRING_LENGTH] * MAX_ERROR_DETAIL_LIST_ITEMS
    details = {
        "reason": oversized,
        "allowed": [str(index) for index in range(MAX_ERROR_DETAIL_LIST_ITEMS + 3)],
        "requires": large_list,
        "expected": large_list,
    }

    sanitized = sanitize_error_details(details, error_code="INVALID_PARAMS")

    assert sanitized is not None
    assert sanitized["reason"].endswith("...")
    assert len(sanitized["reason"]) == MAX_ERROR_DETAIL_STRING_LENGTH
    assert sanitized["allowed"] == [
        str(index) for index in range(MAX_ERROR_DETAIL_LIST_ITEMS)
    ]
    assert "requires" in sanitized
    assert "expected" not in sanitized
    assert _serialized_size(sanitized) <= MAX_ERROR_DETAIL_SERIALIZED_BYTES


def test_sanitize_error_details_filters_pydantic_validation_errors():
    """
    GIVEN Pydantic-style validation errors with raw input and context
    WHEN details are sanitized for public response details
    THEN each validation error keeps only type, loc, and msg
    """
    details = {
        "errors": [
            {
                "type": "string_pattern_mismatch",
                "loc": ("body", "checksum"),
                "msg": "String should match pattern.",
                "input": "raw-client-value",
                "ctx": {"pattern": "[a-f0-9]{64}"},
                "url": "https://errors.pydantic.dev/example",
            }
        ]
    }

    sanitized = sanitize_error_details(details, error_code="INVALID_REQUEST")

    assert sanitized == {
        "errors": [
            {
                "type": "string_pattern_mismatch",
                "loc": ["body", "checksum"],
                "msg": "String should match pattern.",
            }
        ]
    }


def test_sanitize_error_details_omits_reason_for_internal_error():
    """
    GIVEN an INTERNAL_ERROR with raw diagnostic reason details
    WHEN details are sanitized for public serialization
    THEN the raw reason is omitted from the public details payload
    """
    details = {
        "reason": "database password appeared in an exception",
        "action_id": "test_runtime.ping",
    }

    sanitized = sanitize_error_details(details, error_code="INTERNAL_ERROR")

    assert sanitized == {"action_id": "test_runtime.ping"}
