"""Unit tests for extension capability and binary invocation policy."""

from __future__ import annotations

import pytest

from star.actions.build_engine.policy_enforcer import (
    enforce_build_policies,
    validate_invocation_policy_catalog,
)
from star.actions.exceptions import ActionSpecsPolicyError
from star.actions.models import (
    CommandTokenSource,
    InvocationTokenRole,
    SpecProvenance,
)
from star.actions.security.binary_policies import (
    BINARY_INVOCATION_POLICIES,
    BinaryInvocationPolicy,
    InvocationAuthorization,
    InvocationForm,
)
from star.actions.security.capabilities import InvocationCapability
from star.core.config import Settings


def _settings(*, capabilities: str = "all") -> Settings:
    """Build explicit settings for policy-enforcer tests."""

    return Settings.model_validate(
        {
            "star_root_dir": "/tmp/star-policy-test",  # noqa: S108
            "star_enabled_extension_capabilities": capabilities,
        }
    )


def _extension_module(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    *,
    capabilities: list[str] | None,
    binary: str,
    args: dict | None = None,
    flags: dict | None = None,
    command: list | None = None,
):
    """Build one mounted module with explicit capability metadata."""

    action = make_action_spec_input(
        args=args,
        flags=flags,
        command=command or [{"binary": binary}],
    )
    payload = make_module_payload(
        module_name="extension_module",
        binaries=[binary],
        actions={"run": action},
    )
    payload["capabilities"] = capabilities
    return make_module_spec(payload).with_runtime_identity(
        ("user",),
        SpecProvenance.EXTENSION,
    )


def _file_arg() -> dict:
    """Return the smallest valid managed input definition."""

    return {
        "type": "file_id",
        "required": True,
        "description": "Managed input file",
    }


# ============================================================================
# Capability admission
# ============================================================================


def test_enforcer_accepts_reviewed_extension_file_inspection_module(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an extension that requests file-inspection and uses file safely
    WHEN build-time policy enforcement runs
    THEN it produces immutable execution policy for the action
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="file",
        args={"input_file": _file_arg()},
        command=[{"binary": "file"}, {"arg": "input_file"}],
    )

    result = enforce_build_policies([module], _settings())

    assert result.for_action("user.extension_module.run").allowed == ("file",)
    invocation = result.invocation_for_action("user.extension_module.run")
    assert invocation is not None
    assert invocation.binary == "file"
    assert tuple(
        (token.template_index, token.source, token.role, token.reference)
        for token in invocation.template_tokens
    ) == (
        (0, CommandTokenSource.BINARY, InvocationTokenRole.BINARY, None),
        (1, CommandTokenSource.ARG, InvocationTokenRole.MANAGED_INPUT, "input_file"),
    )


