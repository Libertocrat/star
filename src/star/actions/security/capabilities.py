"""Reviewed invocation capabilities for the STAR action DSL."""

from __future__ import annotations

from enum import Enum


class InvocationCapability(str, Enum):
    """Reviewed execution capabilities used by invocation authorization.

    Attributes:
        FILE_INSPECTION: Safe managed-file inspection utilities.
        TEXT_SEARCH: Bounded text-pattern matching utilities.
        CHECKSUM: Managed-file SHA-256 checksum utilities.
    """

    FILE_INSPECTION = "file-inspection"
    TEXT_SEARCH = "text-search"
    CHECKSUM = "checksum"


def resolve_enabled_capabilities(
    value: str,
) -> frozenset[InvocationCapability]:
    """Resolve one normalized settings value into enabled capabilities.

    Args:
        value: Canonical ``all``, ``none``, or comma-separated capability names.

    Returns:
        Enabled reviewed capabilities.

    Raises:
        ValueError: If a configured capability is not part of the reviewed catalog.
    """

    if value == "all":
        return frozenset(InvocationCapability)

    if value == "none":
        return frozenset()

    resolved: set[InvocationCapability] = set()
    for name in value.split(","):
        try:
            resolved.add(InvocationCapability(name))
        except ValueError as exc:
            raise ValueError(
                f"unknown invocation capability '{name}' in operator configuration"
            ) from exc

    return frozenset(resolved)


def parse_declared_capabilities(
    values: list[str] | None,
) -> tuple[InvocationCapability, ...]:
    """Parse one module capability declaration without assigning authorization.

    Args:
        values: Raw module YAML capability values.

    Returns:
        Canonical capability tuple preserving declaration order.

    Raises:
        ValueError: If entries are blank, duplicated, malformed, or unknown.
    """

    if values is None:
        return ()

    if not values:
        raise ValueError("capabilities must be a non-empty list when declared")

    declared: list[InvocationCapability] = []
    seen: set[InvocationCapability] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            raise ValueError("capabilities must contain only strings")
        if raw_value.strip() != raw_value or raw_value == "":
            raise ValueError(
                "capabilities must use canonical lowercase kebab-case names"
            )
        try:
            capability = InvocationCapability(raw_value)
        except ValueError as exc:
            raise ValueError(f"unknown invocation capability '{raw_value}'") from exc
        if capability in seen:
            raise ValueError(f"duplicate invocation capability '{raw_value}'")
        seen.add(capability)
        declared.append(capability)

    return tuple(declared)
