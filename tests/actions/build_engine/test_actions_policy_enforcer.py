"""Unit tests for extension capability and binary invocation policy."""

from __future__ import annotations

import pytest

from star.actions.build_engine.policy_enforcer import enforce_build_policies
from star.actions.exceptions import ActionSpecsPolicyError
from star.actions.models import SpecProvenance
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

    with pytest.raises(ActionSpecsPolicyError, match="unknown extension capability"):
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

    with pytest.raises(ActionSpecsPolicyError, match="unknown extension capability"):
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

    with pytest.raises(ActionSpecsPolicyError, match="not authorize a used binary"):
        enforce_build_policies([module], _settings())


def test_enforcer_ignores_known_core_capabilities_for_authorization(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a CORE module with advisory text-search capability and echo action
    WHEN build-time policy enforcement runs
    THEN the advisory declaration does not restrict CORE execution policy
    """
    payload = make_module_payload(
        binaries=["echo"],
        actions={"run": make_action_spec_input(command=[{"binary": "echo"}])},
    )
    payload["capabilities"] = ["text-search"]
    module = make_module_spec(payload)

    result = enforce_build_policies([module], _settings(capabilities="none"))

    assert result.for_action("test_module.run").allowed == ("echo",)


# ============================================================================
# Invocation grammar
# ============================================================================


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
