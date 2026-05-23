"""Binary execution policy helpers for STAR action runtime security."""

from __future__ import annotations

import star.actions.security.block_lists as block_lists
from star.actions.models.security import BinaryPolicy
from star.core.config import Settings
from star.core.utils.parsing import parse_csv_set


def _merge_blocklists(*lists: tuple[str, ...]) -> tuple[str, ...]:
    """Merge multiple blocklists preserving insertion order and uniqueness.

    Args:
        *lists: One or more blocklist tuples to merge.

    Returns:
        Deduplicated tuple in first-seen order.
    """

    seen = set()
    merged = []

    for lst in lists:
        for item in lst:
            if item not in seen:
                seen.add(item)
                merged.append(item)

    return tuple(merged)


DEFAULT_BLOCKED_BINARIES: tuple[str, ...] = _merge_blocklists(
    block_lists.BLOCKED_SHELLS,
    block_lists.BLOCKED_INTERPRETERS,
    block_lists.BLOCKED_NETWORK,
    block_lists.BLOCKED_FILESYSTEM,
    block_lists.BLOCKED_PERMISSIONS,
    block_lists.BLOCKED_PRIVILEGE,
    block_lists.BLOCKED_INFRA,
    block_lists.BLOCKED_FILESYSTEM_LOW_LEVEL,
    block_lists.BLOCKED_PROCESS,
    block_lists.BLOCKED_DEBUG,
    block_lists.BLOCKED_NETWORK_INTROSPECTION,
    block_lists.BLOCKED_ENV,
)


def is_simple_binary_name(binary: str) -> bool:
    """Return whether a binary token is a simple name.

    Args:
            binary: Raw binary token.

    Returns:
            True when the token is non-empty and does not include path separators.
    """

    stripped = binary.strip()
    return stripped != "" and "/" not in stripped and "\\" not in stripped


def validate_binary_name_or_raise(binary: str) -> None:
    """Validate one binary name and raise on invalid values.

    Args:
            binary: Binary token to validate.

    Raises:
            ValueError: If the binary token is empty or path-like.
    """

    if not is_simple_binary_name(binary):
        raise ValueError(f"Invalid binary name: '{binary}'")


def build_binary_policy(
    module_binaries: tuple[str, ...],
    settings: Settings,
) -> BinaryPolicy:
    """Build one deterministic per-action effective binary policy.

    Args:
            module_binaries: Module-declared allowlist binaries.
            settings: Runtime settings with optional policy extras.

    Returns:
            BinaryPolicy containing sorted effective allow/block tuples.
    """

    for binary in module_binaries:
        validate_binary_name_or_raise(binary)

    blocked_extra = parse_csv_set(
        getattr(settings, "star_blocked_binaries_extra", None)
    )

    for binary in blocked_extra:
        validate_binary_name_or_raise(binary)

    blocked = set(DEFAULT_BLOCKED_BINARIES)
    blocked.update(blocked_extra)

    allowed = set(module_binaries)
    allowed.difference_update(blocked)

    return BinaryPolicy(
        allowed=tuple(sorted(allowed)),
        blocked=tuple(sorted(blocked)),
    )


def is_binary_blocked(binary: str, policy: BinaryPolicy) -> bool:
    """Return whether a binary is blocked by policy.

    Args:
            binary: Binary token to check.
            policy: Effective execution policy.

    Returns:
            True when binary is blocked.
    """

    return binary in policy.blocked


def is_binary_allowed(binary: str, policy: BinaryPolicy) -> bool:
    """Return whether a binary is allowed by policy.

    Args:
            binary: Binary token to check.
            policy: Effective execution policy.

    Returns:
            True when binary is allowed.
    """

    return binary in policy.allowed
