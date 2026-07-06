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
from pydantic import ValidationError

from star.actions.dispatcher import DispatchedActionResult, dispatch_action
from star.actions.exceptions import ActionBinaryBlockedError, ActionNotFoundError
from star.actions.models import ActionExecutionResult
from star.actions.runtime.file_manager import (
    cleanup_output_placeholders as cleanup_real_output_placeholders,
)
from star.core.utils.file_storage import load_file_metadata

# ============================================================================
# Runtime Dispatch
# ============================================================================


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

    async def _fake_execute(argv, spec, timeout=None):  # noqa: ASYNC109
        """Capture executor inputs and return deterministic success result."""
        captured["argv"] = argv
        captured["spec_name"] = spec.name
        captured["timeout"] = timeout
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

    async def _fake_execute(_argv, _spec, timeout=None):  # noqa: ASYNC109
        """Capture the timeout and return deterministic success result."""
        captured["timeout"] = timeout
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

    async def _raise_blocked(_argv, _spec, timeout=None):  # noqa: ASYNC109
        """Raise a deterministic blocked-binary runtime error for tests."""
        del timeout
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

    async def _raise_cancelled(argv, _spec, timeout=None):  # noqa: ASYNC109
        """Raise cancellation after dispatcher has rendered the command."""
        captured["argv"] = argv
        del timeout
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
