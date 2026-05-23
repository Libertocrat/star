"""Unit tests for the STAR DSL specs loader.

These tests freeze loader-layer invariants:
- deterministic discovery across ordered spec directories
- strict parse behavior for malformed or invalid module files
- security validation before YAML parsing
- fail-fast semantics for bulk loading
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import star.actions.build_engine.loader as loader_module
from star.actions.build_engine.loader import (
    _mask_path,
    discover_spec_files,
    load_module_spec,
    load_module_specs,
    validate_yaml_file_safety,
)
from star.actions.exceptions import ActionSpecsParseError
from star.actions.schemas import ModuleSpec
from star.core.config import Settings

# ============================================================================
# Local fixtures and helpers
# ============================================================================


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return deterministic Settings for loader tests.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        Settings object with deterministic root directory.
    """

    return Settings.model_validate(
        {
            "star_root_dir": str(tmp_path),
            "star_max_yml_bytes": 512,
        }
    )


@pytest.fixture
def core_specs_dir(tmp_path: Path) -> Path:
    """Return an isolated core specs directory for loader tests."""
    path = tmp_path / "core-specs"
    path.mkdir()
    return path


@pytest.fixture
def user_specs_dir(tmp_path: Path) -> Path:
    """Return an isolated user specs directory for loader tests."""
    path = tmp_path / "user-specs"
    path.mkdir()
    return path


@pytest.fixture
def make_module_payload():
    """Return a factory for minimal valid `ModuleSpec`-compatible payloads."""

    def _make(module_name: str) -> dict[str, Any]:
        """Build a minimal valid module payload.

        Args:
            module_name: Bare module name to embed in payload.

        Returns:
            ModuleSpec-compatible dictionary.
        """

        return {
            "version": 1,
            "module": module_name,
            "description": f"{module_name} module",
            "binaries": ["echo"],
            "actions": {
                "ping": {
                    "description": "Simple command",
                    "command": [{"binary": "echo"}],
                }
            },
        }

    return _make


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Write a dictionary payload to YAML file using UTF-8 encoding."""
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def write_yaml_str(path: Path, content: str) -> None:
    """Write raw YAML string to file using UTF-8 encoding.

    Args:
        path: Target file path.
        content: YAML string content.
    """

    path.write_text(content, encoding="utf-8")


@pytest.fixture
def valid_yaml_module_str() -> str:
    """Return a realistic STAR DSL YAML string with full feature coverage.

    This fixture represents a human-written DSL module including all supported
    elements such as args, flags, constraints, and mixed command tokens.

    Returns:
        YAML string representing a valid DSL module.
    """

    return """
version: 1
module: test_module
description: "Test module for loader"

authors:
    - "Tester <tester@example.com>"

tags: [test, loader, dsl]

binaries:
    - echo
    - printf

actions:

    complex_action:
        description: "A complex action"
        summary: "Complex test"

        args:
            input:
                type: string
                required: true
                description: "Input string"

            count:
                type: int
                required: false
                default: 5
                constraints:
                    min: 1
                    max: 10
                description: "Repeat count"

            file:
                type: file_id
                required: false
                constraints:
                    max_size: 1024
                description: "Optional file reference"

        flags:
            verbose:
                value: "-v"
                default: false
                description: "Verbose output"

        command:
            - binary: echo
            - flag: verbose
            - "Processing:"
            - arg: input
            - "x"
            - arg: count
"""


@pytest.fixture
def make_invalid_yaml():
    """Return a factory for generating parametrically malformed YAML DSL strings.

    This fixture builds a base valid YAML structure and allows injecting
    controlled corruption via keyword flags.

    Each flag overrides a specific fragment of the YAML, enabling precise
    testing of failure scenarios while keeping the structure consistent.

    Returns:
        Callable that produces malformed YAML strings based on flags.
    """

    def _make(
        *,
        syntax_error: bool = False,
        bad_indent: bool = False,
        non_mapping_root: bool = False,
        empty: bool = False,
    ) -> str:
        """Generate malformed YAML variants for parser failure scenarios.

        Args:
            syntax_error: Produce invalid YAML syntax.
            bad_indent: Produce invalid indentation.
            non_mapping_root: Produce a non-mapping YAML root.
            empty: Produce an effectively empty YAML document.

        Returns:
            YAML string with requested malformed characteristic.
        """

        # ------------------------------------------------------------------
        # Hard overrides (highest priority)
        # ------------------------------------------------------------------

        if syntax_error:
            return "module: [broken\n"

        if non_mapping_root:
            return """
