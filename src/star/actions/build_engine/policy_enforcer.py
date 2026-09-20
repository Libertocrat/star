"""Build-time capability and invocation policy enforcement for DSL modules."""

from __future__ import annotations

from typing import NoReturn

from star.actions.engine_config import CONST_TEMPLATE_PLACEHOLDER_PATTERN
from star.actions.exceptions import ActionSpecsPolicyError
from star.actions.models.core import ParamType
from star.actions.models.provenance import SpecProvenance
from star.actions.models.security import (
    BinaryPolicy,
    CommandTokenSource,
    CompiledInvocationPolicy,
    CompiledTemplateTokenPolicy,
    EffectiveCatalogPolicy,
    InvocationTokenRole,
)
from star.actions.schemas.action import ActionSpecInput
from star.actions.schemas.dsl import ArgCmd, BinaryCmd, FlagCmd, OutputCmd
from star.actions.schemas.module import ModuleSpec
from star.actions.security.binary_policies import (
    BINARY_INVOCATION_POLICIES,
    BinaryInvocationPolicy,
    InvocationAuthorization,
    InvocationForm,
    OperandKind,
    OperandPolicy,
    OptionPolicy,
    get_binary_invocation_policy,
)
from star.actions.security.capabilities import (
    InvocationCapability,
    parse_declared_capabilities,
    resolve_enabled_capabilities,
)
from star.actions.security.policy import build_binary_policy
from star.core.config import Settings


def enforce_build_policies(
    modules: list[ModuleSpec],
    settings: Settings,
) -> EffectiveCatalogPolicy:
    """Enforce invocation policy and compile effective per-action policy.

    Args:
        modules: Structurally and semantically validated DSL modules.
        settings: Explicit runtime settings snapshot.

    Returns:
        Immutable binary policy indexed by final action FQDN.

    Raises:
        ActionSpecsPolicyError: If catalog integrity, module capabilities,
            binary admission, or command grammar violates STAR policy.
    """

    validate_invocation_policy_catalog()
    try:
        enabled_capabilities = resolve_enabled_capabilities(
            settings.star_enabled_extension_capabilities
        )
    except ValueError as exc:
        raise ActionSpecsPolicyError(str(exc)) from exc

    policies: dict[str, BinaryPolicy] = {}
    invocation_policies: dict[str, CompiledInvocationPolicy] = {}
    for module in modules:
        declared_capabilities = _parse_module_capabilities(module)
        module_policy = _build_module_binary_policy(module, settings)

        if module.provenance is SpecProvenance.EXTENSION:
            _enforce_enabled_capabilities(
                module,
                declared_capabilities,
                enabled_capabilities,
            )

        compiled_module_policies: list[CompiledInvocationPolicy] = []
        for action_name, action in module.actions.items():
            compiled_policy = compile_invocation_policy(
                module,
                action_name,
                action,
                declared_capabilities,
            )
            compiled_module_policies.append(compiled_policy)
            invocation_policies[_action_fqdn(module, action_name)] = compiled_policy

        if module.provenance is SpecProvenance.EXTENSION:
            _enforce_declared_capabilities_used(
                module,
                declared_capabilities,
                compiled_module_policies,
            )

        for action_name in module.actions:
            policies[_action_fqdn(module, action_name)] = module_policy

    return EffectiveCatalogPolicy(
        action_policies=policies,
        invocation_policies=invocation_policies,
    )


