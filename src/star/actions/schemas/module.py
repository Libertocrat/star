"""Pydantic DSL schema for module-level action definitions."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

from star.actions.models.core import SpecProvenance

from .action import ActionSpecInput


class ModuleSpec(BaseModel):
    """Root DSL module definition.

    Attributes:
        _namespace: Runtime namespace metadata injected by the loader.
        _provenance: Runtime provenance injected by the loader.
        version: DSL module version.
        module: Bare DSL module name.
        description: Human-readable module description.
        authors: Optional module authors list.
        tags: Optional module tags as a YAML list.
        binaries: Allowed binaries for actions in this module.
        actions: Mapping of action name to action definitions.
    """

    model_config = ConfigDict(extra="forbid")

    _namespace: tuple[str, ...] = PrivateAttr(default_factory=tuple)
    _provenance: SpecProvenance = PrivateAttr(default=SpecProvenance.CORE)

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
    def provenance(self) -> SpecProvenance:
        """Return provenance derived from the configured specification root."""

        return self._provenance

    def with_runtime_identity(
        self,
        namespace: tuple[str, ...],
        provenance: SpecProvenance,
    ) -> "ModuleSpec":
        """Attach loader-derived runtime identity metadata.

        Args:
            namespace: Namespace parts derived from the module file path.
            provenance: Origin classification derived from the spec root.

        Returns:
            This module instance with runtime namespace metadata attached.
        """

        self._namespace = namespace
        self._provenance = provenance
        return self
