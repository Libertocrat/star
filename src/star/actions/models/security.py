"""Security-related runtime models for STAR actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BinaryPolicy:
    """Execution policy for allowed and blocked binaries.

    Attributes:
        allowed: Tuple of binaries explicitly allowed for execution.
        blocked: Tuple of binaries explicitly blocked for execution.
    """

    allowed: tuple[str, ...]
    blocked: tuple[str, ...]
