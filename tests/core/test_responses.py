"""Tests for STAR HTTP JSON response helpers."""

from __future__ import annotations

import json

from star.core.errors import INTERNAL_ERROR, INVALID_REQUEST, StarError
from star.core.responses import error_json_response, star_error_json_response


def _json_body(response) -> dict:
    """Decode a JSONResponse body into a dictionary."""

    return json.loads(response.body.decode("utf-8"))


def test_error_json_response_uses_error_definition_status_by_default():
    """
    GIVEN a stable STAR error definition
    WHEN error_json_response builds a response without status override
    THEN the response uses the definition HTTP status and envelope shape
    """
    response = error_json_response(INVALID_REQUEST)

    body = _json_body(response)

    assert response.status_code == INVALID_REQUEST.http_status
    assert body == {
        "success": False,
        "data": None,
        "error": {
            "code": INVALID_REQUEST.code,
            "message": INVALID_REQUEST.default_message,
            "details": None,
        },
    }


def test_error_json_response_preserves_overrides_headers_and_details():
    """
    GIVEN custom message, details, headers, and status override
    WHEN error_json_response builds the response
    THEN all explicit HTTP and envelope values are preserved
    """
    headers = {"X-Request-Id": "123e4567-e89b-12d3-a456-426614174000"}
    details = {"reason": "synthetic"}

    # Use a client-facing error because INTERNAL_ERROR intentionally drops reason.
    response = error_json_response(
        INVALID_REQUEST,
        message="Invalid override.",
        details=details,
        headers=headers,
        status_code=418,
    )
    body = _json_body(response)

    assert response.status_code == 418
    assert response.headers["X-Request-Id"] == headers["X-Request-Id"]
    assert body["error"] == {
        "code": INVALID_REQUEST.code,
        "message": "Invalid override.",
        "details": details,
    }


def test_error_json_response_does_not_mutate_input_headers():
    """
    GIVEN a caller-owned headers mapping
    WHEN error_json_response builds the response
    THEN the original mapping remains unchanged
    """
    headers = {"Retry-After": "1"}

    response = error_json_response(INVALID_REQUEST, headers=headers)

    response.headers["Retry-After"] = "2"

    assert headers == {"Retry-After": "1"}


def test_star_error_json_response_preserves_star_error_contract():
    """
    GIVEN a transport-ready StarError with safe details
    WHEN star_error_json_response builds the response
    THEN the StarError status, message, and details are preserved
    """
    exc = StarError(
        INVALID_REQUEST,
        message="Invalid cursor.",
        details={"param": "cursor"},
    )

    response = star_error_json_response(exc)
    body = _json_body(response)

    assert response.status_code == INVALID_REQUEST.http_status
    assert body["error"] == {
        "code": INVALID_REQUEST.code,
        "message": "Invalid cursor.",
        "details": {"param": "cursor"},
    }


def test_error_json_response_sanitizes_direct_details():
    """
    GIVEN direct response details with unsafe validation error fields
    WHEN error_json_response builds the response envelope
    THEN only public-safe error details are serialized
    """
    response = error_json_response(
        INVALID_REQUEST,
        details={
            "errors": [
                {
                    "type": "value_error",
                    "loc": ("body", "checksum"),
                    "msg": "Value error.",
                    "input": "raw-client-value",
                    "ctx": {"error": "internal"},
                    "url": "https://errors.pydantic.dev/example",
                }
            ],
            "secret": "do-not-expose",
        },
    )
    body = _json_body(response)

    assert body["error"]["details"] == {
        "errors": [
            {
                "type": "value_error",
                "loc": ["body", "checksum"],
                "msg": "Value error.",
            }
        ]
    }


def test_star_error_json_response_omits_internal_error_reason():
    """
    GIVEN a StarError for INTERNAL_ERROR with raw reason details
    WHEN star_error_json_response builds the response envelope
    THEN the public response omits the internal reason
    """
    exc = StarError(
        INTERNAL_ERROR,
        details={"reason": "raw stack detail", "action_id": "test_runtime.ping"},
    )

    response = star_error_json_response(exc)
    body = _json_body(response)

    assert exc.details == {"action_id": "test_runtime.ping"}
    assert body["error"]["details"] == {"action_id": "test_runtime.ping"}
