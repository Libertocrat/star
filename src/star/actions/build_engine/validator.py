"""Semantic validator for STAR DSL v1 module specifications.

This module validates already parsed `ModuleSpec` objects and enforces the
security-critical semantic rules of the STAR YAML DSL. Validation is strict,
deterministic, fail-fast, and non-mutating.

The validator is intentionally isolated from runtime execution concerns:

- it does not build runtime `ActionSpec` objects
- it does not interact with the action registry
- it does not normalize or coerce command structures beyond the published rules

Its responsibility is to reject semantically invalid DSL modules before they
can reach any later compilation or runtime layers.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any, NoReturn, cast
from uuid import UUID

from star.actions.engine_config import (
    CONST_TEMPLATE_ALLOWED_ARG_TYPES,
    IDENTIFIER_NAME_PATTERN,
    MIME_LIKE_PATTERN,
    RESERVED_OUTPUT_NAMES,
    REVIEWED_COMMAND_LITERAL_PATH_ALLOWLIST,
    TAG_NAME_PATTERN,
    WINDOWS_DRIVE_PATH_PATTERN,
)
from star.actions.exceptions import ActionSpecsParseError
from star.actions.models.core import ParamType
from star.actions.schemas.action import ActionSpecInput
from star.actions.schemas.dsl import (
    ArgCmd,
    ArgSpec,
    BinaryCmd,
    FlagCmd,
    FlagSpec,
    OutputCmd,
    OutputSpec,
)
from star.actions.schemas.module import ModuleSpec
from star.actions.security.policy import DEFAULT_BLOCKED_BINARIES, is_simple_binary_name

logger = logging.getLogger("star.actions.build_engine.validator")


def validate_modules(modules: list[ModuleSpec]) -> None:
    """Validate parsed STAR DSL modules for semantic correctness.

    Args:
        modules: Parsed and structurally validated module specifications.

    Raises:
        ActionSpecsParseError: If any semantic validation rule is violated.
    """

    _validate_unique_module_identities(modules)

    for module in modules:
        _validate_module(module)


def _validate_unique_module_identities(modules: list[ModuleSpec]) -> None:
    """Ensure effective module identities are unique.

    Args:
        modules: Parsed module specifications.

    Raises:
        ActionSpecsParseError: If the same effective module identity appears
            more than once.
    """

    seen: set[tuple[str, ...]] = set()

    for module in modules:
        module_identity = _module_identity(module)

        if module_identity in seen:
            _raise_module_error(
                ".".join(module_identity),
                ("duplicate fully qualified module " f"'{'.'.join(module_identity)}'"),
            )
        seen.add(module_identity)


def _module_identity(module: ModuleSpec) -> tuple[str, ...]:
    """Return the effective module identity used for uniqueness checks.

    Args:
        module: Parsed module specification.

    Returns:
        Tuple with namespace parts plus module name.
    """

    return (*module.namespace, module.module)


def _validate_module(module: ModuleSpec) -> None:
    """Validate one STAR DSL module.

    Args:
        module: Parsed module specification.

    Raises:
        ActionSpecsParseError: If any module-level or action-level rule fails.
    """

    _validate_module_version(module)
    _validate_identifier(
        module_name=module.module,
        identifier_kind="module",
        identifier_value=module.module,
    )
    _validate_module_has_actions(module)
    _validate_module_binaries(module)
    _validate_module_tags(module)

    for action_name, action in module.actions.items():
        _validate_action(module, action_name, action)


def _validate_module_has_actions(module: ModuleSpec) -> None:
    """Ensure a module declares at least one action.

    Args:
        module: Module specification to validate.

    Raises:
        ActionSpecsParseError: If the module action mapping is empty.
    """

    if not module.actions:
        _raise_module_error(module.module, "module must define at least one action")


def _validate_module_version(module: ModuleSpec) -> None:
    """Ensure the module uses the supported DSL version.

    Args:
        module: Module specification to validate.

    Raises:
        ActionSpecsParseError: If the module version is unsupported.
    """

    if module.version != 1:
        _raise_module_error(
            module.module,
            f"unsupported DSL version '{module.version}'; only version 1 is supported",
        )


def _validate_module_binaries(module: ModuleSpec) -> None:
    """Ensure module-level binary declarations are unique.

    Args:
        module: Module specification to validate.

    Raises:
        ActionSpecsParseError: If duplicate binaries are declared.
    """

    if not module.binaries:
        _raise_module_error(module.module, "module must declare at least one binary")

    seen: set[str] = set()
    blocked_binaries = set(DEFAULT_BLOCKED_BINARIES)

    for binary in module.binaries:
        _validate_identifier(
            module_name=module.module,
            identifier_kind="binary",
            identifier_value=binary,
        )
        if not is_simple_binary_name(binary) and ("/" in binary or "\\" in binary):
            _raise_module_error(
                module.module,
                f"binary '{binary}' must be a simple name without path separators",
            )
        if binary in blocked_binaries:
            _raise_module_error(
                module.module,
                f"binary '{binary}' is blocked by STAR default policy",
            )
        if binary in seen:
            _raise_module_error(
                module.module,
                f"duplicate binary '{binary}' declared in module binaries",
            )
        seen.add(binary)


def _validate_module_tags(module: ModuleSpec) -> None:
    """Validate optional module tags as a YAML list.

    Args:
        module: Module specification to validate.

    Raises:
        ActionSpecsParseError: If module tags are invalid.
    """

    _validate_tags_list(
        tags=module.tags,
        module_name=module.module,
        action_name=None,
    )


def _validate_action_tags(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate optional action tags as a YAML list.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification to validate.

    Raises:
        ActionSpecsParseError: If action tags are invalid.
    """

    _validate_tags_list(
        tags=action.tags,
        module_name=module_name,
        action_name=action_name,
    )