- version: 1
- module: broken
"""

        if empty:
            return "# empty YAML\n"

        # ------------------------------------------------------------------
        # Parametric fragments
        # ------------------------------------------------------------------

        command_block = """
      command:
        - binary: echo
        - arg: value
"""

        if bad_indent:
            command_block = """
      command:
        - binary: echo
         - arg: value
"""

        # ------------------------------------------------------------------
        # Final YAML assembly
        # ------------------------------------------------------------------

        return f"""
version: 1
module: broken_module
description: "Broken module"

binaries:
  - echo

actions:
  test:
    description: "Test"
{command_block}
"""

    return _make


# ============================================================================
# discover_spec_files
# ============================================================================


def test_discover_spec_files_returns_sorted_files_per_directory_order(
    core_specs_dir: Path,
    user_specs_dir: Path,
):
    """
    GIVEN ordered core and user directories with nested valid YAML modules
    WHEN discover_spec_files is called
    THEN files are sorted by relative path while preserving directory order
    """
    (core_specs_dir / "a").mkdir()
    (core_specs_dir / "b").mkdir()
    (user_specs_dir / "custom").mkdir()

    (core_specs_dir / "a" / "zeta.yml").write_text("version: 1\n", encoding="utf-8")
    (core_specs_dir / "a" / "alpha.yml").write_text("version: 1\n", encoding="utf-8")
    (core_specs_dir / "b" / "beta.yml").write_text("version: 1\n", encoding="utf-8")
    (user_specs_dir / "custom" / "tool.yml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )

    discovered = discover_spec_files([core_specs_dir, user_specs_dir])

    assert [path.relative_to(core_specs_dir).as_posix() for path in discovered[:3]] == [
        "a/alpha.yml",
        "a/zeta.yml",
        "b/beta.yml",
    ]
    assert discovered[3].relative_to(user_specs_dir).as_posix() == "custom/tool.yml"


def test_discover_spec_files_skips_missing_directories(tmp_path: Path):
    """
    GIVEN an ordered list with missing spec directories
    WHEN discover_spec_files is called
    THEN an empty list is returned
    """
    missing_one = tmp_path / "missing-one"
    missing_two = tmp_path / "missing-two"

    discovered = discover_spec_files([missing_one, missing_two])

    assert discovered == []


def test_discover_spec_files_raises_when_path_is_not_directory(tmp_path: Path):
    """
    GIVEN an existing path that is a regular file
    WHEN discover_spec_files is called
    THEN ActionSpecsParseError is raised
    """
    file_path = tmp_path / "not_a_directory"
    file_path.write_text("not-a-dir\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="not a directory"):
        discover_spec_files([file_path])


def test_discover_spec_files_allows_nested_directories(core_specs_dir: Path):
    """
    GIVEN valid YAML modules inside nested namespace directories
    WHEN discover_spec_files is called
    THEN nested YAML files are discovered in deterministic relative-path order
    """
    nested = core_specs_dir / "file" / "crypto"
    nested.mkdir(parents=True)
    (nested / "hash.yml").write_text("version: 1\n", encoding="utf-8")
    (core_specs_dir / "file" / "compress.yml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )

    discovered = discover_spec_files([core_specs_dir])

    assert [path.relative_to(core_specs_dir).as_posix() for path in discovered] == [
        "file/compress.yml",
        "file/crypto/hash.yml",
    ]


def test_discover_spec_files_raises_when_invalid_extension_exists(core_specs_dir: Path):
    """
    GIVEN a specs directory containing a non-YAML file
    WHEN discover_spec_files is called
    THEN ActionSpecsParseError is raised
    """
    (core_specs_dir / "module.txt").write_text("bad\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="Invalid STAR DSL spec extension"):
        discover_spec_files([core_specs_dir])


def test_discover_spec_files_ignores_empty_directories(core_specs_dir: Path):
    """
    GIVEN empty namespace directories under a specs root
    WHEN discover_spec_files is called
    THEN no files are returned and no error is raised
    """
    (core_specs_dir / "file" / "crypto").mkdir(parents=True)

    discovered = discover_spec_files([core_specs_dir])

    assert discovered == []


def test_discover_spec_files_ignores_hidden_directories(core_specs_dir: Path):
    """
    GIVEN YAML files under a hidden directory
    WHEN discover_spec_files is called
    THEN hidden directory contents are ignored
    """
    hidden_dir = core_specs_dir / ".hidden_specs"
    hidden_dir.mkdir()
    (hidden_dir / "ignored.yml").write_text("version: 1\n", encoding="utf-8")

    discovered = discover_spec_files([core_specs_dir])

    assert discovered == []


def test_discover_spec_files_rejects_invalid_extension_in_nested_directory(
    core_specs_dir: Path,
):
    """
    GIVEN a non-YAML file inside a valid namespace directory
    WHEN discover_spec_files is called
    THEN ActionSpecsParseError is raised
    """
    nested = core_specs_dir / "file"
    nested.mkdir()
    (nested / "module.txt").write_text("bad\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="Invalid STAR DSL spec extension"):
        discover_spec_files([core_specs_dir])


@pytest.mark.parametrize(
    "dirname",
    ["Invalid", "bad-name", "bad name", "123bad", "_hidden"],
    ids=[
        "invalid_uppercase",
        "invalid_dash",
        "invalid_space",
        "invalid_start_digit",
        "invalid_hidden",
    ],
)
def test_discover_spec_files_rejects_invalid_namespace_directory_name(
    core_specs_dir: Path,
    dirname: str,
):
    """
    GIVEN a namespace directory whose name violates STAR naming rules
    WHEN discover_spec_files is called
    THEN ActionSpecsParseError is raised
    """
    nested = core_specs_dir / dirname
    nested.mkdir()
    (nested / "module.yml").write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="[Ii]nvalid namespace directory"):
        discover_spec_files([core_specs_dir])


@pytest.mark.parametrize(
    "filename",
    ["Invalid.yml", "bad-name.yml", "bad name.yml", "123bad.yml", "_hidden.yml"],
    ids=[
        "invalid_uppercase",
        "invalid_dash",
        "invalid_space",
        "invalid_start_digit",
        "invalid_hidden",
    ],
)
def test_discover_spec_files_rejects_invalid_yaml_filename_stem(
    core_specs_dir: Path,
    filename: str,
):
    """
    GIVEN a YAML filename stem that violates STAR naming rules
    WHEN discover_spec_files is called
    THEN ActionSpecsParseError is raised
    """
    nested = core_specs_dir / "file"
    nested.mkdir()
    (nested / filename).write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="[Ii]nvalid module filename"):
        discover_spec_files([core_specs_dir])


# ============================================================================
# load_module_spec
# ============================================================================


def test_load_module_spec_returns_modulespec_for_valid_yml(
    core_specs_dir: Path,
    make_module_payload,
):
    """
    GIVEN a structurally valid DSL `.yml` module
    WHEN load_module_spec is called
    THEN a validated ModuleSpec instance is returned
    """
    spec_file = core_specs_dir / "valid.yml"
    write_yaml(spec_file, make_module_payload("checksum"))

    module = load_module_spec(spec_file)

    assert isinstance(module, ModuleSpec)
    assert module.module == "checksum"


def test_load_module_spec_raises_for_invalid_yaml(core_specs_dir: Path):
    """
    GIVEN a `.yml` file with invalid YAML syntax
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised with a masked path message
    """
    spec_file = core_specs_dir / "invalid_yaml.yml"
    spec_file.write_text("module: [broken\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="invalid_yaml.yml"):
        load_module_spec(spec_file)


def test_load_module_spec_raises_for_empty_yaml(core_specs_dir: Path):
    """
    GIVEN an empty YAML document
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised
    """
    spec_file = core_specs_dir / "empty.yml"
    spec_file.write_text("# comments only\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="YAML document is empty"):
        load_module_spec(spec_file)


@pytest.mark.parametrize(
    "yaml_content",
    [
        "- item1\n- item2\n",
        "plain-string\n",
        "123\n",
    ],
    ids=[
        "yaml_root_list",
        "yaml_root_scalar_string",
        "yaml_root_scalar_int",
    ],
)
def test_load_module_spec_raises_when_yaml_root_is_not_mapping(
    core_specs_dir: Path,
    yaml_content: str,
):
    """
    GIVEN a YAML document whose root is not a mapping
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised
    """
    spec_file = core_specs_dir / "bad_root.yml"
    spec_file.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="YAML root must be a mapping"):
        load_module_spec(spec_file)


def test_load_module_spec_raises_when_modulespec_validation_fails(
    core_specs_dir: Path,
):
    """
    GIVEN a YAML mapping that fails ModuleSpec structural validation
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised
    """
    spec_file = core_specs_dir / "invalid_schema.yml"
    write_yaml(
        spec_file,
        {
            "version": 1,
            "module": "broken",
        },
    )

    with pytest.raises(
        ActionSpecsParseError,
        match="invalid_schema.yml",
    ):
        load_module_spec(spec_file)


def test_load_module_spec_rejects_flat_arg_constraints(
    core_specs_dir: Path,
):
    """
    GIVEN an arg using flat constraint fields
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised due to unknown fields
    """
    spec_file = core_specs_dir / "flat_constraints.yml"
    write_yaml_str(
        spec_file,
        """
