"""
Integration tests for the AuthMiddleware.

These tests validate authentication behavior as an HTTP-level contract.
They ensure that:

- Protected endpoints enforce Bearer token authentication.
- Explicitly exempt endpoints ignore authentication entirely.
- Unauthorized responses follow the ResponseEnvelope failure contract.
- Request IDs propagate correctly through authentication failures.

They do NOT validate cryptographic correctness, token generation,
or business logic.
"""

import pytest

from star.core.errors import UNAUTHORIZED

TEST_ACTION_ID = "test_runtime.ping"

# ============================================================================
# Exempt Endpoints
# ============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/metrics",
    ],
    ids=[
        "health",
        "metrics",
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
        pytest.param(None, id="valid-auth"),
    ],
    ids=[
        "missing-auth",
        "invalid-auth",
        "valid-auth",
    ],
)
def test_auth_is_ignored_for_exempt_endpoints(
    client,
    auth_headers,
    path,
    headers,
):
    """
    GIVEN an endpoint that is explicitly exempt from authentication
    WHEN it is called with missing, invalid, or valid Authorization headers
    THEN the request is allowed to proceed and returns a successful response
    """
    request_headers = auth_headers if headers is None else headers

    response = client.get(path, headers=request_headers)

    assert response.status_code == 200


# ============================================================================
# Protected Endpoints
# ============================================================================


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
    ],
    ids=[
        "missing-auth",
        "invalid-auth",
    ],
)
def test_protected_endpoint_rejects_missing_or_invalid_auth(
    client,
    valid_registry,
    headers,
):
    """
    GIVEN a protected endpoint
    WHEN it is called without a token or with an invalid token
    THEN the request is rejected with HTTP 401
    """
    client.app.state.action_registry = valid_registry

    response = client.post(
        f"/v1/actions/{TEST_ACTION_ID}",
        json={},
        headers=headers,
    )

    assert response.status_code == UNAUTHORIZED.http_status
    body = response.json()
    assert body["error"] is not None
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_protected_endpoint_allows_valid_auth(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN a protected endpoint
    WHEN it is called with a valid Authorization header
    THEN the request is allowed to proceed
    """
    client.app.state.action_registry = valid_registry

    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{TEST_ACTION_ID}",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 200


# ============================================================================
# Auth middleware: unauthorized response contract
# ============================================================================


def test_unauthorized_response_uses_response_envelope_failure(
    client,
    valid_registry,
):
    """
    GIVEN a protected endpoint
    WHEN authentication fails
    THEN the response body follows the ResponseEnvelope failure contract
    """
    client.app.state.action_registry = valid_registry

    response = client.post(f"/v1/actions/{TEST_ACTION_ID}", json={})

    assert response.status_code == 401

    body = response.json()
    assert isinstance(body, dict)

    # ResponseEnvelope failure invariants
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == UNAUTHORIZED.code
    assert "message" in body["error"]


def test_unauthorized_response_sets_www_authenticate_header(
    client,
    valid_registry,
):
    """
    GIVEN a protected endpoint
    WHEN authentication fails
    THEN the WWW-Authenticate header is set to indicate Bearer authentication
    """
    client.app.state.action_registry = valid_registry

    response = client.post(f"/v1/actions/{TEST_ACTION_ID}", json={})

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
