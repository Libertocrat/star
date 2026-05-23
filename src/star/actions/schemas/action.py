"""Pydantic DSL schema for per-action input definitions."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

from .dsl import ArgSpec, CommandElement, FlagSpec, OutputSpec


class ActionSpecInput(BaseModel):
    """Raw action definition as declared in the DSL.

    Attributes:
        description: Long description for the action.
        summary: Optional short summary.
        tags: Optional action tags as a YAML list. These are merged with
            module-level tags at build time.
        allow_stdout_as_file: Whether runtime stdout may be materialized as a
            managed file.
        args: Optional mapping of argument definitions.
        flags: Optional mapping of flag definitions.
        outputs: Optional mapping of output definitions.
        command: Ordered command template elements.
    """

    description: str
    summary: Optional[str] = None
    tags: Optional[List[str]] = None
    allow_stdout_as_file: bool = True

    args: Optional[Dict[str, ArgSpec]] = None
    flags: Optional[Dict[str, FlagSpec]] = None
    outputs: Optional[Dict[str, OutputSpec]] = None

    command: List[CommandElement]