version: 1
module: flat_constraints
description: "flat constraints test yml"
binaries:
    - echo
actions:
    ping:
        description: "ping"
        args:
            value:
                type: int
                required: false
                default: 1
                min: 1
                max: 10
                description: "value"
        command:
            - binary: echo
            - arg: value
""".strip(),
    )

    with pytest.raises(ActionSpecsParseError, match="flat_constraints.yml"):
        load_module_spec(spec_file)


# ============================================================================
# load_module_spec (specs YAML realism layer)
# ============================================================================


def test_load_module_spec_parses_realistic_yaml(
    core_specs_dir: Path,
    valid_yaml_module_str: str,
):
    """
    GIVEN a realistic specs YAML module with args, flags, and mixed command tokens
    WHEN load_module_spec is called
    THEN a valid ModuleSpec is returned preserving structure
    """
    spec_file = core_specs_dir / "realistic.yml"
    write_yaml_str(spec_file, valid_yaml_module_str)

    module = load_module_spec(spec_file)

    assert module.module == "test_module"
    assert "complex_action" in module.actions

    action = module.actions["complex_action"]

    assert action.args is not None
    assert action.flags is not None

    assert "input" in action.args
    assert "count" in action.args
    assert "verbose" in action.flags

    assert len(action.command) > 0


@pytest.mark.parametrize(
    "error_case",
    [
        "syntax_error",
        "bad_indent",
        "non_mapping_root",
        "empty",
    ],
    ids=[
        "invalid_yaml_syntax",
        "bad_indentation",
        "non_mapping_root",
        "empty_yaml",
    ],
)
def test_load_module_spec_fails_on_invalid_yaml_variants(
    core_specs_dir: Path,
    make_invalid_yaml,
    error_case: str,
):
    """
    GIVEN malformed YAML variants generated by `make_invalid_yaml`
    WHEN load_module_spec is called
    THEN ActionSpecsParseError is raised for each variant
    """

    # Explicit mapping (prevents silent mismatch bugs)
    kwargs_map = {
        "syntax_error": {"syntax_error": True},
        "bad_indent": {"bad_indent": True},
        "non_mapping_root": {"non_mapping_root": True},
        "empty": {"empty": True},
    }

    spec_file = core_specs_dir / f"{error_case}.yml"
    write_yaml_str(spec_file, make_invalid_yaml(**kwargs_map[error_case]))

    with pytest.raises(ActionSpecsParseError):
        load_module_spec(spec_file)


# ============================================================================
# validate_yaml_file_safety
# ============================================================================


def test_validate_yaml_file_safety_rejects_large_file(
    core_specs_dir: Path,
    settings: Settings,
):
    """
    GIVEN a YAML file larger than star_max_yml_bytes
    WHEN validate_yaml_file_safety is called
    THEN ActionSpecsParseError is raised
    """
    oversized = core_specs_dir / "big.yml"
    oversized.write_text("a" * (settings.star_max_yml_bytes + 1), encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="exceeds maximum allowed size"):
        validate_yaml_file_safety(oversized, settings)


def test_validate_yaml_file_safety_rejects_disallowed_control_chars(
    core_specs_dir: Path,
    settings: Settings,
):
    """
    GIVEN a YAML file containing disallowed control characters
    WHEN validate_yaml_file_safety is called
    THEN ActionSpecsParseError is raised
    """
    bad = core_specs_dir / "control.yml"
    bad.write_text("version: 1\nmodule: control\x01\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="disallowed control characters"):
        validate_yaml_file_safety(bad, settings)


def test_validate_yaml_file_safety_rejects_disallowed_yaml_patterns(
    core_specs_dir: Path,
    settings: Settings,
):
    """
    GIVEN a YAML file containing a disallowed YAML tag pattern
    WHEN validate_yaml_file_safety is called
    THEN ActionSpecsParseError is raised
    """
    bad = core_specs_dir / "unsafe.yml"
    bad.write_text("value: !!python/object/new:os.system\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="disallowed YAML pattern"):
        validate_yaml_file_safety(bad, settings)


# ============================================================================
# load_module_specs
# ============================================================================


def test_load_module_specs_loads_from_core_and_user_dirs(
    core_specs_dir: Path,
    user_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN core and user specs directories with valid modules
    WHEN load_module_specs is called
    THEN both modules are present in deterministic order
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)
    monkeypatch.setattr(loader_module, "USER_SPECS_DIR", user_specs_dir)

    write_yaml(core_specs_dir / "coremod.yml", make_module_payload("coremod"))
    write_yaml(user_specs_dir / "usermod.yaml", make_module_payload("usermod"))

    modules = load_module_specs([core_specs_dir, user_specs_dir], settings)

    assert [module.module for module in modules] == ["coremod", "usermod"]
    assert all(isinstance(module, ModuleSpec) for module in modules)


def test_load_module_specs_allows_duplicate_module_names_in_different_namespaces(
    core_specs_dir: Path,
    user_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN two modules with the same base module name in different namespaces
    WHEN load_module_specs is called
    THEN both modules are loaded successfully with different runtime namespaces
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)
    monkeypatch.setattr(loader_module, "USER_SPECS_DIR", user_specs_dir)

    (core_specs_dir / "file").mkdir()
    (core_specs_dir / "security").mkdir()
    write_yaml(core_specs_dir / "file" / "crypto.yml", make_module_payload("crypto"))
    write_yaml(
        core_specs_dir / "security" / "crypto.yml",
        make_module_payload("crypto"),
    )

    modules = load_module_specs([core_specs_dir, user_specs_dir], settings)

    assert [module.module for module in modules] == ["crypto", "crypto"]
    assert [module.namespace for module in modules] == [("file",), ("security",)]


def test_load_module_specs_rejects_duplicate_effective_module_identity(
    core_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN two files producing the same namespace plus module identity
    WHEN load_module_specs is called
    THEN ActionSpecsParseError is raised
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)

    (core_specs_dir / "file").mkdir()
    write_yaml(core_specs_dir / "file" / "crypto.yml", make_module_payload("crypto"))
    write_yaml(core_specs_dir / "file" / "crypto.yaml", make_module_payload("crypto"))

    with pytest.raises(ActionSpecsParseError, match="Duplicate fully qualified module"):
        load_module_specs([core_specs_dir], settings)


def test_load_module_specs_rejects_filename_module_mismatch(
    core_specs_dir: Path,
    make_module_payload,
    settings: Settings,
):
    """
    GIVEN a file whose stem does not match `module` field
    WHEN load_module_specs is called
    THEN ActionSpecsParseError is raised
    """
    write_yaml(core_specs_dir / "crypto.yml", make_module_payload("hashing"))

    with pytest.raises(ActionSpecsParseError, match="Module name mismatch"):
        load_module_specs([core_specs_dir], settings)


def test_load_module_specs_rejects_invalid_extension(
    core_specs_dir: Path,
    settings: Settings,
):
    """
    GIVEN a `.txt` file in specs directory
    WHEN load_module_specs is called
    THEN ActionSpecsParseError is raised
    """
    (core_specs_dir / "invalid.txt").write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(ActionSpecsParseError, match="Invalid STAR DSL spec extension"):
        load_module_specs([core_specs_dir], settings)


def test_load_module_specs_loads_nested_module(
    core_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN a module stored under nested namespace directories
    WHEN load_module_specs is called
    THEN the module is loaded successfully
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)

    (core_specs_dir / "file" / "crypto").mkdir(parents=True)
    write_yaml(
        core_specs_dir / "file" / "crypto" / "hash.yml",
        make_module_payload("hash"),
    )

    modules = load_module_specs([core_specs_dir], settings)

    assert len(modules) == 1
    assert modules[0].module == "hash"
    assert modules[0].namespace == ("file", "crypto")


def test_load_module_specs_attaches_core_namespace_from_relative_dirs(
    core_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN a core module inside nested namespace directories
    WHEN load_module_specs is called
    THEN the loaded ModuleSpec exposes the expected runtime namespace
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)

    (core_specs_dir / "file").mkdir()
    write_yaml(core_specs_dir / "file" / "crypto.yml", make_module_payload("crypto"))

    module = load_module_specs([core_specs_dir], settings)[0]

    assert module.namespace == ("file",)
    assert module.source == "core"


def test_load_module_specs_attaches_user_namespace_prefix(
    core_specs_dir: Path,
    user_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN a user module inside nested namespace directories
    WHEN load_module_specs is called
    THEN the loaded ModuleSpec namespace starts with user
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)
    monkeypatch.setattr(loader_module, "USER_SPECS_DIR", user_specs_dir)

    (user_specs_dir / "custom").mkdir()
    write_yaml(
        user_specs_dir / "custom" / "my_module.yml",
        make_module_payload("my_module"),
    )

    module = load_module_specs([user_specs_dir], settings)[0]

    assert module.namespace == ("user", "custom")
    assert module.source == "user"


def test_load_module_specs_uses_empty_namespace_for_root_core_module(
    core_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN a root-level core module
    WHEN load_module_specs is called
    THEN module namespace is empty
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)

    write_yaml(core_specs_dir / "checksum.yml", make_module_payload("checksum"))

    module = load_module_specs([core_specs_dir], settings)[0]

    assert module.namespace == ()


def test_load_module_specs_uses_user_namespace_for_root_user_module(
    core_specs_dir: Path,
    user_specs_dir: Path,
    make_module_payload,
    settings: Settings,
    monkeypatch,
):
    """
    GIVEN a root-level user module
    WHEN load_module_specs is called
    THEN module namespace starts with user
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)
    monkeypatch.setattr(loader_module, "USER_SPECS_DIR", user_specs_dir)

    write_yaml(user_specs_dir / "checksum.yml", make_module_payload("checksum"))

    module = load_module_specs([user_specs_dir], settings)[0]

    assert module.namespace == ("user",)


