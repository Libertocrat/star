"""
Unit tests for the STAR runtime dispatcher.

These tests freeze dispatcher invariants for the DSL runtime architecture:
- action resolution through immutable registry
- strict params validation through generated params models
- runtime result contract preserving rendered and execution states
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from star.actions.dispatcher import DispatchedActionResult, dispatch_action
from star.actions.exceptions import (
    ActionBinaryBlockedError,
    ActionExecutionTimeoutError,
    ActionNotFoundError,
    ActionRuntimeExecError,
)
from star.actions.models import (
    ActionExecutionResult,
    ActionSpec,
    ArgDef,
    ParamType,
)
from star.actions.models.core import SecretDelivery
from star.actions.models.security import BinaryPolicy
from star.actions.registry import ActionRegistry
from star.actions.runtime.file_manager import (
    cleanup_output_placeholders as cleanup_real_output_placeholders,
)
from star.core.utils.file_storage import get_secret_tmp_dir, load_file_metadata

# ============================================================================
# Runtime Dispatch
# ============================================================================


def _make_file_secret_spec() -> ActionSpec:
    """Build an action spec with one file-delivered secret param.

    Returns:
        ActionSpec that renders a secret as a temporary file argv reference.
    """

    class Params(BaseModel):
        """Params model with a sensitive password field.

        Attributes:
            password: Secret password consumed by file delivery.
        """

        password: SecretStr

    return ActionSpec(
        name="secret_runtime.file_secret",
        namespace=(),
        module="secret_runtime",
        action="file_secret",
        version=1,
        params_model=Params,
        binary="cat",
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "const", "value": "file:{password}"},
        ),
        execution_policy=BinaryPolicy(allowed=("cat",), blocked=()),
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file"),
                description="password",
            )
        },
        flag_defs={},
        defaults={},
    )


@pytest.mark.asyncio
async def test_dispatch_action_success(valid_registry):
    """
    GIVEN a valid registry and a known action without required params
    WHEN dispatch_action is called
    THEN a DispatchedActionResult with process outputs is returned
    """
    result = await dispatch_action(
        valid_registry,
        "test_runtime.ping",
        {},
    )

    assert isinstance(result, DispatchedActionResult)
    assert isinstance(result.execution, ActionExecutionResult)
    assert result.execution.returncode == 0
    assert isinstance(result.execution.stdout, bytes)
    assert b"hello" in result.execution.stdout


@pytest.mark.asyncio
async def test_dispatch_action_unknown_raises(valid_registry):
    """
    GIVEN a valid registry
    WHEN dispatch_action is called with an unknown action name
    THEN ActionNotFoundError is raised
    """
    with pytest.raises(ActionNotFoundError):
        await dispatch_action(valid_registry, "test_runtime.unknown", {})


@pytest.mark.asyncio
async def test_dispatch_action_invalid_params(valid_registry):
    """
    GIVEN a known action with a required integer argument
    WHEN dispatch_action receives params with an invalid type
    THEN a Pydantic ValidationError is raised
    """
    with pytest.raises(ValidationError):
        await dispatch_action(
            valid_registry,
            "test_runtime.repeat",
            {"count": "not-an-int"},
        )


@pytest.mark.asyncio
async def test_dispatch_action_passes_spec_to_executor(valid_registry, monkeypatch):
    """
    GIVEN a known action and dispatcher runtime flow
    WHEN dispatch_action calls the executor
    THEN executor receives both argv and resolved ActionSpec
    """

    captured: dict[str, object] = {}

    async def _fake_execute(
        argv,
        spec,
        timeout=None,  # noqa: ASYNC109
        stdin_data=None,
    ):
        """Capture executor inputs and return deterministic success result."""
        captured["argv"] = argv
        captured["spec_name"] = spec.name
        captured["timeout"] = timeout
        captured["stdin_data"] = stdin_data
        return ActionExecutionResult(
            returncode=0,
            stdout=b"ok",
            stderr=b"",
            exec_time=0.001,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    await dispatch_action(valid_registry, "test_runtime.ping", {})

    assert captured["argv"] == ["echo", "hello"]
    assert captured["spec_name"] == "test_runtime.ping"
    assert captured["timeout"] is None
    assert captured["stdin_data"] is None


@pytest.mark.asyncio
async def test_dispatch_action_passes_secret_stdin_data_to_executor(monkeypatch):
    """
    GIVEN a registered action with a stdin-delivered secret param
    WHEN dispatch_action calls the executor
    THEN executor receives clean argv plus secret stdin bytes
    """

    class Params(BaseModel):
        """Params model with a sensitive password field.

        Attributes:
            password: Secret password consumed by stdin delivery.
        """

        password: SecretStr

    spec = ActionSpec(
        name="secret_runtime.echo_secret",
        namespace=(),
        module="secret_runtime",
        action="echo_secret",
        version=1,
        params_model=Params,
        binary="cat",
        command_template=({"kind": "binary", "value": "cat"},),
        execution_policy=BinaryPolicy(allowed=("cat",), blocked=()),
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="stdin", append_newline=True),
                description="password",
            )
        },
        flag_defs={},
        defaults={},
    )
    registry = ActionRegistry({"secret_runtime.echo_secret": spec}, [])
    captured: dict[str, object] = {}

    async def _fake_execute(
        argv,
        _spec,
        timeout=None,  # noqa: ASYNC109
        stdin_data=None,
    ):
        """Capture executor inputs and return deterministic success result."""
        captured["argv"] = argv
        captured["timeout"] = timeout
        captured["stdin_data"] = stdin_data
        return ActionExecutionResult(
            returncode=0,
            stdout=b"ok",
            stderr=b"",
            exec_time=0.001,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    await dispatch_action(
        registry,
        "secret_runtime.echo_secret",
        {"password": "topsecret"},
    )

    assert captured["argv"] == ["cat"]
    assert captured["stdin_data"] == b"topsecret\n"


@pytest.mark.asyncio
async def test_dispatch_action_cleans_file_secret_after_success(
    monkeypatch,
    settings,
):
    """
    GIVEN a registered action with a file-delivered secret param
    WHEN dispatch_action completes successfully
    THEN the temporary secret file is removed after executor returns
    """

    spec = _make_file_secret_spec()
    registry = ActionRegistry({"secret_runtime.file_secret": spec}, [])
    captured: dict[str, object] = {}

    async def _fake_execute(argv, _spec, **kwargs):
        """Verify the secret file exists during execution."""
        secret_ref = argv[1]
        secret_path = get_secret_tmp_dir(settings) / secret_ref.removeprefix("file:")
        captured["secret_path"] = secret_path
        captured["stdin_data"] = kwargs.get("stdin_data")
        assert secret_path.exists()
        assert secret_path.read_text(encoding="utf-8") == "topsecret"
        return ActionExecutionResult(
            returncode=0,
            stdout=b"ok",
            stderr=b"",
            exec_time=0.001,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    await dispatch_action(
        registry,
        "secret_runtime.file_secret",
        {"password": "topsecret"},
        settings=settings,
    )

    secret_path = captured["secret_path"]
    assert captured["stdin_data"] is None
    assert not secret_path.exists()


@pytest.mark.asyncio
async def test_dispatch_action_cleans_file_secret_after_nonzero_exit(
    monkeypatch,
    settings,
):
    """
    GIVEN a file-delivered secret action whose process returns nonzero
    WHEN dispatch_action receives the execution result
    THEN the temporary secret file is still removed
    """

    spec = _make_file_secret_spec()
    registry = ActionRegistry({"secret_runtime.file_secret": spec}, [])
    captured: dict[str, object] = {}

    async def _fake_execute(argv, _spec, **_kwargs):
        """Return a deterministic nonzero result."""
        secret_ref = argv[1]
        secret_path = get_secret_tmp_dir(settings) / secret_ref.removeprefix("file:")
        captured["secret_path"] = secret_path
        assert secret_path.exists()
        return ActionExecutionResult(
            returncode=2,
            stdout=b"",
            stderr=b"failed",
            exec_time=0.001,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    result = await dispatch_action(
        registry,
        "secret_runtime.file_secret",
        {"password": "topsecret"},
        settings=settings,
    )

    secret_path = captured["secret_path"]
    assert result.execution.returncode == 2
    assert not secret_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "match"),
    [
        (ActionRuntimeExecError("failed"), "failed"),
        (ActionExecutionTimeoutError("timed out"), "timed out"),
    ],
    ids=("exec_error", "timeout"),
)
async def test_dispatch_action_cleans_file_secret_after_executor_error(
    monkeypatch,
    settings,
    raised,
    match,
):
    """
    GIVEN a file-delivered secret action whose executor raises
    WHEN dispatch_action propagates the runtime error
    THEN the temporary secret file is removed
    """

    spec = _make_file_secret_spec()
    registry = ActionRegistry({"secret_runtime.file_secret": spec}, [])
    captured: dict[str, object] = {}

    async def _fake_execute(argv, _spec, **_kwargs):
        """Raise after confirming the secret file exists."""
        secret_ref = argv[1]
        secret_path = get_secret_tmp_dir(settings) / secret_ref.removeprefix("file:")
        captured["secret_path"] = secret_path
        assert secret_path.exists()
        raise raised

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    with pytest.raises(type(raised), match=match):
        await dispatch_action(
            registry,
            "secret_runtime.file_secret",
            {"password": "topsecret"},
            settings=settings,
        )

    secret_path = captured["secret_path"]
    assert not secret_path.exists()


@pytest.mark.asyncio
async def test_dispatch_action_cleans_file_secret_after_cancellation(
    monkeypatch,
    settings,
):
    """
    GIVEN a file-delivered secret action whose executor is cancelled
    WHEN dispatch_action propagates cancellation
    THEN the temporary secret file is removed
    """

    spec = _make_file_secret_spec()
    registry = ActionRegistry({"secret_runtime.file_secret": spec}, [])
    captured: dict[str, object] = {}

    async def _fake_execute(argv, _spec, **_kwargs):
        """Raise cancellation after confirming the secret file exists."""
        secret_ref = argv[1]
        secret_path = get_secret_tmp_dir(settings) / secret_ref.removeprefix("file:")
        captured["secret_path"] = secret_path
        assert secret_path.exists()
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatch_action(
            registry,
            "secret_runtime.file_secret",
            {"password": "topsecret"},
            settings=settings,
        )

    secret_path = captured["secret_path"]
    assert not secret_path.exists()


@pytest.mark.asyncio
async def test_dispatch_action_passes_runtime_settings_timeout(
    valid_registry,
    monkeypatch,
    settings,
):
    """
    GIVEN runtime settings with a configured timeout
    WHEN dispatch_action calls the executor
    THEN executor receives the timeout in seconds
    """

    captured: dict[str, object] = {}

    async def _fake_execute(
        _argv,
        _spec,
        timeout=None,  # noqa: ASYNC109
        stdin_data=None,
    ):
        """Capture the timeout and return deterministic success result."""
        captured["timeout"] = timeout
        captured["stdin_data"] = stdin_data
        return ActionExecutionResult(
            returncode=0,
            stdout=b"ok",
            stderr=b"",
            exec_time=0.001,
            pid=123,
        )

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _fake_execute,
    )

    await dispatch_action(valid_registry, "test_runtime.ping", {}, settings=settings)

    assert captured["timeout"] == settings.star_timeout_ms / 1000.0
    assert captured["stdin_data"] is None


@pytest.mark.asyncio
async def test_dispatch_action_propagates_policy_runtime_errors(
    valid_registry,
    monkeypatch,
):
    """
    GIVEN the executor raises a binary-policy runtime error
    WHEN dispatch_action is called
    THEN the same exception propagates unchanged
    """

    async def _raise_blocked(
        _argv,
        _spec,
        timeout=None,  # noqa: ASYNC109
        stdin_data=None,
    ):
        """Raise a deterministic blocked-binary runtime error for tests."""
        del timeout
        del stdin_data
        raise ActionBinaryBlockedError("blocked")

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _raise_blocked,
    )

    with pytest.raises(ActionBinaryBlockedError, match="blocked"):
        await dispatch_action(valid_registry, "test_runtime.ping", {})


@pytest.mark.asyncio
async def test_dispatch_action_cleans_placeholders_when_cancelled(
    valid_registry,
    monkeypatch,
    settings,
):
    """
    GIVEN execution is cancelled after rendering a command output placeholder
    WHEN dispatch_action handles the cancellation
    THEN the rendered output placeholder is cleaned before propagation
    """

    captured: dict[str, object] = {}

    async def _raise_cancelled(
        argv,
        _spec,
        timeout=None,  # noqa: ASYNC109
        stdin_data=None,
    ):
        """Raise cancellation after dispatcher has rendered the command."""
        captured["argv"] = argv
        del timeout
        del stdin_data
        raise asyncio.CancelledError

    def _capture_cleanup(output_files, settings=None):
        """Capture cleanup inputs and delete created placeholder artifacts."""
        captured["output_files"] = output_files
        captured["settings"] = settings
        cleanup_real_output_placeholders(output_files, settings=settings)

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _raise_cancelled,
    )
    monkeypatch.setattr(
        "star.actions.dispatcher.cleanup_output_placeholders",
        _capture_cleanup,
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatch_action(
            valid_registry, "test_runtime.write_output", {}, settings=settings
        )

    output_files = captured["output_files"]
    argv = captured["argv"]
    assert output_files
    assert argv[0:3] == ["openssl", "rand", "-out"]
    assert argv[3].endswith(f"file_{output_files['cmd_out']}.bin")
    assert argv[4] == "16"
    assert set(output_files) == {"cmd_out"}
    assert load_file_metadata(output_files["cmd_out"], settings) is None
    assert captured["settings"] is settings
