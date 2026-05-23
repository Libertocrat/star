"""Async subprocess executor for STAR runtime-rendered commands.

This module executes fully rendered and validated argv commands produced by
the runtime renderer. It is a pure execution boundary and does not interpret
DSL, mutate argv, sanitize output, or perform telemetry side effects.
"""

from __future__ import annotations

import asyncio
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


async def execute_command(
    argv: list[str],
    spec: ActionSpec,
    timeout: float | None = None,  # noqa: ASYNC109
) -> ActionExecutionResult:
    """Execute a validated command using an async subprocess.

    Args:
        argv: Fully resolved command arguments.
        timeout: Optional timeout in seconds.

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

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pid = proc.pid

        if timeout is None:
            stdout, stderr = await proc.communicate()
        else:
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.communicate()
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
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ActionRuntimeExecError(f"Failed to execute command: {argv[0]}") from exc
    except Exception as exc:
        raise ActionRuntimeExecError(
            f"Unexpected failure during command execution: {argv[0]}"
        ) from exc
