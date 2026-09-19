"""Source-derived provenance for STAR action specifications."""

from __future__ import annotations

from enum import Enum


class SpecProvenance(str, Enum):
    """Origin classification assigned to a loaded DSL module.

    The loader derives this value from the configured specification root. It is
    not serializable DSL input and therefore cannot be chosen by a module
    author.

    Attributes:
        CORE: Module shipped as part of the STAR core.
        EXTENSION: Module mounted after the STAR core is built.
    """

    CORE = "core"
    EXTENSION = "extension"
