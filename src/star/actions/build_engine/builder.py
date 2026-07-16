"""Compilation layer for validated STAR DSL module specifications.

This module transforms already-validated `ModuleSpec` objects into immutable
runtime `ActionSpec` instances. It is the bridge between DSL parsing/validation
and runtime registration.

Builder responsibilities are intentionally narrow:

- compile validated DSL actions into runtime models
- generate a dynamic Pydantic `params_model` per action
- normalize command template tokens into runtime primitives
- preserve metadata required by later registry and execution layers

The builder does not perform semantic validation, filesystem access, registry
registration, or subprocess execution.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from pydantic import UUID4, BaseModel, Field, SecretStr, create_model

from star.actions.exceptions import ActionSpecsBuildError
from star.actions.models.core import (
    ActionSpec,
    ArgDef,
    CommandElement,
    FlagDef,
    OutputDef,
    OutputSource,
    OutputType,
    ParamType,
    SecretDelivery,
)
from star.actions.schemas.action import ActionSpecInput
from star.actions.schemas.dsl import ArgCmd as SchemaArgCmd
from star.actions.schemas.dsl import BinaryCmd as SchemaBinaryCmd
from star.actions.schemas.dsl import FlagCmd as SchemaFlagCmd
from star.actions.schemas.dsl import OutputCmd as SchemaOutputCmd
from star.actions.schemas.module import ModuleSpec
from star.actions.security.policy import build_binary_policy, is_binary_allowed
from star.core.config import Settings

logger = logging.getLogger("star.actions.build_engine.builder")


def build_actions(
    modules: list[ModuleSpec],
    settings: Settings,
) -> dict[str, ActionSpec]:
    """Compile validated modules into a flat runtime action mapping.

    Args:
        modules: Validated STAR DSL modules.

    Returns:
        Flat dictionary keyed by the final runtime action name, including any
        directory-derived namespace.

    Raises:
        ActionSpecsBuildError: If validated specs cannot be compiled into
                runtime `ActionSpec` objects.
    """

    logger.info("Building runtime action specs from %d module(s)", len(modules))

    compiled: dict[str, ActionSpec] = {}

    for module in modules:
        for action_name, action in module.actions.items():
            action_fqdn = _build_action_fqdn(module, action_name)

            if action_fqdn in compiled:
                raise ActionSpecsBuildError(
                    f"Failed to build action '{action_fqdn}': duplicate fully "
                    "qualified action name"
                )

            try:
                compiled[action_fqdn] = _build_action(
                    module,
                    action_name,
                    action,
                    settings,
                )
            except ActionSpecsBuildError:
                raise
            except Exception as exc:
                logger.exception(
                    "Unexpected failure while building action %s",
                    action_fqdn,
                )
                raise ActionSpecsBuildError(
                    f"Failed to build action '{action_fqdn}'"
                ) from exc

    logger.info("Compiled %d runtime action spec(s)", len(compiled))
    return compiled


def _build_action(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    settings: Settings,
) -> ActionSpec:
    """Compile one validated DSL action into a runtime `ActionSpec`.

    Args:
        module: Parent validated module.
        action_name: Action name inside the module.
        action: Validated action definition.

    Returns:
        Immutable runtime `ActionSpec`.

    Raises:
        ActionSpecsBuildError: If the action cannot be compiled.
    """

    action_fqdn = _build_action_fqdn(module, action_name)

    try:
        arg_defs = _build_arg_defs(action)
        flag_defs = _build_flag_defs(action)
        output_defs = _build_output_defs(action)
        defaults = _build_defaults(arg_defs, flag_defs)
        command_template = _build_command_template(action)
        binary = _extract_primary_binary(command_template)
        execution_policy = build_binary_policy(tuple(module.binaries), settings)
        if not is_binary_allowed(binary, execution_policy):
            raise ActionSpecsBuildError(
                f"Failed to build action '{action_fqdn}': binary '{binary}' "
                "is not allowed by effective policy"
            )
        params_model = _build_params_model(action_fqdn, arg_defs, flag_defs)
        module_tags = _normalize_tags(module.tags)
        action_tags = _normalize_tags(action.tags)
        effective_tags = _merge_tags(module_tags, action_tags)

        compiled = ActionSpec(
            name=action_fqdn,
            namespace=module.namespace,
            module=module.module,
            action=action_name,
            version=module.version,
            params_model=params_model,
            binary=binary,
            command_template=command_template,
            execution_policy=execution_policy,
            arg_defs=arg_defs,
            flag_defs=flag_defs,
            defaults=defaults,
            outputs=output_defs,
            allow_stdout_as_file=action.allow_stdout_as_file,
            authors=_parse_authors(module.authors),
            tags=effective_tags,
            summary=action.summary or action.description,
            description=action.description,
            deprecated=False,
            params_example=None,
        )
    except ActionSpecsBuildError:
        raise
    except Exception as exc:
        logger.exception("Unexpected failure while building action %s", action_fqdn)
        raise ActionSpecsBuildError(f"Failed to build action '{action_fqdn}'") from exc

    logger.info("Compiled runtime action %s", action_fqdn)
    return compiled


def _build_action_fqdn(module: ModuleSpec, action_name: str) -> str:
    """Build the fully qualified runtime action name.

    Args:
        module: Parent validated module.
        action_name: Action name inside the module.

    Returns:
        Fully-qualified runtime action name.
    """

    return ".".join((*module.namespace, module.module, action_name))


def _build_arg_defs(action: ActionSpecInput) -> dict[str, ArgDef]:
    """Compile validated DSL args into runtime `ArgDef` objects.

    Args:
        action: Validated action definition.

    Returns:
        Runtime arg definitions keyed by argument name.
    """

    compiled: dict[str, ArgDef] = {}

    for arg_name, arg_spec in (action.args or {}).items():
        required = True if arg_spec.type == ParamType.LIST else bool(arg_spec.required)
        compiled[arg_name] = ArgDef(
            type=arg_spec.type,
            items=arg_spec.items,
            required=required,
            default=arg_spec.default,
            constraints=arg_spec.constraints,
            delivery=(
                None
                if arg_spec.delivery is None
                else SecretDelivery(
                    type=arg_spec.delivery.type,
                    append_newline=arg_spec.delivery.append_newline,
                )
            ),
            description=arg_spec.description,
        )

    return compiled


def _build_flag_defs(action: ActionSpecInput) -> dict[str, FlagDef]:
    """Compile validated DSL flags into runtime `FlagDef` objects.

    Args:
        action: Validated action definition.

    Returns:
        Runtime flag definitions keyed by flag name.
    """

    compiled: dict[str, FlagDef] = {}

    for flag_name, flag_spec in (action.flags or {}).items():
        compiled[flag_name] = FlagDef(
            value=flag_spec.value,
            default=flag_spec.default,
            description=flag_spec.description,
        )

    return compiled


def _build_defaults(
    arg_defs: dict[str, ArgDef],
    flag_defs: dict[str, FlagDef],
) -> dict[str, Any]:
    """Build the flattened runtime defaults mapping for one action.

    Args:
        arg_defs: Runtime arg definitions.
        flag_defs: Runtime flag definitions.

    Returns:
        Flat defaults dictionary containing all optional args and all flags.
    """

    defaults: dict[str, Any] = {}

    for arg_name, arg_def in arg_defs.items():
        if not arg_def.required:
            defaults[arg_name] = arg_def.default

    for flag_name, flag_def in flag_defs.items():
        defaults[flag_name] = flag_def.default

    return defaults


def _build_output_defs(action: ActionSpecInput) -> dict[str, OutputDef]:
    """Compile validated DSL outputs into runtime `OutputDef` objects.

    Args:
        action: Validated action definition.

    Returns:
        Runtime output definitions keyed by output name.
    """

    compiled: dict[str, OutputDef] = {}

    for output_name, output_spec in (action.outputs or {}).items():
        compiled[output_name] = OutputDef(
            type=OutputType(output_spec.type),
            source=OutputSource(output_spec.source),
            description=output_spec.description,
        )

    return compiled


def _build_command_template(action: ActionSpecInput) -> tuple[CommandElement, ...]:
    """Normalize DSL command tokens into runtime command template elements.

    Args:
        action: Validated action definition.

    Returns:
        Tuple of runtime command elements.
    """

    compiled: list[CommandElement] = []

    for element in action.command:
        compiled.append(_normalize_command_element(element))

    return tuple(compiled)


def _normalize_command_element(
    element: str | SchemaBinaryCmd | SchemaArgCmd | SchemaFlagCmd | SchemaOutputCmd,
) -> CommandElement:
    """Convert one DSL command element into the runtime token shape.

    Args:
        element: DSL command element from the parsed action definition.

    Returns:
        Runtime command token with a discriminating `kind` field.

    Raises:
        ActionSpecsBuildError: If the element type is unsupported.
    """

    if isinstance(element, str):
        return {"kind": "const", "value": element}

    if isinstance(element, SchemaBinaryCmd):
        return {"kind": "binary", "value": element.binary}

    if isinstance(element, SchemaArgCmd):
        return {"kind": "arg", "name": element.arg}

    if isinstance(element, SchemaFlagCmd):
        return {"kind": "flag", "name": element.flag}

    if isinstance(element, SchemaOutputCmd):
        return {"kind": "output", "name": element.output}

    raise ActionSpecsBuildError(
        f"Failed to build command template: unsupported type {type(element)}"
    )


def _extract_primary_binary(command_template: tuple[CommandElement, ...]) -> str:
    """Extract the first binary token from a compiled command template.

    Args:
        command_template: Normalized runtime command template.

    Returns:
        Binary string from the first binary token.

    Raises:
        ActionSpecsBuildError: If no binary token is present.
    """

    for element in command_template:
        if element["kind"] == "binary":
            return element["value"]

    logger.error("No binary token found in command template.")
    raise ActionSpecsBuildError(
        "Failed to build action: no binary token found in command template"
    )


def _build_params_model(
    action_fqdn: str,
    arg_defs: dict[str, ArgDef],
    flag_defs: dict[str, FlagDef],
) -> type[BaseModel]:
    """Generate the dynamic Pydantic params model for one action.

    Args:
        action_fqdn: Fully qualified action name.
        arg_defs: Runtime arg definitions.
        flag_defs: Runtime flag definitions.

    Returns:
        Dynamically generated Pydantic model class.
    """

    field_definitions: dict[str, tuple[Any, Any]] = {}

    for arg_name, arg_def in arg_defs.items():
        annotation = _map_param_type_to_python(arg_def)
        if arg_def.required:
            field_info = Field(
                ...,
                description=arg_def.description,
                json_schema_extra=_build_param_json_schema_extra(arg_def),
                repr=arg_def.type != ParamType.SECRET,
            )
            field_definitions[arg_name] = (
                annotation,
                field_info,
            )
        else:
            field_info = Field(
                default=arg_def.default,
                description=arg_def.description,
                json_schema_extra=_build_param_json_schema_extra(arg_def),
                repr=arg_def.type != ParamType.SECRET,
            )
            field_definitions[arg_name] = (
                annotation,
                field_info,
            )

    for flag_name, flag_def in flag_defs.items():
        field_definitions[flag_name] = (
            bool,
            Field(default=flag_def.default, description=flag_def.description),
        )

    model_name = _build_model_name(action_fqdn)
    return cast(
        type[BaseModel],
        create_model(model_name, **cast(Any, field_definitions)),
    )


def _build_model_name(action_fqdn: str) -> str:
    """Build a readable dynamic params model name from an action FQDN.

    Args:
        action_fqdn: Fully qualified action name.

    Returns:
        CamelCase model name with `Params` suffix.
    """

    parts = [part for part in re.split(r"[._]", action_fqdn) if part]
    return "".join(part.capitalize() for part in parts) + "Params"


def _build_param_json_schema_extra(arg_def: ArgDef) -> dict[str, Any] | None:
    """Build optional JSON Schema metadata for generated params fields.

    Args:
        arg_def: Runtime argument definition.

    Returns:
        Optional JSON Schema extras for the generated field.
    """

    if arg_def.type != ParamType.SECRET:
        return None

    return {
        "format": "password",
        "writeOnly": True,
        "sensitive": True,
    }


def _normalize_tags(tags_input: list[str] | None) -> tuple[str, ...]:
    """Normalize YAML tag lists into a deduplicated tuple.

    Args:
        tags_input: Raw YAML tag list or `None`.

    Returns:
        Lowercased, stripped, deduplicated tags preserving first appearance.
    """

    if tags_input is None:
        return ()

    tags: list[str] = []
    seen: set[str] = set()

    for token in tags_input:
        if not isinstance(token, str):
            continue
        normalized = token.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)

    return tuple(tags)


def _merge_tags(*tag_groups: tuple[str, ...]) -> tuple[str, ...]:
    """Merge tag groups into a deduplicated tuple preserving first appearance.

    Args:
        *tag_groups: Tag tuples that have already been normalized.

    Returns:
        Deduplicated tag tuple preserving first occurrence across groups.
    """

    tags: list[str] = []
    seen: set[str] = set()

    for group in tag_groups:
        for tag in group:
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)

    return tuple(tags)


def _parse_authors(authors: list[str] | None) -> tuple[str, ...] | None:
    """Normalize module authors into a runtime tuple.

    Args:
        authors: Raw author list or `None`.

    Returns:
        Tuple of author strings, or `None` when absent.
    """

    if authors is None:
        return None

    return tuple(authors)


def _map_param_type_to_python(arg_def: ArgDef) -> type[Any]:
    """Map a DSL `ParamType` to a Python/Pydantic field type.

    Args:
        arg_def: Runtime argument definition.

    Returns:
        Python or Pydantic-compatible type used in the dynamic params model.

    Raises:
        ActionSpecsBuildError: If the parameter type is unsupported.
    """

    param_type = arg_def.type

    if param_type == ParamType.INT:
        return int
    if param_type == ParamType.FLOAT:
        return float
    if param_type == ParamType.STRING:
        return str
    if param_type == ParamType.SECRET:
        return SecretStr
    if param_type == ParamType.BOOL:
        return bool
    if param_type == ParamType.FILE_ID:
        # NOTE: Use UUID4 to enforce strict UUID v4 validation at request level.
        return cast(type[Any], UUID4)
    if param_type == ParamType.LIST:
        if arg_def.items == ParamType.STRING:
            return cast(type[Any], list[str])
        if arg_def.items == ParamType.FILE_ID:
            return cast(type[Any], list[UUID4])
        raise ActionSpecsBuildError(
            "Unsupported list item type during build: "
            f"{arg_def.items.value if arg_def.items is not None else 'None'}"
        )

    raise ActionSpecsBuildError(
        f"Unsupported parameter type during build: {param_type}"
    )
