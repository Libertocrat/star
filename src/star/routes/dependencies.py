"""Typed runtime dependency helpers for STAR route boundaries."""

from __future__ import annotations

from fastapi import Request

from star.actions.registry import ActionRegistry
from star.core.config import Settings
from star.core.errors import INTERNAL_ERROR, StarError


def get_runtime_settings(request: Request) -> Settings:
    """Resolve the runtime settings from application state.

    Args:
        request: Incoming FastAPI request.

    Returns:
        Validated runtime Settings instance.

    Raises:
        StarError: If settings are missing or have an unexpected type.
    """

    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise StarError(
            INTERNAL_ERROR,
            message="Runtime settings are not available.",
        )
    return settings


def get_action_registry(request: Request) -> ActionRegistry:
    """Resolve the runtime action registry from application state.

    Args:
        request: Incoming FastAPI request.

    Returns:
        Validated runtime ActionRegistry instance.

    Raises:
        StarError: If the registry is missing or has an unexpected type.
    """

    registry = getattr(request.app.state, "action_registry", None)
    if not isinstance(registry, ActionRegistry):
        raise StarError(
            INTERNAL_ERROR,
            message="Action registry is not available.",
        )
    return registry
