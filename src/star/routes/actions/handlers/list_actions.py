"""Route handler for STAR `GET /v1/actions` discovery endpoint."""

from __future__ import annotations

import csv

from fastapi import Request

from star.actions.engine_config import TAG_NAME_PATTERN
from star.actions.presentation.catalog import filter_modules
from star.actions.presentation.serializers import modules_to_response
from star.core.errors import INTERNAL_ERROR, INVALID_PARAMS, StarError
from star.routes.actions.schemas import (
    ListActionsData,
    ListActionsRequest,
    TagMatchMode,
)


def _validate_query_param(value: str | None, name: str) -> str | None:
    """Normalize and validate an optional query parameter value.

    Args:
        value: Raw query parameter value.
        name: Query parameter name for error context.

    Returns:
        Normalized query value or None when unset.

    Raises:
        StarError: If the parameter contains disallowed characters.
    """

    if value is None:
        return None

    value = value.strip()

    # Basic hardening: reject NUL bytes in query values.
    if "\x00" in value:
        raise StarError(
            INVALID_PARAMS,
            f"Invalid {name} parameter.",
            details={"param": name},
        )

    return value


async def list_actions_handler(
    request: Request,
    req: ListActionsRequest,
) -> ListActionsData:
    """List available STAR actions grouped by module with optional filters.

    Args:
        request: Incoming FastAPI request.
        req: Typed action discovery query parameters.

    Returns:
        Typed discovery payload for module summaries.

    Raises:
        StarError: If registry access, validation, or filtering fails.
    """

    try:
        registry = getattr(request.app.state, "action_registry", None)
        if registry is None:
            raise StarError(INTERNAL_ERROR, "Action registry not available.")

        q = _validate_query_param(req.q, "q")
        parsed_tags = _parse_tags_query_param(req.tags)
        tag_match = _validate_tag_match(req.match, has_tags=bool(parsed_tags))

        modules = registry.module_summaries
        filtered = filter_modules(modules, q=q, tags=parsed_tags, match=tag_match)
        response_dict = modules_to_response(filtered)

        return ListActionsData(**response_dict)
    except StarError:
        raise
    except Exception as exc:
        raise StarError(INTERNAL_ERROR, "Failed to list actions.") from exc


def _parse_tags_query_param(value: str | None) -> tuple[str, ...]:
    """Parse and validate the optional tags query parameter.

    Args:
        value: Raw `tags` query parameter value.

    Returns:
        Normalized, deduplicated tag tuple preserving first appearance.

    Raises:
        StarError: If the parameter is blank, invalid CSV, contains empty
            entries, NUL bytes, or invalid tag tokens.
    """

    if value is None:
        return ()

    normalized_value = value.strip()

    def _raise_invalid_tags() -> None:
        raise StarError(
            INVALID_PARAMS,
            "Invalid tags parameter.",
            details={"param": "tags"},
        )

    if normalized_value == "" or "\x00" in normalized_value:
        _raise_invalid_tags()

    try:
        rows = list(csv.reader([normalized_value]))
    except csv.Error:
        _raise_invalid_tags()

    if not rows or not rows[0]:
        _raise_invalid_tags()

    parsed: list[str] = []
    seen: set[str] = set()

    for raw_token in rows[0]:
        token = raw_token.strip()
        if token == "" or "\x00" in token:
            _raise_invalid_tags()
        if not TAG_NAME_PATTERN.fullmatch(token):
            _raise_invalid_tags()

        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        parsed.append(lowered)

    if not parsed:
        _raise_invalid_tags()

    return tuple(parsed)


def _validate_tag_match(
    match_value: str | None,
    *,
    has_tags: bool,
) -> str:
    """Validate the optional tag match mode.

    Args:
        match_value: Raw `match` query parameter value.
        has_tags: Whether a non-empty `tags` filter was provided.

    Returns:
        Normalized match value, defaulting to `any`.

    Raises:
        StarError: If match is invalid or provided without tags.
    """

    if match_value is None:
        return TagMatchMode.ANY.value

    match_normalized = match_value.strip().lower()
    allowed_matches = {mode.value for mode in TagMatchMode}
    if (
        match_normalized == ""
        or "\x00" in match_normalized
        or match_normalized not in allowed_matches
    ):
        raise StarError(
            INVALID_PARAMS,
            "Invalid match parameter.",
            details={"param": "match", "allowed": sorted(allowed_matches)},
        )

    if not has_tags:
        raise StarError(
            INVALID_PARAMS,
            "The match parameter requires tags.",
            details={"param": "match", "requires": "tags"},
        )

    return match_normalized
