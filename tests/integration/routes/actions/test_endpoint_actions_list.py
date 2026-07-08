"""Integration tests for the /v1/actions discovery endpoint."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def actions_client(client, valid_registry):
    """Return client with deterministic action registry injected."""

    client.app.state.action_registry = valid_registry
    return client


def _get_actions(actions_client, auth_headers, **params):
    """Call GET /v1/actions with authenticated headers."""

    return actions_client.get("/v1/actions", headers=auth_headers, params=params)


def _action_names(modules: list[dict[str, Any]]) -> list[str]:
    """Flatten action names from grouped module payloads."""

    return [action["action"] for module in modules for action in module["actions"]]


def test_list_actions_returns_modules(actions_client, auth_headers):
    """
    GIVEN a valid registry loaded in application state
    WHEN GET /v1/actions is requested
    THEN the response returns modules inside the success envelope
    """

    response = _get_actions(actions_client, auth_headers)

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert "modules" in data
    assert isinstance(data["modules"], list)
    assert len(data["modules"]) > 0
    assert all(
        "tags" in action for module in data["modules"] for action in module["actions"]
    )


@pytest.mark.parametrize(
    "registry_value",
    [None, object()],
    ids=["none", "wrong_type"],
)
def test_list_actions_returns_internal_error_when_registry_is_invalid(
    client,
    auth_headers,
    registry_value,
):
    """
    GIVEN application state without a valid action registry
    WHEN GET /v1/actions is requested
    THEN the endpoint returns the stable INTERNAL_ERROR envelope
    """

    client.app.state.action_registry = registry_value

    response = client.get("/v1/actions", headers=auth_headers)

    body = response.json()

    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Action registry is not available."


def test_list_actions_filters_by_any_tags_by_default(actions_client, auth_headers):
    """
    GIVEN multiple requested tags without explicit match
    WHEN GET /v1/actions is called
    THEN actions matching any requested tag are returned
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="validation,defaults",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules
    assert _action_names(modules) == ["default_test", "range_test"]


def test_list_actions_filters_by_tags_match_any(actions_client, auth_headers):
    """
    GIVEN multiple requested tags and match any
    WHEN GET /v1/actions is called
    THEN actions matching at least one requested tag are returned
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="validation,defaults",
        match="any",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules
    assert _action_names(modules) == ["default_test", "range_test"]


def test_list_actions_filters_by_tags_match_all(actions_client, auth_headers):
    """
    GIVEN multiple requested tags and match all
    WHEN GET /v1/actions is called
    THEN only actions containing all requested tags are returned
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="test,validation",
        match="all",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules
    assert _action_names(modules) == ["range_test"]


def test_list_actions_tags_match_all_empty_intersection(actions_client, auth_headers):
    """
    GIVEN tags that are not all present on any one action
    WHEN GET /v1/actions is called with match all
    THEN no actions are returned
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="validation,defaults",
        match="all",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules == []


def test_list_actions_tags_csv_whitespace_is_normalized(actions_client, auth_headers):
    """
    GIVEN tags with surrounding CSV whitespace
    WHEN GET /v1/actions is called
    THEN tags are normalized before filtering
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags=" validation , defaults ",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules
    assert _action_names(modules) == ["default_test", "range_test"]


def test_list_actions_tags_csv_duplicates_are_deduplicated(
    actions_client, auth_headers
):
    """
    GIVEN duplicate tags in the query string
    WHEN GET /v1/actions is called
    THEN duplicate query tags do not change filtering behavior
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="validation,validation",
    )

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules
    assert _action_names(modules) == ["range_test"]

    for module in modules:
        for action in module["actions"]:
            assert "validation" in [tag.lower() for tag in action["tags"]]


def test_list_actions_filter_by_query(actions_client, auth_headers):
    """
    GIVEN actions with effective tags
    WHEN GET /v1/actions is requested with q=numeric
    THEN returned actions match action name, summary, description, or tags
    """

    response = _get_actions(actions_client, auth_headers, q="numeric")

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]
    assert modules

    for module in modules:
        for action in module["actions"]:
            assert (
                "numeric" in action["action"].lower()
                or (action["summary"] and "numeric" in action["summary"].lower())
                or (
                    action["description"] and "numeric" in action["description"].lower()
                )
                or any("numeric" in tag.lower() for tag in action["tags"])
            )


def test_list_actions_query_and_tags_combine_with_and(actions_client, auth_headers):
    """
    GIVEN q matches one action and tags match another action
    WHEN q and tags are provided
    THEN only actions matching both filters are returned
    """

    negative = _get_actions(
        actions_client,
        auth_headers,
        q="optional",
        tags="validation",
        match="any",
    )

    assert negative.status_code == 200
    assert negative.json()["data"]["modules"] == []

    positive = _get_actions(
        actions_client,
        auth_headers,
        q="optional",
        tags="defaults",
        match="all",
    )

    assert positive.status_code == 200
    modules = positive.json()["data"]["modules"]
    assert _action_names(modules) == ["default_test"]


def test_list_actions_rejects_invalid_match(actions_client, auth_headers):
    """
    GIVEN an unsupported match value
    WHEN GET /v1/actions is called
    THEN INVALID_PARAMS is returned
    """

    response = _get_actions(
        actions_client,
        auth_headers,
        tags="validation",
        match="invalid",
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"


def test_list_actions_rejects_match_without_tags(actions_client, auth_headers):
    """
    GIVEN match without tags
    WHEN GET /v1/actions is called
    THEN INVALID_PARAMS is returned
    """

    response = _get_actions(actions_client, auth_headers, match="all")

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"


@pytest.mark.parametrize(
    "tags",
    [
        "",
        ",validation",
        "validation,,defaults",
        "BadTag",
        "bad tag",
        "bad/tag",
    ],
    ids=[
        "empty",
        "leading_empty_entry",
        "middle_empty_entry",
        "uppercase",
        "contains_space",
        "contains_slash",
    ],
)
def test_list_actions_rejects_invalid_tags(actions_client, auth_headers, tags):
    """
    GIVEN invalid tags query values
    WHEN GET /v1/actions is called
    THEN INVALID_PARAMS is returned
    """

    response = _get_actions(actions_client, auth_headers, tags=tags)

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"


def test_list_actions_no_matches(actions_client, auth_headers):
    """
    GIVEN a query that does not match any action fields
    WHEN GET /v1/actions is requested with q=nonexistent
    THEN modules is returned as an empty list
    """

    response = _get_actions(actions_client, auth_headers, q="nonexistent")

    assert response.status_code == 200

    modules = response.json()["data"]["modules"]

    assert modules == []


def test_list_actions_invalid_param(actions_client, auth_headers):
    """
    GIVEN a query parameter containing a NUL byte
    WHEN GET /v1/actions is requested
    THEN the endpoint rejects the request with INVALID_PARAMS
    """

    response = _get_actions(actions_client, auth_headers, q="\x00")

    assert response.status_code == 400

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"