def test_mask_path_preserves_nested_relative_path(
    core_specs_dir: Path,
    user_specs_dir: Path,
    monkeypatch,
):
    """
    GIVEN nested core and user module paths
    WHEN _mask_path is called
    THEN the base directory is masked and relative subpath is preserved
    """
    monkeypatch.setattr(loader_module, "CORE_SPECS_DIR", core_specs_dir)
    monkeypatch.setattr(loader_module, "USER_SPECS_DIR", user_specs_dir)

    core_path = core_specs_dir / "file" / "crypto.yml"
    user_path = user_specs_dir / "custom" / "my_module.yml"

    assert _mask_path(core_path) == "CORE/file/crypto.yml"
    assert _mask_path(user_path) == "USER/custom/my_module.yml"


def test_load_module_specs_fails_fast_on_first_invalid_file(
    core_specs_dir: Path,
    make_module_payload,
    monkeypatch,
    settings: Settings,
):
    """
    GIVEN discovered files where the first one is invalid
    WHEN load_module_specs is called
    THEN ActionSpecsParseError is raised and loading stops immediately
    """
    invalid_file = core_specs_dir / "a_invalid.yml"
    invalid_file.write_text("module: [broken\n", encoding="utf-8")
    write_yaml(core_specs_dir / "b_valid.yml", make_module_payload("b_valid"))

    real_load_module_spec = loader_module.load_module_spec
    calls: list[str] = []

    def _tracking_load(path: Path) -> ModuleSpec:
        """Record load order and delegate to the real loader."""
        calls.append(path.name)
        return real_load_module_spec(path)

    monkeypatch.setattr(loader_module, "load_module_spec", _tracking_load)

    with pytest.raises(ActionSpecsParseError):
        load_module_specs([core_specs_dir], settings)

    assert calls == ["a_invalid.yml"]
