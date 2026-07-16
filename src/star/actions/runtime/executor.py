"""Async subprocess executor for STAR runtime-rendered commands.

This module executes fully rendered and validated argv commands produced by
the runtime renderer. It is a pure execution boundary and does not interpret
DSL, mutate argv, sanitize output, or perform telemetry side effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time

from star.actions.exceptions import (
    ActionBinaryBlockedError,
    ActionBinaryNotAllowedError,
    ActionBinaryPathForbiddenError,
    ActionExecutionTimeoutError,
    ActionRuntimeExecError,
)
from star.actions.models.core import ActionSpec
from star.actions.models.runtime import ActionExecutionResult
from star.actions.security.policy import (
    is_binary_allowed,
    is_binary_blocked,
    is_simple_binary_name,
)

_TERMINATION_GRACE_SECONDS = 0.2
_SUPPORTS_PROCESS_GROUPS = os.name == "posix"


async def execute_command(
    argv: list[str],
    spec: ActionSpec,
    timeout: float | None = None,  # noqa: ASYNC109
    stdin_data: bytes | None = None,
) -> ActionExecutionResult:
    """Execute a validated command using an async subprocess.

    Args:
        argv: Fully resolved command arguments.
        timeout: Optional timeout in seconds.
        stdin_data: Optional bytes to write to subprocess stdin.

    Returns:
        ActionExecutionResult containing process outputs.

    Raises:
        ValueError: If argv is empty or timeout is invalid.
        TypeError: If argv contains non-string values.
        ActionRuntimeExecError: If subprocess execution fails.
        ActionExecutionTimeoutError: If execution exceeds timeout.
    """

    if not argv:
        raise ValueError("argv must not be empty")

    for item in argv:
        if not isinstance(item, str):
            raise TypeError("argv must contain only strings")

    if stdin_data is not None and not isinstance(stdin_data, bytes):
        raise TypeError("stdin_data must be bytes")

    binary = argv[0]
    if not is_simple_binary_name(binary):
        raise ActionBinaryPathForbiddenError("Binary paths are forbidden")

    if is_binary_blocked(binary, spec.execution_policy):
        raise ActionBinaryBlockedError(
            f"Binary '{binary}' is blocked by execution policy"
        )

    if not is_binary_allowed(binary, spec.execution_policy):
        raise ActionBinaryNotAllowedError(
            f"Binary '{binary}' is not allowed by execution policy"
        )

    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be greater than 0")

    start = time.perf_counter()
    pid: int | None = None

    proc: asyncio.subprocess.Process | None = None
    communicate_task: asyncio.Task[tuple[bytes, bytes]] | None = None

    try:
        if _SUPPORTS_PROCESS_GROUPS:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin_data is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin_data is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        pid = proc.pid
        communicate_task = asyncio.create_task(proc.communicate(stdin_data))

        if timeout is None:
            stdout, stderr = await asyncio.shield(communicate_task)
        else:
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                await asyncio.shield(_terminate_process(proc, communicate_task))
                raise ActionExecutionTimeoutError(
                    f"Command execution timed out after {timeout} seconds"
                ) from exc

        end = time.perf_counter()
        exec_time = end - start
        returncode = proc.returncode
        if returncode is None:
            raise ActionRuntimeExecError(f"Failed to execute command: {argv[0]}")

        return ActionExecutionResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            exec_time=exec_time,
            pid=pid,
        )

    except (
        ActionExecutionTimeoutError,
        ActionBinaryBlockedError,
        ActionBinaryNotAllowedError,
        ActionBinaryPathForbiddenError,
    ):
        raise
    except asyncio.CancelledError:
        if proc is not None and communicate_task is not None:
            await asyncio.shield(_terminate_process(proc, communicate_task))
        raise
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ActionRuntimeExecError(f"Failed to execute command: {argv[0]}") from exc
    except Exception as exc:
        raise ActionRuntimeExecError(
            f"Unexpected failure during command execution: {argv[0]}"
        ) from exc


async def _terminate_process(
    proc: asyncio.subprocess.Process,
    communicate_task: asyncio.Task[tuple[bytes, bytes]],
) -> None:
    """Terminate and reap a subprocess owned by one action invocation.

    Args:
        proc: Async subprocess process to terminate.
        communicate_task: Task draining stdout and stderr for the process.
    """

    if proc.returncode is None:
        _request_process_termination(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATION_GRACE_SECONDS)
        except asyncio.TimeoutError:
            _force_process_kill(proc)
            await proc.wait()

    if not communicate_task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await communicate_task
    else:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            communicate_task.result()


def _request_process_termination(proc: asyncio.subprocess.Process) -> None:
    """Request graceful termination for the owned process.

    Args:
        proc: Process receiving the termination request.
    """

    with contextlib.suppress(ProcessLookupError):
        if _SUPPORTS_PROCESS_GROUPS:
            os.killpg(proc.pid, signal.SIGTERM)
            return

        proc.terminate()


def _force_process_kill(proc: asyncio.subprocess.Process) -> None:
    """Force-kill the owned process after graceful termination fails.

    Args:
        proc: Process receiving the kill request.
    """

    with contextlib.suppress(ProcessLookupError):
        if _SUPPORTS_PROCESS_GROUPS:
            os.killpg(proc.pid, signal.SIGKILL)
            return

        proc.kill()
