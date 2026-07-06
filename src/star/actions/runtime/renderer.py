"""Runtime command renderer for STAR DSL actions.

This module transforms a validated `ActionSpec` plus validated runtime params
into a fully resolved `RenderedAction` for subprocess-safe execution.
"""

from __future__ import annotations

import unicodedata
from typing import Any, cast
from uuid import UUID

from pydantic import UUID4

from star.actions.engine_config import CONST_TEMPLATE_PLACEHOLDER_PATTERN
from star.actions.exceptions import (
    ActionInvalidArgError,
    ActionRuntimeError,
    ActionRuntimeRenderError,
)
from star.actions.models.core import (
    ActionSpec,
    ArgCmd,
    ArgDef,
    BinaryCmd,
    ConstCmd,
    FlagCmd,
    OutputCmd,
    ParamType,
)
from star.actions.models.runtime import RenderedAction
from star.actions.runtime.file_manager import (
    cleanup_output_placeholders,
    create_command_output_placeholders,
    resolve_output_blob_path,
)
from star.core.config import Settings
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import get_blob_path, load_file_metadata


def render_command(
    spec: ActionSpec,
    params: dict[str, Any],
    settings: Settings | None = None,
) -> RenderedAction:
    """Render a validated action invocation into final runtime render state.

    Pipeline (strict order):
        1. Merge defaults + params.
        2. Reject any resolved value that is None.
        3. Resolve `file_id` args into persisted blob paths + metadata.
        4. Apply type-specific runtime constraints.
        5. Build and return final argv from command_template.

    Args:
        spec: Fully built immutable action runtime specification.
        params: Already-validated params payload for the action.

    Returns:
        RenderedAction containing final argv and output placeholder file ids.

    Raises:
        ActionInvalidArgError: If any runtime value is invalid.
        ActionRuntimeRenderError: If rendering fails due to internal errors.
    """

    try:
        resolved: dict[str, Any] = {**spec.defaults, **params}

        for name, value in resolved.items():
            if value is None:
                raise ActionInvalidArgError(f"Param '{name}' cannot be None")

        resolved_arg_values: dict[str, list[str]] = {}
        for name, arg_def in spec.arg_defs.items():
            try:
                resolved_arg_values[name] = _resolve_arg(
                    arg_def,
                    resolved[name],
                    settings=settings,
                )
            except ActionInvalidArgError as exc:
                raise ActionInvalidArgError(
                    f"Param '{name}' is invalid: {exc}"
                ) from exc

        output_files = create_command_output_placeholders(spec, settings=settings)

        argv: list[str] = []
        for token in spec.command_template:
            kind = token["kind"]

            if kind == "binary":
                binary_token = cast(BinaryCmd, token)
                argv.append(binary_token["value"])
                continue

            if kind == "const":
                const_token = cast(ConstCmd, token)
                argv.append(
                    _render_const_literal(
                        const_token["value"],
                        spec=spec,
                        resolved=resolved,
                    )
                )
                continue

            if kind == "arg":
                arg_token = cast(ArgCmd, token)
                name = arg_token["name"]
                argv.extend(resolved_arg_values[name])
                continue

            if kind == "flag":
                flag_token = cast(FlagCmd, token)
                name = flag_token["name"]
                if resolved[name] is True:
                    argv.append(spec.flag_defs[name].value)
                continue

            if kind == "output":
                output_token = cast(OutputCmd, token)
                output_name = output_token["name"]
                file_id = output_files.get(output_name)
                if file_id is None:
                    raise ActionRuntimeRenderError(
                        f"Output '{output_name}' has no command placeholder"
                    )
                argv.append(resolve_output_blob_path(file_id, settings=settings))
                continue

            raise ActionRuntimeRenderError(f"Unsupported command token kind: {kind}")

        return RenderedAction(argv=argv, output_files=output_files)

    except ActionRuntimeError:
        if "output_files" in locals() and output_files:
            cleanup_output_placeholders(output_files, settings=settings)
        raise
    except Exception as exc:
        if "output_files" in locals() and output_files:
            cleanup_output_placeholders(output_files, settings=settings)
        raise ActionRuntimeRenderError(
            "Unexpected failure while rendering command"
        ) from exc


