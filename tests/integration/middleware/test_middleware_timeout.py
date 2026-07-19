"""
Integration tests for the TimeoutMiddleware.

These tests validate timeout enforcement as an HTTP-level contract.
They ensure that:

- Slow handlers are terminated with HTTP 504.
- The ResponseEnvelope error contract is preserved.
- X-Request-Id propagates correctly.
- Metrics are incremented correctly.
- Exempt endpoints bypass timeout.
- StarError is NOT converted into timeout.
- Timeout takes priority over domain errors.

They do NOT validate internal asyncio mechanics.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from star.app import create_app
from star.core.config import Settings
from star.core.errors import INVALID_REQUEST, TIMEOUT, StarError
from star.core.schemas.envelope import ResponseEnvelope
from star.middleware.timeout import TIMEOUTS_TOTAL
from star.routes.actions.schemas import ExecuteActionData

TEST_ACTION_ID = "test_runtime.ping"

# ============================================================================
# Helpers
# ============================================================================


def _timeout_metric_value(path: str, method: str) -> float:
    """Return current `star_timeouts_total` value for a label set.

    Args:
        path: Normalized request path label.
        method: Uppercase HTTP method label.

    Returns:
        Aggregated metric value for the provided labels.
    """
    total = 0.0
    for metric in TIMEOUTS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name != "star_timeouts_total":
                continue
            labels = sample.labels
            if labels.get("path") == path and labels.get("method") == method:
                total += float(sample.value)
    return total


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def low_timeout_settings(api_token, star_root_dir) -> Settings:
    """Return settings with a strict 100ms timeout.

    Args:
        api_token: Authentication token fixture.
        star_root_dir: Root directory fixture.

    Returns:
        Settings configured for low timeout tests.
    """
    return Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
            "star_timeout_ms": 100,
        }
    )


@pytest.fixture
def low_timeout_app(low_timeout_settings, valid_registry):
    """Create app configured with 100ms timeout for deterministic tests.

    Args:
        low_timeout_settings: Settings fixture with low timeout.

    Returns:
        FastAPI application configured for timeout tests.
    """
    app = create_app(low_timeout_settings)
    app.state.action_registry = valid_registry
    return app


@pytest.fixture
def low_timeout_client(low_timeout_app):
    """Create HTTP client bound to low-timeout app.

    Args:
        low_timeout_app: App fixture configured for timeout tests.

    Yields:
        TestClient bound to the configured app.
    """
    with TestClient(low_timeout_app) as client:
        yield client


# ============================================================================
# Slow Endpoint Handler Fixtures
# ============================================================================


@pytest.fixture
def slow_health_endpoint(monkeypatch):
    """Patch `/health` to simulate a slow response.

    Args:
        monkeypatch: Pytest helper for runtime attribute patching.

    Returns:
        None. The route handler is patched in-place.
    """

    async def slow_health():
        """Simulate delayed health response beyond timeout threshold."""
        await asyncio.sleep(0.2)
        payload = ResponseEnvelope.from_success({"status": "ok"}).model_dump()
        return JSONResponse(payload)

    monkeypatch.setattr(
        "star.routes.health.health",
        slow_health,
    )


@pytest.fixture
def slow_metrics_endpoint(monkeypatch):
    """Patch `/metrics` to simulate a slow response.

    Args:
        monkeypatch: Pytest helper for runtime attribute patching.

    Returns:
        None. The route handler is patched in-place.
    """

    async def slow_metrics():
        """Simulate delayed metrics response beyond timeout threshold."""
        await asyncio.sleep(0.2)
        data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    monkeypatch.setattr("star.routes.metrics.metrics", slow_metrics)


@pytest.fixture
def slow_execute_endpoint_success(monkeypatch):
    """Patch execute handler to simulate slow successful execution.

    Args:
        monkeypatch: Pytest helper for runtime attribute patching.

    Returns:
        None. The dispatcher is patched in-place.
    """

    async def slow_execute_handler(_request, _action_id, _payload):
        """Simulate delayed successful execute handler response."""
        await asyncio.sleep(0.2)
        return ExecuteActionData(
            exit_code=0,
            stdout="ok",
            stdout_encoding="utf-8",
            stderr="",
            stderr_encoding="utf-8",
            exec_time=0.2,
            pid=12345,
            truncated=False,
            redacted=False,
        )

    monkeypatch.setattr(
        "star.routes.actions.router.execute_action_handler",
        slow_execute_handler,
    )


@pytest.fixture
def slow_execute_endpoint_error(monkeypatch):
    """Patch execute handler to simulate slow execution raising StarError.

    Args:
        monkeypatch: Pytest helper for runtime attribute patching.

    Returns:
        None. The dispatcher is patched in-place.
    """

    async def slow_execute_handler(_request, _action_id, _payload):
        """Simulate delayed execute handler raising a domain error."""
        await asyncio.sleep(0.2)
        raise StarError(
            INVALID_REQUEST,
            message="delayed boom",
        )

    monkeypatch.setattr(
        "star.routes.actions.router.execute_action_handler",
        slow_execute_handler,
    )


# ============================================================================
# Generic Slow Handler Timeout
# ============================================================================


def test_generic_slow_handler_is_intercepted_by_timeout(
    low_timeout_app,
    low_timeout_client,
    auth_headers,
):
    """
    GIVEN a timeout of 100ms
    WHEN a handler sleeps longer than the timeout
    THEN HTTP 504 is returned with proper envelope and metric increment
    """

    @low_timeout_app.get("/test-slow")
    async def slow_handler():
        """Simulate a generic slow route for timeout interception tests."""
        await asyncio.sleep(0.5)
        return {"ok": True}

    before = _timeout_metric_value("/test-slow", "GET")

    response = low_timeout_client.get("/test-slow", headers=auth_headers)

    assert response.status_code == TIMEOUT.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == TIMEOUT.code
    assert "X-Request-Id" in response.headers

    after = _timeout_metric_value("/test-slow", "GET")
    assert after == before + 1.0


# ============================================================================
# Execute Endpoint Behavior
# ============================================================================


def test_star_action_error_is_not_converted_to_timeout(
    low_timeout_client,
    auth_headers,
):
    """
    GIVEN a handler that raises StarError immediately
    WHEN it executes
    THEN it is NOT converted into a timeout response
    """
    response = low_timeout_client.post(
        "/v1/actions/raise_star_action_error",
        json={"params": {}},
        headers=auth_headers,
    )

    assert response.status_code != TIMEOUT.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["data"] is None
    assert body["error"]["code"] != TIMEOUT.code


def test_slow_execute_success_is_intercepted_by_timeout(
    low_timeout_client,
    slow_execute_endpoint_success,
    auth_headers,
):
    """
    GIVEN a slow successful action (> timeout)
    WHEN it executes
    THEN TIMEOUT takes priority
    """

    payload = {"params": {}}

    action_path = f"/v1/actions/{TEST_ACTION_ID}"
    before = _timeout_metric_value(action_path, "POST")

    response = low_timeout_client.post(
        action_path,
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == TIMEOUT.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == TIMEOUT.code

    after = _timeout_metric_value(action_path, "POST")
    assert after == before + 1.0


def test_slow_execute_error_is_intercepted_by_timeout(
    low_timeout_client,
    slow_execute_endpoint_error,
    auth_headers,
):
    """
    GIVEN a slow action that eventually raises StarError
    WHEN it exceeds timeout
    THEN TIMEOUT is returned instead of domain error
    """

    payload = {"params": {}}

    action_path = f"/v1/actions/{TEST_ACTION_ID}"
    before = _timeout_metric_value(action_path, "POST")

    response = low_timeout_client.post(
        action_path,
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == TIMEOUT.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"] is not None
    assert body["error"]["code"] == TIMEOUT.code
    assert body["error"]["code"] != INVALID_REQUEST.code

    after = _timeout_metric_value(action_path, "POST")
    assert after == before + 1.0


# ============================================================================
# Exempt Endpoints
# ============================================================================


def test_health_endpoint_is_exempt_from_timeout(low_timeout_client):
    """
    GIVEN a low timeout configuration
    WHEN /health is requested
    THEN it returns 200 and is not timed out
    """

    response = low_timeout_client.get("/health")

    assert response.status_code == 200


def test_metrics_endpoint_is_exempt_from_timeout(low_timeout_client):
    """
    GIVEN a low timeout configuration
    WHEN /metrics is requested
    THEN it returns 200 and is not timed out
    """

    response = low_timeout_client.get("/metrics")

    assert response.status_code == 200


def test_slow_health_is_not_intercepted_by_timeout(
    low_timeout_client,
    slow_health_endpoint,
):
    """
    GIVEN a slow /health handler (> timeout)
    WHEN it executes
    THEN it is NOT intercepted
    """

    before = _timeout_metric_value("/health", "GET")

    response = low_timeout_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    after = _timeout_metric_value("/health", "GET")
    assert after == before


def test_slow_metrics_is_not_intercepted_by_timeout(
    low_timeout_client,
    slow_metrics_endpoint,
):
    """
    GIVEN a slow /metrics handler (> timeout)
    WHEN it executes
    THEN it is NOT intercepted
    """

    before = _timeout_metric_value("/metrics", "GET")

    response = low_timeout_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers.get("content-type") == CONTENT_TYPE_LATEST

    after = _timeout_metric_value("/metrics", "GET")
    assert after == before


# ============================================================================
# Metrics Path Normalization
# ============================================================================


def test_timeout_metric_uses_normalized_path(
    low_timeout_app,
    low_timeout_client,
    auth_headers,
):
    """
    GIVEN a slow handler registered at /test-slow-normalized
    WHEN it is called with trailing slash and query string
    THEN metric label uses normalized path
    """

    @low_timeout_app.get("/test-slow-normalized")
    async def slow():
        """Simulate a slow route used to verify metric path normalization."""
        await asyncio.sleep(0.5)
        return {"ok": True}

    before_normalized = _timeout_metric_value("/test-slow-normalized", "GET")
    before_raw = _timeout_metric_value("/test-slow-normalized/", "GET")

    response = low_timeout_client.get(
        "/test-slow-normalized/?a=1", headers=auth_headers
    )

    after_normalized = _timeout_metric_value("/test-slow-normalized", "GET")
    after_raw = _timeout_metric_value("/test-slow-normalized/", "GET")

    assert response.status_code == TIMEOUT.http_status
    payload = response.json()
    assert payload["error"]["code"] == TIMEOUT.code
    assert after_normalized == before_normalized + 1.0
    assert after_raw == before_raw
