"""Public contract and example builders for STAR action specs."""

from __future__ import annotations

from typing import Any

from star.actions.models.core import ActionSpec, ParamType


def _build_required_arg_example_value(
    arg_name: str,
    param_type: ParamType,
    item_type: ParamType | None = None,
) -> Any:
    """Build a deterministic example value for one required argument.

    Args:
        arg_name: Action argument name.
        param_type: Logical argument type.
        item_type: Optional list item type when argument is a list.

    Returns:
        Example value aligned with the declared parameter type.
    """

    if param_type == ParamType.INT:
        return 1

    if param_type == ParamType.FLOAT:
        return 1.0

    if param_type == ParamType.STRING:
        return f"{arg_name}_value"

    if param_type == ParamType.BOOL:
        return True

    if param_type == ParamType.FILE_ID:
        return "3fa85f64-5717-4562-b3fc-2c963f66afa6"

    if param_type == ParamType.LIST:
        if item_type == ParamType.STRING:
            return [f"{arg_name}_item_1", f"{arg_name}_item_2"]
        if item_type == ParamType.FILE_ID:
            return [
                "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "98e56387-3364-4ce2-9c66-44d23ec4e23a",
            ]
        return []

    return None


def _format_arg_type_for_docs(
    param_type: ParamType,
    item_type: ParamType | None = None,
) -> str:
    """Format argument type for public docs and human-readable contracts.

    Args:
        param_type: Logical argument type.
        item_type: Optional list item type when argument is a list.

    Returns:
        Public type label used in docs.
    """

    if param_type != ParamType.LIST:
        return param_type.value
    else:
        if item_type == ParamType.STRING:
            return "list[string]"
        if item_type == ParamType.FILE_ID:
            return "list[file_id]"

        return "list"


def _format_contract_type(
    param_type: ParamType,
    item_type: ParamType | None = None,
) -> str:
    """Format type labels used in params contracts.

    Args:
        param_type: Logical argument type.
        item_type: Optional list item type when argument is a list.

    Returns:
        Contract type label.
    """

    if param_type == ParamType.LIST:
        if item_type == ParamType.STRING:
            return "list[string]"
        if item_type == ParamType.FILE_ID:
            return "list[file_id]"
        return "list"

    return param_type.value


