"""
Integration tests for the RequestIntegrityMiddleware.

These tests validate request-integrity enforcement as an HTTP-level contract.
They ensure that:

- Unsupported content types are rejected on protected JSON endpoints.
- Header-integrity violations are rejected before downstream middleware.
- Conflicting `Content-Length` and `Transfer-Encoding` headers are rejected.
- Body size limits are enforced using `Content-Length` and streaming reads.
- Request bodies are rejected on methods that STAR treats as bodyless.
- Rejections preserve envelope/headers and increment expected metrics.

They do NOT unit-test middleware internals.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from star.app import create_app
from star.core.config import Settings
from star.core.errors import INVALID_REQUEST, REQUEST_BODY_TOO_LARGE
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


async def _call_asgi_streaming_request(
    app,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    chunks: list[bytes],
):
    """Call an ASGI app with explicit streamed request-body chunks.

    This helper intentionally bypasses `TestClient` because `TestClient` and
    HTTPX normalize many requests and may provide `Content-Length`. These tests
    need direct control over the ASGI `receive()` stream to exercise the
    middleware branch used when `Content-Length` is absent.

    Args:
        app: ASGI application under test.
        method: HTTP method to place in the ASGI scope.
        path: Request path to place in the ASGI scope.
        headers: HTTP headers to include in the ASGI scope.
        chunks: Body chunks yielded as separate `http.request` messages.

    Returns:
        ASGI response messages emitted by the application.
    """
    pending_chunks = list(chunks)
    sent_messages = []
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in headers.items()
    ]

    async def receive():
        """Return streamed request chunks without `Content-Length`."""

        if pending_chunks:
            return {
                "type": "http.request",
                "body": pending_chunks.pop(0),
                "more_body": bool(pending_chunks),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        """Capture ASGI response messages."""

        sent_messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "raw_headers": raw_headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        },
        receive,
        send,
    )

    return sent_messages


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def low_max_bytes_settings(api_token, star_root_dir) -> Settings:
    """Return settings with strict body-size limits for deterministic tests.

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
            "star_max_body_bytes": 16,
            "star_max_file_bytes": 100_000,
        }
    )


@pytest.fixture
def low_max_bytes_app(low_max_bytes_settings):
    """Create app configured with small general body size limit.

    Args:
        low_max_bytes_settings: Settings fixture with strict body-size limits.

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
# Header Integrity and Transfer Encoding Conflict
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
    GIVEN a strict STAR_MAX_BODY_BYTES limit
    WHEN Content-Length declares a value above the general body limit
    THEN middleware rejects with REQUEST_BODY_TOO_LARGE
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

    assert response.status_code == REQUEST_BODY_TOO_LARGE.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == REQUEST_BODY_TOO_LARGE.code
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


@pytest.mark.asyncio
async def test_execute_streaming_body_exceeding_limit_is_rejected(
    low_max_bytes_app,
    auth_headers,
):
    """
    GIVEN a strict STAR_MAX_BODY_BYTES limit and no Content-Length header
    WHEN a streamed action request exceeds the general body limit
    THEN middleware rejects with REQUEST_BODY_TOO_LARGE
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "body_exceeds_limit"
    before = _integrity_metric_value("/v1/actions/noop", "POST", reason)

    sent_messages = await _call_asgi_streaming_request(
        low_max_bytes_app,
        method="POST",
        path="/v1/actions/noop",
        headers={
            "Authorization": auth_headers["Authorization"],
            "Content-Type": "application/json",
        },
        chunks=[b'{"params":{"path":"', b"A" * 32, b'"}}'],
    )

    start = next(
        message for message in sent_messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    body = json.loads(response_body.decode("utf-8"))

    assert start["status"] == REQUEST_BODY_TOO_LARGE.http_status
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == REQUEST_BODY_TOO_LARGE.code
    assert any(
        name.lower() == b"x-request-id" for name, _value in start.get("headers", [])
    )

    after = _integrity_metric_value("/v1/actions/noop", "POST", reason)
    assert after == before + 1.0


def test_multipart_upload_uses_file_limit_instead_of_general_body_limit(
    low_max_bytes_client,
    auth_headers,
):
    """
    GIVEN STAR_MAX_BODY_BYTES is smaller than a normal multipart request
    WHEN POST /v1/files uploads a small file below STAR_MAX_FILE_BYTES
    THEN the multipart request is accepted by request-integrity body limits
    """
    response = low_max_bytes_client.post(
        "/v1/files",
        headers=auth_headers,
        files={
            "file": (
                "small.txt",
                b"hello",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None


def test_get_with_body_is_rejected(client, auth_headers):
    """
    GIVEN GET endpoints in STAR do not accept request bodies
    WHEN a GET request carries a non-empty body
    THEN middleware rejects with INVALID_REQUEST
    AND the star_request_integrity_rejections_total metric is incremented
    """
    reason = "body_not_allowed"
    before = _integrity_metric_value("/v1/actions", "GET", reason)

    response = client.request(
        "GET",
        "/v1/actions",
        content=b'{"unexpected":true}',
        headers={
            **auth_headers,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert body["error"]["message"] == "Request body is not allowed for this method"
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value("/v1/actions", "GET", reason)
    assert after == before + 1.0


def test_delete_with_body_is_rejected(client, auth_headers):
    """
    GIVEN DELETE endpoints in STAR do not accept request bodies
    WHEN a DELETE request carries a non-empty body
    THEN middleware rejects with INVALID_REQUEST
    AND the star_request_integrity_rejections_total metric is incremented
    """
    file_id = "00000000-0000-0000-0000-000000000000"
    path = f"/v1/files/{file_id}"
    reason = "body_not_allowed"
    before = _integrity_metric_value(path, "DELETE", reason)

    response = client.request(
        "DELETE",
        path,
        content=b'{"unexpected":true}',
        headers={
            **auth_headers,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code
    assert body["error"]["message"] == "Request body is not allowed for this method"
    assert "X-Request-Id" in response.headers

    after = _integrity_metric_value(path, "DELETE", reason)
    assert after == before + 1.0