def _render_const_literal(
    literal: str,
    *,
    spec: ActionSpec,
    resolved: dict[str, Any],
) -> str:
    """Render one `const` command token with restricted placeholders.

    Args:
        literal: Raw const token value from the command template.
        spec: Action runtime specification.
        resolved: Resolved runtime params after defaults + request params merge.

    Returns:
        Final rendered const token value.

    Raises:
        ActionInvalidArgError: If a referenced arg value is invalid.
        ActionRuntimeRenderError: If validated template metadata cannot be
            resolved at runtime due to an internal inconsistency.
    """

    if "{" not in literal and "}" not in literal:
        return literal

    placeholders = tuple(
        dict.fromkeys(CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(literal))
    )
    rendered = literal

    for placeholder_name in placeholders:
        try:
            arg_def = spec.arg_defs[placeholder_name]
            placeholder_value = resolved[placeholder_name]
        except KeyError as exc:
            raise ActionRuntimeRenderError(
                "Unexpected const template resolution failure"
            ) from exc

        replacement = _resolve_const_placeholder_value(
            name=placeholder_name,
            arg_def=arg_def,
            value=placeholder_value,
        )
        rendered = rendered.replace(f"{{{placeholder_name}}}", replacement)

    _validate_rendered_const_token(rendered)
    return rendered


def _resolve_const_placeholder_value(name: str, arg_def: ArgDef, value: Any) -> str:
    """Resolve one const placeholder from a runtime arg value.

    Args:
        name: Placeholder/arg name.
        arg_def: Runtime arg definition.
        value: Resolved runtime arg value.

    Returns:
        String value to inject into const literal.

    Raises:
        ActionInvalidArgError: If value is invalid for template interpolation.
        ActionRuntimeRenderError: If a validated placeholder resolves to an
            unexpected arg type at runtime.
    """

    constraints = arg_def.constraints or {}

    if arg_def.type in {ParamType.INT, ParamType.FLOAT}:
        _validate_numeric_constraints(name, value, constraints)
        return str(value)

    if arg_def.type == ParamType.STRING:
        _validate_string_constraints(name, value, constraints)
        string_value = cast(str, value)
        _validate_template_string_value(name, string_value)
        return string_value

    raise ActionRuntimeRenderError(
        ("Unexpected const template arg type " f"'{arg_def.type.value}' for '{name}'")
    )


def _validate_template_string_value(name: str, value: str) -> None:
    """Validate runtime string value safety for const placeholder injection.

    Args:
        name: Placeholder/arg name.
        value: Runtime string value.

    Raises:
        ActionInvalidArgError: If value is unsafe for interpolation.
    """

    if any(character.isspace() for character in value):
        raise ActionInvalidArgError(
            (
                f"Param '{name}' cannot contain whitespace when "
                "used in const template placeholders"
            )
        )

    if "\x00" in value:
        raise ActionInvalidArgError(
            (
                f"Param '{name}' cannot contain NULL bytes when "
                "used in const template placeholders"
            )
        )

    if _contains_control_characters(value):
        raise ActionInvalidArgError(
            (
                f"Param '{name}' cannot contain control characters when "
                "used in const template placeholders"
            )
        )


def _validate_rendered_const_token(value: str) -> None:
    """Validate the final rendered const token before appending to argv.

    Args:
        value: Final rendered const token value.

    Raises:
        ActionInvalidArgError: If rendered token violates token safety rules.
    """

    if value == "":
        raise ActionInvalidArgError("Rendered const token must not be empty")

    if value.strip() == "":
        raise ActionInvalidArgError("Rendered const token must not be whitespace-only")

    if "\x00" in value:
        raise ActionInvalidArgError("Rendered const token must not contain NULL bytes")

    if _contains_control_characters(value):
        raise ActionInvalidArgError(
            "Rendered const token must not contain control characters"
        )


