"""Output materialization builder for STAR runtime action executions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from star.actions.exceptions import ActionRuntimeOutputError
from star.actions.models.core import ActionSpec, OutputSource, OutputType
from star.actions.models.runtime import (
    ActionExecutionOutput,
    ActionExecutionResult,
    RenderedAction,
)
from star.actions.runtime.file_manager import (
    cleanup_output_file,
    create_ready_file_from_bytes,
    finalize_command_output_file,
)
from star.core.config import Settings
from star.routes.files.schemas import FileMetadata


def build_outputs(
    spec: ActionSpec,
    rendered: RenderedAction,
    execution_result: ActionExecutionResult,
    sanitized_output: ActionExecutionOutput,
    *,
    stdout_as_file: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build final action outputs mapping from runtime execution state.

    Args:
            spec: Runtime action specification.
            rendered: Rendered action state with output placeholder references.
            execution_result: Raw execution result.
            sanitized_output: Sanitized execution output.
            stdout_as_file: Whether sanitized stdout should be materialized as
                `outputs.stdout_file` when allowed by action policy.

    Returns:
            Deterministic mapping of output name to `FileMetadata` or `None`.

    Raises:
            ActionRuntimeOutputError: If output materialization fails unexpectedly.
    """

    if not spec.outputs and not stdout_as_file:
        return {}

    outputs: dict[str, FileMetadata | None] = {}

    if execution_result.returncode != 0:
        for output_name, output_def in spec.outputs.items():
            if output_def.type != OutputType.FILE:
                continue
            if output_def.source == OutputSource.COMMAND:
                file_id = rendered.output_files.get(output_name)
                if file_id is not None:
                    cleanup_output_file(file_id, settings=settings)
            outputs[output_name] = None
        if stdout_as_file:
            outputs["stdout_file"] = None
        return outputs

    try:
        for output_name, output_def in spec.outputs.items():
            if output_def.type != OutputType.FILE:
                continue

            if output_def.source == OutputSource.COMMAND:
                file_id = rendered.output_files.get(output_name)
                if file_id is None:
                    raise ActionRuntimeOutputError(
                        f"Missing command output placeholder for '{output_name}'"
                    )

                outputs[output_name] = finalize_command_output_file(
                    file_id=file_id,
                    action_name=spec.action,
                    output_name=output_name,
                    settings=settings,
                )
                continue

        if stdout_as_file:
            outputs["stdout_file"] = create_ready_file_from_bytes(
                original_filename=f"{spec.action}.stdout.txt",
                content=sanitized_output.stdout,
                extension=".txt",
                mime_type="text/plain",
                settings=settings,
            )

    except ActionRuntimeOutputError:
        _cleanup_known_outputs(outputs, rendered, settings=settings)
        raise
    except Exception as exc:
        _cleanup_known_outputs(outputs, rendered, settings=settings)
        raise ActionRuntimeOutputError("Failed to materialize action outputs") from exc

    return outputs


def _cleanup_known_outputs(
    outputs: dict[str, FileMetadata | None],
    rendered: RenderedAction,
    settings: Settings | None = None,
) -> None:
    """Cleanup already-created output artifacts after materialization errors.

    Args:
            outputs: Partially materialized outputs mapping.
            rendered: Rendered action state.
    """

    seen: set[UUID] = set()

    for value in outputs.values():
        if isinstance(value, FileMetadata):
            cleanup_output_file(value.id, settings=settings)
            seen.add(value.id)

    for file_id in rendered.output_files.values():
        if file_id in seen:
            continue
        cleanup_output_file(file_id, settings=settings)
