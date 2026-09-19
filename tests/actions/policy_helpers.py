"""Test-only factories for compiled invocation policy values."""

from __future__ import annotations

from typing import cast

from star.actions.engine_config import CONST_TEMPLATE_PLACEHOLDER_PATTERN
from star.actions.models import (
    CommandTokenSource,
    CompiledInvocationPolicy,
    CompiledTemplateTokenPolicy,
    InvocationTokenRole,
    SpecProvenance,
)
from star.actions.models.core import (
    ArgCmd,
    BinaryCmd,
    CommandElement,
    ConstCmd,
    FlagCmd,
    OutputCmd,
)
from star.actions.security.binary_policies import (
    InvocationAuthorization,
    InvocationForm,
    OptionPolicy,
)
from star.actions.security.capabilities import InvocationCapability


def make_test_invocation_policy(
    binary: str,
    command_template: tuple[CommandElement, ...],
    *,
    provenance: SpecProvenance = SpecProvenance.CORE,
    flag_values: dict[str, str] | None = None,
) -> CompiledInvocationPolicy:
    """Build an exact synthetic policy for a normalized test template.

    Args:
        binary: Expected executable name.
        command_template: Normalized runtime command template.
        provenance: Loader-derived provenance represented by the test spec.
        flag_values: Exact option values keyed by referenced flag name.

    Returns:
        Immutable test-only invocation policy.
    """

    resolved_flag_values = flag_values or {}
    required_capabilities = (
        frozenset({InvocationCapability.FILE_INSPECTION})
        if provenance is SpecProvenance.EXTENSION
        else frozenset()
    )
    authorization = InvocationAuthorization(provenance, required_capabilities)
    options: list[OptionPolicy] = []
    for token in command_template:
        if token["kind"] != "flag":
            continue
        flag_token = cast(FlagCmd, token)
        options.append(OptionPolicy((resolved_flag_values[flag_token["name"]],)))
    form = InvocationForm(authorizations=(authorization,), options=tuple(options))
    compiled_tokens: list[CompiledTemplateTokenPolicy] = []

    for template_index, token in enumerate(command_template):
        kind = token["kind"]
        reference: str | None = None
        template_references: tuple[str, ...] = ()
        exact_value: str | None = None
        min_count = 1
        if kind == "binary":
            binary_token = cast(BinaryCmd, token)
            source = CommandTokenSource.BINARY
            role = InvocationTokenRole.BINARY
            exact_value = binary_token["value"]
        elif kind == "const":
            const_token = cast(ConstCmd, token)
            source = CommandTokenSource.CONST
            role = InvocationTokenRole.LITERAL
            template_references = tuple(
                CONST_TEMPLATE_PLACEHOLDER_PATTERN.findall(const_token["value"])
            )
            if not template_references:
                exact_value = const_token["value"]
        elif kind == "arg":
            arg_token = cast(ArgCmd, token)
            source = CommandTokenSource.ARG
            role = InvocationTokenRole.LITERAL
            reference = arg_token["name"]
        elif kind == "flag":
            flag_token = cast(FlagCmd, token)
            source = CommandTokenSource.FLAG
            role = InvocationTokenRole.OPTION
            reference = flag_token["name"]
            exact_value = resolved_flag_values[reference]
            min_count = 0
        elif kind == "output":
            output_token = cast(OutputCmd, token)
            source = CommandTokenSource.OUTPUT
            role = InvocationTokenRole.MANAGED_OUTPUT
            reference = output_token["name"]
        else:
            # A few renderer rejection tests deliberately retain malformed
            # normalized tokens so execution reaches the runtime guard.
            source = CommandTokenSource.CONST
            role = InvocationTokenRole.LITERAL
            min_count = 0

        compiled_tokens.append(
            CompiledTemplateTokenPolicy(
                template_index=template_index,
                source=source,
                role=role,
                reference=reference,
                template_references=template_references,
                exact_value=exact_value,
                min_count=min_count,
            )
        )

    return CompiledInvocationPolicy(
        binary=binary,
        form=form,
        authorization=authorization,
        template_tokens=tuple(compiled_tokens),
    )