def test_enforcer_rejects_unreviewed_core_binary(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a CORE action that names an allowlisted but unreviewed binary
    WHEN build-time invocation policy enforcement runs
    THEN the action is rejected instead of receiving a provenance exemption
    """
    action = make_action_spec_input(command=[{"binary": "echo"}, "hello"])
    module = make_module_spec(
        make_module_payload(
            module_name="core_module",
            binaries=["echo"],
            actions={"run": action},
        )
    )

    with pytest.raises(ActionSpecsPolicyError, match="no reviewed invocation policy"):
        enforce_build_policies([module], _settings())


def test_catalog_validator_rejects_duplicate_provenance_authorization(monkeypatch):
    """
    GIVEN a reviewed form with duplicate CORE authorization entries
    WHEN the catalog integrity validator runs
    THEN registry construction fails closed before any action is compiled
    """
    authorization = InvocationAuthorization(SpecProvenance.CORE)
    malformed = BinaryInvocationPolicy(
        binary="malformed",
        forms=(
            InvocationForm(
                authorizations=(authorization, authorization),
                options=(),
            ),
        ),
    )
    monkeypatch.setitem(BINARY_INVOCATION_POLICIES, "malformed", malformed)

    with pytest.raises(ActionSpecsPolicyError, match="duplicate provenance"):
        validate_invocation_policy_catalog()


def test_catalog_validator_rejects_capability_scoped_core_authorization(monkeypatch):
    """
    GIVEN a malformed CORE authorization that requires an extension capability
    WHEN the catalog integrity validator runs
    THEN the invalid provenance and capability coupling is rejected
    """
    malformed = BinaryInvocationPolicy(
        binary="malformed",
        forms=(
            InvocationForm(
                authorizations=(
                    InvocationAuthorization(
                        SpecProvenance.CORE,
                        frozenset({InvocationCapability.FILE_INSPECTION}),
                    ),
                ),
                options=(),
            ),
        ),
    )
    monkeypatch.setitem(BINARY_INVOCATION_POLICIES, "malformed", malformed)

    with pytest.raises(ActionSpecsPolicyError, match="capabilities for CORE"):
        validate_invocation_policy_catalog()


def test_enforcer_rejects_extension_without_capabilities(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a mounted module without a capability declaration
    WHEN build-time policy enforcement runs
    THEN the registry build fails closed
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=None,
        binary="file",
        args={"input_file": _file_arg()},
        command=[{"binary": "file"}, {"arg": "input_file"}],
    )

    with pytest.raises(ActionSpecsPolicyError, match="must declare capabilities"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_unknown_operator_capability(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN syntactically valid operator configuration with an unknown capability
    WHEN the registry policy stage resolves the reviewed catalog
    THEN startup fails without accepting an unreviewed capability
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="file",
        args={"input_file": _file_arg()},
        command=[{"binary": "file"}, {"arg": "input_file"}],
    )

    with pytest.raises(ActionSpecsPolicyError, match="unknown invocation capability"):
        enforce_build_policies([module], _settings(capabilities="unknown-capability"))


def test_enforcer_rejects_unknown_module_capability(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a mounted module with an unreviewed capability name
    WHEN build-time policy enforcement runs
    THEN the module is rejected before its binary can be authorized
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["unknown-capability"],
        binary="file",
        args={"input_file": _file_arg()},
        command=[{"binary": "file"}, {"arg": "input_file"}],
    )

    with pytest.raises(ActionSpecsPolicyError, match="unknown invocation capability"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_disabled_extension_capability(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a valid extension capability disabled by operator configuration
    WHEN build-time policy enforcement runs
    THEN the module is rejected before compilation
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["text-search"],
        binary="grep",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "Search pattern",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "grep"},
            "-e",
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="disabled by operator policy"):
        enforce_build_policies([module], _settings(capabilities="none"))


def test_enforcer_rejects_binary_without_declared_capability(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an extension that declares file-inspection but uses grep
    WHEN build-time policy enforcement runs
    THEN the binary admission fails closed
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="grep",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "Search pattern",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "grep"},
            "-e",
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="not authorized by declared"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_core_only_openssl_form_for_extension(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an extension that declares a known capability and invokes OpenSSL
    WHEN build-time policy enforcement evaluates the provenance-scoped forms
    THEN it rejects the CORE-only OpenSSL operation
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["checksum"],
        binary="openssl",
        command=[{"binary": "openssl"}, "rand", "-hex", "16"],
    )

    with pytest.raises(ActionSpecsPolicyError, match="no reviewed extension"):
        enforce_build_policies([module], _settings())


def test_enforcer_ignores_known_core_capabilities_for_authorization(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a CORE module with advisory text-search capability and reviewed cat action
    WHEN build-time policy enforcement runs
    THEN the advisory declaration does not restrict CORE execution policy
    """
    payload = make_module_payload(
        binaries=["cat"],
        actions={
            "run": make_action_spec_input(
                command=[{"binary": "cat"}, "/proc/sys/kernel/random/uuid"]
            )
        },
    )
    payload["capabilities"] = ["text-search"]
    module = make_module_spec(payload)

    result = enforce_build_policies([module], _settings(capabilities="none"))

    assert result.for_action("test_module.run").allowed == ("cat",)
    assert (
        result.invocation_for_action("test_module.run").authorization.provenance
        is SpecProvenance.CORE
    )


# ============================================================================
# Invocation grammar
# ============================================================================


def test_enforcer_accepts_bounded_core_seq_with_optional_width_flag(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a CORE seq action with a bounded integer and reviewed width flag
    WHEN build-time policy enforcement runs
    THEN it compiles the typed invocation without an extension exemption
    """

    action = make_action_spec_input(
        args={
            "last": {
                "type": "int",
                "required": True,
                "constraints": {"min": 1, "max": 10000},
                "description": "Inclusive sequence endpoint",
            }
        },
        flags={
            "equal_width": {
                "value": "-w",
                "default": False,
                "description": "Pad sequence values",
            }
        },
        command=[
            {"binary": "seq"},
            {"flag": "equal_width"},
            {"arg": "last"},
        ],
    )
    module = make_module_spec(
        make_module_payload(binaries=["seq"], actions={"run": action})
    )

    result = enforce_build_policies([module], _settings())
    invocation = result.invocation_for_action("test_module.run")

    assert invocation is not None
    assert invocation.authorization.provenance is SpecProvenance.CORE
    assert tuple(token.role for token in invocation.template_tokens) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.POSITIVE_INT,
    )


def test_enforcer_accepts_extension_cut_with_file_inspection(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an extension that extracts one character from a managed file
    WHEN it declares file-inspection
    THEN the reviewed cut form compiles with typed option and file roles
    """

    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="cut",
        args={
            "position": {
                "type": "int",
                "required": True,
                "constraints": {"min": 1, "max": 10000},
                "description": "One-based character position",
            },
            "input_file": _file_arg(),
        },
        flags={
            "complement": {
                "value": "--complement",
                "default": False,
                "description": "Select all other positions",
            }
        },
        command=[
            {"binary": "cut"},
            {"flag": "complement"},
            "-c",
            {"arg": "position"},
            {"arg": "input_file"},
        ],
    )

    result = enforce_build_policies([module], _settings())
    invocation = result.invocation_for_action("user.extension_module.run")

    assert invocation is not None
    assert invocation.authorization.required_capabilities == frozenset(
        {InvocationCapability.FILE_INSPECTION}
    )
    assert tuple(token.role for token in invocation.template_tokens) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.POSITIVE_INT,
        InvocationTokenRole.MANAGED_INPUT,
    )