def _validate_tags_list(
    *,
    tags: list[str] | None,
    module_name: str,
    action_name: str | None,
) -> None:
    """Validate optional DSL tags encoded as a YAML list.

    Args:
        tags: Raw YAML tag list from module or action metadata.
        module_name: Parent module name for error context.
        action_name: Optional action name for action-scoped errors.

    Raises:
        ActionSpecsParseError: If tags are empty, contain non-string values,
            contain blank strings, or contain strings that violate the tag
            naming pattern.
    """

    if tags is None:
        return

    def _raise_tags_error(message: str) -> NoReturn:
        if action_name is None:
            _raise_module_error(module_name, message)
        _raise_action_error(module_name, action_name, message)

    if not tags:
        _raise_tags_error("tags must be a non-empty list of strings")

    for tag in tags:
        if not isinstance(tag, str):
            _raise_tags_error("tags must be a non-empty list of strings")

        normalized = tag.strip()
        if normalized == "":
            _raise_tags_error("tags must not contain blank entries")

        if TAG_NAME_PATTERN.fullmatch(normalized):
            continue
        _raise_tags_error(
            (
                "invalid tag name "
                f"'{normalized}'; expected pattern '{TAG_NAME_PATTERN.pattern}'"
            )
        )


def _validate_action(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate one action definition inside a module.

    Args:
        module: Parent module specification.
        action_name: Action name as declared in the module mapping.
        action: Parsed action specification.

    Raises:
        ActionSpecsParseError: If the action is semantically invalid.
    """

    _validate_identifier(
        module_name=module.module,
        identifier_kind="action",
        identifier_value=action_name,
    )
    _validate_action_tags(module.module, action_name, action)
    _validate_command_exists(module.module, action_name, action)
    _validate_name_collisions(module.module, action_name, action)
    _validate_argument_names(module.module, action_name, action)
    _validate_flag_names(module.module, action_name, action)
    _validate_output_names(module.module, action_name, action)
    _validate_output_definitions(module.module, action_name, action)
    _validate_binary_rules(module, action_name, action)
    _validate_command_elements(module, action_name, action)
    _validate_command_references(module.module, action_name, action)
    _validate_unused_definitions(module.module, action_name, action)
    _validate_args(module.module, action_name, action)
    _validate_flags(module, action_name, action)


def _validate_command_exists(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Ensure the action command list is not empty.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If the command list is empty.
    """

    if not action.command:
        _raise_action_error(module_name, action_name, "command must not be empty")


def _validate_name_collisions(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Ensure action arg and flag names do not overlap.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If an arg name collides with a flag name.
    """

    arg_names = set((action.args or {}).keys())
    flag_names = set((action.flags or {}).keys())

    collisions = sorted(arg_names & flag_names)
    if collisions:
        collision = collisions[0]
        _raise_action_error(
            module_name,
            action_name,
            f"name collision between arg and flag '{collision}'",
        )


def _validate_argument_names(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate all argument names for one action.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If an arg name violates identifier rules.
    """

    for arg_name in (action.args or {}).keys():
        _validate_identifier(
            module_name=module_name,
            identifier_kind="arg",
            identifier_value=arg_name,
            action_name=action_name,
        )


def _validate_flag_names(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate all flag names for one action.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If a flag name violates identifier rules.
    """

    for flag_name in (action.flags or {}).keys():
        _validate_identifier(
            module_name=module_name,
            identifier_kind="flag",
            identifier_value=flag_name,
            action_name=action_name,
        )


def _validate_output_names(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate all output names and outputs block shape for one action.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If outputs are empty when provided or any output
            name violates identifier rules.
    """

    outputs = action.outputs
    if outputs is None:
        return

    if "outputs" in action.model_fields_set and not outputs:
        _raise_action_error(
            module_name,
            action_name,
            "outputs must be a non-empty mapping when provided",
        )

    for output_name in outputs.keys():
        if output_name in RESERVED_OUTPUT_NAMES:
            _raise_action_error(
                module_name,
                action_name,
                f"output name '{output_name}' is reserved",
            )

        _validate_identifier(
            module_name=module_name,
            identifier_kind="output",
            identifier_value=output_name,
            action_name=action_name,
        )


def _validate_output_definitions(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate output type/source combinations for one action.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If an output definition uses an unsupported
            combination for this DSL version.
    """

    for output_name, output_spec in (action.outputs or {}).items():
        if not _is_supported_output_combination(output_spec):
            _raise_action_error(
                module_name,
                action_name,
                "output "
                f"'{output_name}' has unsupported type/source combination "
                f"'{output_spec.type}+{output_spec.source}'",
            )


def _is_supported_output_combination(output_spec: OutputSpec) -> bool:
    """Return whether one output type/source combination is valid.

    Args:
        output_spec: Output definition to validate.

    Returns:
        True when the combination is allowed for this iteration.
    """

    valid_combinations = {
        ("file", "command"),
    }
    return (output_spec.type, output_spec.source) in valid_combinations


def _validate_binary_rules(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate binary token presence, position, and module allowlist.

    Args:
        module: Parent module specification.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If the command violates binary token rules.
    """

    binary_positions = [
        index
        for index, element in enumerate(action.command)
        if isinstance(element, BinaryCmd)
    ]

    if not binary_positions:
        _raise_action_error(
            module.module,
            action_name,
            "command must contain exactly one binary token",
        )

    if len(binary_positions) > 1:
        _raise_action_error(
            module.module,
            action_name,
            "command must contain exactly one binary token",
        )

    if binary_positions[0] != 0:
        _raise_action_error(
            module.module,
            action_name,
            "binary must be first command element",
        )

    first_element = action.command[0]
    if not isinstance(first_element, BinaryCmd):
        _raise_action_error(
            module.module,
            action_name,
            "binary must be first command element",
        )

    if first_element.binary not in module.binaries:
        _raise_action_error(
            module.module,
            action_name,
            f"binary '{first_element.binary}' is not declared in module binaries",
        )


def _validate_command_elements(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate the types and literal values of command elements.

    Args:
        module: Parent module specification.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If a command element is unsupported or an
                inline string literal is unsafe.
    """

    args = cast(dict[str, ArgSpec], action.args or {})

    for element in action.command:
        if isinstance(element, str):
            _validate_command_literal(
                module,
                action_name,
                element,
                args,
            )
            continue

        if isinstance(element, (BinaryCmd, ArgCmd, FlagCmd, OutputCmd)):
            continue

        _raise_action_error(
            module.module,
            action_name,
            "command contains an unsupported element type",
        )


def _validate_command_literal(
    module: ModuleSpec,
    action_name: str,
    literal: str,
    args: dict[str, ArgSpec],
) -> None:
    """Validate one inline string literal inside a command template.

    Args:
        module: Parent module specification.
        action_name: Action name.
        literal: Literal command token.
        args: Action argument definitions indexed by arg name.

    Raises:
        ActionSpecsParseError: If the literal is empty/blank, contains
            control characters, or defines invalid placeholders.
    """

    if literal == "":
        _raise_action_error(
            module.module,
            action_name,
            "command literal must not be empty",
        )

    if literal.strip() == "":
        _raise_action_error(
            module.module,
            action_name,
            "command literal must not be whitespace-only",
        )

    if "\x00" in literal:
        _raise_action_error(
            module.module,
            action_name,
            "command literal must not contain NULL bytes",
        )

    if _contains_control_characters(literal):
        _raise_action_error(
            module.module,
            action_name,
            "command literal must not contain control characters",
        )

    if _is_disallowed_path_literal(module, action_name, literal):
        _raise_action_error(
            module.module,
            action_name,
            "command literal must not contain host paths",
        )

    if "{" not in literal and "}" not in literal:
        return

    try:
        placeholders = _extract_command_literal_placeholders(literal)
    except ValueError as exc:
        _raise_action_error(
            module.module,
            action_name,
            f"command literal has invalid placeholder syntax ({exc})",
        )

    for placeholder_name in placeholders:
        arg_spec = args.get(placeholder_name)
        if arg_spec is None:
            _raise_action_error(
                module.module,
                action_name,
                (
                    "command literal placeholder "
                    f"'{{{placeholder_name}}}' references undefined arg "
                    f"'{placeholder_name}'"
                ),
            )

        if arg_spec.type not in CONST_TEMPLATE_ALLOWED_ARG_TYPES:
            _raise_action_error(
                module.module,
                action_name,
                (
                    "command literal placeholder "
                    f"'{{{placeholder_name}}}' references arg "
                    f"'{placeholder_name}' with unsupported type "
                    f"'{arg_spec.type.value}'"
                ),
            )


def _is_disallowed_path_literal(
    module: ModuleSpec,
    action_name: str,
    literal: str,
) -> bool:
    """Return whether a DSL literal is a disallowed host path.

    Args:
        module: Parent module specification.
        action_name: Action name.
        literal: Literal argv token declared by the DSL.

    Returns:
        True when the literal looks like a host path and is not explicitly
        allowlisted for a reviewed core action.
    """

    if (
        module.source,
        module.module,
        action_name,
        literal,
    ) in REVIEWED_COMMAND_LITERAL_PATH_ALLOWLIST:
        return False

    return _looks_like_host_path_literal(literal)


def _looks_like_host_path_literal(literal: str) -> bool:
    """Return whether a literal has host-path syntax.

    Args:
        literal: Literal argv token declared by the DSL.

    Returns:
        True when the literal contains absolute, Windows, UNC, backslash, or
        traversal-like path syntax.
    """

    if literal.startswith("/"):
        return True

    if "\\" in literal:
        return True

    if WINDOWS_DRIVE_PATH_PATTERN.match(literal):
        return True

    return ".." in literal.split("/")


def _extract_command_literal_placeholders(literal: str) -> tuple[str, ...]:
    """Extract placeholder arg names from one command literal.

    Supported placeholder syntax is strictly `{arg_name}`.

    Args:
        literal: Literal command token that may contain placeholders.

    Returns:
        Tuple of placeholder arg names in left-to-right order.

    Raises:
        ValueError: If brace usage or placeholder name syntax is invalid.
    """

    placeholders: list[str] = []
    index = 0

    while index < len(literal):
        current = literal[index]

        if current == "}":
            raise ValueError("unmatched closing brace '}'")

        if current != "{":
            index += 1
            continue

        if index > 0 and literal[index - 1] == "$":
            raise ValueError("`${...}` syntax is not supported")

        closing_index = literal.find("}", index + 1)
        if closing_index == -1:
            raise ValueError("unmatched opening brace '{'")

        placeholder_name = literal[index + 1 : closing_index]
        if placeholder_name == "":
            raise ValueError("empty placeholder '{}' is not allowed")

        if "{" in placeholder_name or "}" in placeholder_name:
            raise ValueError("nested placeholders are not supported")

        if not IDENTIFIER_NAME_PATTERN.fullmatch(placeholder_name):
            raise ValueError(
                f"invalid placeholder name '{placeholder_name}'; "
                "expected '{arg_name}'"
            )

        placeholders.append(placeholder_name)
        index = closing_index + 1

    return tuple(placeholders)


def _validate_command_references(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Ensure all command arg/flag/output references resolve to definitions.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If a command references an undefined arg, flag,
            or output.
    """

    args = action.args or {}
    flags = action.flags or {}
    outputs = action.outputs or {}

    for element in action.command:
        if isinstance(element, ArgCmd) and element.arg not in args:
            _raise_action_error(
                module_name,
                action_name,
                f"arg '{element.arg}' referenced in command but not defined",
            )

        if isinstance(element, FlagCmd) and element.flag not in flags:
            _raise_action_error(
                module_name,
                action_name,
                f"flag '{element.flag}' referenced in command but not defined",
            )

        if isinstance(element, OutputCmd) and element.output not in outputs:
            _raise_action_error(
                module_name,
                action_name,
                f"output '{element.output}' referenced in command but not defined",
            )


def _validate_unused_definitions(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Ensure declared args/flags/outputs follow command usage rules.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If any declared definition violates usage rules.
    """

    used_args = {
        element.arg for element in action.command if isinstance(element, ArgCmd)
    }
    for element in action.command:
        if not isinstance(element, str):
            continue

        try:
            used_args.update(_extract_command_literal_placeholders(element))
        except ValueError as exc:
            _raise_action_error(
                module_name,
                action_name,
                f"command literal has invalid placeholder syntax ({exc})",
            )

    used_flags = {
        element.flag for element in action.command if isinstance(element, FlagCmd)
    }
    output_reference_counts: dict[str, int] = {}
    for element in action.command:
        if isinstance(element, OutputCmd):
            output_reference_counts[element.output] = (
                output_reference_counts.get(element.output, 0) + 1
            )

    for arg_name in (action.args or {}).keys():
        if arg_name not in used_args:
            _raise_action_error(
                module_name,
                action_name,
                f"arg '{arg_name}' is defined but not used in command",
            )

    for flag_name in (action.flags or {}).keys():
        if flag_name not in used_flags:
            _raise_action_error(
                module_name,
                action_name,
                f"flag '{flag_name}' is defined but not used in command",
            )

    for output_name, output_spec in (action.outputs or {}).items():
        references = output_reference_counts.get(output_name, 0)
        if output_spec.type == "file" and output_spec.source == "command":
            if references == 0:
                _raise_action_error(
                    module_name,
                    action_name,
                    f"output '{output_name}' with type 'file' and source 'command' "
                    "must be referenced exactly once in command",
                )
            if references > 1:
                _raise_action_error(
                    module_name,
                    action_name,
                    f"output '{output_name}' with type 'file' and source 'command' "
                    "must be referenced exactly once in command",
                )


def _validate_args(
    module_name: str,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate semantic rules for all action arguments.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If any argument rule is violated.
    """

    for arg_name, arg_spec in (action.args or {}).items():
        _validate_arg_list_items_rules(
            module_name=module_name,
            action_name=action_name,
            arg_name=arg_name,
            arg_spec=arg_spec,
        )
        _validate_arg_required_default_rules(
            module_name=module_name,
            action_name=action_name,
            arg_name=arg_name,
            arg_spec=arg_spec,
        )
        _validate_arg_default(module_name, action_name, arg_name, arg_spec)
        _validate_arg_constraints(module_name, action_name, arg_name, arg_spec)


def _validate_arg_list_items_rules(
    module_name: str,
    action_name: str,
    arg_name: str,
    arg_spec: ArgSpec,
) -> None:
    """Validate `list` + `items` structural compatibility rules.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        arg_name: Argument name.
        arg_spec: Argument definition.

    Raises:
        ActionSpecsParseError: If `items` is incompatible with declared `type`.
    """

    is_list_type = arg_spec.type == ParamType.LIST
    has_items = "items" in arg_spec.model_fields_set

    if is_list_type and not has_items:
        _raise_action_error(
            module_name,
            action_name,
            f"arg '{arg_name}' with type 'list' must define 'items'",
        )

    if not is_list_type and has_items:
        _raise_action_error(
            module_name,
            action_name,
            f"arg '{arg_name}' with type '{arg_spec.type.value}' cannot define 'items'",
        )

    if is_list_type and arg_spec.items not in {ParamType.STRING, ParamType.FILE_ID}:
        _raise_action_error(
            module_name,
            action_name,
            f"arg '{arg_name}' list 'items' must be 'string' or 'file_id'",
        )


def _validate_arg_required_default_rules(
    module_name: str,
    action_name: str,
    arg_name: str,
    arg_spec: ArgSpec,
) -> None:
    """Validate the relationship between `required` and `default`.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        arg_name: Argument name.
        arg_spec: Argument definition.

    Raises:
        ActionSpecsParseError: If `required` is invalid, if a required arg also
                defines a default, or if an optional arg omits its default.
    """

    required = arg_spec.required
    has_default = "default" in arg_spec.model_fields_set
    has_required = "required" in arg_spec.model_fields_set

    if arg_spec.type == ParamType.LIST:
        if has_default:
            _raise_action_error(
                module_name,
                action_name,
                f"arg '{arg_name}' with type 'list' cannot define a default",
            )

        if has_required and required is False:
            _raise_action_error(
                module_name,
                action_name,
                f"arg '{arg_name}' with type 'list' cannot set required to False",
            )
        return

    if required and has_default:
        _raise_action_error(
            module_name,
            action_name,
            f"arg '{arg_name}' cannot be required and define a default",
        )

    if not required and not has_default:
        _raise_action_error(
            module_name,
            action_name,
            f"arg '{arg_name}' must define a default when not required",
        )


def _validate_arg_default(
    module_name: str,
    action_name: str,
    arg_name: str,
    arg_spec: ArgSpec,
) -> None:
    """Validate an argument default against its declared DSL type.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        arg_name: Argument name.
        arg_spec: Argument definition.

    Raises:
        ActionSpecsParseError: If a provided default value is incompatible with
                the declared parameter type.
    """

    if "default" not in arg_spec.model_fields_set:
        return

    default = arg_spec.default
    param_type = arg_spec.type

    is_valid = False
    if param_type == ParamType.INT:
        is_valid = _is_int_compatible(default)
    elif param_type == ParamType.FLOAT:
        is_valid = _is_float_compatible(default)
    elif param_type == ParamType.BOOL:
        is_valid = type(default) is bool
    elif param_type == ParamType.STRING:
        is_valid = isinstance(default, str)
    elif param_type == ParamType.FILE_ID:
        is_valid = isinstance(default, str) and _is_uuid4(default)
    elif param_type == ParamType.LIST:
        is_valid = isinstance(default, list)

    if not is_valid:
        _raise_action_error(
            module_name,
            action_name,
            f"default for arg '{arg_name}' is incompatible with declared type "
            f"'{param_type.value}'",
        )


def _validate_arg_constraints(
    module_name: str,
    action_name: str,
    arg_name: str,
    arg_spec: ArgSpec,
) -> None:
    """Validate the `constraints` block for one argument.

    Args:
        module_name: Parent module name.
        action_name: Action name.
        arg_name: Argument name.
        arg_spec: Argument definition.

    Raises:
        ActionSpecsParseError: If constraints are invalid for the declared
                argument type.
    """

    try:
        validate_constraints(
            arg_name=arg_name,
            arg_type=arg_spec.type,
            constraints=arg_spec.constraints,
        )
    except ValueError as exc:
        _raise_action_error(module_name, action_name, str(exc))


def validate_constraints(
    arg_name: str,
    arg_type: ParamType,
    constraints: dict[str, Any] | None,
) -> None:
    """Validate one argument constraints block by parameter type.

    Args:
        arg_name: Argument name.
        arg_type: Declared argument type.
        constraints: Raw constraints mapping, if provided.

    Raises:
        ValueError: If any constraint rule is invalid.
    """

    if constraints is None:
        return

    if not isinstance(constraints, dict):
        raise ValueError(f"arg '{arg_name}' constraints must be a mapping")

    allowed_keys = _allowed_constraint_keys(arg_type)
    unknown_keys = sorted(key for key in constraints if key not in allowed_keys)
    if unknown_keys:
        unknown_display = ", ".join(unknown_keys)
        raise ValueError(
            f"arg '{arg_name}' has unsupported constraint key(s): {unknown_display} "
            f"for type '{arg_type.value}'"
        )

    if arg_type == ParamType.INT:
        _validate_numeric_constraints(
            arg_name,
            constraints,
            require_integer_bounds=True,
        )
        return

    if arg_type == ParamType.FLOAT:
        _validate_numeric_constraints(
            arg_name,
            constraints,
            require_integer_bounds=False,
        )
        return

    if arg_type == ParamType.STRING:
        _validate_string_constraints(arg_name, constraints)
        return

    if arg_type == ParamType.FILE_ID:
        _validate_file_constraints(arg_name, constraints)
        return

    if arg_type == ParamType.LIST:
        _validate_list_constraints(arg_name, constraints)
        return

    if constraints:
        raise ValueError(
            f"arg '{arg_name}' constraints are not supported for type "
            f"'{arg_type.value}'"
        )


def _allowed_constraint_keys(arg_type: ParamType) -> set[str]:
    """Return the allowed constraint keys for one parameter type.

    Args:
        arg_type: Declared argument type.

    Returns:
        Set of valid constraint keys.
    """

    if arg_type in {ParamType.INT, ParamType.FLOAT}:
        return {"min", "max"}
    if arg_type == ParamType.STRING:
        return {"min_length", "max_length", "allowed_values"}
    if arg_type == ParamType.FILE_ID:
        return {"max_size", "allowed_extensions", "allowed_mime_types"}
    if arg_type == ParamType.LIST:
        return {"min_items", "max_items"}
    return set()


def _validate_numeric_constraints(
    arg_name: str,
    constraints: dict[str, Any],
    *,
    require_integer_bounds: bool,
) -> None:
    """Validate numeric constraints.

    Args:
        arg_name: Argument name.
        constraints: Constraints dictionary.
        require_integer_bounds: Whether constraints must be integer-valued.

    Raises:
        ValueError: If constraints are not valid numeric bounds.
    """

    min_value = constraints.get("min")
    max_value = constraints.get("max")
    numeric_min: float | None = None
    numeric_max: float | None = None

    if "min" in constraints:
        if not _is_numeric(min_value):
            raise ValueError(f"arg '{arg_name}' min constraint must be a number")
        numeric_min = float(cast(float, min_value))

    if "max" in constraints:
        if not _is_numeric(max_value):
            raise ValueError(f"arg '{arg_name}' max constraint must be a number")
        numeric_max = float(cast(float, max_value))

    if (
        require_integer_bounds
        and numeric_min is not None
        and not numeric_min.is_integer()
    ):
        raise ValueError(f"arg '{arg_name}' min must be an integer value")

    if (
        require_integer_bounds
        and numeric_max is not None
        and not numeric_max.is_integer()
    ):
        raise ValueError(f"arg '{arg_name}' max must be an integer value")

    if (
        numeric_min is not None
        and numeric_max is not None
        and numeric_min > numeric_max
    ):
        raise ValueError(f"arg '{arg_name}' has min greater than max")


def _validate_string_constraints(arg_name: str, constraints: dict[str, Any]) -> None:
    """Validate string-specific constraints.

    Args:
        arg_name: Argument name.
        constraints: Constraints dictionary.

    Raises:
        ValueError: If constraints are invalid.
    """

    min_length = constraints.get("min_length")
    max_length = constraints.get("max_length")
    allowed_values = constraints.get("allowed_values")
    validated_min_length: int | None = None

    if "min_length" in constraints:
        if type(min_length) is not int:
            raise ValueError(f"arg '{arg_name}' min_length must be an integer")
        validated_min_length = min_length
        if min_length < 0:
            raise ValueError(f"arg '{arg_name}' min_length must be >= 0")

    if "max_length" in constraints:
        if type(max_length) is not int:
            raise ValueError(f"arg '{arg_name}' max_length must be an integer")
        if max_length <= 0:
            raise ValueError(f"arg '{arg_name}' max_length must be > 0")
        if validated_min_length is not None and max_length < validated_min_length:
            raise ValueError(f"arg '{arg_name}' max_length must be >= min_length")

    if "allowed_values" in constraints:
        if not isinstance(allowed_values, list) or len(allowed_values) == 0:
            raise ValueError(
                f"arg '{arg_name}' allowed_values must be a non-empty list of strings"
            )
        if not all(isinstance(item, str) for item in allowed_values):
            raise ValueError(
                f"arg '{arg_name}' allowed_values must be a non-empty list of strings"
            )
        if len(set(allowed_values)) != len(allowed_values):
            raise ValueError(f"arg '{arg_name}' allowed_values must be unique")


def _validate_file_constraints(arg_name: str, constraints: dict[str, Any]) -> None:
    """Validate file-specific constraints.

    Args:
        arg_name: Argument name.
        constraints: Constraints dictionary.

    Raises:
        ValueError: If constraints are invalid.
    """

    max_size = constraints.get("max_size")
    allowed_extensions = constraints.get("allowed_extensions")
    allowed_mime_types = constraints.get("allowed_mime_types")

    if "max_size" in constraints:
        if type(max_size) is not int:
            raise ValueError(f"arg '{arg_name}' max_size constraint must be an integer")
        if max_size <= 0:
            raise ValueError(f"arg '{arg_name}' max_size must be greater than 0")

    if "allowed_extensions" in constraints:
        if not isinstance(allowed_extensions, list) or not all(
            isinstance(item, str) for item in allowed_extensions
        ):
            raise ValueError(
                f"arg '{arg_name}' allowed_extensions must be a list of strings"
            )

    if "allowed_mime_types" in constraints:
        if not isinstance(allowed_mime_types, list) or not all(
            isinstance(item, str) for item in allowed_mime_types
        ):
            raise ValueError(
                f"arg '{arg_name}' allowed_mime_types must be a list of strings"
            )
        if not all(
            MIME_LIKE_PATTERN.fullmatch(item.strip().lower())
            for item in allowed_mime_types
        ):
            raise ValueError(
                f"arg '{arg_name}' allowed_mime_types must contain valid "
                "mime-like strings"
            )


def _validate_list_constraints(arg_name: str, constraints: dict[str, Any]) -> None:
    """Validate list-specific constraints.

    Args:
        arg_name: Argument name.
        constraints: Constraints dictionary.

    Raises:
        ValueError: If constraints are invalid.
    """

    min_items = constraints.get("min_items")
    max_items = constraints.get("max_items")
    validated_min_items: int | None = None

    if "min_items" in constraints:
        if type(min_items) is not int:
            raise ValueError(f"arg '{arg_name}' min_items must be an integer")
        validated_min_items = min_items
        if min_items < 0:
            raise ValueError(f"arg '{arg_name}' min_items must be >= 0")

    if "max_items" in constraints:
        if type(max_items) is not int:
            raise ValueError(f"arg '{arg_name}' max_items must be an integer")
        if max_items <= 0:
            raise ValueError(f"arg '{arg_name}' max_items must be > 0")
        if validated_min_items is not None and max_items < validated_min_items:
            raise ValueError(f"arg '{arg_name}' max_items must be >= min_items")


def _is_numeric(value: Any) -> bool:
    """Return whether a value is numeric while excluding booleans.

    Args:
        value: Value to inspect.

    Returns:
        True when value is `int` or `float` and not `bool`.
    """

    return type(value) in {int, float}


def _validate_flags(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
) -> None:
    """Validate semantic rules for all action flags.

    Args:
        module: Parent module specification.
        action_name: Action name.
        action: Action specification.

    Raises:
        ActionSpecsParseError: If any flag rule is violated.
    """

    for flag_name, flag_spec in (action.flags or {}).items():
        _validate_flag_value(module, action_name, flag_name, flag_spec)


def _validate_flag_value(
    module: ModuleSpec,
    action_name: str,
    flag_name: str,
    flag_spec: FlagSpec,
) -> None:
    """Validate the literal command value associated with one flag.

    Args:
        module: Parent module specification.
        action_name: Action name.
        flag_name: Flag name.
        flag_spec: Flag definition.

    Raises:
        ActionSpecsParseError: If the flag literal value is empty or blank.
    """

    if flag_spec.value == "":
        _raise_action_error(
            module.module,
            action_name,
            f"flag '{flag_name}' value must not be empty",
        )

    if flag_spec.value.strip() == "":
        _raise_action_error(
            module.module,
            action_name,
            f"flag '{flag_name}' value must not be whitespace-only",
        )

    if "\x00" in flag_spec.value:
        _raise_action_error(
            module.module,
            action_name,
            f"flag '{flag_name}' value must not contain NULL bytes",
        )

    if _contains_control_characters(flag_spec.value):
        _raise_action_error(
            module.module,
            action_name,
            f"flag '{flag_name}' value must not contain control characters",
        )

    if _looks_like_host_path_literal(flag_spec.value):
        _raise_action_error(
            module.module,
            action_name,
            f"flag '{flag_name}' value must not contain host paths",
        )


def _validate_identifier(
    module_name: str,
    identifier_kind: str,
    identifier_value: str,
    action_name: str | None = None,
) -> None:
    """Validate an identifier against the STAR DSL naming regex.

    Args:
        module_name: Current module name.
        identifier_kind: Human-readable identifier kind such as `module`,
                `action`, `arg`, or `flag`.
        identifier_value: Identifier value to validate.
        action_name: Optional action context for arg/flag validation.

    Raises:
        ActionSpecsParseError: If the identifier does not match the naming
                pattern `^[a-z][a-z0-9_]*$`.
    """

    if IDENTIFIER_NAME_PATTERN.fullmatch(identifier_value):
        return

    message = (
        f"invalid {identifier_kind} name '{identifier_value}'; expected pattern "
        "'^[a-z][a-z0-9_]*$'"
    )
    if action_name is None:
        _raise_module_error(module_name, message)
    else:
        _raise_action_error(module_name, action_name, message)


def _contains_control_characters(value: str) -> bool:
    """Return whether a string contains Unicode control characters.

    Args:
        value: String to inspect.

    Returns:
        True if the string contains any control character, otherwise False.
    """

    return any(unicodedata.category(char).startswith("C") for char in value)


def _is_int_compatible(value: object) -> bool:
    """Return whether a value is valid for an `int` DSL default.

    Args:
        value: Value to inspect.

    Returns:
        True when the value is an `int` or an integer-valued `float`, while
        excluding `bool`.
    """

    return type(value) is int or (type(value) is float and value.is_integer())


def _is_float_compatible(value: object) -> bool:
    """Return whether a value is valid for a `float` DSL default.

    Args:
        value: Value to inspect.

    Returns:
        True when the value is an `int` or `float`, while excluding `bool`.
    """

    return type(value) in {int, float}


def _is_uuid4(value: str) -> bool:
    """Return whether a string is a valid UUID version 4.

    Args:
        value: Candidate UUID string.

    Returns:
        True if the value parses as a UUID v4, otherwise False.
    """

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4


def _raise_module_error(module_name: str, message: str) -> NoReturn:
    """Raise a module-scoped semantic validation error.

    Args:
        module_name: Module identity label to include in the error.
        message: Human-readable failure detail.

    Raises:
        ActionSpecsParseError: Always.
    """

    full_message = f"Invalid DSL module '{module_name}': {message}"
    logger.error(full_message)
    raise ActionSpecsParseError(full_message)


def _raise_action_error(module_name: str, action_name: str, message: str) -> NoReturn:
    """Raise an action-scoped semantic validation error.

    Args:
        module_name: Module identity label to include in the error.
        action_name: Action name to include in the error.
        message: Human-readable failure detail.

    Raises:
        ActionSpecsParseError: Always.
    """

    _raise_module_error(module_name, f"{message} in action '{action_name}'")
