"""Tests for final typed extension invocation policy verification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from star.actions.build_engine.builder import build_actions
from star.actions.build_engine.policy_enforcer import enforce_build_policies
from star.actions.build_engine.validator import validate_modules
from star.actions.exceptions import ActionInvocationPolicyError
from star.actions.models import (
    ActionSpec,
    ArgDef,
    BinaryPolicy,
    CommandTokenSource,
    CompiledExtensionInvocationPolicy,
    CompiledTemplateTokenPolicy,
    InvocationTokenRole,
    OutputDef,
    OutputSource,
    OutputType,
    ParamType,
    RenderedAction,
    RenderedArgvToken,
    SpecProvenance,
)
from star.actions.models.core import SecretDelivery
from star.actions.runtime import executor as executor_module
from star.actions.runtime.file_manager import (
    cleanup_output_placeholders,
    create_command_output_placeholders,
    create_ready_file_from_bytes,
    resolve_output_blob_path,
)
from star.actions.runtime.policy_verifier import verify_rendered_invocation
from star.actions.runtime.renderer import render_command
from star.actions.runtime.secret_manager import cleanup_secret_files, create_secret_file
from star.actions.security.binary_policies import (
    InvocationForm,
    OperandKind,
    OperandPolicy,
)
from star.core.config import Settings
from star.core.schemas.files import FileMetadata

# =============================================================================
# Helpers
# =============================================================================


def _settings(tmp_path: Path) -> Settings:
    """Build an isolated runtime settings snapshot for verifier tests.

    Args:
        tmp_path: Per-test temporary storage root.

    Returns:
        Validated settings with all extension capabilities enabled.
    """

    return Settings.model_validate(
        {
            "star_api_token": "a" * 64,
            "star_root_dir": str(tmp_path),
            "star_enabled_extension_capabilities": "all",
        }
    )


def _create_managed_input(
    settings: Settings,
    content: bytes = b"content\n",
) -> FileMetadata:
    """Create one ready managed input under an isolated STAR root.

    Args:
        settings: Isolated runtime settings snapshot.
        content: Bytes stored in the managed input.

    Returns:
        Persisted ready file metadata.
    """

    return create_ready_file_from_bytes(
        original_filename="input.txt",
        content=content,
        extension=".txt",
        mime_type="text/plain",
        settings=settings,
    )


def _build_extension_action(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    *,
    settings: Settings,
    binary: str,
    capability: str,
    args: dict,
    command: list,
    flags: dict | None = None,
) -> ActionSpec:
    """Build one extension through validation, policy, and compilation.

    Args:
        make_module_payload: Factory for raw module payloads.
        make_module_spec: Factory for validated module models.
        make_action_spec_input: Factory for action input models.
        settings: Isolated runtime settings snapshot.
        binary: Reviewed extension executable.
        capability: Capability authorizing the executable.
        args: DSL argument definitions.
        command: DSL command template.
        flags: Optional DSL flag definitions.

    Returns:
        Compiled extension action specification.
    """

    action = make_action_spec_input(args=args, flags=flags, command=command)
    payload = make_module_payload(
        module_name="runtime_policy",
        binaries=[binary],
        actions={"run": action},
    )
    payload["capabilities"] = [capability]
    module = make_module_spec(payload).with_runtime_identity(
        ("user",),
        SpecProvenance.EXTENSION,
    )
    validate_modules([module])
    catalog_policy = enforce_build_policies([module], settings)
    return build_actions([module], catalog_policy)["user.runtime_policy.run"]


def _build_grep_invocation(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    *,
    tmp_path: Path,
) -> tuple[ActionSpec, RenderedAction, Settings]:
    """Build and render one policy-valid grep extension invocation.

    Args:
        make_module_payload: Factory for raw module payloads.
        make_module_spec: Factory for validated module models.
        make_action_spec_input: Factory for action input models.
        tmp_path: Per-test temporary storage root.

    Returns:
        Compiled action, rendered invocation, and runtime settings.
    """

    settings = _settings(tmp_path)
    spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="grep",
        capability="text-search",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "Search pattern",
            },
            "input_file": {
                "type": "file_id",
                "required": True,
                "description": "Managed input",
            },
        },
        flags={
            "ignore_case": {
                "value": "-i",
                "default": False,
                "description": "Ignore case",
            }
        },
        command=[
            {"binary": "grep"},
            {"flag": "ignore_case"},
            "-e",
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )
    metadata = _create_managed_input(settings, b"alpha/beta\n")
    rendered = render_command(
        spec,
        {
            "pattern": "alpha/beta",
            "input_file": metadata.id,
            "ignore_case": True,
        },
        settings=settings,
    )
    return spec, rendered, settings


# =============================================================================
# Typed Rendering And Runtime Rejection
# =============================================================================


def test_rendered_extension_tokens_preserve_compiled_roles_and_origin(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    tmp_path: Path,
):
    """
    GIVEN a grep extension built through the complete DSL pipeline
    WHEN its runtime params are rendered and verified
    THEN typed tokens preserve exact origins, roles, and managed identity
    """

    spec, rendered, settings = _build_grep_invocation(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        tmp_path=tmp_path,
    )

    verify_rendered_invocation(rendered, spec, settings=settings)

    assert rendered.argv[:4] == ["grep", "-i", "-e", "alpha/beta"]
    assert tuple(token.source for token in rendered.tokens) == (
        CommandTokenSource.BINARY,
        CommandTokenSource.FLAG,
        CommandTokenSource.CONST,
        CommandTokenSource.ARG,
        CommandTokenSource.ARG,
    )
    assert tuple(token.role for token in rendered.tokens) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.PATTERN,
        InvocationTokenRole.MANAGED_INPUT,
    )
    assert rendered.tokens[-1].managed_file_id is not None
    with pytest.raises(TypeError):
        rendered.output_files["replacement"] = uuid4()  # type: ignore[index]


def test_verifier_rejects_corrupted_rendered_extension_state(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    tmp_path: Path,
):
    """
    GIVEN one valid typed extension invocation
    WHEN its binary, option, role, ordering, bounds, or ownership is corrupted
    THEN every altered form fails closed with the same safe runtime error
    """

    spec, rendered, settings = _build_grep_invocation(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        tmp_path=tmp_path,
    )
    tokens = rendered.tokens
    corruptions = (
        replace(rendered, tokens=(replace(tokens[0], value="file"), *tokens[1:])),
        replace(
            rendered,
            tokens=(*tokens[:2], replace(tokens[2], value="--regexp"), *tokens[3:]),
        ),
        replace(
            rendered,
            tokens=(
                *tokens[:3],
                replace(tokens[3], role=InvocationTokenRole.OPTION),
                *tokens[4:],
            ),
        ),
        replace(rendered, tokens=(tokens[0], tokens[2], tokens[1], *tokens[3:])),
        replace(
            rendered,
            tokens=(*tokens[:3], replace(tokens[3], value="x" * 4097), *tokens[4:]),
        ),
        replace(
            rendered,
            tokens=(*tokens[:-1], replace(tokens[-1], managed_file_id=uuid4())),
        ),
        replace(
            rendered,
            tokens=(
                *tokens[:3],
                replace(tokens[3], managed_file_id=uuid4()),
                *tokens[4:],
            ),
        ),
        replace(
            rendered,
            tokens=(
                *tokens,
                RenderedArgvToken(
                    value="extra",
                    template_index=99,
                    source=CommandTokenSource.CONST,
                    role=InvocationTokenRole.PATTERN,
                ),
            ),
        ),
    )

    for corrupted in corruptions:
        with pytest.raises(
            ActionInvocationPolicyError,
            match="failed runtime policy verification",
        ):
            verify_rendered_invocation(corrupted, spec, settings=settings)

    policy = spec.extension_invocation_policy
    assert policy is not None
    corrupted_spec = replace(
        spec,
        extension_invocation_policy=replace(
            policy,
            template_tokens=(policy.template_tokens[0], *policy.template_tokens[2:]),
        ),
    )
    with pytest.raises(ActionInvocationPolicyError):
        verify_rendered_invocation(rendered, corrupted_spec, settings=settings)


def test_verifier_binds_const_pattern_to_compiled_placeholder_sources(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    tmp_path: Path,
):
    """
    GIVEN a reviewed const pattern with one finite runtime placeholder
    WHEN rendering retains its placeholder reference
    THEN verification accepts the exact source and rejects a substituted source
    """

    settings = _settings(tmp_path)
    metadata = _create_managed_input(settings)
    spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="grep",
        capability="text-search",
        args={
            "mode": {
                "type": "string",
                "required": True,
                "constraints": {"allowed_values": ["alpha", "beta"]},
                "description": "Finite pattern suffix",
            },
            "input_file": {
                "type": "file_id",
                "required": True,
                "description": "Managed input",
            },
        },
        command=[
            {"binary": "grep"},
            "-e",
            "prefix:{mode}",
            {"arg": "input_file"},
        ],
    )
    rendered = render_command(
        spec,
        {"mode": "alpha", "input_file": metadata.id},
        settings=settings,
    )

    verify_rendered_invocation(rendered, spec, settings=settings)

    assert rendered.tokens[2].value == "prefix:alpha"
    assert rendered.tokens[2].template_references == ("mode",)
    corrupted = replace(
        rendered,
        tokens=(
            *rendered.tokens[:2],
            replace(rendered.tokens[2], template_references=("other",)),
            *rendered.tokens[3:],
        ),
    )
    with pytest.raises(ActionInvocationPolicyError):
        verify_rendered_invocation(corrupted, spec, settings=settings)


# =============================================================================
# Supported Invocation Forms
# =============================================================================


def test_verifier_rejects_required_option_group_omitted_at_runtime(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    tmp_path: Path,
):
    """
    GIVEN a wc extension whose reviewed selector flags are all optional params
    WHEN all selector flags render as false
    THEN runtime rejects the missing required-any-of option group
    """

    settings = _settings(tmp_path)
    flags = {
        name: {"value": value, "default": False, "description": name}
        for name, value in (("lines", "-l"), ("words", "-w"), ("chars", "-m"))
    }
    spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="wc",
        capability="file-inspection",
        args={
            "input_file": {
                "type": "file_id",
                "required": True,
                "description": "Managed input",
            }
        },
        flags=flags,
        command=[
            {"binary": "wc"},
            {"flag": "lines"},
            {"flag": "words"},
            {"flag": "chars"},
            {"arg": "input_file"},
        ],
    )
    metadata = _create_managed_input(settings)
    rendered = render_command(spec, {"input_file": metadata.id}, settings=settings)

    with pytest.raises(ActionInvocationPolicyError):
        verify_rendered_invocation(rendered, spec, settings=settings)


def test_verifier_accepts_all_current_extension_invocation_shapes(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    tmp_path: Path,
):
    """
    GIVEN valid file, head, tail, wc, and sha256sum extension definitions
    WHEN their typed runtime forms are rendered and verified
    THEN every currently reviewed non-grep invocation shape is accepted
    """

    settings = _settings(tmp_path)
    first = _create_managed_input(settings, b"first\n")
    second = _create_managed_input(settings, b"second\n")
    file_arg = {
        "input_file": {
            "type": "file_id",
            "required": True,
            "description": "Managed input",
        }
    }

    file_spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="file",
        capability="file-inspection",
        args=file_arg,
        command=[{"binary": "file"}, "--mime-type", {"arg": "input_file"}],
    )
    file_rendered = render_command(
        file_spec,
        {"input_file": first.id},
        settings=settings,
    )
    verify_rendered_invocation(file_rendered, file_spec, settings=settings)

    for binary in ("head", "tail"):
        line_spec = _build_extension_action(
            make_module_payload,
            make_module_spec,
            make_action_spec_input,
            settings=settings,
            binary=binary,
            capability="file-inspection",
            args={
                "lines": {
                    "type": "int",
                    "required": True,
                    "constraints": {"min": 1, "max": 10000},
                    "description": "Line count",
                },
                **file_arg,
            },
            command=[
                {"binary": binary},
                "-n",
                {"arg": "lines"},
                {"arg": "input_file"},
            ],
        )
        line_rendered = render_command(
            line_spec,
            {"lines": 10, "input_file": first.id},
            settings=settings,
        )
        verify_rendered_invocation(line_rendered, line_spec, settings=settings)
        assert line_rendered.tokens[2].role is InvocationTokenRole.POSITIVE_INT

    wc_flags = {
        name: {"value": value, "default": False, "description": name}
        for name, value in (("lines", "-l"), ("words", "-w"), ("chars", "-m"))
    }
    wc_spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="wc",
        capability="file-inspection",
        args=file_arg,
        flags=wc_flags,
        command=[
            {"binary": "wc"},
            {"flag": "lines"},
            {"flag": "words"},
            {"flag": "chars"},
            {"arg": "input_file"},
        ],
    )
    wc_rendered = render_command(
        wc_spec,
        {"lines": True, "input_file": first.id},
        settings=settings,
    )
    verify_rendered_invocation(wc_rendered, wc_spec, settings=settings)

    checksum_spec = _build_extension_action(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        settings=settings,
        binary="sha256sum",
        capability="checksum",
        args={
            "input_files": {
                "type": "list",
                "items": "file_id",
                "constraints": {"min_items": 1, "max_items": 32},
                "description": "Managed inputs",
            }
        },
        command=[{"binary": "sha256sum"}, {"arg": "input_files"}],
    )
    checksum_rendered = render_command(
        checksum_spec,
        {"input_files": [first.id, second.id]},
        settings=settings,
    )
    verify_rendered_invocation(
        checksum_rendered,
        checksum_spec,
        settings=settings,
    )
    assert len(checksum_rendered.tokens) == 3

    expanded = replace(
        checksum_rendered,
        tokens=(
            checksum_rendered.tokens[0],
            *(checksum_rendered.tokens[1:] * 17),
        ),
    )
    with pytest.raises(ActionInvocationPolicyError):
        verify_rendered_invocation(expanded, checksum_spec, settings=settings)


# =============================================================================
# Pre-spawn And Resource Ownership Boundaries
# =============================================================================


@pytest.mark.asyncio
async def test_executor_does_not_spawn_when_extension_verification_fails(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    monkeypatch,
    tmp_path: Path,
):
    """
    GIVEN a rendered extension whose pattern role was corrupted after rendering
    WHEN execution reaches the final pre-spawn boundary
    THEN policy rejection occurs without creating a subprocess
    """

    spec, rendered, settings = _build_grep_invocation(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        tmp_path=tmp_path,
    )
    tokens = rendered.tokens
    corrupted = replace(
        rendered,
        tokens=(
            *tokens[:3],
            replace(tokens[3], role=InvocationTokenRole.OPTION),
            *tokens[4:],
        ),
    )
    spawned = False

    async def _fail_if_spawned(*_args, **_kwargs):
        """Record an unexpected attempt to create a subprocess."""

        nonlocal spawned
        spawned = True
        raise AssertionError("subprocess must not be created")

    monkeypatch.setattr(
        executor_module.asyncio,
        "create_subprocess_exec",
        _fail_if_spawned,
    )

    with pytest.raises(ActionInvocationPolicyError):
        await executor_module.execute_command(
            corrupted,
            spec,
            settings=settings,
        )

    assert spawned is False


def test_verifier_checks_output_and_secret_file_invocation_ownership(
    tmp_path: Path,
):
    """
    GIVEN synthetic reviewed output and secret-file roles for a future policy
    WHEN their rendered paths match the current invocation ownership ledgers
    THEN verification accepts them and rejects a secret outside that ledger
    """

    settings = _settings(tmp_path)
    form = InvocationForm(
        options=(),
        positional_operands=(
            OperandPolicy(OperandKind.MANAGED_OUTPUT),
            OperandPolicy(OperandKind.SECRET_FILE),
        ),
    )
    invocation_policy = CompiledExtensionInvocationPolicy(
        binary="echo",
        form=form,
        template_tokens=(
            CompiledTemplateTokenPolicy(
                0,
                CommandTokenSource.BINARY,
                InvocationTokenRole.BINARY,
                exact_value="echo",
            ),
            CompiledTemplateTokenPolicy(
                1,
                CommandTokenSource.OUTPUT,
                InvocationTokenRole.MANAGED_OUTPUT,
                reference="result",
            ),
            CompiledTemplateTokenPolicy(
                2,
                CommandTokenSource.ARG,
                InvocationTokenRole.SECRET_FILE,
                reference="password",
            ),
        ),
    )
    spec = ActionSpec(
        name="user.synthetic.ownership",
        namespace=("user",),
        module="synthetic",
        action="ownership",
        version=1,
        params_model=BaseModel,
        binary="echo",
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "output", "name": "result"},
            {"kind": "arg", "name": "password"},
        ),
        execution_policy=BinaryPolicy(("echo",), ()),
        arg_defs={
            "password": ArgDef(
                ParamType.SECRET,
                required=True,
                delivery=SecretDelivery("file"),
            )
        },
        flag_defs={},
        defaults={},
        provenance=SpecProvenance.EXTENSION,
        extension_invocation_policy=invocation_policy,
        outputs={"result": OutputDef(OutputType.FILE, OutputSource.COMMAND)},
    )
    output_files = create_command_output_placeholders(spec, settings=settings)
    secret_path = create_secret_file(
        "synthetic-secret",
        append_newline=False,
        settings=settings,
    )
    output_id = output_files["result"]
    rendered = RenderedAction(
        tokens=(
            RenderedArgvToken(
                value="echo",
                template_index=0,
                source=CommandTokenSource.BINARY,
                role=InvocationTokenRole.BINARY,
            ),
            RenderedArgvToken(
                value=resolve_output_blob_path(output_id, settings=settings),
                template_index=1,
                source=CommandTokenSource.OUTPUT,
                role=InvocationTokenRole.MANAGED_OUTPUT,
                reference="result",
                managed_file_id=output_id,
            ),
            RenderedArgvToken(
                value=str(secret_path),
                template_index=2,
                source=CommandTokenSource.ARG,
                role=InvocationTokenRole.SECRET_FILE,
                reference="password",
            ),
        ),
        output_files=output_files,
        secret_files=(secret_path,),
    )

    try:
        verify_rendered_invocation(rendered, spec, settings=settings)
        corrupted = replace(rendered, secret_files=())
        with pytest.raises(ActionInvocationPolicyError):
            verify_rendered_invocation(corrupted, spec, settings=settings)
    finally:
        cleanup_secret_files((secret_path,), settings=settings)
        cleanup_output_placeholders(output_files, settings=settings)
