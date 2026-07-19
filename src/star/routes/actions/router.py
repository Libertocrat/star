"""HTTP route that exposes STAR's action execution endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from star.core.errors import StarError
from star.core.responses import star_error_json_response
from star.core.schemas.envelope import ResponseEnvelope
from star.routes.actions.handlers.execute_action import execute_action_handler
from star.routes.actions.handlers.get_action_specs import get_action_specs_handler
from star.routes.actions.handlers.list_actions import list_actions_handler
from star.routes.actions.schemas import (
    ExecuteActionData,
    ExecuteActionRequest,
    GetActionData,
    ListActionsData,
    ListActionsRequest,
)

router = APIRouter(prefix="/v1", tags=["Actions"])


@router.get(
    "/actions",
    response_model=ResponseEnvelope[ListActionsData],
    summary="List available actions grouped by module with optional filtering.",
    description=(
        "Discover registered actions grouped by module.\n\n"
        "Query parameters:\n"
        "- `q`: Optional free-text search over action name, summary, "
        "description, and effective tags.\n"
        "- `tags`: Optional CSV tag filter (for example "
        "`hashing,checksum`). Tokens are trimmed, normalized to lowercase, "
        "and deduplicated.\n"
        "- `match`: Optional tag matching mode, `any` or `all`.\n\n"
        "Filter behavior:\n"
        "- `tags` without `match` defaults to `any`.\n"
        "- `match` requires `tags`.\n"
        "- When both `q` and `tags` are provided, both filters are combined "
        "with logical AND."
    ),
)
async def list_actions(
    request: Request,
    req: Annotated[ListActionsRequest, Depends()],
) -> JSONResponse | ResponseEnvelope[ListActionsData]:
    """List DSL-defined actions grouped by module with optional filtering."""

    try:
        result = await list_actions_handler(request, req=req)
        return ResponseEnvelope.from_success(result)
    except StarError as exc:
        return star_error_json_response(exc)


@router.get(
    "/actions/{action_id}",
    response_model=ResponseEnvelope[GetActionData],
    summary="Get public specification of an action",
    description=(
        "Retrieve the full public contract of a DSL-defined action.\n\n"
        "Includes:\n"
        "- Arguments\n"
        "- Flags\n"
        "- Outputs\n"
        "- Request/response schemas\n"
    ),
)
async def get_action_specs(
    request: Request,
    action_id: str,
) -> JSONResponse | ResponseEnvelope[GetActionData]:
    """Get public spec for a single action."""

    try:
        result = await get_action_specs_handler(request, action_id)
        return ResponseEnvelope.from_success(result)
    except StarError as exc:
        return star_error_json_response(exc)


@router.post(
    "/actions/{action_id}",
    response_model=ResponseEnvelope[ExecuteActionData],
    summary="Execute a registered action with given parameters.",
)
async def execute_action(
    request: Request,
    action_id: str,
    req: ExecuteActionRequest,
) -> JSONResponse | ResponseEnvelope[ExecuteActionData]:
    """Execute an allow-listed DSL action via the runtime handler."""

    try:
        data = await execute_action_handler(request, action_id, req)
        return ResponseEnvelope.from_success(data)
    except StarError as exc:
        return star_error_json_response(exc)
