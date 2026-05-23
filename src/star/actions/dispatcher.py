"""Runtime dispatcher for STAR action execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from star.actions.exceptions import ActionRuntimeExecError
from star.actions.models import ActionExecutionResult, ActionSpec, RenderedAction
from star.actions.registry import ActionRegistry
from star.actions.runtime import executor as runtime_executor
from star.actions.runtime.file_manager import cleanup_output_placeholders
from star.actions.runtime.renderer import render_command
from star.core.config import Settings


@dataclass(frozen=True, slots=True)
class DispatchedActionResult:
    """Runtime dispatch result preserving rendered and executed state.

    Attributes:
        rendered: Rendered command state used for subprocess execution.
        execution: Completed subprocess result.
        spec: Action specification resolved for this dispatch.
    """

    rendered: RenderedAction
    execution: ActionExecutionResult
    spec: ActionSpec


async def dispatch_action(
    registry: ActionRegistry,
    action_name: str,
    params: dict[str, Any],
    settings: Settings | None = None,
) -> DispatchedActionResult:
    """Resolve, validate, render and execute an action.

    This function is intentionally HTTP-agnostic and lets runtime exceptions
    propagate unchanged so they can be translated by the route handler layer.
    """

    action_spec = registry.get(action_name)
    validated = action_spec.params_model.model_validate(params)
    params_dict = validated.model_dump(mode="python")
    rendered = render_command(action_spec, params_dict, settings=settings)
    try:
        execution = await runtime_executor.execute_command(rendered.argv, action_spec)
    except ActionRuntimeExecError:
        cleanup_output_placeholders(rendered.output_files, settings=settings)
        raise

    return DispatchedActionResult(
        rendered=rendered, execution=execution, spec=action_spec
    )
