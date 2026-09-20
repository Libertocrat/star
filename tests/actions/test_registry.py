"""
Unit tests for the STAR DSL runtime registry.

These tests freeze immutable registry invariants:
- loading and compiling DSL modules
- deterministic action lookup
- explicit not-found behavior
- stable listing and membership semantics
"""

from __future__ import annotations

import pytest

import star.actions.build_engine.loader as loader_module
import star.actions.registry as registry_module
from star.actions.exceptions import (
    ActionNotFoundError,
    ActionSpecsParseError,
    ActionSpecsPolicyError,
)
from star.actions.models import ActionSpec, InvocationTokenRole, ParamType
from star.actions.registry import ActionRegistry, build_registry_from_specs
from star.core.config import Settings

# ============================================================================
# Registry Build
# ============================================================================


def test_build_registry_from_specs_success(tmp_path, monkeypatch):
    """
    GIVEN a minimal valid DSL module under a temporary specs directory
    WHEN build_registry_from_specs is called
    THEN it returns an ActionRegistry with the declared action available
    """
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    (specs_dir / "sample.yml").write_text(
        """
version: 1
module: sample
description: "Sample runtime module"
binaries:
  - openssl

actions:
  ping:
    description: "Ping action"
    summary: "Ping"
    command:
      - binary: openssl
      - rand
      - -hex
      - "16"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.model_validate(
        {
            "star_root_dir": str(tmp_path),
        }
    )

    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))

    registry = build_registry_from_specs(settings)

    assert isinstance(registry, ActionRegistry)
    assert registry.has("sample.ping") is True

    spec = registry.get("sample.ping")
    assert isinstance(spec, ActionSpec)


def test_build_registry_from_specs_uses_directory_namespace(tmp_path, monkeypatch):
    """
    GIVEN a valid DSL module inside a namespace directory
    WHEN build_registry_from_specs is called
    THEN the registry exposes the fully namespaced action name
    """
    specs_dir = tmp_path / "specs"
    nested = specs_dir / "file"
    nested.mkdir(parents=True, exist_ok=True)

    (nested / "sample.yml").write_text(
        """
version: 1
module: sample
description: "Sample runtime module"
binaries:
  - openssl

actions:
    ping:
        description: "Ping action"
        summary: "Ping"
        command:
            - binary: openssl
            - rand
            - -hex
            - "16"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.model_validate(
        {
            "star_root_dir": str(tmp_path),
        }
    )

    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))

    registry = build_registry_from_specs(settings)

    assert registry.has("file.sample.ping") is True
    assert registry.has("sample.ping") is False


def test_build_registry_from_specs_rejects_invalid_extension_before_publication(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN an extension module that embeds a static host path
    WHEN the runtime registry is built
    THEN startup fails before publishing an action registry
    """
    specs_dir = tmp_path / "extensions"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "sample.yml").write_text(
        """
version: 1
module: sample
description: "Invalid extension module"
binaries:
  - echo

actions:
  ping:
    description: "Ping"
    command:
      - binary: echo
      - --config=templates/report.txt
""".strip(),
        encoding="utf-8",
    )
    settings = Settings.model_validate({"star_root_dir": str(tmp_path)})
    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))
    monkeypatch.setattr(loader_module, "EXTENSION_SPECS_DIR", specs_dir)

    with pytest.raises(ActionSpecsParseError, match="host paths"):
        build_registry_from_specs(settings)


def test_build_registry_from_specs_rejects_extension_without_capabilities(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a mounted extension that uses a reviewed binary without capabilities
    WHEN the runtime registry is built
    THEN startup rejects it before an executable action can be published
    """
    specs_dir = tmp_path / "extensions"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "sample.yml").write_text(
        """
version: 1
module: sample
description: "Unapproved extension module"
binaries:
  - file

actions:
  inspect:
    description: "Inspect a managed file"
    summary: "Inspect"
    args:
      input_file:
        type: file_id
        description: "Managed input file"
        required: true
    command:
      - binary: file
      - arg: input_file
""".strip(),
        encoding="utf-8",
    )
    settings = Settings.model_validate({"star_root_dir": str(tmp_path)})
    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))
    monkeypatch.setattr(loader_module, "EXTENSION_SPECS_DIR", specs_dir)

    with pytest.raises(ActionSpecsPolicyError, match="must declare capabilities"):
        build_registry_from_specs(settings)


