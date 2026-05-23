"""
Integration tests for the /health endpoint.

These tests validate the liveness contract of the STAR service.
They ensure the service is reachable and responds with a stable payload.
"""

# ============================================================================
# Success Cases
# ============================================================================


def test_health_endpoint_returns_200(client):
    """
    GIVEN a running STAR application
    WHEN the /health endpoint is requested
    THEN it returns HTTP 200
    """
    response = client.get("/health")

    assert response.status_code == 200


def test_health_endpoint_returns_expected_payload(client):
    """
    GIVEN a running STAR application
    WHEN the /health endpoint is requested
    THEN it returns the expected health payload
    """
    response = client.get("/health")

    body = response.json()

    assert isinstance(body, dict)
    assert body["success"] is True
    assert body["data"] is not None
    assert body["data"]["status"] == "ok"
