"""
Integration tests for the POST /v1/actions/{action_id} endpoint.

These tests validate the HTTP contract and wiring of the execute route.
They ensure that requests are validated, delegated to the dispatcher,
and that responses follow the ResponseEnvelope contract.

They do NOT test dispatcher internals or action business logic.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, SecretStr

from star.actions.exceptions import (
    ActionBinaryBlockedError,
    ActionBinaryNotAllowedError,
    ActionBinaryPathForbiddenError,
    ActionInvocationInputStateError,
    ActionInvocationIntegrityError,
    ActionInvocationParamsError,
    ActionRuntimeExecError,
)
from star.actions.models import ActionExecutionResult
from star.actions.runtime.file_manager import create_ready_file_from_bytes
from star.core.errors import StarError
from star.core.files import get_secret_tmp_dir
from star.routes.actions.handlers.execute_action import execute_action_handler
from star.routes.actions.schemas import ExecuteActionRequest

# ============================================================================
# Request Validation
# ============================================================================


def test_execute_rejects_invalid_payload(client, auth_headers):
    """
    GIVEN an invalid execute request payload
    WHEN the endpoint is called with an invalid body
    THEN it returns HTTP 422 due to request validation failure
    """
    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": ["invalid"]},
    )

    assert response.status_code == 422


# ============================================================================
# Success Cases
# ============================================================================


def test_execute_returns_success_envelope_for_valid_action(
    client, auth_headers, valid_registry
):
    """
    GIVEN a valid execute request for a registered action
    WHEN the endpoint is called
    THEN it returns HTTP 200 with a success ResponseEnvelope
    """

    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.repeat"
    payload = {
        "params": {"count": 5},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    assert response.status_code == 200

    body = response.json()
    assert isinstance(body, dict)
    assert body["success"] is True
    assert body["data"]["stdout"].splitlines() == ["1", "2", "3", "4", "5"]
    assert body["error"] is None
    assert body["data"]["exit_code"] == 0
    assert "stdout" in body["data"]
    assert "stdout_encoding" in body["data"]


def test_execute_uses_default_param_value(client, auth_headers, valid_registry):
    """
    GIVEN an action with a default parameter value
    WHEN no parameter is provided
    THEN the default value is used in execution
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.default_test"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["stdout"].splitlines() == ["1", "2", "3", "4", "5"]


@pytest.mark.asyncio
async def test_execute_handler_secret_file_delivery_omits_secret_and_path(
    valid_registry,
    settings,
):
    """Keep file-delivered secrets and temporary paths out of public results.

    GIVEN the execute handler receives a reviewed encryption action
    WHEN it encrypts a managed input using a file-delivered secret
    THEN the public result omits the secret and temporary path
    AND the temporary secret file is cleaned up
    """

    source_file = create_ready_file_from_bytes(
        original_filename="plaintext.txt",
        content=b"confidential input\n",
        extension=".txt",
        mime_type="text/plain",
        settings=settings,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                action_registry=valid_registry,
                settings=settings,
            )
        )
    )
    payload = ExecuteActionRequest(
        params={"input_file": str(source_file.id), "password": "topsecret"}
    )

    data = await execute_action_handler(
        request,
        "test_runtime.encrypt_secret",
        payload,
    )

    assert data.exit_code == 0
    assert data.outputs is not None
    assert data.outputs["encrypted_file"] is not None
    assert "topsecret" not in repr(data)
    assert str(get_secret_tmp_dir(settings)) not in data.stdout
    assert list(get_secret_tmp_dir(settings).glob("secret_*.tmp")) == []


# ============================================================================
# Domain Errors
# ============================================================================


def test_execute_returns_error_envelope_for_unknown_action(
    client,
    auth_headers,
):
    """
    GIVEN an execute request for an unknown action
    WHEN the endpoint is called
    THEN it returns a stable error ResponseEnvelope
    """
    action_id = "non_existent_action"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    from star.core.errors import ACTION_NOT_FOUND

    assert response.status_code == ACTION_NOT_FOUND.http_status

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == ACTION_NOT_FOUND.code
    assert "message" in body["error"]


@pytest.mark.parametrize(
    "registry_value",
    [None, object()],
    ids=["none", "wrong_type"],
)
def test_execute_returns_internal_error_when_registry_is_invalid(
    client,
    auth_headers,
    registry_value,
):
    """
    GIVEN application state without a valid action registry
    WHEN POST /v1/actions/{action_id} is requested
    THEN the endpoint returns the stable INTERNAL_ERROR envelope
    """

    client.app.state.action_registry = registry_value

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Action registry is not available."