def _build_file_metadata_example(
    *,
    original_filename: str,
    mime_type: str,
    extension: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Build a deterministic file metadata example payload.

    Args:
        original_filename: Original filename shown in API payloads.
        mime_type: MIME type shown in API payloads.
        extension: File extension including leading dot.
        size_bytes: File size in bytes.

    Returns:
        Dictionary matching the public file metadata shape.
    """

    return {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "original_filename": original_filename,
        "stored_filename": "file_3fa85f64-5717-4562-b3fc-2c963f66afa6.bin",
        "mime_type": mime_type,
        "extension": extension,
        "size_bytes": size_bytes,
        "sha256": "8e9aa02fb68dfb526d787f6b66adda7b651dd3f9f3b4a03e266d466161f4c39e",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "status": "ready",
    }


def _build_outputs_example(spec: ActionSpec) -> dict[str, Any] | None:
    """Build an outputs example payload for declared action outputs.

    Args:
        spec: Runtime action specification.

    Returns:
        Outputs example mapping for YAML-declared outputs, or None when the
        action has no declared outputs.
    """

    if not spec.outputs and not spec.allow_stdout_as_file:
        return None

    outputs_example: dict[str, Any] = {}

    for output_name, output_def in spec.outputs.items():
        if output_def.type.value != "file":
            outputs_example[output_name] = None
            continue

        outputs_example[output_name] = _build_file_metadata_example(
            original_filename=f"action.{output_name}.bin",
            mime_type="application/octet-stream",
            extension=".bin",
            size_bytes=1024,
        )

    return outputs_example


def build_params_contract(spec: ActionSpec) -> dict[str, Any]:
    """Build the public params contract for one action.

    Args:
        spec: Runtime action specification.

    Returns:
        Public contract dictionary for request params.
    """

    params: dict[str, dict[str, Any]] = {}
    required: list[str] = []

    for arg_name, arg_def in spec.arg_defs.items():
        contract_type = _format_contract_type(arg_def.type, arg_def.items)
        item_contract: dict[str, Any] = {
            "type": contract_type,
            "required": bool(arg_def.required),
            "default": spec.defaults.get(arg_name),
            "description": arg_def.description,
            "constraints": arg_def.constraints,
        }

        if arg_def.type == ParamType.FILE_ID or (
            arg_def.type == ParamType.LIST and arg_def.items == ParamType.FILE_ID
        ):
            item_contract["format"] = "uuid4"

        params[arg_name] = item_contract

        if arg_def.required:
            required.append(arg_name)

    for flag_name, flag_def in spec.flag_defs.items():
        params[flag_name] = {
            "type": "bool",
            "required": False,
            "default": flag_def.default,
            "description": flag_def.description,
            "constraints": None,
        }

    return {
        "params": params,
        "stdout_as_file": {
            "type": "bool",
            "required": False,
            "default": False,
            "allowed": spec.allow_stdout_as_file,
            "description": (
                "When true, STAR stores sanitized stdout as a managed text file "
                "under outputs.stdout_file if the action allows it."
            ),
        },
        "required": required,
    }


def build_params_example(spec: ActionSpec) -> dict[str, Any]:
    """Build an example params payload for one action.

    Args:
        spec: Runtime action specification.

    Returns:
        Example params dictionary.
    """

    if spec.params_example is not None:
        return spec.params_example.model_dump(exclude_none=False)

    params_example: dict[str, Any] = {}

    for arg_name, arg_def in spec.arg_defs.items():
        if arg_def.required:
            params_example[arg_name] = _build_required_arg_example_value(
                arg_name,
                arg_def.type,
                arg_def.items,
            )
            continue

        if arg_name in spec.defaults:
            params_example[arg_name] = spec.defaults[arg_name]

    for flag_name, flag_def in spec.flag_defs.items():
        params_example[flag_name] = flag_def.default

    return params_example


def build_response_contract(spec: ActionSpec) -> dict[str, Any]:
    """Build the public response contract for one action.

    Args:
        spec: Runtime action specification.

    Returns:
        Public response contract dictionary.
    """

    outputs_contract: dict[str, Any] | None = None

    if spec.outputs or spec.allow_stdout_as_file:
        outputs_contract = {}
        for output_name, output_def in spec.outputs.items():
            output_type = (
                "FileMetadata"
                if output_def.type.value == "file"
                else output_def.type.value
            )
            outputs_contract[output_name] = {
                "type": output_type,
                "source": output_def.source.value,
                "description": output_def.description,
                "nullable": True,
            }

        if spec.allow_stdout_as_file:
            outputs_contract["stdout_file"] = {
                "type": "FileMetadata",
                "source": "stdout",
                "description": (
                    "Managed text file created from sanitized stdout when "
                    "request option stdout_as_file is true."
                ),
                "nullable": True,
                "reserved": True,
            }

    return {
        "success": {"type": "bool"},
        "error": {"type": "object", "nullable": True},
        "data": {
            "exit_code": {"type": "int"},
            "stdout": {"type": "string"},
            "stdout_encoding": {"type": "string"},
            "stderr": {"type": "string"},
            "stderr_encoding": {"type": "string"},
            "exec_time": {"type": "float"},
            "pid": {"type": "int", "nullable": True},
            "truncated": {"type": "bool"},
            "redacted": {"type": "bool"},
            "outputs": outputs_contract,
        },
    }


def build_response_example(spec: ActionSpec) -> dict[str, Any]:
    """Build an example execute response payload for one action.

    Args:
        spec: Runtime action specification.

    Returns:
        Example response dictionary using the public envelope shape.
    """

    outputs_example = _build_outputs_example(spec)

    return {
        "success": True,
        "error": None,
        "data": {
            "exit_code": 0,
            "stdout": "",
            "stdout_encoding": "utf-8",
            "stderr": "",
            "stderr_encoding": "utf-8",
            "exec_time": 0.01,
            "pid": 12345,
            "truncated": False,
            "redacted": False,
            "outputs": outputs_example,
        },
    }


def build_action_contracts(spec: ActionSpec) -> dict[str, Any]:
    """Build all public contracts and examples for one action.

    Args:
        spec: Runtime action specification.

    Returns:
        Mapping containing params and response contracts plus examples.
    """

    return {
        "params_contract": build_params_contract(spec),
        "params_example": build_params_example(spec),
        "response_contract": build_response_contract(spec),
        "response_example": build_response_example(spec),
    }
