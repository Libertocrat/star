"""Build-time capability and invocation policy enforcement for DSL modules."""

from __future__ import annotations

from typing import NoReturn

from star.actions.engine_config import CONST_TEMPLATE_PLACEHOLDER_PATTERN
from star.actions.exceptions import ActionSpecsPolicyError
from star.actions.models.core import ParamType, SpecProvenance
from star.actions.models.security import BinaryPolicy, EffectiveCatalogPolicy
from star.actions.schemas.action import ActionSpecInput
from star.actions.schemas.dsl import ArgCmd, BinaryCmd, FlagCmd, OutputCmd
from star.actions.schemas.module import ModuleSpec
from star.actions.security.binary_policies import (
    BinaryInvocationPolicy,
    InvocationForm,
    OperandKind,
    OperandPolicy,
    OptionPolicy,
    get_binary_invocation_policy,
)
from star.actions.security.capabilities import (
    ExtensionCapability,
    parse_declared_capabilities,
    resolve_enabled_extension_capabilities,
)
from star.actions.security.policy import build_binary_policy
from star.core.config import Settings


def enforce_build_policies(
    modules: list[ModuleSpec],
    settings: Settings,
) -> EffectiveCatalogPolicy:
    """Enforce extension policy and compile effective per-action binary policy.

    Args:
        modules: Structurally and semantically validated DSL modules.
        settings: Explicit runtime settings snapshot.

    Returns:
        Immutable binary policy indexed by final action FQDN.

    Raises:
        ActionSpecsPolicyError: If module capabilities, binary admission, or an
            extension command grammar violates STAR policy.
    """

    try:
        enabled_capabilities = resolve_enabled_extension_capabilities(
            settings.star_enabled_extension_capabilities
        )
    except ValueError as exc:
        raise ActionSpecsPolicyError(str(exc)) from exc

    policies: dict[str, BinaryPolicy] = {}
    for module in modules:
        declared_capabilities = _parse_module_capabilities(module)
        module_policy = _build_module_binary_policy(module, settings)

        if module.provenance is SpecProvenance.EXTENSION:
            _enforce_extension_module_capabilities(
                module,
                declared_capabilities,
                enabled_capabilities,
            )

            for action_name, action in module.actions.items():
                _enforce_extension_action_policy(
                    module,
                    action_name,
                    action,
                    declared_capabilities,
                )

        for action_name in module.actions:
            policies[_action_fqdn(module, action_name)] = module_policy

    return EffectiveCatalogPolicy(policies)


def _parse_module_capabilities(
    module: ModuleSpec,
) -> tuple[ExtensionCapability, ...]:
    """Parse and validate a module's capability declaration.

    Args:
        module: Module whose raw capability list is evaluated.

    Returns:
        Canonical declared capability values.

    Raises:
        ActionSpecsPolicyError: If declared capabilities are malformed.
    """

    try:
        declared = parse_declared_capabilities(module.capabilities)
    except ValueError as exc:
        _raise_module_error(module, str(exc))

    if module.provenance is SpecProvenance.EXTENSION and not declared:
        _raise_module_error(module, "extension modules must declare capabilities")

    return declared


def _build_module_binary_policy(
    module: ModuleSpec,
    settings: Settings,
) -> BinaryPolicy:
    """Build the existing effective binary policy with safe error ownership."""

    try:
        return build_binary_policy(tuple(module.binaries), settings)
    except ValueError as exc:
        _raise_module_error(module, str(exc))


def _enforce_extension_module_capabilities(
    module: ModuleSpec,
    declared_capabilities: tuple[ExtensionCapability, ...],
    enabled_capabilities: frozenset[ExtensionCapability],
) -> None:
    """Authorize all extension-declared and action-used binaries.

    Args:
        module: Extension module assigned by the loader.
        declared_capabilities: Canonical capabilities requested by the module.
        enabled_capabilities: Operator-enabled capabilities.

    Raises:
        ActionSpecsPolicyError: If the module requests disabled capabilities or
            declares binaries that are not reviewed by its capabilities.
    """

    declared_set = frozenset(declared_capabilities)
    disabled = sorted(
        capability.value for capability in declared_set - enabled_capabilities
    )
    if disabled:
        _raise_module_error(
            module,
            "extension capability is disabled by operator policy: "
            + ", ".join(disabled),
        )

    action_binaries = frozenset(
        _action_binary(action) for action in module.actions.values()
    )
    for capability in declared_capabilities:
        if any(
            capability in _policy_for_binary(binary, module).capabilities
            for binary in action_binaries
        ):
            continue
        _raise_module_error(
            module,
            (
                f"declared capability '{capability.value}' does not authorize "
                "a used binary"
            ),
        )

    for binary in module.binaries:
        policy = _policy_for_binary(binary, module)
        if policy.capabilities & declared_set:
            continue
        _raise_module_error(
            module,
            f"binary '{binary}' is not authorized by declared capabilities",
        )


