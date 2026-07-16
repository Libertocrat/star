"""Unit tests for STAR runtime command executor.

This suite validates subprocess execution behavior, timeout handling,
error wrapping, and runtime execution metadata.
"""

from __future__ import annotations

import asyncio
import signal

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
from star.actions.runtime import executor as executor_module
from star.actions.runtime.executor import execute_command


class _FakeAsyncProcess:
    """Controllable async subprocess stand-in for cleanup tests.

    Attributes:
        pid: Synthetic process identifier used by process-group signaling.
        returncode: Process exit status set by the test-controlled signal path.
        communicated: Whether stdout and stderr were drained.
        exit_event: Event that releases `wait()`.
        communicate_event: Event that releases `communicate()`.
    """

    pid = 4242

    def __init__(self) -> None:
        """Initialize pending fake process state."""

        self.returncode: int | None = None
        self.communicated = False
        self.communicate_input: bytes | None = None
        self.exit_event = asyncio.Event()
        self.communicate_event = asyncio.Event()

    async def communicate(
        self, input: bytes | None = None
    ) -> tuple[bytes, bytes]:  # noqa: A002
        """Wait for test-controlled drain release and return output bytes.

        Args:
            input: Optional bytes written to stdin by the executor.

        Returns:
            Tuple containing stdout and stderr bytes.
        """

        self.communicate_input = input
        await self.communicate_event.wait()
        self.communicated = True
        return b"", b""

    async def wait(self) -> int:
        """Wait for test-controlled process exit and return the status code.

        Returns:
            Process return code.
        """

        if self.returncode is None:
            await self.exit_event.wait()
        return 0 if self.returncode is None else self.returncode

    def send_signal(self, sig: signal.Signals) -> None:
        """Receive direct-process signals on non-POSIX fallback paths.

        Args:
            sig: Signal delivered by the executor.
        """

        if sig == signal.SIGKILL:
            self.returncode = -int(sig)
            self.exit_event.set()
            self.communicate_event.set()


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
# Preconditions
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


@pytest.mark.asyncio
async def test_execute_command__rejects_non_bytes_stdin_data():
    """
    GIVEN stdin_data with a non-bytes value
    WHEN execute_command is called
    THEN TypeError is raised before subprocess creation
    """

    with pytest.raises(TypeError, match="stdin_data must be bytes"):
        await execute_command(["cat"], _make_spec(), stdin_data="secret")


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
# Policy Validation
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
# Successful Execution
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


@pytest.mark.asyncio
async def test_execute_command__writes_stdin_data_to_process():
    """
    GIVEN a command that reads from stdin
    WHEN execute_command receives stdin_data
    THEN the process receives those bytes and returns them on stdout
    """

    result = await execute_command(["cat"], _make_spec(), stdin_data=b"hello secret")

    assert result.returncode == 0
    assert result.stdout == b"hello secret"


# ============================================================================
# Non-Zero Exit
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
# Timeout Handling
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


@pytest.mark.asyncio
async def test_execute_command__timeout_terminates_process_group(monkeypatch):
    """
    GIVEN a subprocess that ignores graceful termination
    WHEN execute_command times out
    THEN the owned process group is terminated, killed, and drained
    """

    fake_proc = _FakeAsyncProcess()
    signals_sent: list[signal.Signals] = []

    async def _fake_spawn(*_args, **kwargs):
        """Return a controlled subprocess and assert POSIX session isolation."""
        assert kwargs["start_new_session"] is True
        return fake_proc

    def _fake_killpg(pid: int, sig: signal.Signals) -> None:
        """Capture process-group signals and release the fake on SIGKILL."""
        assert pid == fake_proc.pid
        signals_sent.append(sig)
        if sig == signal.SIGKILL:
            fake_proc.returncode = -int(sig)
            fake_proc.exit_event.set()
            fake_proc.communicate_event.set()

    monkeypatch.setattr(executor_module, "_SUPPORTS_PROCESS_GROUPS", True)
    monkeypatch.setattr(executor_module, "_TERMINATION_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(executor_module.asyncio, "create_subprocess_exec", _fake_spawn)
    monkeypatch.setattr(executor_module.os, "killpg", _fake_killpg)

    with pytest.raises(ActionExecutionTimeoutError, match="timed out"):
        await execute_command(
            ["sleep", "1"],
            _make_spec(),
            timeout=0.001,
            stdin_data=b"secret",
        )

    assert signals_sent == [signal.SIGTERM, signal.SIGKILL]
    assert fake_proc.communicated is True
    assert fake_proc.communicate_input == b"secret"


@pytest.mark.asyncio
async def test_execute_command__cancellation_terminates_process_group(monkeypatch):
    """
    GIVEN a running subprocess
    WHEN execute_command is cancelled
    THEN cleanup completes before CancelledError propagates
    """

    fake_proc = _FakeAsyncProcess()
    signals_sent: list[signal.Signals] = []

    async def _fake_spawn(*_args, **kwargs):
        """Return a controlled subprocess and assert POSIX session isolation."""
        assert kwargs["start_new_session"] is True
        return fake_proc

    def _fake_killpg(pid: int, sig: signal.Signals) -> None:
        """Capture process-group signals and release the fake on SIGKILL."""
        assert pid == fake_proc.pid
        signals_sent.append(sig)
        if sig == signal.SIGKILL:
            fake_proc.returncode = -int(sig)
            fake_proc.exit_event.set()
            fake_proc.communicate_event.set()

    monkeypatch.setattr(executor_module, "_SUPPORTS_PROCESS_GROUPS", True)
    monkeypatch.setattr(executor_module, "_TERMINATION_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(executor_module.asyncio, "create_subprocess_exec", _fake_spawn)
    monkeypatch.setattr(executor_module.os, "killpg", _fake_killpg)

    task = asyncio.create_task(
        execute_command(["sleep", "1"], _make_spec(), stdin_data=b"secret")
    )
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert signals_sent == [signal.SIGTERM, signal.SIGKILL]
    assert fake_proc.communicated is True
    assert fake_proc.communicate_input == b"secret"


# ============================================================================
# Spawn Failures
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
# Execution Metadata
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
# Stdout / Stderr Contract
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
# Edge Cases
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
# Parametrized Valid Executions
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