@pytest.mark.parametrize(
    "binary,capabilities,command",
    [
        (
            "seq",
            ["file-inspection"],
            [{"binary": "seq"}, "10"],
        ),
        (
            "cut",
            ["checksum"],
            [
                {"binary": "cut"},
                "-c",
                "1",
                {"arg": "input_file"},
            ],
        ),
    ],
    ids=["seq_core_only", "cut_requires_file_inspection"],
)
def test_enforcer_rejects_new_forms_without_matching_extension_authorization(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
    binary,
    capabilities,
    command,
):
    """
    GIVEN an extension that selects a reviewed binary without its authorization
    WHEN build-time policy enforcement runs
    THEN registry construction fails before any action is published
    """

    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=capabilities,
        binary=binary,
        args={"input_file": _file_arg()} if binary == "cut" else None,
        command=command,
    )

    with pytest.raises(
        ActionSpecsPolicyError, match="no reviewed extension|not authorized"
    ):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_cut_position_domain_above_policy_limit(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a cut extension whose character position exceeds the reviewed bound
    WHEN build-time policy enforcement runs
    THEN the broader integer domain is rejected before compilation
    """

    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="cut",
        args={
            "position": {
                "type": "int",
                "required": True,
                "constraints": {"min": 1, "max": 10001},
                "description": "Character position",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "cut"},
            "-c",
            {"arg": "position"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="domain exceeds"):
        enforce_build_policies([module], _settings())


def test_enforcer_accepts_regex_pattern_with_slash_and_managed_input(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a grep extension with a bounded runtime pattern and managed file
    WHEN the pattern may contain slash characters at execution time
    THEN the build policy accepts its typed grammar
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["text-search"],
        binary="grep",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 4096},
                "description": "URL-compatible regular expression",
            },
            "input_file": _file_arg(),
        },
        flags={
            "extended": {
                "value": "-E",
                "default": True,
                "description": "Use ERE syntax",
            }
        },
        command=[
            {"binary": "grep"},
            {"flag": "extended"},
            "-e",
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )

    enforce_build_policies([module], _settings())


def test_enforcer_rejects_grep_pattern_domain_above_policy_limit(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a grep extension whose pattern domain exceeds 4096 characters
    WHEN build-time policy enforcement runs
    THEN it rejects the broader declared string domain
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["text-search"],
        binary="grep",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 4097},
                "description": "Search pattern",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "grep"},
            "-e",
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="maximum allowed length"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_raw_string_file_operand(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an extension that supplies a raw string where file expects an input
    WHEN build-time policy enforcement runs
    THEN it rejects the untyped file operand
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="file",
        command=[{"binary": "file"}, "report.txt"],
    )

    with pytest.raises(ActionSpecsPolicyError, match="managed file_id"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_grep_without_canonical_pattern_option(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a grep extension with a positional dynamic pattern
    WHEN build-time policy enforcement runs
    THEN it requires the unambiguous -e pattern form
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["text-search"],
        binary="grep",
        args={
            "pattern": {
                "type": "string",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "Search pattern",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "grep"},
            {"arg": "pattern"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="required option '-e' is missing"):
        enforce_build_policies([module], _settings())


def test_enforcer_rejects_head_integer_domain_above_policy_limit(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a head extension whose lines argument exceeds the reviewed domain
    WHEN build-time policy enforcement runs
    THEN it rejects the broader declared integer range
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["file-inspection"],
        binary="head",
        args={
            "lines": {
                "type": "int",
                "required": False,
                "default": 10,
                "constraints": {"min": 1, "max": 10001},
                "description": "Requested line count",
            },
            "input_file": _file_arg(),
        },
        command=[
            {"binary": "head"},
            "-n",
            {"arg": "lines"},
            {"arg": "input_file"},
        ],
    )

    with pytest.raises(ActionSpecsPolicyError, match="domain exceeds"):
        enforce_build_policies([module], _settings())


def test_enforcer_accepts_bounded_sha256_list_inputs(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a checksum extension with a bounded list of managed file IDs
    WHEN build-time policy enforcement runs
    THEN the list operand is authorized without raw paths
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["checksum"],
        binary="sha256sum",
        args={
            "input_files": {
                "type": "list",
                "items": "file_id",
                "constraints": {"min_items": 2, "max_items": 32},
                "description": "Managed input files",
            }
        },
        command=[{"binary": "sha256sum"}, {"arg": "input_files"}],
    )

    enforce_build_policies([module], _settings())


def test_enforcer_rejects_sha256_list_domain_above_policy_limit(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a checksum extension whose managed file list can exceed 32 items
    WHEN build-time policy enforcement runs
    THEN it rejects the broader declared list cardinality
    """
    module = _extension_module(
        make_module_payload,
        make_module_spec,
        make_action_spec_input,
        capabilities=["checksum"],
        binary="sha256sum",
        args={
            "input_files": {
                "type": "list",
                "items": "file_id",
                "constraints": {"min_items": 2, "max_items": 33},
                "description": "Managed input files",
            }
        },
        command=[{"binary": "sha256sum"}, {"arg": "input_files"}],
    )

    with pytest.raises(ActionSpecsPolicyError, match="expected 1 to 32"):
        enforce_build_policies([module], _settings())
