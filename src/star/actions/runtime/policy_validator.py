"""Request-dependent extension invocation policy validation."""

from __future__ import annotations

from typing import Any, Mapping, NoReturn

from star.actions.exceptions import (
    ActionInvocationIntegrityError,
    ActionInvocationParamsError,
)
from star.actions.models.core import ActionSpec, SpecProvenance
from star.actions.models.security import (
    CommandTokenSource,
    CompiledTemplateTokenPolicy,
    InvocationTokenRole,
)

_PARAM_FAILURE = "Extension invocation parameters do not satisfy the action policy."


def validate_extension_invocation_params(
    spec: ActionSpec,
    params: Mapping[str, Any],
) -> None:
    """Validate request-dependent extension option requirements before render.

    Args:
        spec: Compiled action specification selected by the registry.
        params: Pydantic-validated action parameters with defaults applied.

    Raises:
        ActionInvocationParamsError: If enabled client flags do not satisfy a
            required option or required-any-of group.
        ActionInvocationIntegrityError: If compiled policy state is inconsistent.
    """

    if spec.provenance is SpecProvenance.CORE:
        return

    policy = spec.extension_invocation_policy
    if policy is None or policy.binary != spec.binary:
        _integrity_failure()

    options_by_name = {
        name: option for option in policy.form.options for name in option.names
    }
    seen_options: set[str] = set()
    seen_groups: set[str] = set()

    for template_token in policy.template_tokens:
        if template_token.role is not InvocationTokenRole.OPTION:
            continue

        option_value = _enabled_option_value(template_token, params)
        if option_value is None:
            continue
        option = options_by_name.get(option_value)
        if option is None or option.canonical_name in seen_options:
            _integrity_failure()
        seen_options.add(option.canonical_name)
        if option.exclusive_group is not None:
            if option.exclusive_group in seen_groups:
                _integrity_failure()
            seen_groups.add(option.exclusive_group)

    for option in policy.form.options:
        if option.required and option.canonical_name not in seen_options:
            _params_failure()
    for required_group in policy.form.required_any_of:
        if not seen_options.intersection(required_group):
            _params_failure()


def _enabled_option_value(
    template_token: CompiledTemplateTokenPolicy,
    params: Mapping[str, Any],
) -> str | None:
    """Return an enabled reviewed option or no value for a false flag.

    Args:
        template_token: Compiled policy for one option template position.
        params: Pydantic-validated action parameters with defaults applied.

    Returns:
        Exact enabled option spelling, or ``None`` for a disabled flag.

    Raises:
        ActionInvocationIntegrityError: If compiled option policy or validated
            flag state is inconsistent.
    """

    value = template_token.exact_value
    if value is None:
        _integrity_failure()

    if template_token.source is CommandTokenSource.CONST:
        return value
    if template_token.source is not CommandTokenSource.FLAG:
        _integrity_failure()

    reference = template_token.reference
    if reference is None or reference not in params:
        _integrity_failure()
    enabled = params[reference]
    if type(enabled) is not bool:
        _integrity_failure()
    return value if enabled else None


def _params_failure() -> NoReturn:
    """Raise the safe request-parameter policy failure.

    Raises:
        ActionInvocationParamsError: Always.
    """

    raise ActionInvocationParamsError(_PARAM_FAILURE)


def _integrity_failure() -> NoReturn:
    """Raise the safe compiled-policy integrity failure.

    Raises:
        ActionInvocationIntegrityError: Always.
    """

    raise ActionInvocationIntegrityError(
        "Compiled extension invocation policy is inconsistent."
    )
