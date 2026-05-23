"""Pydantic DSL schema for module-level action definitions."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, PrivateAttr

from .action import ActionSpecInput


class ModuleSpec(BaseModel):
    """Root DSL module definition.

    Attributes:
        _namespace: Runtime namespace metadata injected by the loader.
        _source: Runtime source label injected by the loader.
        version: DSL module version.
        module: Bare DSL module name.
        description: Human-readable module description.
        authors: Optional module authors list.
        tags: Optional module tags as a YAML list.
        binaries: Allowed binaries for actions in this module.
        actions: Mapping of action name to action definitions.
    """

    _namespace: tuple[str, ...] = PrivateAttr(default_factory=tuple)
    _source: str = PrivateAttr(default="core")

    version: int
    module: str
    description: str

    authors: Optional[List[str]] = None
    tags: Optional[List[str]] = None

    binaries: List[str]

    actions: Dict[str, ActionSpecInput]

    @property
    def namespace(self) -> tuple[str, ...]:
        """Return runtime namespace derived from specs directory layout."""

        return self._namespace

    @property
    def source(self) -> str:
        """Return runtime source label for this module."""

        return self._source

    def with_runtime_namespace(
        self,
        namespace: tuple[str, ...],
        source: str,
    ) -> "ModuleSpec":
        """Attach loader-derived runtime namespace metadata.

        Args:
            namespace: Namespace parts derived from the module file path.
            source: Source label such as `core` or `user`.

        Returns:
            This module instance with runtime namespace metadata attached.
        """

        self._namespace = namespace
        self._source = source
        return self
