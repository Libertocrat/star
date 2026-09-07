"""Security-related runtime models for STAR actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from star.actions.security.binary_policies import InvocationForm


@dataclass(frozen=True, slots=True)
class BinaryPolicy:
    """Execution policy for allowed and blocked binaries.

    Attributes:
        allowed: Tuple of binaries explicitly allowed for execution.
        blocked: Tuple of binaries explicitly blocked for execution.
    """

    allowed: tuple[str, ...]
    blocked: tuple[str, ...]


class CommandTokenSource(str, Enum):
    """Structural source that produced one rendered argv token.

    Attributes:
        BINARY: Fixed executable token from the compiled command template.
        CONST: Static or interpolated constant token.
        ARG: Runtime argument token.
        FLAG: Conditional flag token.
        OUTPUT: Server-owned command output token.
    """

    BINARY = "binary"
    CONST = "const"
    ARG = "arg"
    FLAG = "flag"
    OUTPUT = "output"


class InvocationTokenRole(str, Enum):
    """Semantic role authorized for an extension argv token.

    Attributes:
        BINARY: Fixed reviewed executable.
        OPTION: Exact reviewed command option.
        POSITIVE_INT: Canonical bounded positive integer.
        PATTERN: Bounded runtime or static search pattern.
        MANAGED_INPUT: STAR-managed ready input file.
        MANAGED_OUTPUT: Invocation-owned managed output placeholder.
        SECRET_FILE: Invocation-owned ephemeral secret file.
    """

    BINARY = "binary"
    OPTION = "option"
    POSITIVE_INT = "positive_int"
    PATTERN = "pattern"
    MANAGED_INPUT = "managed_input"
    MANAGED_OUTPUT = "managed_output"
    SECRET_FILE = "secret_file"  # noqa: S105 - semantic role, not a secret


@dataclass(frozen=True, slots=True)
class CompiledTemplateTokenPolicy:
    """Immutable policy for one command-template expansion.

    Attributes:
        template_index: Zero-based command-template position.
        source: Structural command token source.
        role: Authorized semantic role for rendered tokens.
        reference: Referenced arg, flag, or output name when applicable.
        template_references: Ordered placeholder names for a const template.
        exact_value: Required exact rendered value when fixed by the DSL.
        min_count: Minimum rendered tokens contributed by this position.
        max_count: Maximum rendered tokens contributed by this position.
        min_value: Inclusive numeric lower bound when applicable.
        max_value: Inclusive numeric upper bound when applicable.
        max_length: Maximum pattern length when applicable.
    """

    template_index: int
    source: CommandTokenSource
    role: InvocationTokenRole
    reference: str | None = None
    template_references: tuple[str, ...] = ()
    exact_value: str | None = None
    min_count: int = 1
    max_count: int = 1
    min_value: int | None = None
    max_value: int | None = None
    max_length: int | None = None


@dataclass(frozen=True, slots=True)
class CompiledExtensionInvocationPolicy:
    """Reviewed invocation form compiled for one extension action.

    Attributes:
        binary: Exact reviewed executable.
        form: Selected immutable reviewed invocation grammar.
        template_tokens: Ordered template-position policies.
    """

    binary: str
    form: InvocationForm
    template_tokens: tuple[CompiledTemplateTokenPolicy, ...]


@dataclass(frozen=True, slots=True)
class EffectiveCatalogPolicy:
    """Immutable build-time execution policy indexed by final action name.

    Attributes:
        action_policies: Effective binary policy for each action FQDN.
        extension_invocation_policies: Compiled invocation policies for
            extension actions only.
    """

    action_policies: Mapping[str, BinaryPolicy]
    extension_invocation_policies: Mapping[str, CompiledExtensionInvocationPolicy] = (
        field(default_factory=dict)
    )

    def __post_init__(self) -> None:
        """Freeze a defensive copy of the action policy mapping."""

        object.__setattr__(
            self,
            "action_policies",
            MappingProxyType(dict(self.action_policies)),
        )
        object.__setattr__(
            self,
            "extension_invocation_policies",
            MappingProxyType(dict(self.extension_invocation_policies)),
        )

    def for_action(self, action_name: str) -> BinaryPolicy:
        """Return the effective binary policy for one action.

        Args:
            action_name: Fully qualified action name.

        Raises:
            KeyError: If no compiled policy exists for the action.
        """

        return self.action_policies[action_name]

    def invocation_for_action(
        self,
        action_name: str,
    ) -> CompiledExtensionInvocationPolicy | None:
        """Return the compiled extension invocation policy when present.

        Args:
            action_name: Fully qualified action name.

        Returns:
            Compiled extension policy, or ``None`` for a core action.
        """

        return self.extension_invocation_policies.get(action_name)