def _enforce_extension_action_policy(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    declared_capabilities: tuple[ExtensionCapability, ...],
) -> None:
    """Require an extension action to match a reviewed binary grammar.

    Args:
        module: Parent extension module.
        action_name: Action identifier within the module.
        action: Validated action AST.
        declared_capabilities: Module capabilities already authorized.

    Raises:
        ActionSpecsPolicyError: If the action binary or invocation is not
            covered by the reviewed policy.
    """

    binary = _action_binary(action)
    policy = _policy_for_binary(binary, module)
    if not policy.capabilities.intersection(declared_capabilities):
        _raise_action_error(
            module,
            action_name,
            f"binary '{binary}' is not authorized by declared capabilities",
        )

    errors: list[str] = []
    for form in policy.forms:
        try:
            _enforce_invocation_form(module, action_name, action, form)
            return
        except _FormMismatch as exc:
            errors.append(str(exc))

    detail = errors[0] if errors else "no reviewed invocation form is available"
    _raise_action_error(module, action_name, detail)


def _enforce_invocation_form(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    form: InvocationForm,
) -> None:
    """Validate one extension command template against one invocation form."""

    options_by_name = {name: option for option in form.options for name in option.names}
    seen_options: set[str] = set()
    seen_groups: set[str] = set()
    positional_tokens: list[object] = []
    index = 1
    command = action.command
    reached_positional = False

    while index < len(command):
        token = command[index]
        option = _resolve_option_token(token, action, options_by_name)
        if option is not None:
            if reached_positional:
                raise _FormMismatch("options must appear before positional operands")
            if option.canonical_name in seen_options:
                raise _FormMismatch(
                    f"option '{option.canonical_name}' must not appear more than once"
                )
            if option.exclusive_group is not None:
                if option.exclusive_group in seen_groups:
                    raise _FormMismatch("conflicting options are not allowed")
                seen_groups.add(option.exclusive_group)
            seen_options.add(option.canonical_name)

            if option.value_kind is None:
                index += 1
                continue
            if index + 1 >= len(command):
                raise _FormMismatch(
                    f"option '{option.canonical_name}' requires a value"
                )
            _validate_value_token(
                module,
                action_name,
                action,
                command[index + 1],
                option.value_kind,
                min_value=option.min_value,
                max_value=option.max_value,
                max_length=option.max_length,
            )
            index += 2
            continue

        reached_positional = True
        positional_tokens.append(token)
        index += 1

    for option in form.options:
        if option.required and option.canonical_name not in seen_options:
            raise _FormMismatch(f"required option '{option.canonical_name}' is missing")

    for required_group in form.required_any_of:
        if not seen_options.intersection(required_group):
            choices = ", ".join(sorted(required_group))
            raise _FormMismatch(f"one required option is missing ({choices})")

    _validate_positional_operands(
        module,
        action_name,
        action,
        positional_tokens,
        form.positional_operands,
    )


def _resolve_option_token(
    token: object,
    action: ActionSpecInput,
    options_by_name: dict[str, OptionPolicy],
) -> OptionPolicy | None:
    """Return policy for one option token or reject unsupported option syntax."""

    value: str | None = None
    if isinstance(token, str) and token.startswith("-"):
        value = token
    elif isinstance(token, FlagCmd):
        value = (action.flags or {})[token.flag].value

    if value is None:
        return None
    if value == "--" or "=" in value or _looks_like_short_option_cluster(value):
        raise _FormMismatch(f"unsupported option syntax '{value}'")
    option = options_by_name.get(value)
    if option is None:
        raise _FormMismatch(f"option '{value}' is not allowed")
    if isinstance(token, FlagCmd) and option.value_kind is not None:
        raise _FormMismatch(f"option '{value}' requires a non-flag value")
    return option


