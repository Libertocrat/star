"""
Core domain models for STAR actions.

This module defines the canonical in-memory representation of actions
(`ActionSpec`) as used by the registry and dispatcher, along with the typed
supporting structures that describe action inputs and execution behavior.

Design principles:
- ActionSpec is a passive, immutable data structure.
- It contains no execution logic.
- It is produced by the build engine from `.yml` files.
- It is consumed by the dispatcher and runtime execution layer.
- Pydantic is used for external validation (DSL parsing and request params),
  while these dataclasses model the validated internal runtime state.
- Internal structures should remain strongly typed to support mypy, improve
  readability, and reduce runtime errors caused by loosely typed nested dicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Literal, Optional, Tuple, Type, TypedDict, Union
from uuid import UUID

from pydantic import BaseModel

from star.actions.models.security import BinaryPolicy


class ParamType(str, Enum):
    """Supported logical parameter types for STAR action arguments.

    This enum defines the set of value types currently supported by the
    STAR build engine for action arguments declared in `.yml` files.

    The value of each enum member matches the literal string expected in
    the DSL. The build engine will later map these logical types to the
    appropriate Python or Pydantic runtime types when generating the
    action-specific `params_model`.

    Notes:
        - This enum applies to action arguments, not flags.
        - Flags are modeled separately and always resolve to `bool`.
        - Type-specific constraints are validated later by the build engine.
          For example, `max_size` is valid only for `file_id`.
    """

    INT = "int"
    FLOAT = "float"
    STRING = "string"
    BOOL = "bool"
    FILE_ID = "file_id"
    LIST = "list"


class OutputType(str, Enum):
    """Supported logical output types for STAR action outputs."""

    FILE = "file"
    DATA = "data"


class OutputSource(str, Enum):
    """Supported logical output sources for STAR action outputs."""

    COMMAND = "command"
    STDOUT = "stdout"
    STDERR = "stderr"


class BinaryCmd(TypedDict):
    """Runtime command token representing the executable binary.

    Attributes:
        kind: Discriminator value fixed to `binary`.
        value: Binary name to execute.
    """

    kind: Literal["binary"]
    value: str


class ArgCmd(TypedDict):
    """Runtime command token referencing a resolved argument value.

    Attributes:
        kind: Discriminator value fixed to `arg`.
        name: Argument key resolved from action params.
    """

    kind: Literal["arg"]
    name: str


class FlagCmd(TypedDict):
    """Runtime command token referencing a conditional flag parameter.

    Attributes:
        kind: Discriminator value fixed to `flag`.
        name: Flag key resolved from action params.
    """

    kind: Literal["flag"]
    name: str


class OutputCmd(TypedDict):
    """Runtime command token referencing a declared output placeholder.

    Attributes:
        kind: Discriminator value fixed to `output`.
        name: Output key resolved from action output definitions.
    """

    kind: Literal["output"]
    name: str


class ConstCmd(TypedDict):
    """Runtime command token containing a literal command value.

    Attributes:
        kind: Discriminator value fixed to `const`.
        value: Literal value inserted directly into argv.
    """

    kind: Literal["const"]
    value: str


CommandElement = Union[BinaryCmd, ArgCmd, FlagCmd, OutputCmd, ConstCmd]


@dataclass(frozen=True, slots=True)
class ArgDef:
    """Typed internal definition of an action argument.

    ArgDef represents a validated action argument after the `.yml` file has
    been parsed and normalized by the build engine. It is intentionally kept
    generic and lightweight: a single structure is used for all argument types,
    while type-specific rule enforcement is delegated to the specs validator.

    This avoids over-engineering the runtime model with many specialized
    subclasses while still preserving strong typing and clear semantics.

    Attributes:
        type: Logical runtime parameter type.
        items: Item type when `type` is `list`.
        required: Whether the argument is required.
        default: Default value for optional arguments.
        constraints: Optional runtime constraints.
        description: Human-readable argument description.
    """

    type: ParamType
    items: ParamType | None = None
    required: bool = False
    default: Any | None = None
    constraints: dict[str, Any] | None = None

    description: str = ""


@dataclass(frozen=True, slots=True)
class FlagDef:
    """Typed internal definition of an action flag.

    Attributes:
        value: Literal flag token injected into argv when enabled.
        default: Default boolean state.
        description: Human-readable flag description.
    """

    value: str
    default: bool
    description: str


@dataclass(frozen=True, slots=True)
class OutputDef:
    """Typed internal definition of an action output.

    Attributes:
        type: Logical output type.
        source: Runtime source used to materialize output data.
        description: Human-readable output description.
    """

    type: OutputType
    source: OutputSource
    description: str = ""


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Canonical specification object describing a registered STAR action.

    Attributes:
        name: Final runtime action name.
        namespace: Runtime namespace tuple derived from the module file path.
        module: Bare DSL module name from YAML.
        action: DSL action key within the module.
        version: DSL version used by the module.
        params_model: Generated Pydantic model for runtime parameter validation.
        binary: Primary binary extracted from command template.
        command_template: Normalized immutable command token sequence.
        execution_policy: Effective per-action binary execution policy.
        arg_defs: Runtime argument definitions keyed by arg name.
        flag_defs: Runtime flag definitions keyed by flag name.
        defaults: Flattened runtime defaults for args and flags.
        outputs: Runtime output definitions keyed by output name.
        allow_stdout_as_file: Whether this action allows sanitized stdout to be
            stored as a managed file.
        authors: Optional module authors metadata.
        tags: Optional normalized module tags.
        summary: Optional short action summary.
        description: Optional long action description.
        deprecated: Whether action is marked deprecated.
        params_example: Optional params example payload for docs.
    """

    name: str
    namespace: tuple[str, ...]
    module: str
    action: str
    version: int

    params_model: Type[BaseModel]

    binary: str
    command_template: Tuple[CommandElement, ...]
    execution_policy: BinaryPolicy

    arg_defs: dict[str, ArgDef]
    flag_defs: dict[str, FlagDef]
    defaults: dict[str, Any]
    outputs: dict[str, OutputDef] = field(default_factory=dict)
    allow_stdout_as_file: bool = True

    authors: tuple[str, ...] | None = None
    tags: tuple[str, ...] = ()

    summary: Optional[str] = None
    description: Optional[str] = None
    deprecated: bool = False

    params_example: BaseModel | None = None

    @property
    def fqdn(self) -> str:
        """Return the canonical runtime action name."""
        return self.name

    @property
    def has_args(self) -> bool:
        """Return whether the action defines runtime arguments."""
        return bool(self.arg_defs)

    @property
    def has_flags(self) -> bool:
        """Return whether the action defines runtime flags."""
        return bool(self.flag_defs)

    @property
    def has_defaults(self) -> bool:
        """Return whether the action defines any default runtime params."""
        return bool(self.defaults)

    @property
    def has_outputs(self) -> bool:
        """Return whether the action defines runtime outputs."""
        return bool(self.outputs)

    def model_dump(self) -> dict[str, Any]:
        """Serialize ActionSpec into a JSON-compatible dictionary.

        Returns:
            JSON-friendly dictionary with normalized nested values.
        """
        raw = {
            "name": self.name,
            "namespace": self.namespace,
            "module": self.module,
            "action": self.action,
            "version": self.version,
            "params_model": self.params_model,
            "binary": self.binary,
            "command_template": self.command_template,
            "execution_policy": self.execution_policy,
            "arg_defs": self.arg_defs,
            "flag_defs": self.flag_defs,
            "outputs": self.outputs,
            "allow_stdout_as_file": self.allow_stdout_as_file,
            "defaults": self.defaults,
            "authors": self.authors,
            "tags": self.tags,
            "summary": self.summary,
            "description": self.description,
            "deprecated": self.deprecated,
            "params_example": self.params_example,
        }
        return _to_jsonable(raw)

    def model_dump_json(self, *, indent: int | None = 2) -> str:
        """Serialize ActionSpec into a JSON string.

        Args:
            indent: Optional indentation level for pretty-printing.

        Returns:
            JSON string representation of this ActionSpec.
        """
        return json.dumps(self.model_dump(), indent=indent, sort_keys=False)


def _to_jsonable(value: Any) -> Any:
    """Recursively convert runtime values into JSON-serializable structures."""

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, type) and issubclass(value, BaseModel):
        return {
            "name": value.__name__,
            "schema": value.model_json_schema(),
        }

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]

    return value
