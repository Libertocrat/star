"""Security-related runtime models for STAR actions."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class BinaryPolicy:
    """Execution policy for allowed and blocked binaries.

    Attributes:
        allowed: Tuple of binaries explicitly allowed for execution.
        blocked: Tuple of binaries explicitly blocked for execution.
    """

    allowed: tuple[str, ...]
    blocked: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EffectiveCatalogPolicy:
    """Immutable build-time binary policy indexed by final action name.

    Attributes:
        action_policies: Effective binary policy for each action FQDN.
    """

    action_policies: Mapping[str, BinaryPolicy]

    def __post_init__(self) -> None:
        """Freeze a defensive copy of the action policy mapping."""

        object.__setattr__(
            self,
            "action_policies",
            MappingProxyType(dict(self.action_policies)),
        )

    def for_action(self, action_name: str) -> BinaryPolicy:
        """Return the effective binary policy for one action.

        Args:
            action_name: Fully qualified action name.

        Raises:
            KeyError: If no compiled policy exists for the action.
        """

        return self.action_policies[action_name]
