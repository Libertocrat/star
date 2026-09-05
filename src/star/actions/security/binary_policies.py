"""Immutable invocation profiles for reviewed extension binaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from star.actions.security.capabilities import ExtensionCapability


class OperandKind(str, Enum):
    """Semantic kinds accepted by one command operand position.

    Attributes:
        POSITIVE_INT: Integer with a bounded positive domain.
        PATTERN: Bounded user-controlled text pattern.
        MANAGED_INPUT: One or more managed file identifiers.
        MANAGED_OUTPUT: A server-owned output placeholder.
        SECRET_FILE: An invocation-owned secret file reference.
    """

    POSITIVE_INT = "positive_int"
    PATTERN = "pattern"
    MANAGED_INPUT = "managed_input"
    MANAGED_OUTPUT = "managed_output"
    SECRET_FILE = "secret_file"  # noqa: S105 - semantic operand kind, not a secret


@dataclass(frozen=True, slots=True)
class OptionPolicy:
    """Policy for one option and an optional following operand.

    Attributes:
        names: Exact accepted option spellings; the first is canonical.
        value_kind: Expected semantic kind for the following token, if any.
        required: Whether this exact option must appear.
        exclusive_group: Optional group allowing at most one member.
        min_value: Inclusive lower bound for numeric values.
        max_value: Inclusive upper bound for numeric values.
        max_length: Maximum pattern length.
    """

    names: tuple[str, ...]
    value_kind: OperandKind | None = None
    required: bool = False
    exclusive_group: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    max_length: int | None = None

    @property
    def canonical_name(self) -> str:
        """Return the canonical spelling used by policy tracking."""

        return self.names[0]


@dataclass(frozen=True, slots=True)
class OperandPolicy:
    """Policy for a positional operand segment.

    Attributes:
        kind: Expected semantic value kind.
        min_count: Minimum expanded argv tokens accepted in this segment.
        max_count: Maximum expanded argv tokens accepted in this segment.
    """

    kind: OperandKind
    min_count: int = 1
    max_count: int = 1


@dataclass(frozen=True, slots=True)
class InvocationForm:
    """One canonical, shell-free invocation grammar for a binary.

    Attributes:
        options: Exact options accepted before positional operands.
        required_any_of: Canonical-option groups requiring one selected option.
        positional_operands: Ordered positional operand segments.
    """

    options: tuple[OptionPolicy, ...]
    required_any_of: tuple[frozenset[str], ...] = ()
    positional_operands: tuple[OperandPolicy, ...] = ()


@dataclass(frozen=True, slots=True)
class BinaryInvocationPolicy:
    """Reviewed extension grammar and capability grants for one binary.

    Attributes:
        binary: Simple executable name.
        capabilities: Capabilities that permit this binary.
        forms: Canonical invocation grammars accepted for the binary.
    """

    binary: str
    capabilities: frozenset[ExtensionCapability]
    forms: tuple[InvocationForm, ...]


_ONE_MANAGED_INPUT = (OperandPolicy(OperandKind.MANAGED_INPUT),)

BINARY_INVOCATION_POLICIES: dict[str, BinaryInvocationPolicy] = {
    "file": BinaryInvocationPolicy(
        binary="file",
        capabilities=frozenset({ExtensionCapability.FILE_INSPECTION}),
        forms=(
            InvocationForm(
                options=(
                    OptionPolicy(("-b", "--brief")),
                    OptionPolicy(("-i", "--mime")),
                    OptionPolicy(("--mime-type",)),
                    OptionPolicy(("--mime-encoding",)),
                ),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
        ),
    ),
    "head": BinaryInvocationPolicy(
        binary="head",
        capabilities=frozenset({ExtensionCapability.FILE_INSPECTION}),
        forms=(
            InvocationForm(
                options=(
                    OptionPolicy(
                        ("-n", "--lines"),
                        value_kind=OperandKind.POSITIVE_INT,
                        required=True,
                        min_value=1,
                        max_value=10000,
                    ),
                ),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
        ),
    ),
    "tail": BinaryInvocationPolicy(
        binary="tail",
        capabilities=frozenset({ExtensionCapability.FILE_INSPECTION}),
        forms=(
            InvocationForm(
                options=(
                    OptionPolicy(
                        ("-n", "--lines"),
                        value_kind=OperandKind.POSITIVE_INT,
                        required=True,
                        min_value=1,
                        max_value=10000,
                    ),
                ),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
        ),
    ),
    "wc": BinaryInvocationPolicy(
        binary="wc",
        capabilities=frozenset({ExtensionCapability.FILE_INSPECTION}),
        forms=(
            InvocationForm(
                options=(
                    OptionPolicy(("-l", "--lines")),
                    OptionPolicy(("-w", "--words")),
                    OptionPolicy(("-m", "--chars")),
                ),
                required_any_of=(frozenset({"-l", "-w", "-m"}),),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
        ),
    ),
    "grep": BinaryInvocationPolicy(
        binary="grep",
        capabilities=frozenset({ExtensionCapability.TEXT_SEARCH}),
        forms=(
            InvocationForm(
                options=(
                    OptionPolicy(
                        ("-E", "--extended-regexp"), exclusive_group="pattern_mode"
                    ),
                    OptionPolicy(
                        ("-F", "--fixed-strings"), exclusive_group="pattern_mode"
                    ),
                    OptionPolicy(("-i", "--ignore-case")),
                    OptionPolicy(("-n", "--line-number")),
                    OptionPolicy(("-v", "--invert-match")),
                    OptionPolicy(("-o", "--only-matching")),
                    OptionPolicy(("-c", "--count")),
                    OptionPolicy(
                        ("-e", "--regexp"),
                        value_kind=OperandKind.PATTERN,
                        required=True,
                        max_length=4096,
                    ),
                ),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
        ),
    ),
    "sha256sum": BinaryInvocationPolicy(
        binary="sha256sum",
        capabilities=frozenset({ExtensionCapability.CHECKSUM}),
        forms=(
            InvocationForm(
                options=(),
                positional_operands=(
                    OperandPolicy(OperandKind.MANAGED_INPUT, min_count=1, max_count=32),
                ),
            ),
        ),
    ),
}


def get_binary_invocation_policy(binary: str) -> BinaryInvocationPolicy | None:
    """Return the reviewed extension policy for one binary, if supported."""

    return BINARY_INVOCATION_POLICIES.get(binary)