def _resolve_arg(
    arg_def: ArgDef,
    value: Any,
    settings: Settings | None = None,
) -> list[str]:
    """Resolve and validate one argument into argv-safe tokens.

    Args:
        arg_def: Runtime argument definition.
        value: User-provided value.
        settings: Optional pre-loaded runtime settings.

    Returns:
        List of string tokens to be appended to argv.

    Raises:
        ActionInvalidArgError: If value is invalid.
    """

    constraints = arg_def.constraints or {}

    if arg_def.type == ParamType.INT or arg_def.type == ParamType.FLOAT:
        _validate_numeric_constraints("value", value, constraints)
        return [str(value)]

    if arg_def.type == ParamType.STRING:
        _validate_string_constraints("value", value, constraints)
        return [value]

    if arg_def.type == ParamType.FILE_ID:
        file_uuid = _coerce_file_id("value", value)
        blob_path, metadata = _resolve_file_id_to_path(
            file_uuid,
            arg_def,
            settings=settings,
        )
        _validate_file_constraints("value", metadata, constraints)
        return [blob_path]

    if arg_def.type == ParamType.BOOL:
        if type(value) is not bool:
            raise ActionInvalidArgError("must be a boolean")
        return [str(value)]

    if arg_def.type == ParamType.LIST:
        _validate_list_constraints("value", value, constraints)
        resolved_values: list[str] = []

        if arg_def.items == ParamType.STRING:
            for item in value:
                _validate_string_constraints("value", item, {})
                resolved_values.append(item)
            return resolved_values

        elif arg_def.items == ParamType.FILE_ID:
            for item in value:
                file_uuid = _coerce_file_id("value", item)
                resolved_values.append(
                    _resolve_single_file_id(file_uuid, settings=settings)
                )
            return resolved_values

        items_type = (
            arg_def.items if arg_def.items is not None else "unknown items type"
        )
        raise ActionInvalidArgError(f"unsupported list item type '{items_type}'")

    raise ActionInvalidArgError(f"unsupported argument type '{arg_def.type.value}'")


def _resolve_single_file_id(
    file_id: UUID4,
    settings: Settings | None = None,
) -> str:
    """Resolve a single file_id into a safe filesystem path.

    Args:
        file_id: UUID of the file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Absolute path to the file blob.

    Raises:
        ActionInvalidArgError: If metadata or blob is missing.
    """

    blob_path, _ = _resolve_file_id_to_path(
        cast(UUID, file_id),
        ArgDef(type=ParamType.FILE_ID, required=True, description="file_id"),
        settings=settings,
    )
    return blob_path


def _resolve_file_id_to_path(
    file_id: UUID,
    arg_def: ArgDef,
    settings: Settings | None = None,
) -> tuple[str, FileMetadata]:
    """Resolve a `file_id` argument into blob path and loaded metadata.

    Args:
        file_id: File UUID to resolve.
        arg_def: Runtime argument definition for the file parameter.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Tuple of `(blob_path_str, metadata)`.

    Raises:
        ActionInvalidArgError: If metadata or blob file does not exist.
    """

    _ = arg_def

    metadata = load_file_metadata(file_id, settings)
    if metadata is None:
        raise ActionInvalidArgError(f"File '{file_id}' was not found")

    if metadata.status != "ready":
        raise ActionInvalidArgError(f"File '{file_id}' is not ready for use")

    blob_path = get_blob_path(file_id, settings)
    if not blob_path.exists():
        raise ActionInvalidArgError(f"File blob for '{file_id}' was not found")

    return str(blob_path), metadata


def _validate_arg(
    name: str,
    value: Any,
    arg_def: ArgDef,
    metadata: FileMetadata | None,
) -> None:
    """Apply type-specific runtime constraints to a resolved argument.

    Args:
        name: Argument name.
        value: Resolved runtime value (file_id already converted to path).
        arg_def: Argument definition with declared type/constraints.
        metadata: Pre-loaded file metadata for file_id args; otherwise None.

    Raises:
        ActionInvalidArgError: If validation fails.
    """

    constraints = arg_def.constraints or {}

    if arg_def.type in (ParamType.INT, ParamType.FLOAT):
        _validate_numeric_constraints(name, value, constraints)
        return

    if arg_def.type == ParamType.STRING:
        _validate_string_constraints(name, value, constraints)
        return

    if arg_def.type == ParamType.FILE_ID:
        _validate_file_constraints(name, metadata, constraints)
        return

    if arg_def.type == ParamType.LIST:
        _validate_list_constraints(name, value, constraints)
        return


def _validate_numeric_constraints(
    name: str,
    value: Any,
    constraints: dict[str, Any],
) -> None:
    """Validate numeric bounds for int/float arguments.

    Args:
        name: Argument name.
        value: Runtime numeric value.
        arg_def: Numeric argument definition.

    Raises:
        ActionInvalidArgError: If value is not numeric or outside constraints.
    """

    min_value = constraints.get("min")
    max_value = constraints.get("max")

    if type(value) not in {int, float}:
        raise ActionInvalidArgError(f"Param '{name}' must be numeric")

    if min_value is not None and value < min_value:
        raise ActionInvalidArgError(
            f"Param '{name}' must be greater than or equal to {min_value}"
        )

    if max_value is not None and value > max_value:
        raise ActionInvalidArgError(
            f"Param '{name}' must be less than or equal to {max_value}"
        )


