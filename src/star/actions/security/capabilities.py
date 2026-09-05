"""Reviewed extension capabilities for the STAR action DSL."""

from __future__ import annotations

from enum import Enum


class ExtensionCapability(str, Enum):
    """Reviewed execution capabilities available to mounted DSL modules.

    Attributes:
        FILE_INSPECTION: Safe managed-file inspection utilities.
        TEXT_SEARCH: Bounded text-pattern matching utilities.
        CHECKSUM: Managed-file SHA-256 checksum utilities.
    """

    FILE_INSPECTION = "file-inspection"
    TEXT_SEARCH = "text-search"
    CHECKSUM = "checksum"


def resolve_enabled_extension_capabilities(
    value: str,
) -> frozenset[ExtensionCapability]:
    """Resolve one normalized settings value into enabled capabilities.

    Args:
        value: Canonical ``all``, ``none``, or comma-separated capability names.

    Returns:
        Enabled reviewed capabilities.

    Raises:
        ValueError: If a configured capability is not part of the reviewed catalog.
    """

    if value == "all":
        return frozenset(ExtensionCapability)

    if value == "none":
        return frozenset()

    resolved: set[ExtensionCapability] = set()
    for name in value.split(","):
        try:
            resolved.add(ExtensionCapability(name))
        except ValueError as exc:
            raise ValueError(
                f"unknown extension capability '{name}' in operator configuration"
            ) from exc

    return frozenset(resolved)


def parse_declared_capabilities(
    values: list[str] | None,
) -> tuple[ExtensionCapability, ...]:
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

    declared: list[ExtensionCapability] = []
    seen: set[ExtensionCapability] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            raise ValueError("capabilities must contain only strings")
        if raw_value.strip() != raw_value or raw_value == "":
            raise ValueError(
                "capabilities must use canonical lowercase kebab-case names"
            )
        try:
            capability = ExtensionCapability(raw_value)
        except ValueError as exc:
            raise ValueError(f"unknown extension capability '{raw_value}'") from exc
        if capability in seen:
            raise ValueError(f"duplicate extension capability '{raw_value}'")
        seen.add(capability)
        declared.append(capability)

    return tuple(declared)