@pytest.mark.parametrize(
    "settings_value",
    [None, object()],
    ids=["none", "wrong_type"],
)
def test_execute_returns_internal_error_when_runtime_settings_are_invalid(
    client,
    auth_headers,
    valid_registry,
    settings_value,
):
    """
    GIVEN application state without valid runtime settings
    WHEN POST /v1/actions/{action_id} is requested
    THEN the endpoint returns the stable INTERNAL_ERROR envelope
    """

    client.app.state.action_registry = valid_registry
    client.app.state.settings = settings_value

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Runtime settings are not available."


def test_execute_omits_internal_error_reason_from_runtime_failure(
    client,
    auth_headers,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN action dispatch raises an internal runtime execution failure
    WHEN the endpoint maps the failure to INTERNAL_ERROR
    THEN raw diagnostic reason details are omitted from the public envelope
    """
    client.app.state.action_registry = valid_registry

    async def _raise_runtime_error(*_args, **_kwargs):
        """Raise a deterministic runtime execution failure."""

        raise ActionRuntimeExecError("raw runtime secret detail")

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise_runtime_error,
    )

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )
    body = response.json()

    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["details"] == {}
    assert "reason" not in body["error"]["details"]
    assert "raw runtime secret detail" not in response.text


def test_execute_maps_late_managed_input_state_to_invalid_params(
    client,
    auth_headers,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN a managed action input becomes unavailable after rendering
    WHEN the final runtime gate reports the late input-state failure
    THEN the endpoint returns the stable INVALID_PARAMS envelope
    """

    client.app.state.action_registry = valid_registry

    async def _raise_input_state_error(*_args, **_kwargs):
        """Raise a deterministic late managed-input failure."""

        raise ActionInvocationInputStateError(
            "Managed file input is no longer available."
        )

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise_input_state_error,
    )

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )
    body = response.json()

    assert response.status_code == 400
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INVALID_PARAMS"
    assert body["error"]["details"] == {
        "reason": "Managed file input is no longer available."
    }


def test_execute_maps_extension_policy_params_to_invalid_params(
    client,
    auth_headers,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN extension request values fail a compiled invocation requirement
    WHEN dispatch rejects them before rendering the command
    THEN the endpoint returns the stable INVALID_PARAMS envelope
    """

    client.app.state.action_registry = valid_registry

    async def _raise_params_error(*_args, **_kwargs):
        """Raise a deterministic extension parameter-policy failure."""

        raise ActionInvocationParamsError(
            "Extension invocation parameters do not satisfy the action policy."
        )

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise_params_error,
    )

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )
    body = response.json()

    assert response.status_code == 400
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INVALID_PARAMS"
    assert body["error"]["details"] == {
        "reason": "Extension invocation parameters do not satisfy the action policy."
    }


def test_execute_maps_invocation_integrity_to_internal_error(
    client,
    auth_headers,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN the final invocation verifier detects internal state corruption
    WHEN the action handler maps the integrity failure
    THEN it returns a safe INTERNAL_ERROR envelope without its raw reason
    """

    client.app.state.action_registry = valid_registry

    async def _raise_integrity_error(*_args, **_kwargs):
        """Raise a deterministic integrity failure with unsafe diagnostics."""

        raise ActionInvocationIntegrityError("unsafe internal argv detail")

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise_integrity_error,
    )

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )
    body = response.json()

    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["details"] == {}
    assert "unsafe internal argv detail" not in response.text


def test_execute_invalid_param_type_maps_to_invalid_params(
    client, auth_headers, valid_registry
):
    """
    GIVEN an action expecting an integer parameter
    WHEN a non-integer value is provided
    THEN the endpoint returns INVALID_PARAMS error
    AND Pydantic error sensitive fields are sanitized
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.repeat"
    payload = {
        "params": {"count": "not-an-int"},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert response.status_code == 400
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"

    error_details = body["error"]["details"]
    assert set(error_details) == {"errors"}
    assert error_details["errors"]
    for error in error_details["errors"]:
        assert set(error) <= {"type", "loc", "msg"}
        assert "input" not in error
        assert "ctx" not in error
        assert "url" not in error
    assert "not-an-int" not in response.text


def test_execute_missing_required_param_maps_to_invalid_params(
    client, auth_headers, valid_registry
):
    """
    GIVEN an action with a required parameter
    WHEN the parameter is omitted
    THEN the endpoint returns INVALID_PARAMS error
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.repeat"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert response.status_code == 400
    assert body["error"]["code"] == "INVALID_PARAMS"


