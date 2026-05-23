"""Unit tests for STAR runtime command executor.

This suite validates subprocess execution behavior, timeout handling,
error wrapping, and runtime execution metadata.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from star.actions.exceptions import (
    ActionBinaryBlockedError,
    ActionBinaryNotAllowedError,
    ActionBinaryPathForbiddenError,
    ActionExecutionTimeoutError,
    ActionRuntimeExecError,
)
from star.actions.models.core import ActionSpec
from star.actions.models.security import BinaryPolicy
from star.actions.runtime.executor import execute_command


def _make_spec(
    *,
    allowed: tuple[str, ...] = (
        "echo",
        "cat",
        "sleep",
        "star_binary_that_does_not_exist_123456",
    ),
    blocked: tuple[str, ...] = (),
) -> ActionSpec:
    """Create a minimal ActionSpec configured for executor unit tests.

    Args:
        allowed: Allowed binary tuple for effective policy.
        blocked: Blocked binary tuple for effective policy.

    Returns:
        ActionSpec ready for execute_command tests.
    """

    return ActionSpec(
        name="test.exec",
        namespace=(),
        module="test",
        action="exec",
        version=1,
        params_model=BaseModel,
        binary="echo",
        command_template=({"kind": "binary", "value": "echo"},),
        execution_policy=BinaryPolicy(allowed=allowed, blocked=blocked),
        arg_defs={},
        flag_defs={},
        defaults={},
        authors=None,
        tags=(),
        summary=None,
        description=None,
        deprecated=False,
        params_example=None,
    )


# ============================================================================
# PRECONDITIONS
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__rejects_empty_argv():
    """
    GIVEN an empty argv list
    WHEN execute_command is called
    THEN ValueError is raised
    """

    with pytest.raises(ValueError, match="argv must not be empty"):
        await execute_command([], _make_spec())


@pytest.mark.parametrize(
    "argv",
    [
        [1],
        ["echo", 123],
        ["echo", None],
    ],
    ids=["int_only", "mixed_int", "none_value"],
)
@pytest.mark.asyncio
async def test_execute_command__rejects_non_string_argv(argv):
    """
    GIVEN argv containing non-string elements
    WHEN execute_command is called
    THEN TypeError is raised
    """

    with pytest.raises(TypeError, match="argv must contain only strings"):
        await execute_command(argv, _make_spec())


@pytest.mark.parametrize(
    "timeout",
    [0, -1],
    ids=["zero", "negative"],
)
@pytest.mark.asyncio
async def test_execute_command__rejects_invalid_timeout(
    timeout: float,  # noqa: ASYNC109
):
    """
    GIVEN an invalid timeout value
    WHEN execute_command is called
    THEN ValueError is raised
    """

    with pytest.raises(ValueError, match="timeout must be greater than 0"):
        await execute_command(["echo", "ok"], _make_spec(), timeout=timeout)


# ============================================================================
# POLICY VALIDATION
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__blocked_binary_raises():
    """
    GIVEN a blocked binary by effective policy
    WHEN execute_command is called
    THEN ActionBinaryBlockedError is raised
    """

    spec = _make_spec(allowed=("echo",), blocked=("echo",))

    with pytest.raises(ActionBinaryBlockedError, match="blocked"):
        await execute_command(["echo", "ok"], spec)


@pytest.mark.asyncio
async def test_execute_command__not_allowed_binary_raises():
    """
    GIVEN a binary outside the allowlist
    WHEN execute_command is called
    THEN ActionBinaryNotAllowedError is raised
    """

    spec = _make_spec(allowed=("cat",), blocked=())

    with pytest.raises(ActionBinaryNotAllowedError, match="not allowed"):
        await execute_command(["echo", "ok"], spec)


@pytest.mark.asyncio
async def test_execute_command__path_like_binary_raises():
    """
    GIVEN a path-like binary token
    WHEN execute_command is called
    THEN ActionBinaryPathForbiddenError is raised
    """

    spec = _make_spec(allowed=("echo", "bin/echo"), blocked=())

    with pytest.raises(ActionBinaryPathForbiddenError, match="forbidden"):
        await execute_command(["bin/echo", "ok"], spec)


# ============================================================================
# SUCCESSFUL EXECUTION
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__simple_success():
    """
    GIVEN a valid echo command
    WHEN execute_command is called
    THEN execution succeeds with expected output
    """

    result = await execute_command(["echo", "hello"], _make_spec())

    assert result.returncode == 0
    assert result.stdout == b"hello\n"


@pytest.mark.asyncio
async def test_execute_command__command_with_arguments():
    """
    GIVEN a command with multiple arguments
    WHEN execute_command is called
    THEN stdout reflects correct argument ordering
    """

    result = await execute_command(["echo", "alpha", "beta", "gamma"], _make_spec())

    assert result.returncode == 0
    assert result.stdout == b"alpha beta gamma\n"


# ============================================================================
# NON-ZERO EXIT
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__non_zero_exit_returns_result():
    """
    GIVEN a command that fails
    WHEN execute_command is called
    THEN result is returned without raising
    """

    result = await execute_command(
        ["cat", "/definitely/missing-star-file"], _make_spec()
    )

    assert result.returncode != 0
    assert isinstance(result.stderr, bytes)
    assert result.stderr != b""


# ============================================================================
# TIMEOUT HANDLING
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__timeout_raises_error():
    """
    GIVEN a long-running command
    WHEN timeout is exceeded
    THEN ActionExecutionTimeoutError is raised
    """

    with pytest.raises(ActionExecutionTimeoutError, match="timed out"):
        await execute_command(["sleep", "1"], _make_spec(), timeout=0.01)


@pytest.mark.asyncio
async def test_execute_command__timeout_error_message():
    """
    GIVEN a timeout error
    WHEN exception is raised
    THEN error message contains timeout value
    """

    with pytest.raises(ActionExecutionTimeoutError, match="0.01"):
        await execute_command(["sleep", "1"], _make_spec(), timeout=0.01)


# ============================================================================
# SPAWN FAILURES
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__binary_not_found():
    """
    GIVEN a non-existent binary
    WHEN execute_command is called
    THEN ActionRuntimeExecError is raised
    """

    with pytest.raises(ActionRuntimeExecError, match="Failed to execute command"):
        await execute_command(["star_binary_that_does_not_exist_123456"], _make_spec())


# ============================================================================
# EXECUTION METADATA
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__exec_time_is_positive():
    """
    GIVEN a successful execution
    WHEN execute_command is called
    THEN exec_time is greater than zero
    """

    result = await execute_command(["echo", "ok"], _make_spec())

    assert result.returncode == 0
    assert result.exec_time > 0


@pytest.mark.asyncio
async def test_execute_command__pid_is_set():
    """
    GIVEN a successful execution
    WHEN execute_command is called
    THEN pid is a valid integer
    """

    result = await execute_command(["echo", "ok"], _make_spec())

    assert result.returncode == 0
    assert isinstance(result.pid, int)
    assert result.pid > 0


# ============================================================================
# STDOUT / STDERR CONTRACT
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__stdout_is_bytes():
    """
    GIVEN a successful execution
    WHEN execute_command is called
    THEN stdout is bytes
    """

    result = await execute_command(["echo", "ok"], _make_spec())

    assert result.returncode == 0
    assert isinstance(result.stdout, bytes)


@pytest.mark.asyncio
async def test_execute_command__stderr_is_bytes():
    """
    GIVEN a failing command
    WHEN execute_command is called
    THEN stderr is bytes
    """

    result = await execute_command(
        ["cat", "/definitely/missing-star-file"], _make_spec()
    )

    assert isinstance(result.stderr, bytes)
    assert result.stderr != b""


# ============================================================================
# EDGE CASES
# ============================================================================


@pytest.mark.asyncio
async def test_execute_command__wraps_unexpected_errors(monkeypatch):
    """
    GIVEN an unexpected internal failure
    WHEN subprocess creation fails
    THEN ActionRuntimeExecError is raised
    """

    async def _boom(*_args, **_kwargs):
        """Raise a deterministic runtime error for monkeypatch coverage."""
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "star.actions.runtime.executor.asyncio.create_subprocess_exec",
        _boom,
    )

    with pytest.raises(
        ActionRuntimeExecError,
        match="Unexpected failure during command execution",
    ):
        await execute_command(["echo", "ok"], _make_spec())


# ============================================================================
# PARAMETRIZED VALID EXECUTIONS
# ============================================================================


@pytest.mark.parametrize(
    "argv",
    [
        ["echo", "hello"],
        ["echo", "star"],
    ],
    ids=["hello", "star"],
)
@pytest.mark.asyncio
async def test_execute_command__multiple_valid_commands(argv: list[str]):
    """
    GIVEN multiple valid commands
    WHEN execute_command is called
    THEN execution succeeds
    """

    result = await execute_command(argv, _make_spec())

    assert result.returncode == 0
