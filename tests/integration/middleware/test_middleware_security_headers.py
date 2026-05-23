"""Integration tests for SecurityHeadersMiddleware.

These tests validate security-header behavior as an HTTP-level contract.
They ensure that:

- Baseline security headers are present in HTTP responses.
- Fingerprinting headers (`Server`, `X-Powered-By`) are removed.
- Baseline headers are overwritten with authoritative values.
- Middleware registration respects `STAR_ENABLE_SECURITY_HEADERS`.

They do NOT unit-test middleware internals.
"""

from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from star.app import create_app
from star.core.config import Settings

# ============================================================================
# Constants
# ============================================================================


PERMISSIONS_POLICY_VALUE = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), payment=(), usb=()"
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def security_headers_test_client(app):
    """Create a client with deterministic test routes for header behavior.

    Args:
        app: FastAPI application fixture.

    Yields:
        TestClient bound to the configured app.
    """

    @app.get("/test-security-headers-leak")
    async def _leak_headers() -> JSONResponse:
        """Return response containing headers that must be removed."""
        response = JSONResponse(content={"ok": True})
        response.headers["Server"] = "uvicorn"
        response.headers["X-Powered-By"] = "FastAPI"
        return response

    @app.get("/test-security-headers-override")
    async def _override_headers() -> JSONResponse:
        """Return response containing headers that middleware must override."""
        response = JSONResponse(content={"ok": True})
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "origin"
        return response

    with TestClient(app) as client:
        yield client


# ============================================================================
# Baseline Headers
# ============================================================================


def test_baseline_security_headers_are_present(client):
    """
    GIVEN SecurityHeadersMiddleware is enabled by default
    WHEN an HTTP endpoint returns a response
    THEN baseline security headers are always present with fixed values
    """

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("permissions-policy") == PERMISSIONS_POLICY_VALUE
    assert "camera=()" in response.headers.get("permissions-policy", "")


# ============================================================================
# Header Removal
# ============================================================================


def test_stack_fingerprinting_headers_are_removed(
    security_headers_test_client,
    auth_headers,
):
    """
    GIVEN a downstream endpoint sets stack-fingerprinting headers
    WHEN SecurityHeadersMiddleware processes response start headers
    THEN `server` and `x-powered-by` headers are removed
    """

    response = security_headers_test_client.get(
        "/test-security-headers-leak",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers


# ============================================================================
# Baseline Overwrite Semantics
# ============================================================================


def test_baseline_headers_are_overwritten_with_authoritative_values(
    security_headers_test_client,
    auth_headers,
):
    """
    GIVEN a downstream endpoint sets conflicting baseline header values
    WHEN SecurityHeadersMiddleware finalizes response headers
    THEN fixed baseline values overwrite downstream values
    """

    response = security_headers_test_client.get(
        "/test-security-headers-override",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers


# ============================================================================
# Settings Toggle
# ============================================================================


def test_security_headers_middleware_is_not_registered_when_toggle_is_false(
    minimal_safe_env,
    monkeypatch,
):
    """
    GIVEN STAR_ENABLE_SECURITY_HEADERS is set to false
    WHEN the app is created from environment-backed settings
    THEN SecurityHeadersMiddleware is not registered in the middleware stack
    """

    monkeypatch.setenv("STAR_ENABLE_SECURITY_HEADERS", "false")

    app = create_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") is None
    assert response.headers.get("x-frame-options") is None
    assert response.headers.get("referrer-policy") is None
    assert response.headers.get("permissions-policy") is None


@pytest.mark.parametrize(
    "flag, expected",
    [
        pytest.param("true", True, id="true"),
        pytest.param("on", True, id="on"),
        pytest.param("1", True, id="1"),
        pytest.param("false", False, id="false"),
        pytest.param("off", False, id="off"),
        pytest.param("0", False, id="0"),
    ],
)
def test_settings_accepts_security_headers_toggle_values(
    api_token,
    star_root_dir,
    flag,
    expected,
):
    """
    GIVEN STAR_ENABLE_SECURITY_HEADERS receives a false-ish value
    WHEN Settings validates typed fields
    THEN star_enable_security_headers is parsed as boolean False
    """

    settings = Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_enable_security_headers": flag,
        }
    )

    assert settings.star_enable_security_headers is expected
