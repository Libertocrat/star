"""Exception types for STAR DSL build engine."""


class ActionSpecsParseError(Exception):
    """Raised when a DSL spec file cannot be loaded, parsed, or validated.

    This includes:
    - file I/O errors
    - YAML syntax errors
    - schema validation errors (Pydantic)
    - semantic validation errors (validator.py)
    """


class ActionSpecsBuildError(Exception):
    """Raised when validated DSL specs cannot be compiled into `ActionSpec`."""


class ActionNotFoundError(Exception):
    """Raised when a requested action name is not present in the registry."""


class ActionRuntimeError(Exception):
    """Base class for STAR action runtime-layer errors."""


class ActionInvalidArgError(ActionRuntimeError):
    """Raised when a user-provided runtime parameter is invalid."""


class ActionRuntimeRenderError(ActionRuntimeError):
    """Raised when runtime command rendering fails unexpectedly."""


class ActionRuntimeExecError(ActionRuntimeError):
    """Raised when command execution fails at the runtime layer."""


class ActionBinaryBlockedError(ActionRuntimeExecError):
    """Raised when execution targets a binary blocked by policy."""


class ActionBinaryNotAllowedError(ActionRuntimeExecError):
    """Raised when execution targets a binary outside the allowlist."""


class ActionBinaryPathForbiddenError(ActionRuntimeExecError):
    """Raised when execution targets a path-like binary token."""


class ActionExecutionTimeoutError(ActionRuntimeExecError):
    """Raised when execution exceeds the allowed timeout."""


class ActionRuntimeOutputError(ActionRuntimeError):
    """Raised when runtime output materialization fails unexpectedly."""