def validate_invocation_policy_catalog() -> None:
    """Validate the immutable reviewed invocation-policy catalog.

    Raises:
        ActionSpecsPolicyError: If catalog keys, forms, provenance entries, or
            capability requirements are inconsistent.
    """

    for catalog_key, policy in BINARY_INVOCATION_POLICIES.items():
        if catalog_key != policy.binary:
            raise ActionSpecsPolicyError(
                "reviewed invocation policy catalog has a binary key mismatch"
            )
        if not policy.forms:
            raise ActionSpecsPolicyError(
                f"binary '{policy.binary}' has no reviewed invocation forms"
            )
        for form in policy.forms:
            if not form.authorizations:
                raise ActionSpecsPolicyError(
                    f"binary '{policy.binary}' has a form without authorization"
                )
            seen_provenances: set[SpecProvenance] = set()
            for authorization in form.authorizations:
                if authorization.provenance in seen_provenances:
                    raise ActionSpecsPolicyError(
                        f"binary '{policy.binary}' has duplicate provenance "
                        "authorization"
                    )
                seen_provenances.add(authorization.provenance)
                if not all(
                    isinstance(capability, InvocationCapability)
                    for capability in authorization.required_capabilities
                ):
                    raise ActionSpecsPolicyError(
                        f"binary '{policy.binary}' has an invalid capability "
                        "requirement"
                    )
                if (
                    authorization.provenance is SpecProvenance.CORE
                    and authorization.required_capabilities
                ):
                    raise ActionSpecsPolicyError(
                        f"binary '{policy.binary}' requires capabilities for CORE"
                    )
                if (
                    authorization.provenance is SpecProvenance.EXTENSION
                    and not authorization.required_capabilities
                ):
                    raise ActionSpecsPolicyError(
                        f"binary '{policy.binary}' has an unscoped EXTENSION "
                        "authorization"
                    )


def _parse_module_capabilities(
    module: ModuleSpec,
) -> tuple[InvocationCapability, ...]:
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


