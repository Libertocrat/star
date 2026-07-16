"""Serialization helpers for STAR action presentation models."""

from __future__ import annotations

from typing import Any

from star.actions.models.core import ActionSpec
from star.actions.models.presentation import (
    ActionPublicSpec,
    ActionSummary,
    ModuleSummary,
)
from star.actions.presentation.contracts import (
    build_params_contract,
    build_params_example,
    build_response_contract,
    build_response_example,
)


def to_action_summary(spec: ActionSpec) -> ActionSummary:
    """Convert one runtime action spec into a discovery summary.

    Args:
        spec: Runtime action specification from the registry.

    Returns:
        API-safe action summary model.
    """

    return ActionSummary(
        action=spec.action,
        action_id=spec.name,
        summary=spec.summary,
        description=spec.description,
        tags=spec.tags,
    )


def to_action_public_spec(spec: ActionSpec) -> ActionPublicSpec:
    """Convert one runtime action spec into a detailed public contract.

    Args:
        spec: Runtime action specification from the registry.

    Returns:
        API-facing detailed action specification.
    """

    args = [
        {
            "name": name,
            "type": arg.type.value,
            "required": arg.required,
            "default": spec.defaults.get(name),
            "constraints": arg.constraints,
            "description": arg.description,
            "sensitive": arg.type.value == "secret",
        }
        for name, arg in spec.arg_defs.items()
    ]

    flags = [
        {
            "name": name,
            "default": flag.default,
            "description": flag.description,
        }
        for name, flag in spec.flag_defs.items()
    ]

    outputs = [
        {
            "name": name,
            "type": out.type.value,
            "source": out.source.value,
            "description": out.description,
        }
        for name, out in spec.outputs.items()
    ]

    return ActionPublicSpec(
        action_id=spec.name,
        action=spec.action,
        summary=spec.summary,
        description=spec.description,
        tags=spec.tags,
        allow_stdout_as_file=spec.allow_stdout_as_file,
        args=args,
        flags=flags,
        outputs=outputs,
        params_contract=build_params_contract(spec),
        params_example=build_params_example(spec),
        response_contract=build_response_contract(spec),
        response_example=build_response_example(spec),
    )


def module_summary_to_dict(module: ModuleSummary) -> dict[str, Any]:
    """Convert one module summary model into API response shape.

    Args:
        module: Public module summary model.

    Returns:
        Dictionary payload compatible with JSON responses.
    """

    return {
        "module": module.module,
        "module_id": module.module_id,
        "namespace": module.namespace,
        "namespace_path": list(module.namespace_path),
        "description": module.description,
        "tags": list(module.tags),
        "authors": list(module.authors or []),
        "actions": [
            {
                "action": action.action,
                "action_id": action.action_id,
                "summary": action.summary,
                "description": action.description,
                "tags": list(action.tags),
            }
            for action in module.actions
        ],
    }


def modules_to_response(modules: list[ModuleSummary]) -> dict[str, Any]:
    """Convert module summaries into the discovery response payload.

    Args:
        modules: Public module summaries.

    Returns:
        Root dictionary payload for module discovery endpoints.
    """

    return {"modules": [module_summary_to_dict(module) for module in modules]}
