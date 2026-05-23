"""
Unit tests for the STAR runtime dispatcher.

These tests freeze dispatcher invariants for the DSL runtime architecture:
- action resolution through immutable registry
- strict params validation through generated params models
- runtime result contract preserving rendered and execution states
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from star.actions.dispatcher import DispatchedActionResult, dispatch_action
from star.actions.exceptions import ActionBinaryBlockedError, ActionNotFoundError
from star.actions.models import ActionExecutionResult

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

    async def _fake_execute(argv, spec):
        """Capture executor inputs and return deterministic success result."""
        captured["argv"] = argv
        captured["spec_name"] = spec.name
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

    async def _raise_blocked(_argv, _spec):
        """Raise a deterministic blocked-binary runtime error for tests."""
        raise ActionBinaryBlockedError("blocked")

    monkeypatch.setattr(
        "star.actions.dispatcher.runtime_executor.execute_command",
        _raise_blocked,
    )

    with pytest.raises(ActionBinaryBlockedError, match="blocked"):
        await dispatch_action(valid_registry, "test_runtime.ping", {})