def test_execute_renderer_error_maps_to_invalid_params(
    client, auth_headers, valid_registry
):
    """
    GIVEN an action receiving a None value
    WHEN the renderer processes the parameters
    THEN the endpoint returns INVALID_PARAMS error
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.repeat"
    payload = {
        "params": {"count": None},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert response.status_code == 400
    assert body["error"]["code"] == "INVALID_PARAMS"


def test_execute_out_of_range_param_maps_to_invalid_params(
    client, auth_headers, valid_registry
):
    """
    GIVEN an action with numeric constraints
    WHEN the value is outside the allowed range
    THEN the endpoint returns INVALID_PARAMS error
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.range_test"
    payload = {
        "params": {"value": 999},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert response.status_code == 400
    assert body["error"]["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_execute_handler_validation_error_omits_secret_input(
    monkeypatch,
    valid_registry,
    settings,
):
    """
    GIVEN dispatcher raises a Pydantic validation error containing secret input
    WHEN the execute handler maps the error
    THEN StarError details omit the rejected input value
    """

    class Params(BaseModel):
        """Params model used to raise a realistic validation error.

        Attributes:
            password: Secret password field.
        """

        password: SecretStr

    async def _raise_validation_error(*_args, **_kwargs):
        """Raise a validation error whose default details include secret input."""
        Params.model_validate({"password": ["topsecret"]})
        raise AssertionError("validation should have failed")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                action_registry=valid_registry,
                settings=settings,
            )
        )
    )

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise_validation_error,
    )

    with pytest.raises(StarError) as exc_info:
        await execute_action_handler(
            request,
            "test_runtime.ping",
            ExecuteActionRequest(params={}),
        )

    error = exc_info.value
    assert error.code == "INVALID_PARAMS"
    assert "topsecret" not in repr(error.details)
    assert "input" not in error.details["errors"][0]


