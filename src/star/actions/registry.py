"""In-memory immutable registry for STAR runtime actions."""

from __future__ import annotations

from collections.abc import Mapping

from star.actions.build_engine.builder import build_actions
from star.actions.build_engine.loader import load_module_specs
from star.actions.build_engine.validator import validate_modules
from star.actions.engine_config import SPEC_DIRS
from star.actions.exceptions import ActionNotFoundError
from star.actions.models import ActionSpec
from star.actions.models.presentation import ModuleSummary
from star.actions.presentation.catalog import build_module_summaries
from star.actions.schemas.module import ModuleSpec
from star.core.config import Settings, get_settings


class ActionRegistry:
    """Immutable runtime action registry keyed by final runtime action name."""

    def __init__(
        self,
        actions: Mapping[str, ActionSpec],
        modules: list[ModuleSpec],
    ) -> None:
        """Initialize immutable registry state.

        Args:
            actions: Mapping keyed by final runtime action name.
            modules: Loaded module definitions used to build the action map.
        """
        self._actions: dict[str, ActionSpec] = dict(actions)
        self.modules: list[ModuleSpec] = list(modules)

        # Module Presentation cache (build time)
        self.module_summaries: list[ModuleSummary] = build_module_summaries(
            self.modules, self._actions
        )

    def get(self, name: str) -> ActionSpec:
        """Resolve one action by name.

        Raises:
            ActionNotFoundError: If the action name is not present.
        """

        try:
            return self._actions[name]
        except KeyError as exc:
            raise ActionNotFoundError(f"Action not found: {name}") from exc

    def has(self, name: str) -> bool:
        """Return True if the action exists in the registry."""

        return name in self._actions

    def list_names(self) -> tuple[str, ...]:
        """Return deterministic action names."""

        return tuple(sorted(self._actions.keys()))


def build_registry_from_specs(
    settings: Settings | None = None,
) -> ActionRegistry:
    """Build an immutable runtime registry from DSL YAML specs."""

    resolved_settings = settings or get_settings()
    modules = load_module_specs(list(SPEC_DIRS), resolved_settings)
    validate_modules(modules)
    actions = build_actions(modules, resolved_settings)
    return ActionRegistry(actions, modules)