def _validate_string_constraints(
    name: str,
    value: Any,
    constraints: dict[str, Any],
) -> None:
    """Validate runtime string constraints.

    Args:
        name: Argument name.
        value: Runtime string value.

    Raises:
        ActionInvalidArgError: If value is empty/whitespace or flag-like.
    """

    if not isinstance(value, str):
        raise ActionInvalidArgError(f"Param '{name}' must be a string")

    stripped = value.strip()

    if stripped == "":
        raise ActionInvalidArgError(f"Param '{name}' cannot be empty")

    if stripped.startswith("-"):
        raise ActionInvalidArgError(
            f"Param '{name}' cannot start with '-' to avoid flag injection"
        )

    min_length = constraints.get("min_length")
    max_length = constraints.get("max_length")
    allowed_values = constraints.get("allowed_values")

    if min_length is not None and len(value) < min_length:
        raise ActionInvalidArgError(f"Param '{name}' must have length >= {min_length}")

    if max_length is not None and len(value) > max_length:
        raise ActionInvalidArgError(f"Param '{name}' must have length <= {max_length}")

    if allowed_values is not None and value not in allowed_values:
        raise ActionInvalidArgError(
            f"Param '{name}' must be one of: "
            f"{', '.join(str(item) for item in allowed_values)}"
        )


def _validate_file_constraints(
    name: str,
    metadata: FileMetadata | None,
    constraints: dict[str, Any],
) -> None:
    """Validate resolved file constraints using preloaded metadata.

    Args:
        name: Argument name.
        metadata: Loaded file metadata.
        arg_def: File argument definition.

    Raises:
        ActionInvalidArgError: If metadata is missing or max_size exceeded.
    """

    if metadata is None:
        raise ActionInvalidArgError(f"Param '{name}' could not resolve file metadata")

    max_size = constraints.get("max_size")
    allowed_extensions = constraints.get("allowed_extensions")
    allowed_mime_types = constraints.get("allowed_mime_types")

    if max_size is not None and metadata.size_bytes > max_size:
        raise ActionInvalidArgError(
            f"Param '{name}' file size must be <= {max_size} bytes"
        )

    if allowed_extensions is not None:
        normalized_extension = metadata.extension.lower().lstrip(".")
        allowed = {extension.lower().lstrip(".") for extension in allowed_extensions}
        if normalized_extension not in allowed:
            raise ActionInvalidArgError(
                f"Param '{name}' extension '{metadata.extension}' is not allowed"
            )

    if allowed_mime_types is not None:
        normalized_mime = metadata.mime_type.lower()
        allowed = {mime_type.lower() for mime_type in allowed_mime_types}
        if normalized_mime not in allowed:
            raise ActionInvalidArgError(
                f"Param '{name}' mime type '{metadata.mime_type}' is not allowed"
            )


def _validate_list_constraints(
    name: str,
    value: Any,
    constraints: dict[str, Any],
) -> None:
    """Validate runtime list constraints.

    Args:
        name: Argument name.
        value: Runtime value.
        constraints: Declared list constraints.

    Raises:
        ActionInvalidArgError: If list validation fails.
    """

    if not isinstance(value, list):
        raise ActionInvalidArgError(f"Param '{name}' must be a list")

    min_items = constraints.get("min_items")
    max_items = constraints.get("max_items")

    if min_items is not None and len(value) < min_items:
        raise ActionInvalidArgError(
            f"Param '{name}' must contain at least {min_items} item(s)"
        )

    if max_items is not None and len(value) > max_items:
        raise ActionInvalidArgError(
            f"Param '{name}' must contain at most {max_items} item(s)"
        )


def _contains_control_characters(value: str) -> bool:
    """Return whether a string contains Unicode control characters.

    Args:
        value: String to inspect.

    Returns:
        True if at least one control character is present.
    """

    return any(unicodedata.category(char).startswith("C") for char in value)


def _coerce_file_id(name: str, value: Any) -> UUID:
    """Convert an incoming runtime value into UUID for file_id resolution.

    Args:
        name: Argument name.
        value: Runtime value expected to identify a file.

    Returns:
        Parsed UUID value.

    Raises:
        ActionInvalidArgError: If value cannot be interpreted as UUID.
    """

    if isinstance(value, UUID):
        return value

    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ActionInvalidArgError(f"Param '{name}' must be a valid file_id") from exc