@pytest.mark.parametrize(
    ("raised_error", "test_id"),
    [
        (ActionBinaryBlockedError("blocked"), "blocked_binary"),
        (ActionBinaryNotAllowedError("not_allowed"), "not_allowed_binary"),
        (ActionBinaryPathForbiddenError("path_forbidden"), "path_like_binary"),
    ],
    ids=["blocked_binary", "not_allowed_binary", "path_like_binary"],
)
def test_execute_binary_policy_errors_map_to_permission_denied(
    client,
    auth_headers,
    monkeypatch,
    raised_error,
    test_id,
):
    """
    GIVEN dispatcher raises a binary-policy runtime error
    WHEN the endpoint is called
    THEN endpoint maps error to PERMISSION_DENIED envelope
    """
    _ = test_id

    async def _raise(*_args, **_kwargs):
        """Raise the parametrized dispatcher error for mapping tests."""
        raise raised_error

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _raise,
    )

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 403
    assert body["success"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"


# ============================================================================
# Response Contract
# ============================================================================


def test_execute_output_encoding_fields_present(client, auth_headers, valid_registry):
    """
    GIVEN a successful execution
    WHEN the response is returned
    THEN encoding metadata is included in the output
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.ping"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert body["data"]["stdout_encoding"] in ("utf-8", "base64")
    assert body["data"]["stderr_encoding"] in ("utf-8", "base64")


def test_execute_stderr_fields_always_present(client, auth_headers, valid_registry):
    """
    GIVEN a successful execution
    WHEN the response is returned
    THEN stderr fields are always present
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.ping"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert "stderr" in body["data"]
    assert "stderr_encoding" in body["data"]


def test_execute_response_envelope_contract(client, auth_headers, valid_registry):
    """
    GIVEN a valid execution request
    WHEN the response is returned
    THEN it follows the ResponseEnvelope contract
    """
    client.app.state.action_registry = valid_registry

    action_id = "test_runtime.ping"
    payload = {
        "params": {},
    }

    response = client.post(
        f"/v1/actions/{action_id}",
        headers=auth_headers,
        json=payload,
    )

    body = response.json()

    assert set(body.keys()) == {"success", "data", "error"}


# ============================================================================
# Outputs Integration
# ============================================================================


def test_execute__returns_file_command_output(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN action with file+command output
    WHEN the endpoint is called
    THEN response contains outputs metadata for command output
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["outputs"] is not None
    assert body["data"]["outputs"]["cmd_out"] is not None
    assert "id" in body["data"]["outputs"]["cmd_out"]


def test_execute__file_command_output_is_ready(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN successful execution
    WHEN the endpoint returns
    THEN command output file status is ready
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 200
    assert body["data"]["outputs"]["cmd_out"]["status"] == "ready"


def test_execute__returns_file_stdout_output(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN an action that allows stdout_as_file
    WHEN the endpoint is called with stdout_as_file enabled
    THEN response contains stdout-derived output metadata
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}, "stdout_as_file": True},
    )

    body = response.json()

    assert response.status_code == 200
    assert body["data"]["outputs"] is not None
    assert body["data"]["outputs"]["stdout_file"] is not None


def test_execute__omits_stdout_file_when_not_requested(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN an action that allows stdout file materialization
    WHEN the endpoint is called without stdout_as_file
    THEN no stdout_file output is returned
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 200
    assert body["data"]["outputs"] is None


def test_execute__stdout_file_contains_stdout(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN stdout output action
    WHEN the endpoint returns outputs
    THEN output blob content matches stdout bytes
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.ping",
        headers=auth_headers,
        json={"params": {}, "stdout_as_file": True},
    )
    body = response.json()
    output_id = body["data"]["outputs"]["stdout_file"]["id"]

    content_response = client.get(
        f"/v1/files/{output_id}/content", headers=auth_headers
    )

    assert response.status_code == 200
    assert content_response.status_code == 200
    assert content_response.content == body["data"]["stdout"].encode("utf-8")


def test_execute__command_failure_returns_null_output(
    client,
    auth_headers,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN command failure action
    WHEN the endpoint is called
    THEN command output is returned as null
    """

    client.app.state.action_registry = valid_registry

    async def _nonzero_output(*_args, **_kwargs):
        """Return a completed nonzero command result without spawning."""

        return ActionExecutionResult(
            returncode=1,
            stdout=b"",
            stderr=b"failed",
            exec_time=0.01,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _nonzero_output,
    )

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}},
    )

    body = response.json()

    assert response.status_code == 200
    assert body["data"]["exit_code"] != 0
    assert body["data"]["outputs"]["cmd_out"] is None


def test_execute__command_failure_cleans_up_files(
    client,
    auth_headers,
    settings,
    valid_registry,
    monkeypatch,
):
    """
    GIVEN command failure action
    WHEN the endpoint is called
    THEN no placeholder metadata files remain after cleanup
    """

    client.app.state.action_registry = valid_registry

    async def _nonzero_output(*_args, **_kwargs):
        """Return a completed nonzero command result without spawning."""

        return ActionExecutionResult(
            returncode=1,
            stdout=b"",
            stderr=b"failed",
            exec_time=0.01,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _nonzero_output,
    )

    meta_dir = Path(settings.star_root_dir) / "data" / "files" / "meta"
    before_count = len(list(meta_dir.glob("file_*.json"))) if meta_dir.exists() else 0

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}},
    )

    after_count = len(list(meta_dir.glob("file_*.json"))) if meta_dir.exists() else 0

    assert response.status_code == 200
    assert after_count == before_count


def test_execute__multiple_outputs_are_returned(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN action declaring command and stdout file outputs
    WHEN the endpoint is called
    THEN both outputs are present in response payload
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}, "stdout_as_file": True},
    )

    body = response.json()
    outputs = body["data"]["outputs"]

    assert response.status_code == 200
    assert outputs is not None
    assert "cmd_out" in outputs
    assert "stdout_file" in outputs


def test_execute__output_order_is_preserved(
    client,
    auth_headers,
    valid_registry,
):
    """
    GIVEN action with multiple declared outputs
    WHEN the endpoint returns outputs
    THEN output key order matches DSL declaration order
    """

    client.app.state.action_registry = valid_registry

    response = client.post(
        "/v1/actions/test_runtime.write_output",
        headers=auth_headers,
        json={"params": {}, "stdout_as_file": True},
    )

    body = response.json()

    assert response.status_code == 200
    assert list(body["data"]["outputs"].keys()) == ["cmd_out", "stdout_file"]


def test_execute_action_rejects_stdout_as_file_when_action_disallows_it(
    client,
    auth_headers,
    monkeypatch,
    valid_registry,
):
    """
    GIVEN an action with allow_stdout_as_file disabled
    WHEN the client requests stdout_as_file
    THEN the handler returns INVALID_PARAMS without executing the action
    """

    client.app.state.action_registry = valid_registry

    async def _unexpected_dispatch(*_args, **_kwargs):
        """Fail test if execution dispatch is reached unexpectedly."""

        raise AssertionError("dispatch_action should not be called")

    monkeypatch.setattr(
        "star.routes.actions.handlers.execute_action.dispatch_action",
        _unexpected_dispatch,
    )

    response = client.post(
        "/v1/actions/test_runtime.no_stdout_file",
        headers=auth_headers,
        json={"params": {}, "stdout_as_file": True},
    )

    body = response.json()

    assert response.status_code == 400
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMS"
    assert body["error"]["details"]["reason"] == "Action does not allow stdout_as_file."
