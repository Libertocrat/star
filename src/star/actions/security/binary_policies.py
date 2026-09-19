"""Immutable invocation profiles for reviewed STAR binaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from star.actions.models.provenance import SpecProvenance
from star.actions.security.capabilities import InvocationCapability


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
        value_prefix: Exact wrapper prefix for a typed following value.
    """

    names: tuple[str, ...]
    value_kind: OperandKind | None = None
    required: bool = False
    exclusive_group: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    max_length: int | None = None
    value_prefix: str | None = None

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
        min_value: Inclusive lower bound for numeric values.
        max_value: Inclusive upper bound for numeric values.
        max_length: Maximum pattern length.
        value_prefix: Exact wrapper prefix for a typed value.
    """

    kind: OperandKind
    min_count: int = 1
    max_count: int = 1
    min_value: int | None = None
    max_value: int | None = None
    max_length: int | None = None
    value_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationAuthorization:
    """Authorization for one loader-derived module provenance.

    Attributes:
        provenance: Module source authorized to use the invocation form.
        required_capabilities: Capabilities required for this authorization.
    """

    provenance: SpecProvenance
    required_capabilities: frozenset[InvocationCapability] = frozenset()


@dataclass(frozen=True, slots=True)
class InvocationForm:
    """One canonical, shell-free invocation grammar for a binary.

    Attributes:
        authorizations: Provenance-specific authorizations for this form.
        options: Exact options accepted before positional operands.
        prefix_literals: Exact literals required after the binary.
        required_any_of: Canonical-option groups requiring one selected option.
        positional_operands: Ordered positional operand segments.
    """

    authorizations: tuple[InvocationAuthorization, ...]
    options: tuple[OptionPolicy, ...]
    prefix_literals: tuple[str, ...] = ()
    required_any_of: tuple[frozenset[str], ...] = ()
    positional_operands: tuple[OperandPolicy, ...] = ()


@dataclass(frozen=True, slots=True)
class BinaryInvocationPolicy:
    """Reviewed provenance-scoped invocation grammar for one binary.

    Attributes:
        binary: Simple executable name.
        forms: Canonical invocation grammars accepted for the binary.
    """

    binary: str
    forms: tuple[InvocationForm, ...]


_CORE = InvocationAuthorization(SpecProvenance.CORE)
_FILE_INSPECTION = frozenset({InvocationCapability.FILE_INSPECTION})
_TEXT_SEARCH = frozenset({InvocationCapability.TEXT_SEARCH})
_CHECKSUM = frozenset({InvocationCapability.CHECKSUM})
_EXTENSION_FILE_INSPECTION = InvocationAuthorization(
    SpecProvenance.EXTENSION,
    _FILE_INSPECTION,
)
_EXTENSION_TEXT_SEARCH = InvocationAuthorization(
    SpecProvenance.EXTENSION,
    _TEXT_SEARCH,
)
_EXTENSION_CHECKSUM = InvocationAuthorization(
    SpecProvenance.EXTENSION,
    _CHECKSUM,
)

_ONE_MANAGED_INPUT = (OperandPolicy(OperandKind.MANAGED_INPUT),)

BINARY_INVOCATION_POLICIES: dict[str, BinaryInvocationPolicy] = {
    "cat": BinaryInvocationPolicy(
        binary="cat",
        forms=(
            InvocationForm(
                authorizations=(_CORE,),
                options=(),
                prefix_literals=("/proc/sys/kernel/random/uuid",),
            ),
        ),
    ),
    "file": BinaryInvocationPolicy(
        binary="file",
        forms=(
            InvocationForm(
                authorizations=(_CORE,),
                options=(),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
            InvocationForm(
                authorizations=(_EXTENSION_FILE_INSPECTION,),
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
    "grep": BinaryInvocationPolicy(
        binary="grep",
        forms=(
            InvocationForm(
                authorizations=(_CORE,),
                options=(
                    OptionPolicy(("-E",)),
                    OptionPolicy(("-i",)),
                    OptionPolicy(("-n",)),
                    OptionPolicy(("-v",)),
                    OptionPolicy(
                        ("-e",),
                        value_kind=OperandKind.PATTERN,
                        required=True,
                        max_length=4096,
                    ),
                ),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
            InvocationForm(
                authorizations=(_EXTENSION_TEXT_SEARCH,),
                options=(
                    OptionPolicy(
                        ("-E", "--extended-regexp"),
                        exclusive_group="pattern_mode",
                    ),
                    OptionPolicy(
                        ("-F", "--fixed-strings"),
                        exclusive_group="pattern_mode",
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
    "head": BinaryInvocationPolicy(
        binary="head",
        forms=(
            InvocationForm(
                authorizations=(_CORE, _EXTENSION_FILE_INSPECTION),
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
    "openssl": BinaryInvocationPolicy(
        binary="openssl",
        forms=(
            InvocationForm(
                authorizations=(_CORE,),
                options=(
                    OptionPolicy(
                        (
                            "-blake2b512",
                            "-blake2s256",
                            "-md5",
                            "-sha1",
                            "-sha224",
                            "-sha256",
                            "-sha384",
                            "-sha512",
                        ),
                        required=True,
                        exclusive_group="digest_algorithm",
                    ),
                ),
                prefix_literals=("dgst",),
                positional_operands=_ONE_MANAGED_INPUT,
            ),
            InvocationForm(
                authorizations=(_CORE,),
                options=(
                    OptionPolicy(("-aes-256-cbc",), required=True),
                    OptionPolicy(("-salt",), required=True),
                    OptionPolicy(("-pbkdf2",), required=True),
                    OptionPolicy(
                        ("-in",),
                        value_kind=OperandKind.MANAGED_INPUT,
                        required=True,
                    ),
                    OptionPolicy(
                        ("-out",),
                        value_kind=OperandKind.MANAGED_OUTPUT,
                        required=True,
                    ),
                    OptionPolicy(
                        ("-pass",),
                        value_kind=OperandKind.SECRET_FILE,
                        required=True,
                        value_prefix="file:",
                    ),
                ),
                prefix_literals=("enc",),
            ),
            InvocationForm(
                authorizations=(_CORE,),
                options=(
                    OptionPolicy(("-d",), required=True),
                    OptionPolicy(("-aes-256-cbc",), required=True),
                    OptionPolicy(("-pbkdf2",), required=True),
                    OptionPolicy(
                        ("-in",),
                        value_kind=OperandKind.MANAGED_INPUT,
                        required=True,
                    ),
                    OptionPolicy(
                        ("-out",),
                        value_kind=OperandKind.MANAGED_OUTPUT,
                        required=True,
                    ),
                    OptionPolicy(
                        ("-pass",),
                        value_kind=OperandKind.SECRET_FILE,
                        required=True,
                        value_prefix="file:",
                    ),
                ),
                prefix_literals=("enc",),
            ),
            InvocationForm(
                authorizations=(_CORE,),
                options=(OptionPolicy(("-hex",), required=True),),
                prefix_literals=("rand",),
                positional_operands=(
                    OperandPolicy(
                        OperandKind.POSITIVE_INT,
                        min_value=1,
                        max_value=128,
                    ),
                ),
            ),
            InvocationForm(
                authorizations=(_CORE,),
                options=(OptionPolicy(("-base64",), required=True),),
                prefix_literals=("rand",),
                positional_operands=(
                    OperandPolicy(
                        OperandKind.POSITIVE_INT,
                        min_value=1,
                        max_value=128,
                    ),
                ),
            ),
            InvocationForm(
                authorizations=(_CORE,),
                options=(
                    OptionPolicy(
                        ("-out",),
                        value_kind=OperandKind.MANAGED_OUTPUT,
                        required=True,
                    ),
                ),
                prefix_literals=("rand",),
                positional_operands=(
                    OperandPolicy(
                        OperandKind.POSITIVE_INT,
                        min_value=1,
                        max_value=4096,
                    ),
                ),
            ),
        ),
    ),
    "sha256sum": BinaryInvocationPolicy(
        binary="sha256sum",
        forms=(
            InvocationForm(
                authorizations=(_CORE, _EXTENSION_CHECKSUM),
                options=(),
                positional_operands=(
                    OperandPolicy(
                        OperandKind.MANAGED_INPUT,
                        min_count=1,
                        max_count=32,
                    ),
                ),
            ),
        ),
    ),
    "tail": BinaryInvocationPolicy(
        binary="tail",
        forms=(
            InvocationForm(
                authorizations=(_CORE, _EXTENSION_FILE_INSPECTION),
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
        forms=(
            *(
                InvocationForm(
                    authorizations=(_CORE,),
                    options=(OptionPolicy((option,), required=True),),
                    positional_operands=_ONE_MANAGED_INPUT,
                )
                for option in ("-l", "-w", "-m")
            ),
            InvocationForm(
                authorizations=(_EXTENSION_FILE_INSPECTION,),
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
}


def get_binary_invocation_policy(binary: str) -> BinaryInvocationPolicy | None:
    """Return the reviewed policy for one binary, if supported."""

    return BINARY_INVOCATION_POLICIES.get(binary)
