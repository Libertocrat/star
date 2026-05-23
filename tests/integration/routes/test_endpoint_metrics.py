"""
Integration tests for the /metrics endpoint.

These tests validate the Prometheus exposition contract of the STAR service.
They ensure that metrics are exposed in a format consumable by Prometheus.
"""

from prometheus_client import CONTENT_TYPE_LATEST

# ============================================================================
# Success Cases
# ============================================================================


def test_metrics_endpoint_returns_200(client):
    """
    GIVEN a running STAR application
    WHEN the /metrics endpoint is requested
    THEN it returns HTTP 200
    """
    response = client.get("/metrics")

    assert response.status_code == 200


def test_metrics_endpoint_returns_prometheus_content_type(client):
    """
    GIVEN a running STAR application
    WHEN the /metrics endpoint is requested
    THEN it returns the Prometheus content type
    """
    response = client.get("/metrics")

    content_type = response.headers.get("Content-Type")
    assert content_type == CONTENT_TYPE_LATEST


def test_metrics_endpoint_returns_non_empty_body(client):
    """
    GIVEN a running STAR application
    WHEN the /metrics endpoint is requested
    THEN it returns a non-empty metrics payload
    """
    response = client.get("/metrics")

    assert response.content
    assert len(response.content) > 0


def test_metrics_payload_uses_prometheus_text_format(client):
    """
    GIVEN a running STAR application
    WHEN the /metrics endpoint is requested
    THEN the payload follows the Prometheus text exposition format
    """
    response = client.get("/metrics")

    payload = response.text

    assert "# HELP" in payload
    assert "# TYPE" in payload
