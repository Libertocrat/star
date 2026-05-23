"""Integration tests for the ObservabilityMiddleware.

These tests validate observability behavior as an HTTP-level contract.
They ensure that:

- Requests increment counters and duration histogram counts.
- 4xx and 5xx responses are classified and counted correctly.
- Inflight gauge returns to baseline after request completion.
- Path labels are normalized for metric cardinality safety.
- /metrics is excluded from observability instrumentation.

They do NOT unit-test middleware internals.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from star.core.errors import INVALID_REQUEST
from star.middleware.observability import (
    HTTP_ERRORS_TOTAL,
    HTTP_INFLIGHT_REQUESTS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)

# ============================================================================
# Helpers
# ============================================================================


def requests_total_value(method: str, path: str, status_code: str) -> float:
    """Return current star_http_requests_total value for a label set."""

    total = 0.0
    for metric in HTTP_REQUESTS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name != "star_http_requests_total":
                continue
            labels = sample.labels
            if (
                labels.get("method") == method
                and labels.get("path") == path
                and labels.get("status_code") == status_code
            ):
                total += float(sample.value)
    return total


def duration_count_value(method: str, path: str, status_class: str) -> float:
    """Return star_http_request_duration_seconds_count for labels."""

    total = 0.0
    for metric in HTTP_REQUEST_DURATION_SECONDS.collect():
        for sample in metric.samples:
            if sample.name != "star_http_request_duration_seconds_count":
                continue
            labels = sample.labels
            if (
                labels.get("method") == method
                and labels.get("path") == path
                and labels.get("status_class") == status_class
            ):
                total += float(sample.value)
    return total


def errors_total_value(status_class: str) -> float:
    """Return current star_http_errors_total value for a status class."""

    total = 0.0
    for metric in HTTP_ERRORS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name != "star_http_errors_total":
                continue
            labels = sample.labels
            if labels.get("status_class") == status_class:
                total += float(sample.value)
    return total


def inflight_gauge_value() -> float:
    """Return current star_http_inflight_requests gauge value."""

    for metric in HTTP_INFLIGHT_REQUESTS.collect():
        for sample in metric.samples:
            if sample.name == "star_http_inflight_requests":
                return float(sample.value)
    return 0.0


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def server_error_client(app):
    """Create a client with a deterministic route that raises HTTP 500.

    Args:
        app: FastAPI application fixture.

    Yields:
        TestClient configured to capture 5xx responses as HTTP payloads.
    """

    @app.get("/test-observability-500")
    async def _raise_error() -> dict[str, str]:
        """Raise deterministic runtime error for middleware observability tests."""
        raise RuntimeError("forced 500 for observability integration test")

    # Make sure to set "raise_server_exceptions=False" to prevent raising exceptions
    # for server errors that would fail the test before we can assert on metrics.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ============================================================================
# Happy Path Request Counting
# ============================================================================


def test_successful_request_increments_requests_total_and_duration_count(client):
    """
    GIVEN a successful endpoint
    WHEN it is requested once
    THEN request and duration metrics increment while error metric does not
    """

    before_requests = requests_total_value("GET", "/health", "200")
    before_duration_count = duration_count_value("GET", "/health", "2xx")
    before_errors_2xx = errors_total_value("2xx")
    baseline_inflight = inflight_gauge_value()

    response = client.get("/health")

    assert response.status_code == 200
    assert requests_total_value("GET", "/health", "200") == before_requests + 1.0
    assert duration_count_value("GET", "/health", "2xx") == before_duration_count + 1.0
    assert errors_total_value("2xx") == before_errors_2xx
    assert inflight_gauge_value() == baseline_inflight


# ============================================================================
# 404 Behavior
# ============================================================================


def test_not_found_increments_requests_total_duration_and_errors_4xx(
    client,
    auth_headers,
):
    """
    GIVEN a non-existent route
    WHEN it is requested
    THEN request, duration, and 4xx error metrics increment
    """

    path = "/does-not-exist"
    before_requests = requests_total_value("GET", path, "404")
    before_duration_count = duration_count_value("GET", path, "4xx")
    before_errors_4xx = errors_total_value("4xx")

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 404
    assert requests_total_value("GET", path, "404") == before_requests + 1.0
    assert duration_count_value("GET", path, "4xx") == before_duration_count + 1.0
    assert errors_total_value("4xx") == before_errors_4xx + 1.0


# ============================================================================
# Downstream Middleware Rejection
# ============================================================================


def test_request_integrity_rejection_is_counted_by_observability_as_4xx(
    client,
    auth_headers,
):
    """
    GIVEN request-integrity content-type enforcement
    WHEN POST /v1/actions/noop is sent with unsupported content type
    THEN observability captures request, duration, and 4xx error metrics
    """

    path = "/v1/actions/noop"
    before_requests = requests_total_value("POST", path, "400")
    before_duration_count = duration_count_value("POST", path, "4xx")
    before_errors_4xx = errors_total_value("4xx")

    response = client.post(
        path,
        content=b"plain-text-payload",
        headers={
            **auth_headers,
            "Content-Type": "text/plain",
        },
    )

    assert response.status_code == INVALID_REQUEST.http_status
    assert requests_total_value("POST", path, "400") == before_requests + 1.0
    assert duration_count_value("POST", path, "4xx") == before_duration_count + 1.0
    assert errors_total_value("4xx") == before_errors_4xx + 1.0


# ============================================================================
# 5xx Behavior
# ============================================================================


def test_internal_server_error_increments_requests_duration_and_errors_5xx(
    server_error_client,
    auth_headers,
):
    """
    GIVEN an endpoint that raises an exception
    WHEN it is requested
    THEN observability captures request, duration, and 5xx error metrics
    """

    path = "/test-observability-500"
    before_requests = requests_total_value("GET", path, "500")
    before_duration_count = duration_count_value("GET", path, "5xx")
    before_errors_5xx = errors_total_value("5xx")

    response = server_error_client.get(path, headers=auth_headers)

    assert response.status_code == 500
    assert requests_total_value("GET", path, "500") == before_requests + 1.0
    assert duration_count_value("GET", path, "5xx") == before_duration_count + 1.0
    assert errors_total_value("5xx") == before_errors_5xx + 1.0


# ============================================================================
# Metrics Endpoint Exclusion
# ============================================================================


@pytest.mark.parametrize(
    "repetitions",
    [
        pytest.param(1, id="single_metrics_scrape"),
        pytest.param(3, id="multiple_metrics_scrapes"),
    ],
)
def test_metrics_endpoint_is_excluded_from_observability_instrumentation(
    client,
    repetitions: int,
):
    """
    GIVEN /metrics is excluded from observability middleware
    WHEN it is requested one or more times
    THEN observability metric series for /metrics remain unchanged
    """

    before_requests = requests_total_value("GET", "/metrics", "200")
    before_duration_count = duration_count_value("GET", "/metrics", "2xx")
    before_errors_4xx = errors_total_value("4xx")
    before_errors_5xx = errors_total_value("5xx")

    for _ in range(repetitions):
        response = client.get("/metrics")
        assert response.status_code == 200

    assert requests_total_value("GET", "/metrics", "200") == before_requests
    assert duration_count_value("GET", "/metrics", "2xx") == before_duration_count
    assert errors_total_value("4xx") == before_errors_4xx
    assert errors_total_value("5xx") == before_errors_5xx


# ============================================================================
# Path Normalization
# ============================================================================


def test_observability_uses_normalized_path_labels(client):
    """
    GIVEN a trailing-slash and query-string path variant
    WHEN requests are made using canonical and variant forms
    THEN metric labels use the normalized canonical path
    """

    before_canonical_requests = requests_total_value("GET", "/health", "200")
    before_raw_requests = requests_total_value("GET", "/health/", "200")
    before_canonical_duration = duration_count_value("GET", "/health", "2xx")
    before_raw_duration = duration_count_value("GET", "/health/", "2xx")

    canonical = client.get("/health")
    variant = client.get("/health/?a=1")

    assert canonical.status_code == 200
    assert variant.status_code == 200

    assert requests_total_value("GET", "/health", "200") == (
        before_canonical_requests + 2.0
    )
    assert requests_total_value("GET", "/health/", "200") == before_raw_requests
    assert duration_count_value("GET", "/health", "2xx") == (
        before_canonical_duration + 2.0
    )
    assert duration_count_value("GET", "/health/", "2xx") == before_raw_duration


# ============================================================================
# Inflight Gauge Safety
# ============================================================================


def test_inflight_gauge_returns_to_baseline_after_successful_request(client):
    """
    GIVEN an inflight gauge baseline
    WHEN a successful request is made
    THEN inflight gauge returns to baseline
    """

    baseline = inflight_gauge_value()

    response = client.get("/health")

    assert response.status_code == 200
    assert inflight_gauge_value() == baseline


def test_inflight_gauge_returns_to_baseline_after_error_request(
    client,
    auth_headers,
):
    """
    GIVEN an inflight gauge baseline
    WHEN an error request is made
    THEN inflight gauge returns to baseline
    """

    baseline = inflight_gauge_value()

    response = client.get("/does-not-exist", headers=auth_headers)

    assert response.status_code == 404
    assert inflight_gauge_value() == baseline