def _enforce_enabled_capabilities(
    module: ModuleSpec,
    declared_capabilities: tuple[InvocationCapability, ...],
    enabled_capabilities: frozenset[InvocationCapability],
) -> None:
    """Require every declared extension capability to be operator-enabled.

    Args:
        module: Extension module assigned by the loader.
        declared_capabilities: Canonical capabilities requested by the module.
        enabled_capabilities: Operator-enabled capabilities.

    Raises:
        ActionSpecsPolicyError: If the module requests disabled capabilities.
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


def _enforce_declared_capabilities_used(
    module: ModuleSpec,
    declared_capabilities: tuple[InvocationCapability, ...],
    compiled_policies: list[CompiledInvocationPolicy],
) -> None:
    """Require every declared capability to authorize a selected action form.

    Args:
        module: Extension module assigned by the loader.
        declared_capabilities: Canonical capabilities requested by the module.
        compiled_policies: Forms selected for every action in the module.

    Raises:
        ActionSpecsPolicyError: If a declared capability remains unused.
    """

    used_capabilities = frozenset(
        capability
        for policy in compiled_policies
        for capability in policy.authorization.required_capabilities
    )
    for capability in declared_capabilities:
        if capability in used_capabilities:
            continue
        _raise_module_error(
            module,
            (
                f"declared capability '{capability.value}' does not authorize "
                "a used binary"
            ),
        )


def compile_invocation_policy(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    declared_capabilities: tuple[InvocationCapability, ...] = (),
) -> CompiledInvocationPolicy:
    """Match and compile one provenance-authorized invocation form.

    Args:
        module: Parent module with loader-derived provenance.
        action_name: Action identifier within the module.
        action: Validated action AST.
        declared_capabilities: Module capabilities already parsed.

    Returns:
        Immutable compiled invocation policy selected for the action.

    Raises:
        ActionSpecsPolicyError: If no authorized reviewed form matches.
    """

    binary = _action_binary(action)
    policy = _policy_for_binary(binary, module)
    declared_set = frozenset(declared_capabilities)
    provenance_forms = tuple(
        (form, authorization)
        for form in policy.forms
        for authorization in form.authorizations
        if authorization.provenance is module.provenance
    )
    authorized_forms = tuple(
        (form, authorization)
        for form, authorization in provenance_forms
        if authorization.required_capabilities.issubset(declared_set)
    )

    if not provenance_forms:
        _raise_action_error(
            module,
            action_name,
            (
                f"binary '{binary}' has no reviewed "
                f"{module.provenance.value} invocation form"
            ),
        )

    if not authorized_forms:
        _raise_action_error(
            module,
            action_name,
            f"binary '{binary}' is not authorized by declared capabilities",
        )

    errors: list[str] = []
    for form, authorization in authorized_forms:
        try:
            _enforce_invocation_form(module, action_name, action, form)
            return _compile_invocation_policy(action, binary, form, authorization)
        except _FormMismatch as exc:
            errors.append(str(exc))

    detail = errors[0] if errors else "no reviewed invocation form is available"
    _raise_action_error(module, action_name, detail)


def _compile_invocation_policy(
    action: ActionSpecInput,
    binary: str,
    form: InvocationForm,
    authorization: InvocationAuthorization,
) -> CompiledInvocationPolicy:
    """Compile one already-matched invocation form.

    Args:
        action: Validated action whose command matched the reviewed form.
        binary: Exact reviewed executable.
        form: Deterministically selected invocation form.
        authorization: Provenance-specific authorization selected for the form.

    Returns:
        Immutable template-position policy consumed by rendering and runtime.
    """

    compiled: list[CompiledTemplateTokenPolicy] = [
        CompiledTemplateTokenPolicy(
            template_index=0,
            source=CommandTokenSource.BINARY,
            role=InvocationTokenRole.BINARY,
            exact_value=binary,
        )
    ]
    options_by_name = {name: option for option in form.options for name in option.names}
    reached_positional = False
    positional_policy = (
        form.positional_operands[0] if form.positional_operands else None
    )
    index = 1

    for prefix_literal in form.prefix_literals:
        compiled.append(
            _compile_template_token(
                action,
                action.command[index],
                template_index=index,
                role=InvocationTokenRole.LITERAL,
                exact_value=prefix_literal,
            )
        )
        index += 1

    while index < len(action.command):
        token = action.command[index]
        option = _resolve_option_token(token, action, options_by_name)
        if option is not None and not reached_positional:
            option_value = _option_token_value(token, action)
            allowed_values = _finite_template_values(option_value, action) or ()
            compiled.append(
                _compile_template_token(
                    action,
                    token,
                    template_index=index,
                    role=InvocationTokenRole.OPTION,
                    exact_value=None if allowed_values else option_value,
                    allowed_values=allowed_values,
                    optional=isinstance(token, FlagCmd),
                )
            )
            if option.value_kind is not None:
                value_token = action.command[index + 1]
                compiled.append(
                    _compile_template_token(
                        action,
                        value_token,
                        template_index=index + 1,
                        role=_role_for_operand_kind(option.value_kind),
                        min_value=option.min_value,
                        max_value=option.max_value,
                        max_length=option.max_length,
                        value_prefix=option.value_prefix,
                    )
                )
                index += 2
                continue
            index += 1
            continue

        reached_positional = True
        if positional_policy is None:
            raise ActionSpecsPolicyError(
                "validated invocation has unexpected positional operand"
            )
        compiled.append(
            _compile_template_token(
                action,
                token,
                template_index=index,
                role=_role_for_operand_kind(positional_policy.kind),
                min_value=positional_policy.min_value,
                max_value=positional_policy.max_value,
                max_length=positional_policy.max_length,
                value_prefix=positional_policy.value_prefix,
            )
        )
        index += 1

    return CompiledInvocationPolicy(
        binary=binary,
        form=form,
        authorization=authorization,
        template_tokens=tuple(compiled),
    )


def _compile_template_token(
    action: ActionSpecInput,
    token: object,
    *,
    template_index: int,
    role: InvocationTokenRole,
    exact_value: str | None = None,
    allowed_values: tuple[str, ...] = (),
    optional: bool = False,
    min_value: int | None = None,
    max_value: int | None = None,
    max_length: int | None = None,
    value_prefix: str | None = None,
) -> CompiledTemplateTokenPolicy:
    """Compile the expected origin and cardinality for one template token.

    Args:
        action: Validated action that owns the token.
        token: Validated DSL command token.
        template_index: Zero-based position in the command template.
        role: Semantic invocation role proven by the selected form.
        exact_value: Optional exact rendered value required by policy.
        allowed_values: Optional finite rendered-value domain.
        optional: Whether the template position may render no token.
        min_value: Optional inclusive numeric lower bound.
        max_value: Optional inclusive numeric upper bound.
        max_length: Optional rendered string length bound.
        value_prefix: Optional exact wrapper prefix.

    Returns:
        Immutable compiled policy for the template position.
    """

    source, reference, literal = _command_token_identity(token)
    minimum, maximum = _compiled_token_cardinality(action, token, role)
    if optional:
        minimum = 0
    return CompiledTemplateTokenPolicy(
        template_index=template_index,
        source=source,
        role=role,
        reference=reference,
        template_references=(
            tuple(CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(token))
            if isinstance(token, str)
            else ()
        ),
        exact_value=exact_value if exact_value is not None else literal,
        allowed_values=allowed_values,
        min_count=minimum,
        max_count=maximum,
        min_value=min_value,
        max_value=max_value,
        max_length=max_length,
        value_prefix=value_prefix,
    )


def _command_token_identity(
    token: object,
) -> tuple[CommandTokenSource, str | None, str | None]:
    """Return structural identity for one DSL token.

    Args:
        token: Validated DSL command token.

    Returns:
        Structural source, referenced name, and optional exact literal.

    Raises:
        ActionSpecsPolicyError: If the token type is not supported.
    """

    if isinstance(token, str):
        literal = (
            None
            if CONST_TEMPLATE_PLACEHOLDER_PATTERN.search(token) is not None
            else token
        )
        return CommandTokenSource.CONST, None, literal
    if isinstance(token, ArgCmd):
        return CommandTokenSource.ARG, token.arg, None
    if isinstance(token, FlagCmd):
        return CommandTokenSource.FLAG, token.flag, None
    if isinstance(token, OutputCmd):
        return CommandTokenSource.OUTPUT, token.output, None
    if isinstance(token, BinaryCmd):
        return CommandTokenSource.BINARY, None, token.binary
    raise ActionSpecsPolicyError("validated command has unsupported token")


def _option_token_value(token: object, action: ActionSpecInput) -> str:
    """Return the exact option spelling selected by one command token.

    Args:
        token: Static or flag-backed option token.
        action: Action that owns any referenced flag.

    Returns:
        Exact option spelling selected by the DSL.

    Raises:
        ActionSpecsPolicyError: If the token cannot represent an option.
    """

    if isinstance(token, str):
        return token
    if isinstance(token, FlagCmd):
        return (action.flags or {})[token.flag].value
    raise ActionSpecsPolicyError("validated invocation option has unsupported token")


def _compiled_token_cardinality(
    action: ActionSpecInput,
    token: object,
    role: InvocationTokenRole,
) -> tuple[int, int]:
    """Return render-time cardinality already proven by build validation.

    Args:
        action: Validated action that owns the token.
        token: Validated DSL command token.
        role: Semantic role selected by the invocation form.

    Returns:
        Minimum and maximum number of rendered argv tokens.
    """

    if role is InvocationTokenRole.MANAGED_INPUT and isinstance(token, ArgCmd):
        arg_spec = (action.args or {})[token.arg]
        if arg_spec.type is ParamType.LIST:
            constraints = arg_spec.constraints or {}
            return constraints["min_items"], constraints["max_items"]
    return (1, 1)


def _role_for_operand_kind(kind: OperandKind) -> InvocationTokenRole:
    """Map a reviewed operand kind to its rendered semantic role.

    Args:
        kind: Operand kind declared by the binary invocation policy.

    Returns:
        Equivalent runtime token role.
    """

    return InvocationTokenRole(kind.value)


def _enforce_invocation_form(
    module: ModuleSpec,
    action_name: str,
    action: ActionSpecInput,
    form: InvocationForm,
) -> None:
    """Validate one command template against one invocation form."""

    options_by_name = {name: option for option in form.options for name in option.names}
    seen_options: set[str] = set()
    seen_groups: set[str] = set()
    positional_tokens: list[object] = []
    index = 1
    command = action.command
    reached_positional = False

    for prefix_literal in form.prefix_literals:
        if index >= len(command) or command[index] != prefix_literal:
            raise _FormMismatch(
                f"required literal '{prefix_literal}' is missing or misplaced"
            )
        index += 1

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
                value_prefix=option.value_prefix,
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
        rendered_values = _finite_template_values(token, action)
        if rendered_values is not None:
            matching = {
                option
                for rendered_value in rendered_values
                for option in (options_by_name.get(rendered_value),)
                if option is not None
            }
            if len(matching) != 1:
                raise _FormMismatch("option template is not allowed")
            option = next(iter(matching))
            if not all(
                rendered_value in option.names for rendered_value in rendered_values
            ):
                raise _FormMismatch("option template is not allowed")
            return option
        value = token
    elif isinstance(token, FlagCmd):
        value = (action.flags or {})[token.flag].value

    if value is None:
        return None
    exact_option = options_by_name.get(value)
    if exact_option is not None:
        if isinstance(token, FlagCmd) and exact_option.value_kind is not None:
            raise _FormMismatch(f"option '{value}' requires a non-flag value")
        return exact_option
    if value == "--" or "=" in value or _looks_like_short_option_cluster(value):
        raise _FormMismatch(f"unsupported option syntax '{value}'")
    raise _FormMismatch(f"option '{value}' is not allowed")


def _finite_template_values(
    value: str,
    action: ActionSpecInput,
) -> tuple[str, ...] | None:
    """Return a finite rendered domain for one single-placeholder template.

    Args:
        value: Static token that may contain a validated placeholder.
        action: Action that owns the referenced argument definition.

    Returns:
        Every possible rendered token, or ``None`` for a literal token.

    Raises:
        _FormMismatch: If a template is not backed by a finite string domain.
    """

    placeholders = tuple(CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(value))
    if not placeholders:
        return None
    if len(placeholders) != 1 or value.count(f"{{{placeholders[0]}}}") != 1:
        raise _FormMismatch("option template must contain one finite placeholder")

    arg_spec = (action.args or {}).get(placeholders[0])
    allowed_values = (
        None if arg_spec is None else (arg_spec.constraints or {}).get("allowed_values")
    )
    if (
        arg_spec is None
        or arg_spec.type is not ParamType.STRING
        or not isinstance(allowed_values, list)
        or not allowed_values
        or not all(isinstance(item, str) for item in allowed_values)
    ):
        raise _FormMismatch("option template requires finite string allowed_values")

    placeholder = f"{{{placeholders[0]}}}"
    return tuple(value.replace(placeholder, item) for item in allowed_values)


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

    if not policies:
        if tokens:
            raise _FormMismatch("positional operands are not allowed")
        return
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
            min_value=policy.min_value,
            max_value=policy.max_value,
            max_length=policy.max_length,
            value_prefix=policy.value_prefix,
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
    value_prefix: str | None = None,
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
        if _is_file_delivered_secret_token(action, token, value_prefix):
            return (1, 1)
        raise _FormMismatch("operand must reference a file-delivered secret")

    if kind is OperandKind.POSITIVE_INT:
        _validate_positive_int_token(action, token, min_value, max_value)
        return (1, 1)

    if kind is OperandKind.PATTERN:
        _validate_pattern_token(module, action_name, action, token, max_length)
        return (1, 1)

    raise _FormMismatch(f"unsupported operand kind '{kind.value}'")


def _is_file_delivered_secret_token(
    action: ActionSpecInput,
    token: object,
    value_prefix: str | None,
) -> bool:
    """Return whether one token is an exact file-delivered secret reference.

    Args:
        action: Action that owns the referenced secret argument.
        token: Direct arg token or wrapped const-template token.
        value_prefix: Required exact wrapper prefix, when applicable.

    Returns:
        Whether the token resolves only to one file-delivered secret reference.
    """

    if isinstance(token, ArgCmd):
        if value_prefix is not None:
            return False
        arg_name = token.arg
    elif isinstance(token, str) and value_prefix is not None:
        placeholders = tuple(CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(token))
        if len(placeholders) != 1 or token != f"{value_prefix}{{{placeholders[0]}}}":
            return False
        arg_name = placeholders[0]
    else:
        return False

    arg_spec = (action.args or {}).get(arg_name)
    return bool(
        arg_spec is not None
        and arg_spec.type is ParamType.SECRET
        and arg_spec.delivery is not None
        and arg_spec.delivery.type == "file"
    )


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
    """Resolve one reviewed binary profile or raise safely."""

    policy = get_binary_invocation_policy(binary)
    if policy is None:
        _raise_module_error(
            module, f"binary '{binary}' has no reviewed invocation policy"
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
