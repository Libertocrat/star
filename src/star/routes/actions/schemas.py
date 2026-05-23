"""Schemas for action execution and discovery endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from star.routes.files.schemas import FileMetadata


class ExecuteActionRequest(BaseModel):
    """Client request body for executing a STAR action.

    Attributes:
        params: Action parameters.
        stdout_as_file: Whether sanitized stdout should be stored as a managed
            file when the action allows it.
    """

    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Action parameters.",
    )
    stdout_as_file: bool = Field(
        default=False,
        description=(
            "When true, STAR stores sanitized stdout as a managed text file "
            "under outputs.stdout_file if the action allows it."
        ),
    )


class ExecuteActionData(BaseModel):
    """Typed success payload for the `POST /v1/actions/{action_id}` endpoint.

    Attributes:
        exit_code: Process return code.
        stdout: Sanitized stdout text or base64 payload.
        stdout_encoding: Encoding label for stdout.
        stderr: Sanitized stderr text or base64 payload.
        stderr_encoding: Encoding label for stderr.
        exec_time: Total execution time in seconds.
        pid: Process identifier when available.
        truncated: Whether stdout or stderr was truncated.
        redacted: Whether output was redacted.
        outputs: Materialized file outputs, if any.
    """

    exit_code: int
    stdout: str
    stdout_encoding: Literal["utf-8", "base64"]
    stderr: str
    stderr_encoding: Literal["utf-8", "base64"]
    exec_time: float
    pid: int | None = None
    truncated: bool
    redacted: bool
    outputs: dict[str, FileMetadata | None] | None = None


class ActionSummarySchema(BaseModel):
    """Public summary payload for one registered action.

    Attributes:
        action: Short DSL action name.
        action_id: Fully qualified runtime action name.
        summary: Optional short summary.
        description: Optional long description.
        tags: Effective public action tags.
    """

    action: str
    action_id: str
    summary: str | None
    description: str | None
    tags: list[str]


class ModuleSummarySchema(BaseModel):
    """Public summary payload for one DSL module.

    Attributes:
        module: Bare module name.
        module_id: Fully qualified module identifier.
        namespace: Dot-separated namespace string.
        namespace_path: Namespace segments.
        description: Public module description.
        tags: Public module tags.
        authors: Public module authors.
        actions: Public action summaries within the module.
    """

    module: str
    module_id: str
    namespace: str
    namespace_path: list[str]
    description: str | None
    tags: list[str]
    authors: list[str]
    actions: list[ActionSummarySchema]


class TagMatchMode(str, Enum):
    """Allowed tag match modes for action discovery filters."""

    ANY = "any"
    ALL = "all"


class ListActionsRequest(BaseModel):
    """Typed query parameters for `GET /v1/actions`.

    Attributes:
        q: Optional free-text filter.
        tags: Optional CSV action tags filter.
        match: Optional tag matching mode for `tags`.
    """

    q: str | None = Field(
        default=None,
        description=(
            "Optional free-text filter matched against action name, summary, "
            "description, and effective tags."
        ),
        examples=["sha256"],
    )
    tags: str | None = Field(
        default=None,
        description=(
            "Optional CSV action tags filter, for example "
            "`hashing,checksum`. Tokens are trimmed, lowercased, and "
            "deduplicated before filtering."
        ),
        examples=["hashing,checksum"],
    )
    match: str | None = Field(
        default=None,
        description=(
            "Optional tag matching mode. Allowed values: `any` or `all`. "
            "When omitted and `tags` is set, behavior defaults to `any`. "
            "Using `match` without `tags` is invalid."
        ),
        examples=["any"],
    )


class ListActionsData(BaseModel):
    """Success payload for `GET /v1/actions`.

    Attributes:
        modules: Discovery summaries grouped by module.
    """

    modules: list[ModuleSummarySchema]


class GetActionData(BaseModel):
    """Success payload for `GET /v1/actions/{action_id}`.

    Attributes:
        action: Short DSL action name.
        action_id: Fully qualified runtime action name.
        summary: Optional short summary.
        description: Optional long description.
        tags: Effective public action tags.
        allow_stdout_as_file: Whether sanitized stdout may be stored as a
            managed file.
        args: Serialized argument definitions.
        flags: Serialized flag definitions.
        outputs: Serialized output definitions.
        params_contract: Public params contract.
        params_example: Public params example payload.
        response_contract: Public response contract.
        response_example: Public response example payload.
    """

    action: str
    action_id: str
    summary: str | None
    description: str | None
    tags: list[str]
    allow_stdout_as_file: bool
    args: list[dict[str, Any]]
    flags: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    params_contract: dict[str, Any]
    params_example: dict[str, Any]
    response_contract: dict[str, Any]
    response_example: dict[str, Any]
