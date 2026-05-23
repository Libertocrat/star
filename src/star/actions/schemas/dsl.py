"""Pydantic DSL schemas for arguments, flags, command tokens, and outputs."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from star.actions.models import ParamType


class ArgSpec(BaseModel):
    """Definition of an argument in the STAR DSL.

    Attributes:
        type: Logical argument type.
        items: Item type when `type` is `list`.
        required: Whether the argument is required.
        default: Default value for optional arguments.
        constraints: Optional argument constraints.
        description: Human-readable argument description.
    """

    model_config = ConfigDict(extra="forbid")

    type: ParamType
    items: ParamType | None = None
    required: Optional[bool] = False
    default: Optional[Any] = None
    constraints: Optional[dict[str, Any]] = None

    description: str


class FlagSpec(BaseModel):
    """Definition of a flag in the STAR DSL.

    Attributes:
        value: Literal flag token injected into argv.
        default: Default boolean state for the flag.
        description: Human-readable flag description.
    """

    model_config = ConfigDict(extra="forbid")

    value: str
    default: bool
    description: str


class BinaryCmd(BaseModel):
    """DSL token representing the selected binary.

    Attributes:
        binary: Binary name to execute.
    """

    model_config = ConfigDict(extra="forbid")

    binary: str


class ArgCmd(BaseModel):
    """DSL token referencing a defined argument.

    Attributes:
        arg: Referenced argument name.
    """

    model_config = ConfigDict(extra="forbid")

    arg: str


class FlagCmd(BaseModel):
    """DSL token referencing a defined flag.

    Attributes:
        flag: Referenced flag name.
    """

    model_config = ConfigDict(extra="forbid")

    flag: str


class OutputCmd(BaseModel):
    """DSL token referencing a defined output.

    Attributes:
        output: Referenced output name.
    """

    model_config = ConfigDict(extra="forbid")

    output: str


class OutputSpec(BaseModel):
    """Definition of one output in the STAR DSL.

    Attributes:
        type: Logical output type.
        source: Runtime source used to materialize the output.
        description: Human-readable output description.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["file", "data"]
    source: Literal["command", "stdout", "stderr"]
    description: str


CommandElement = Union[str, BinaryCmd, ArgCmd, FlagCmd, OutputCmd]
