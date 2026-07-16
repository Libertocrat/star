"""Route handler for STAR `POST /v1/actions/{action_id}` execution."""

from __future__ import annotations

import base64
from typing import Literal

from fastapi import Request
from pydantic import ValidationError

from star.actions.dispatcher import dispatch_action
from star.actions.exceptions import (
    ActionBinaryBlockedError,
    ActionBinaryNotAllowedError,
    ActionBinaryPathForbiddenError,
    ActionExecutionTimeoutError,
    ActionInvalidArgError,
    ActionNotFoundError,
    ActionRuntimeExecError,
    ActionRuntimeOutputError,
    ActionRuntimeRenderError,
)
from star.actions.registry import ActionRegistry
from star.actions.runtime.outputs_builder import build_outputs
from star.actions.runtime.sanitizer import (
    DEFAULT_MAX_STDERR_BYTES,
    DEFAULT_MAX_STDOUT_BYTES,
    transform_output,
)
from star.core.errors import (
    ACTION_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PERMISSION_DENIED,
    TIMEOUT,
    StarError,
)
from star.routes.actions.schemas import ExecuteActionData, ExecuteActionRequest
from star.routes.dependencies import get_action_registry, get_runtime_settings


def _encode_output(data: bytes) -> tuple[str, Literal["utf-8", "base64"]]:
    """Encode process output bytes for JSON transport.

    Args:
        data: Raw process output bytes.

    Returns:
        Tuple containing encoded output and encoding label.
    """

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii"), "base64"


def _validate_stdout_as_file_option(
    registry: ActionRegistry,
    action_id: str,
    stdout_as_file: bool,
) -> None:
    """Validate request-level stdout file materialization option.

    Args:
        registry: Runtime action registry.
        action_id: Requested action identifier.
        stdout_as_file: Request-level stdout file option.

    Raises:
        ActionNotFoundError: If action does not exist.
        StarError: If stdout_as_file is requested but action disallows it.
    """

    if not stdout_as_file:
        return

    spec = registry.get(action_id)
    if spec.allow_stdout_as_file:
        return

    raise StarError(
        INVALID_PARAMS,
        details={
            "reason": "Action does not allow stdout_as_file.",
            "action_id": action_id,
        },
    )


async def execute_action_handler(
    request: Request,
    action_id: str,
    payload: ExecuteActionRequest,
) -> ExecuteActionData:
    """Execute one DSL action and map runtime exceptions to StarError.

    Args:
        request: Incoming FastAPI request.
        action_id: Target action identifier from path parameter.
        payload: Validated execute request payload.

    Returns:
        Typed execution result payload.

    Raises:
        StarError: If action execution or output handling fails.
    """

    registry = get_action_registry(request)
    settings = get_runtime_settings(request)

    try:
        _validate_stdout_as_file_option(
            registry,
            action_id,
            payload.stdout_as_file,
        )

        result = await dispatch_action(
            registry,
            action_id,
            payload.params,
            settings=settings,
        )
    except StarError:
        raise
    except ActionNotFoundError as exc:
        raise StarError(
            ACTION_NOT_FOUND,
            message=f"Action '{action_id}' is not supported.",
            details={"action_id": action_id},
        ) from exc
    except ValidationError as exc:
        raise StarError(
            INVALID_PARAMS,
            details={"errors": exc.errors(include_input=False)},
        ) from exc
    except ActionInvalidArgError as exc:
        raise StarError(
            INVALID_PARAMS,
            details={"reason": str(exc)},
        ) from exc
    except ActionRuntimeRenderError as exc:
        raise StarError(
            INVALID_REQUEST,
            details={"reason": str(exc)},
        ) from exc
    except ActionBinaryBlockedError as exc:
        raise StarError(
            PERMISSION_DENIED,
            details={"reason": str(exc)},
        ) from exc
    except ActionBinaryNotAllowedError as exc:
        raise StarError(
            PERMISSION_DENIED,
            details={"reason": str(exc)},
        ) from exc
    except ActionBinaryPathForbiddenError as exc:
        raise StarError(
            PERMISSION_DENIED,
            details={"reason": str(exc)},
        ) from exc
    except ActionExecutionTimeoutError as exc:
        raise StarError(TIMEOUT) from exc
    except ActionRuntimeOutputError as exc:
        raise StarError(INTERNAL_ERROR, details={"reason": str(exc)}) from exc
    except ActionRuntimeExecError as exc:
        raise StarError(INTERNAL_ERROR, details={"reason": str(exc)}) from exc
    except Exception as exc:
        raise StarError(
            INTERNAL_ERROR,
            details={"reason": "unexpected error"},
        ) from exc

    max_stdout = (
        settings.star_max_stdout_bytes
        if settings.star_max_stdout_bytes is not None
        else DEFAULT_MAX_STDOUT_BYTES
    )
    max_stderr = (
        settings.star_max_stderr_bytes
        if settings.star_max_stderr_bytes is not None
        else DEFAULT_MAX_STDERR_BYTES
    )

    safe = transform_output(
        result.execution,
        max_stdout=max_stdout,
        max_stderr=max_stderr,
        settings=settings,
        secret_redactions=result.rendered.secret_redactions,
    )

    outputs_payload = build_outputs(
        result.spec,
        result.rendered,
        result.execution,
        safe,
        stdout_as_file=payload.stdout_as_file,
        settings=settings,
    )

    stdout, stdout_encoding = _encode_output(safe.stdout)
    stderr, stderr_encoding = _encode_output(safe.stderr)

    return ExecuteActionData(
        exit_code=safe.returncode,
        stdout=stdout,
        stdout_encoding=stdout_encoding,
        stderr=stderr,
        stderr_encoding=stderr_encoding,
        exec_time=safe.exec_time,
        pid=safe.pid,
        truncated=safe.truncated,
        redacted=safe.redacted,
        outputs=outputs_payload if outputs_payload else None,
    )
