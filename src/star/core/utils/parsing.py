"""Lightweight parsing utilities for configuration and text processing.

This module provides small, reusable helpers for parsing common string-based
inputs such as CSV values and environment variables. All functions are pure,
side-effect free, and independent from application-specific logic.
"""

from __future__ import annotations


def parse_csv(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated string into a tuple of cleaned tokens.

    This function splits a CSV string by commas, strips surrounding whitespace
    from each token, and removes empty entries.

    Args:
        value: Raw CSV string (e.g. "a,b,c") or None.

    Returns:
        Tuple of non-empty, trimmed string tokens.

    Examples:
        >>> parse_csv("a,b,c")
        ("a", "b", "c")

        >>> parse_csv(" a , , b ")
        ("a", "b")

        >>> parse_csv(None)
        ()
    """
    if not value:
        return ()

    return tuple(token.strip() for token in value.split(",") if token.strip())


def parse_csv_set(value: str | None) -> set[str]:
    """Parse a comma-separated string into a set of cleaned tokens.

    This function behaves like `parse_csv` but returns a set instead of a tuple,
    removing duplicate values.

    Args:
        value: Raw CSV string or None.

    Returns:
        Set of unique, non-empty, trimmed string tokens.

    Examples:
        >>> parse_csv_set("a,b,a")
        {"a", "b"}
    """
    return set(parse_csv(value))


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse a string into a boolean value.

    Accepted truthy values:
        "1", "true", "yes", "on"

    Accepted falsy values:
        "0", "false", "no", "off"

    Parsing is case-insensitive and ignores surrounding whitespace.

    Args:
        value: Raw string value or None.
        default: Value to return when input is None or empty.

    Returns:
        Parsed boolean value.

    Raises:
        ValueError: If the input string is not a recognized boolean value.

    Examples:
        >>> parse_bool("true")
        True

        >>> parse_bool("0")
        False

        >>> parse_bool(None, default=True)
        True
    """
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized == "":
        return default

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"Invalid boolean value: '{value}'")


def parse_int(value: str | None, default: int | None = None) -> int:
    """Parse a string into an integer.

    Args:
        value: Raw string value or None.
        default: Optional default value if input is None or empty.

    Returns:
        Parsed integer.

    Raises:
        ValueError: If value is not a valid integer and no default is provided.

    Examples:
        >>> parse_int("42")
        42

        >>> parse_int(None, default=10)
        10
    """
    if value is None or value.strip() == "":
        if default is not None:
            return default
        raise ValueError("Integer value is required")

    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid integer value: '{value}'") from exc


def parse_float(value: str | None, default: float | None = None) -> float:
    """Parse a string into a float.

    Args:
        value: Raw string value or None.
        default: Optional default value if input is None or empty.

    Returns:
        Parsed float.

    Raises:
        ValueError: If value is not a valid float and no default is provided.

    Examples:
        >>> parse_float("3.14")
        3.14

        >>> parse_float("", default=1.0)
        1.0
    """
    if value is None or value.strip() == "":
        if default is not None:
            return default
        raise ValueError("Float value is required")

    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid float value: '{value}'") from exc
