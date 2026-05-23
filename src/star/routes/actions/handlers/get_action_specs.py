"""Route handler for GET /v1/actions/{action_id}."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import Request

from star.actions.presentation.serializers import to_action_public_spec
from star.core.errors import ACTION_NOT_FOUND, INTERNAL_ERROR, StarError
from star.routes.actions.schemas import GetActionData


async def get_action_specs_handler(
    request: Request,
    action_id: str,
) -> GetActionData:
    """Return public specification of a single STAR action.

    Args:
            request: FastAPI request.
            action_id: Fully-qualified action identifier.

    Returns:
            Typed public action specification.

    Raises:
            StarError: If action is not found or system fails.
    """

    try:
        registry = getattr(request.app.state, "action_registry", None)
        if registry is None:
            raise StarError(INTERNAL_ERROR, "Action registry not available.")

        try:
            spec = registry.get(action_id)
        except Exception as exc:
            raise StarError(
                ACTION_NOT_FOUND,
                details={"action_id": action_id},
            ) from exc

        public_spec = to_action_public_spec(spec)
        payload = asdict(public_spec)
        payload["tags"] = list(public_spec.tags)
        return GetActionData(**payload)
    except StarError:
        raise
    except Exception as exc:
        raise StarError(INTERNAL_ERROR, "Failed to retrieve action.") from exc
