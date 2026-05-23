"""
Integration tests for the RequestIntegrityMiddleware.

These tests validate request-integrity enforcement as an HTTP-level contract.
They ensure that:

- Unsupported content types are rejected on protected JSON endpoints.
- Header-integrity violations are rejected before downstream middleware.
- Conflicting `Content-Length` and `Transfer-Encoding` headers are rejected.
- Body size limits are enforced using `Content-Length`.
- Rejections preserve envelope/headers and increment expected metrics.

They do NOT unit-test middleware internals.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from star.app import create_app
from star.core.config import Settings
from star.core.errors import FILE_TOO_LARGE, INVALID_REQUEST
from star.middleware.request_integrity import REQUEST_INTEGRITY_REJECTIONS_TOTAL

# ============================================================================
# Helpers
# ============================================================================


def _integrity_metric_value(path: str, method: str, reason: str) -> float:
    """Return current `star_request_integrity_rejections_total` for labels.

    Args:
        path: Normalized request path label.
        method: Uppercase HTTP method label.
        reason: Rejection reason label.

    Returns:
        Aggregated metric value for the provided labels.
    """
    total = 0.0
    for metric in REQUEST_INTEGRITY_REJECTIONS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name != "star_request_integrity_rejections_total":
                continue
            labels = sample.labels
            if (
                labels.get("path") == path
                and labels.get("method") == method
                and labels.get("reason") == reason
            ):
                total += float(sample.value)
    return total


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def low_max_bytes_settings(api_token, star_root_dir) -> Settings:
    """Return settings with a strict body-size limit for deterministic tests.

    Args:
        api_token: Authentication token fixture.
        star_root_dir: Root directory fixture.

    Returns:
        Settings configured for low body-size limit tests.
    """
    return Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_max_file_bytes": 16,
        }
    )


@pytest.fixture
def low_max_bytes_app(low_max_bytes_settings):
    """Create app configured with a small `star_max_file_bytes` value.

    Args:
        low_max_bytes_settings: Settings fixture with strict body-size limit.

    Returns:
        FastAPI application configured for integrity size-limit tests.
    """
    return create_app(low_max_bytes_settings)


@pytest.fixture
def low_max_bytes_client(low_max_bytes_app):
    """Create HTTP client bound to low body-limit app.

    Args:
        low_max_bytes_app: App fixture configured for low body-size limit.

    Yields:
        TestClient bound to the configured app.
    """
    with TestClient(low_max_bytes_app) as client:
        yield client


# ============================================================================
# Content-Type Enforcement
# ============================================================================


def test_execute_rejects_unsupported_content_type(client, auth_headers):
    """
    GIVEN POST /v1/actions/noop requires application/json
    WHEN a request uses an unsupported content type
    THEN middleware rejects with HTTP 400 and INVALID_REQUEST envelope
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "unsupported_content_type"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    # Use "content" with raw bytes to bypass TestClient's default JSON encoding
    # and content-type. Don't use "data" to avoid HTTPX deprecation warnings.
    response = client.post(
        "/v1/actions/noop",
        content=b"plain-text-payload",
        headers={
            **auth_headers,
            "Content-Type": "text/plain",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert body["error"]["message"] == "Unsupported content type"
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


def test_execute_allows_application_json_with_charset(
    client,
    auth_headers,
    sandbox_file_factory,
):
    """
    GIVEN POST /v1/actions/noop requires JSON base media type
    WHEN Content-Type is application/json with charset parameter
    THEN request passes request-integrity validation
    AND the star_request_integrity_rejections_total metric is not incremented
    """
    sf = sandbox_file_factory(name="charset_ok.txt", content=b"hello")

    reason = "unsupported_content_type"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    response = client.post(
        "/v1/actions/noop",
        json={
            "params": {"path": str(sf.rel_path)},
        },
        headers={
            **auth_headers,
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    assert response.status_code != INVALID_REQUEST.http_status

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before


# ============================================================================
# Header Integrity and CL/TE Conflict
# ============================================================================


def test_duplicate_authorization_header_is_rejected(api_token, client):
    """
    GIVEN duplicate Authorization headers in the same request
    WHEN request enters request-integrity middleware
    THEN middleware rejects with INVALID_REQUEST before auth logic
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "duplicate_authorization"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    response = client.post(
        "/v1/actions/noop",
        content=b'{"params":{}}',
        headers=[
            ("Authorization", f"Bearer {api_token}"),
            ("Authorization", "Bearer badtoken"),
            ("Content-Type", "application/json"),
        ],
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert body["error"]["message"] == "Duplicate Authorization headers are not allowed"
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


def test_conflicting_content_length_and_transfer_encoding_is_rejected(
    api_token,
    client,
):
    """
    GIVEN both Content-Length and Transfer-Encoding headers are present
    WHEN request enters request-integrity middleware
    THEN middleware rejects to mitigate CL/TE smuggling ambiguity
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "conflicting_cl_te"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    response = client.post(
        "/v1/actions/noop",
        content=b'{"params":{}}',
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Content-Length": "28",
            "Transfer-Encoding": "chunked",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert (
        body["error"]["message"]
        == "Conflicting Content-Length and Transfer-Encoding headers"
    )
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


# ============================================================================
# Body Size Enforcement
# ============================================================================


def test_invalid_content_length_is_rejected(low_max_bytes_client, auth_headers):
    """
    GIVEN Content-Length must be digits-only
    WHEN a request sends an invalid Content-Length
    THEN middleware rejects with INVALID_REQUEST
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "invalid_content_length"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    response = low_max_bytes_client.post(
        "/v1/actions/noop",
        content=b'{"params":{}}',
        headers={
            **auth_headers,
            "Content-Type": "application/json",
            "Content-Length": "abc",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert body["error"]["message"] == "Invalid Content-Length header"
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


def test_content_length_exceeding_limit_is_rejected(low_max_bytes_client, auth_headers):
    """
    GIVEN a strict star_max_file_bytes limit
    WHEN Content-Length declares a value above the limit
    THEN middleware rejects with FILE_TOO_LARGE
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "content_length_exceeds_limit"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    response = low_max_bytes_client.post(
        "/v1/actions/noop",
        content=b"{}",
        headers={
            **auth_headers,
            "Content-Type": "application/json",
            "Content-Length": "999",
        },
    )

    assert response.status_code == FILE_TOO_LARGE.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == FILE_TOO_LARGE.code
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0