def _looks_like_short_option_cluster(value: str) -> bool:
    """Return whether a short option token combines multiple option letters."""

    return value.startswith("-") and not value.startswith("--") and len(value) > 2


def _validate_positional_operands(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    tokens: list[object],
    policies: tuple[OperandPolicy, ...],
) -> None:
    """Validate positional command tokens against the canonical grammar."""

    if len(policies) != 1:
        raise _FormMismatch("unsupported positional invocation form")

    policy = policies[0]
    minimum = 0
    maximum = 0
    for token in tokens:
        min_count, max_count = _validate_value_token(
            module,
            action_name,
            action,
            token,
            policy.kind,
        )
        minimum += min_count
        maximum += max_count

    if minimum < policy.min_count or maximum > policy.max_count:
        raise _FormMismatch(
            f"expected {policy.min_count} to {policy.max_count} positional operand(s)"
        )


def _validate_value_token(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    token: object,
    kind: OperandKind,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
    max_length: int | None = None,
) -> tuple[int, int]:
    """Prove one DSL token domain fits one reviewed operand kind.

    Returns:
        Minimum and maximum argv token count contributed by the DSL token.
    """

    if kind is OperandKind.MANAGED_INPUT:
        return _validate_managed_input_token(action, token)

    if kind is OperandKind.MANAGED_OUTPUT:
        if isinstance(token, OutputCmd):
            return (1, 1)
        raise _FormMismatch("operand must reference a managed output")

    if kind is OperandKind.SECRET_FILE:
        if isinstance(token, ArgCmd):
            arg_spec = (action.args or {}).get(token.arg)
            if (
                arg_spec is not None
                and arg_spec.type is ParamType.SECRET
                and arg_spec.delivery is not None
                and arg_spec.delivery.type == "file"
            ):
                return (1, 1)
        raise _FormMismatch("operand must reference a file-delivered secret")

    if kind is OperandKind.POSITIVE_INT:
        _validate_positive_int_token(action, token, min_value, max_value)
        return (1, 1)

    if kind is OperandKind.PATTERN:
        _validate_pattern_token(module, action_name, action, token, max_length)
        return (1, 1)

    raise _FormMismatch(f"unsupported operand kind '{kind.value}'")


def _validate_managed_input_token(
    action: ActionSpecInput,
    token: object,
) -> tuple[int, int]:
    """Require one token to expand exclusively to managed file paths."""

    if not isinstance(token, ArgCmd):
        raise _FormMismatch("file operand must reference a managed file_id")
    arg_spec = (action.args or {}).get(token.arg)
    if arg_spec is None:
        raise _FormMismatch("file operand references an undefined argument")
    if arg_spec.type is ParamType.FILE_ID:
        return (1, 1)
    if arg_spec.type is not ParamType.LIST or arg_spec.items is not ParamType.FILE_ID:
        raise _FormMismatch("file operand must reference file_id or list[file_id]")

    constraints = arg_spec.constraints or {}
    min_items = constraints.get("min_items")
    max_items = constraints.get("max_items")
    if not isinstance(min_items, int) or not isinstance(max_items, int):
        raise _FormMismatch("list[file_id] operands require min_items and max_items")
    return (min_items, max_items)


def _validate_positive_int_token(
    action: ActionSpecInput,
    token: object,
    min_value: int | None,
    max_value: int | None,
) -> None:
    """Require an integer token domain to fit the reviewed numeric range."""

    if isinstance(token, str):
        try:
            value = int(token)
        except ValueError as exc:
            raise _FormMismatch("option value must be an integer") from exc
        if str(value) != token or not _within_range(value, min_value, max_value):
            raise _FormMismatch("option value is outside the allowed range")
        return

    if not isinstance(token, ArgCmd):
        raise _FormMismatch("option value must reference an integer argument")
    arg_spec = (action.args or {}).get(token.arg)
    if arg_spec is None or arg_spec.type is not ParamType.INT:
        raise _FormMismatch("option value must reference an int argument")
    constraints = arg_spec.constraints or {}
    lower = constraints.get("min")
    upper = constraints.get("max")
    if not isinstance(lower, int) or not isinstance(upper, int):
        raise _FormMismatch("integer argument requires min and max constraints")
    if not _within_range(lower, min_value, max_value) or not _within_range(
        upper, min_value, max_value
    ):
        raise _FormMismatch("integer argument domain exceeds the allowed range")


