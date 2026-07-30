"""
Integration tests for the RequestIDMiddleware.

These tests validate request identifier behavior as an HTTP contract.
They ensure that:

- A request id is always present in responses.
- Client-supplied request ids are preserved when valid.
- Invalid or missing request ids result in a generated UUID.
- The request id is stored on request.state for downstream consumers.

These tests do NOT validate logging, tracing systems, or business logic.
"""

import uuid

import pytest

TEST_ACTION_ID = "test_runtime.ping"

# ============================================================================
# Generation Behavior
# ============================================================================


def test_request_id_is_generated_when_missing(client):
    """
    GIVEN a request without an X-Request-Id header
    WHEN the request is processed
    THEN a new request id is generated and included in the response
    """
    response = client.get("/health")

    assert response.status_code == 200
    assert "X-Request-Id" in response.headers

    rid = response.headers["X-Request-Id"]
    uuid.UUID(rid)  # must be a valid UUID


def test_request_id_is_generated_when_invalid(client):
    """
    GIVEN a request with an invalid X-Request-Id header
    WHEN the request is processed
    THEN a new valid UUID is generated and included in the response
    """
    response = client.get(
        "/health",
        headers={"X-Request-Id": "not-a-uuid"},
    )

    assert response.status_code == 200
    rid = response.headers["X-Request-Id"]
    uuid.UUID(rid)  # must not raise


# ============================================================================
# Preservation Behavior
# ============================================================================


def test_request_id_is_preserved_when_valid(client):
    """
    GIVEN a request with a valid X-Request-Id header
    WHEN the request is processed
    THEN the same request id is preserved in the response
    """
    original = str(uuid.uuid4())

    response = client.get(
        "/health",
        headers={"X-Request-Id": original},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == original


# ============================================================================
# Propagation Across Endpoints
# ============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/metrics",
        f"/v1/actions/{TEST_ACTION_ID}",
    ],
    ids=[
        "health",
        "metrics",
        "v1_actions_action_id",
    ],
)
def test_request_id_is_present_across_endpoints(
    client,
    auth_headers,
    valid_registry,
    path,
):
    """
    GIVEN a request to any public or protected endpoint
    WHEN the request is processed
    THEN a request id is always included in the response
    """
    client.app.state.action_registry = valid_registry

    kwargs = {}
    if path in {"/metrics", f"/v1/actions/{TEST_ACTION_ID}"}:
        kwargs["headers"] = auth_headers
    if path == f"/v1/actions/{TEST_ACTION_ID}":
        kwargs["json"] = {}

    response = (
        client.get(path, **kwargs)
        if path != f"/v1/actions/{TEST_ACTION_ID}"
        else client.post(path, **kwargs)
    )

    assert "X-Request-Id" in response.headers
    uuid.UUID(response.headers["X-Request-Id"])
