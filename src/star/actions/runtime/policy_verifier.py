"""Final pre-spawn verification for compiled extension invocation policy."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import NoReturn

from star.actions.exceptions import ActionInvocationPolicyError
from star.actions.models.core import ActionSpec, SpecProvenance
from star.actions.models.runtime import RenderedAction, RenderedArgvToken
from star.actions.models.security import (
    CompiledTemplateTokenPolicy,
    InvocationTokenRole,
)
from star.actions.runtime.file_manager import resolve_output_blob_path
from star.core.config import Settings
from star.core.files import get_blob_path, get_secret_tmp_dir, load_file_metadata

_POLICY_FAILURE = "Rendered extension invocation failed runtime policy verification"


def verify_rendered_invocation(
    rendered: RenderedAction,
    spec: ActionSpec,
    *,
    settings: Settings | None = None,
) -> None:
    """Verify typed rendered state immediately before subprocess creation.

    Args:
        rendered: Invocation state produced by the runtime renderer.
        spec: Compiled action specification that authorized the invocation.
        settings: Explicit runtime settings snapshot used for managed resources.

    Raises:
        ActionInvocationPolicyError: If compiled or rendered policy state is
            absent, corrupted, stale, or inconsistent.
    """

    if not rendered.tokens:
        _reject()

    first = rendered.tokens[0]
    if first.value != spec.binary:
        _reject()

    if spec.provenance is SpecProvenance.CORE:
        return

    policy = spec.extension_invocation_policy
    if policy is None or policy.binary != spec.binary:
        _reject()
    if not policy.template_tokens or len(policy.template_tokens) != len(
        spec.command_template
    ):
        _reject()
    first_expected = policy.template_tokens[0]
    if (
        first_expected.template_index != 0
        or first_expected.role is not InvocationTokenRole.BINARY
        or first_expected.exact_value != spec.binary
    ):
        _reject()

    _verify_compiled_expansions(rendered, spec, settings=settings)
    _verify_option_invariants(rendered, spec)


def _verify_compiled_expansions(
    rendered: RenderedAction,
    spec: ActionSpec,
    *,
    settings: Settings | None,
) -> None:
    """Match every rendered token to its immutable template descriptor.

    Args:
        rendered: Typed invocation state produced by the renderer.
        spec: Compiled extension action specification.
        settings: Runtime settings snapshot for managed resources.

    Raises:
        ActionInvocationPolicyError: If expansion order, count, or content does
            not match the compiled policy.
    """

    policy = spec.extension_invocation_policy
    if policy is None:
        _reject()

    rendered_index = 0
    previous_template_index = -1
    for expected_index, expected in enumerate(policy.template_tokens):
        if (
            expected.template_index != expected_index
            or expected.template_index <= previous_template_index
        ):
            _reject()
        previous_template_index = expected.template_index

        start = rendered_index
        while (
            rendered_index < len(rendered.tokens)
            and rendered.tokens[rendered_index].template_index
            == expected.template_index
        ):
            _verify_token(
                rendered.tokens[rendered_index],
                expected,
                rendered,
                settings=settings,
            )
            rendered_index += 1

        count = rendered_index - start
        if count < expected.min_count or count > expected.max_count:
            _reject()

    if rendered_index != len(rendered.tokens):
        _reject()


def _verify_token(
    token: RenderedArgvToken,
    expected: CompiledTemplateTokenPolicy,
    rendered: RenderedAction,
    *,
    settings: Settings | None,
) -> None:
    """Verify one typed token against its compiled origin and value policy.

    Args:
        token: Rendered token under verification.
        expected: Compiled template-position policy.
        rendered: Complete invocation ownership state.
        settings: Runtime settings snapshot for managed resources.

    Raises:
        ActionInvocationPolicyError: If token identity or value is invalid.
    """

    if (
        token.template_index != expected.template_index
        or token.source is not expected.source
        or token.role is not expected.role
        or token.reference != expected.reference
        or token.template_references != expected.template_references
    ):
        _reject()
    if not isinstance(token.value, str) or token.value == "":
        _reject()
    if expected.exact_value is not None and token.value != expected.exact_value:
        _reject()

    role = expected.role
    if (
        role
        not in {
            InvocationTokenRole.MANAGED_INPUT,
            InvocationTokenRole.MANAGED_OUTPUT,
        }
        and token.managed_file_id is not None
    ):
        _reject()
    if role is InvocationTokenRole.POSITIVE_INT:
        _verify_positive_int(token.value, expected)
    elif role is InvocationTokenRole.PATTERN:
        _verify_pattern(token.value, expected)
    elif role is InvocationTokenRole.MANAGED_INPUT:
        _verify_managed_input(token, settings=settings)
    elif role is InvocationTokenRole.MANAGED_OUTPUT:
        _verify_managed_output(token, rendered, settings=settings)
    elif role is InvocationTokenRole.SECRET_FILE:
        _verify_secret_file(token, rendered, settings=settings)


def _verify_positive_int(
    value: str,
    expected: CompiledTemplateTokenPolicy,
) -> None:
    """Require a canonical integer within the compiled inclusive range.

    Args:
        value: Rendered numeric token.
        expected: Compiled numeric bounds.

    Raises:
        ActionInvocationPolicyError: If the value is malformed or out of range.
    """

    try:
        parsed = int(value)
    except ValueError:
        _reject()
    if str(parsed) != value or parsed <= 0:
        _reject()
    if expected.min_value is not None and parsed < expected.min_value:
        _reject()
    if expected.max_value is not None and parsed > expected.max_value:
        _reject()


def _verify_pattern(
    value: str,
    expected: CompiledTemplateTokenPolicy,
) -> None:
    """Require a bounded non-empty pattern while continuing to allow slash.

    Args:
        value: Rendered pattern token.
        expected: Compiled pattern length policy.

    Raises:
        ActionInvocationPolicyError: If the pattern is unsafe or oversized.
    """

    if expected.max_length is None or len(value) > expected.max_length:
        _reject()
    if "\x00" in value or any(unicodedata.category(char) == "Cc" for char in value):
        _reject()


def _verify_managed_input(
    token: RenderedArgvToken,
    *,
    settings: Settings | None,
) -> None:
    """Revalidate one managed input identity, state, and canonical blob path.

    Args:
        token: Managed input token under verification.
        settings: Runtime settings snapshot for managed storage.

    Raises:
        ActionInvocationPolicyError: If identity, state, path, or blob presence
            is inconsistent.
    """

    file_id = token.managed_file_id
    if file_id is None:
        _reject()
    metadata = load_file_metadata(file_id, settings)
    expected_path = get_blob_path(file_id, settings)
    if (
        metadata is None
        or metadata.id != file_id
        or metadata.status != "ready"
        or token.value != str(expected_path)
        or not expected_path.exists()
    ):
        _reject()


def _verify_managed_output(
    token: RenderedArgvToken,
    rendered: RenderedAction,
    *,
    settings: Settings | None,
) -> None:
    """Require an output token to match its invocation-owned ledger entry.

    Args:
        token: Managed output token under verification.
        rendered: Complete invocation ownership state.
        settings: Runtime settings snapshot for managed storage.

    Raises:
        ActionInvocationPolicyError: If ownership, metadata, or path differs.
    """

    file_id = token.managed_file_id
    reference = token.reference
    if file_id is None or reference is None:
        _reject()
    if rendered.output_files.get(reference) != file_id:
        _reject()
    metadata = load_file_metadata(file_id, settings)
    if (
        metadata is None
        or metadata.id != file_id
        or metadata.status != "pending"
        or token.value != resolve_output_blob_path(file_id, settings=settings)
    ):
        _reject()


def _verify_secret_file(
    token: RenderedArgvToken,
    rendered: RenderedAction,
    *,
    settings: Settings | None,
) -> None:
    """Require a secret path to belong to the current invocation ledger.

    Args:
        token: Secret-file token under verification.
        rendered: Complete invocation ownership state.
        settings: Runtime settings snapshot for temporary secret storage.

    Raises:
        ActionInvocationPolicyError: If the path is unowned or unsafe.
    """

    if token.managed_file_id is not None:
        _reject()
    path = Path(token.value)
    if path not in rendered.secret_files:
        _reject()
    if (
        path.parent != get_secret_tmp_dir(settings)
        or path.is_symlink()
        or not path.is_file()
    ):
        _reject()


def _verify_option_invariants(rendered: RenderedAction, spec: ActionSpec) -> None:
    """Recheck required, duplicate, and exclusive extension options.

    Args:
        rendered: Typed invocation state produced by the renderer.
        spec: Compiled extension action specification.

    Raises:
        ActionInvocationPolicyError: If option invariants no longer hold.
    """

    policy = spec.extension_invocation_policy
    if policy is None:
        _reject()
    options_by_name = {
        name: option for option in policy.form.options for name in option.names
    }
    seen_options: set[str] = set()
    seen_groups: set[str] = set()

    for token in rendered.tokens:
        if token.role is not InvocationTokenRole.OPTION:
            continue
        option = options_by_name.get(token.value)
        if option is None or option.canonical_name in seen_options:
            _reject()
        seen_options.add(option.canonical_name)
        if option.exclusive_group is not None:
            if option.exclusive_group in seen_groups:
                _reject()
            seen_groups.add(option.exclusive_group)

    for option in policy.form.options:
        if option.required and option.canonical_name not in seen_options:
            _reject()
    for required_group in policy.form.required_any_of:
        if not seen_options.intersection(required_group):
            _reject()


def _reject() -> NoReturn:
    """Raise the safe runtime policy failure without sensitive details.

    Raises:
        ActionInvocationPolicyError: Always.
    """

    raise ActionInvocationPolicyError(_POLICY_FAILURE)