def _validate_pattern_token(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    token: object,
    max_length: int | None,
) -> None:
    """Require a bounded string pattern without treating slash as a path."""

    if isinstance(token, ArgCmd):
        arg_spec = (action.args or {}).get(token.arg)
        if arg_spec is None or arg_spec.type is not ParamType.STRING:
            raise _FormMismatch("pattern must reference a string argument")
        constraints = arg_spec.constraints or {}
        minimum = constraints.get("min_length")
        maximum = constraints.get("max_length")
        if not isinstance(minimum, int) or minimum < 1:
            raise _FormMismatch("pattern argument requires min_length of at least 1")
        if not isinstance(maximum, int) or max_length is None or maximum > max_length:
            raise _FormMismatch("pattern argument exceeds the maximum allowed length")
        return

    if not isinstance(token, str):
        raise _FormMismatch("pattern must be a string literal or string argument")
    _validate_pattern_literal(module, action_name, action, token, max_length)


def _validate_pattern_literal(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    literal: str,
    max_length: int | None,
) -> None:
    """Validate a literal pattern or a finite validated placeholder expansion."""

    if literal == "":
        raise _FormMismatch("pattern must not be empty")

    placeholders = tuple(CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(literal))
    if not placeholders:
        if max_length is not None and len(literal) > max_length:
            raise _FormMismatch("pattern exceeds the maximum allowed length")
        return

    base_length = len(CONST_TEMPLATE_PLACEHOLDER_PATTERN.sub("", literal))
    rendered_length = base_length
    for name in placeholders:
        arg_spec = (action.args or {}).get(name)
        if arg_spec is None or arg_spec.type is not ParamType.STRING:
            raise _FormMismatch("pattern placeholder must reference a string argument")
        allowed_values = (arg_spec.constraints or {}).get("allowed_values")
        if not isinstance(allowed_values, list) or not allowed_values:
            raise _FormMismatch(
                "pattern placeholder requires finite string allowed_values"
            )
        if not all(isinstance(value, str) for value in allowed_values):
            raise _FormMismatch("pattern placeholder allowed_values must be strings")
        rendered_length += max(len(value) for value in allowed_values)

    if max_length is not None and rendered_length > max_length:
        raise _FormMismatch("pattern placeholder expansion exceeds the maximum length")


def _within_range(
    value: int,
    minimum: int | None,
    maximum: int | None,
) -> bool:
    """Return whether a value is inside optional inclusive bounds."""

    return (minimum is None or value >= minimum) and (
        maximum is None or value <= maximum
    )


def _action_binary(action: ActionSpecInput) -> str:
    """Return the first binary token from an already validated action."""

    first = action.command[0]
    if not isinstance(first, BinaryCmd):
        raise ActionSpecsPolicyError("validated action has no first binary token")
    return first.binary


def _policy_for_binary(binary: str, module: ModuleSpec) -> BinaryInvocationPolicy:
    """Resolve one reviewed extension binary profile or raise safely."""

    policy = get_binary_invocation_policy(binary)
    if policy is None:
        _raise_module_error(
            module, f"binary '{binary}' has no reviewed extension policy"
        )
    return policy


def _action_fqdn(module: ModuleSpec, action_name: str) -> str:
    """Build the canonical action FQDN used by builder and registry."""

    return ".".join((*module.namespace, module.module, action_name))


def _raise_module_error(module: ModuleSpec, message: str) -> NoReturn:
    """Raise one safely scoped module policy error."""

    raise ActionSpecsPolicyError(f"Module '{module.module}': {message}")


def _raise_action_error(
    module: ModuleSpec,
    action_name: str,
    message: str,
) -> NoReturn:
    """Raise one safely scoped action policy error."""

    raise ActionSpecsPolicyError(
        f"Module '{module.module}', action '{action_name}': {message}"
    )


class _FormMismatch(Exception):
    """Internal signal used while trying reviewed invocation forms."""
