"""
Integration tests for the AuthMiddleware.

These tests validate authentication behavior as an HTTP-level contract.
They ensure that:

- Protected endpoints enforce Bearer token authentication.
- Explicitly exempt endpoints ignore authentication entirely.
- Unauthorized responses follow the ResponseEnvelope error contract.
- Request IDs propagate correctly through authentication failures.

They do NOT validate cryptographic correctness, token generation,
or business logic.
"""

import pytest
from fastapi.testclient import TestClient

from star.app import create_app
from star.core.config import Settings
from star.core.errors import UNAUTHORIZED

TEST_ACTION_ID = "test_runtime.ping"

# ============================================================================
# Exempt Endpoints
# ============================================================================


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
def test_auth_is_ignored_for_health_endpoint(
    client,
    auth_headers,
    headers,
):
    """
    GIVEN the health endpoint is unconditionally exempt from authentication
    WHEN it is called with missing, invalid, or valid Authorization headers
    THEN the request is allowed to proceed and returns a successful response
    """
    request_headers = auth_headers if headers is None else headers

    response = client.get("/health", headers=request_headers)

    assert response.status_code == 200


def test_metrics_endpoint_requires_auth_by_default(client, auth_headers):
    """
    GIVEN metrics auth is required by default
    WHEN `/metrics` is called without auth, with invalid auth, and with valid auth
    THEN only the valid authenticated request is allowed
    """
    missing = client.get("/metrics")
    invalid = client.get("/metrics", headers={"Authorization": "Bearer invalid"})
    valid = client.get("/metrics", headers=auth_headers)

    assert missing.status_code == UNAUTHORIZED.http_status
    assert invalid.status_code == UNAUTHORIZED.http_status
    assert valid.status_code == 200


def test_metrics_endpoint_can_be_public_when_auth_is_disabled(
    api_token,
    star_root_dir,
):
    """
    GIVEN metrics auth is explicitly disabled
    WHEN `/metrics` is called without auth
    THEN the request is allowed to proceed
    """
    settings = Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_metrics_require_auth": False,
        }
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200


def test_docs_endpoints_require_auth_when_enabled_by_default(
    api_token,
    auth_headers,
    star_root_dir,
):
    """
    GIVEN docs are enabled and docs auth keeps its secure default
    WHEN docs endpoints are called with and without auth
    THEN only authenticated requests are allowed
    """
    settings = Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_enable_docs": True,
        }
    )
    app = create_app(settings)

    with TestClient(app) as client:
        missing = client.get("/openapi.json")
        valid = client.get("/openapi.json", headers=auth_headers)

    assert missing.status_code == UNAUTHORIZED.http_status
    assert valid.status_code == 200


def test_docs_endpoints_can_be_public_when_docs_auth_is_disabled(
    api_token,
    star_root_dir,
):
    """
    GIVEN docs are enabled and docs auth is explicitly disabled
    WHEN `/openapi.json` is called without auth
    THEN the docs endpoint is publicly reachable
    """
    settings = Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_enable_docs": True,
            "star_docs_require_auth": False,
        }
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/openapi.json")

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


def test_unauthorized_response_uses_response_envelope_error(
    client,
    valid_registry,
):
    """
    GIVEN a protected endpoint
    WHEN authentication fails
    THEN the response body follows the ResponseEnvelope error contract
    """
    client.app.state.action_registry = valid_registry

    response = client.post(f"/v1/actions/{TEST_ACTION_ID}", json={})

    assert response.status_code == 401

    body = response.json()
    assert isinstance(body, dict)

    # ResponseEnvelope error invariants
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