@pytest.mark.parametrize(
    "action_id",
    [
        "base.crypto.encrypt_file_aes256",
        "base.crypto.decrypt_file_aes256",
    ],
    ids=["encrypt", "decrypt"],
)
def test_builtin_aes_actions_do_not_render_passphrase_in_pass_argv(
    tmp_path,
    action_id: str,
):
    """
    GIVEN the built-in AES actions
    WHEN the default registry is built
    THEN password uses secret delivery and is never rendered through pass: argv
    """
    settings = Settings.model_validate(
        {
            "star_root_dir": str(tmp_path),
        }
    )

    registry = build_registry_from_specs(settings)
    spec = registry.get(action_id)
    password = spec.arg_defs["password"]
    const_values = [
        token["value"] for token in spec.command_template if token["kind"] == "const"
    ]
    arg_refs = [
        token["name"] for token in spec.command_template if token["kind"] == "arg"
    ]

    assert password.type == ParamType.SECRET
    assert password.delivery is not None
    assert "-pass" in const_values
    assert "password" not in arg_refs
    assert not any(value.startswith("pass:") for value in const_values)

    if password.delivery.type == "file":
        assert password.delivery.append_newline is False
        assert "file:{password}" in const_values
    elif password.delivery.type == "stdin":
        assert "stdin" in const_values
    else:
        pytest.fail(f"unexpected AES password delivery: {password.delivery.type}")


# ============================================================================
# Action Lookup
# ============================================================================


def test_registry_get_returns_action_spec(valid_registry):
    """
    GIVEN a valid immutable registry
    WHEN one known action is retrieved via get()
    THEN the returned object is an ActionSpec
    """
    result = valid_registry.get("test_runtime.ping")

    assert isinstance(result, ActionSpec)
    assert result.name == "test_runtime.ping"


def test_registry_get_unknown_action_raises(valid_registry):
    """
    GIVEN a valid immutable registry
    WHEN an unknown action is requested via get()
    THEN ActionNotFoundError is raised
    """
    with pytest.raises(ActionNotFoundError):
        valid_registry.get("test_runtime.missing")


def test_valid_registry_compiles_diverse_reviewed_core_forms(valid_registry):
    """
    GIVEN the shared runtime registry fixture
    WHEN it is compiled through STAR's production policy pipeline
    THEN it retains representative reviewed binaries and typed DSL primitives
    """

    specs = [valid_registry.get(name) for name in valid_registry.list_names()]
    inspect_column = valid_registry.get("test_runtime.inspect_column")
    encrypt_secret = valid_registry.get("test_runtime.encrypt_secret")

    assert {spec.binary for spec in specs} == {"cat", "cut", "openssl", "seq"}
    assert tuple(
        token.role for token in inspect_column.invocation_policy.template_tokens
    ) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.POSITIVE_INT,
        InvocationTokenRole.MANAGED_INPUT,
    )
    assert inspect_column.defaults == {"position": 1, "complement": False}
    assert encrypt_secret.invocation_policy.template_tokens[-1].role is (
        InvocationTokenRole.SECRET_FILE
    )


# ============================================================================
# Membership and listing
# ============================================================================


def test_registry_has(valid_registry):
    """
    GIVEN a valid immutable registry
    WHEN has() is called with known and unknown actions
    THEN it returns True for existing names and False otherwise
    """
    assert valid_registry.has("test_runtime.ping") is True
    assert valid_registry.has("test_runtime.unknown") is False


def test_registry_list_names_sorted(valid_registry):
    """
    GIVEN a valid immutable registry with multiple actions
    WHEN list_names() is called
    THEN names are returned in deterministic sorted order
    """
    names = valid_registry.list_names()

    assert isinstance(names, tuple)
    assert names == tuple(sorted(names))
    assert "test_runtime.ping" in names
    assert "test_runtime.repeat" in names
