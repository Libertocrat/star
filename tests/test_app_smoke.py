# tests/test_app_smoke.py
"""
Smoke tests for the STAR FastAPI application.

These tests ensure that the application can be instantiated,
routes are registered, and the service responds to basic health checks.
They do NOT test business logic or security invariants.
"""

from fastapi.testclient import TestClient

from star.app import create_app
from star.core.config import Settings

# ============================================================================
# Application Startup
# ============================================================================


def test_app_starts_successfully(api_token, tmp_path):
    """
    GIVEN a valid Settings object
    WHEN the FastAPI app is created
    THEN the application instance is created without errors
    """
    settings = Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(tmp_path),
        }
    )

    app = create_app(settings)

    assert app is not None
    assert app.title == "Secure Templated Actions Runtime (STAR)"


# ============================================================================
# Health Endpoint
# ============================================================================


def test_health_endpoint_returns_200(tmp_path):
    """
    GIVEN a running STAR application
    WHEN the /health endpoint is requested
    THEN it returns HTTP 200 with expected payload
    """
    settings = Settings.model_validate(
        {
            "star_api_token": "test-token",
            "star_root_dir": str(tmp_path),
        }
    )

    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    assert body["data"]["status"] == "ok"
