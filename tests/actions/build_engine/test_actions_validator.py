"""Unit tests for the STAR DSL semantic validator.

These tests freeze validator-layer invariants:
- semantic validation is strict, deterministic, and fail-fast
- module-level and action-level rules are enforced with clear errors
- loader-owned structural parsing is out of scope
"""

from __future__ import annotations

from typing import cast

import pytest

from star.actions.build_engine.validator import validate_modules
from star.actions.exceptions import ActionSpecsParseError
from star.actions.schemas.dsl import ArgCmd, BinaryCmd, FlagCmd, OutputCmd

# ============================================================================
# Validate Modules Happy Path
# ============================================================================


def test_validate_modules_accepts_valid_module(make_valid_module):
    """
    GIVEN a semantically valid DSL module
    WHEN validate_modules is called
    THEN validation succeeds without raising an exception
    """
    module = make_valid_module()

    validate_modules([module])


# ============================================================================
# Module-Level Validation
# ============================================================================


@pytest.mark.parametrize(
    "version",
    [0, 2, 999],
    ids=["zero", "future", "invalid_large"],
)
def test_validate_modules_rejects_unsupported_version(
    make_module_payload,
    make_module_spec,
    version: int,
):
    """
    GIVEN a module with an unsupported DSL version
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(version=version))

    with pytest.raises(ActionSpecsParseError, match="unsupported DSL version"):
        validate_modules([module])


def test_validate_modules_rejects_empty_binaries(make_module_payload, make_module_spec):
    """
    GIVEN a module with no declared binaries
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=[]))

    with pytest.raises(ActionSpecsParseError, match="must declare at least one binary"):
        validate_modules([module])


def test_validate_modules_rejects_duplicate_binary_names(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module that declares the same binary twice
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=["echo", "echo"]))

    with pytest.raises(ActionSpecsParseError, match="duplicate binary 'echo'"):
        validate_modules([module])


def test_validate_modules_rejects_duplicate_root_module_identities(make_valid_module):
    """
    GIVEN two modules with the same module name and no runtime namespace
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    first = make_valid_module()
    second = make_valid_module()

    with pytest.raises(ActionSpecsParseError, match="duplicate fully qualified module"):
        validate_modules([first, second])


def test_validate_modules_allows_duplicate_module_names_in_different_namespaces(
    make_valid_module,
):
    """
    GIVEN two modules with the same base module name but different runtime namespaces
    WHEN validate_modules is called
    THEN validation succeeds
    """
    first = make_valid_module().with_runtime_namespace(("file",), "core")
    second = make_valid_module().with_runtime_namespace(("security",), "core")

    validate_modules([first, second])


def test_validate_modules_rejects_module_without_actions(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with an empty action mapping
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(actions={}))

    with pytest.raises(ActionSpecsParseError, match="must define at least one action"):
        validate_modules([module])


@pytest.mark.parametrize(
    "name",
    ["", "Invalid", "123abc", "bad-name", "_hidden", "bad name"],
    ids=[
        "empty",
        "uppercase",
        "starts_with_digit",
        "hyphen",
        "leading_underscore",
        "space",
    ],
)
def test_validate_modules_rejects_invalid_module_names(
    make_module_payload,
    make_module_spec,
    name: str,
):
    """
    GIVEN a module whose name violates the DSL naming regex
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(module_name=name))

    with pytest.raises(ActionSpecsParseError, match="invalid module name"):
        validate_modules([module])


@pytest.mark.parametrize(
    "binary_name",
    ["", "Invalid", "123abc", "bad-name", "_hidden", "bad name"],
    ids=[
        "empty",
        "uppercase",
        "starts_with_digit",
        "hyphen",
        "leading_underscore",
        "space",
    ],
)
def test_validate_modules_rejects_invalid_binary_names(
    make_module_payload,
    make_module_spec,
    binary_name: str,
):
    """
    GIVEN a module whose binary name violates the DSL naming regex
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=[binary_name]))

    with pytest.raises(ActionSpecsParseError, match="invalid binary name"):
        validate_modules([module])


def test_validate_modules_rejects_blocked_binary(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with a default-blocked binary in binaries
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=["bash"]))

    with pytest.raises(ActionSpecsParseError, match="blocked by STAR default policy"):
        validate_modules([module])


def test_validate_modules_rejects_binary_with_forward_slash(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with a forward-slash binary name
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=["bin/echo"]))

    with pytest.raises(ActionSpecsParseError, match="invalid binary name"):
        validate_modules([module])


def test_validate_modules_rejects_binary_with_backslash(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with a backslash binary name
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(make_module_payload(binaries=[r"bin\\echo"]))

    with pytest.raises(ActionSpecsParseError, match="invalid binary name"):
        validate_modules([module])


@pytest.mark.parametrize(
    "tags,error_message",
    [
        ([], "tags must be a non-empty list of strings"),
        (["   "], "tags must not contain blank entries"),
        (["alpha", ""], "tags must not contain blank entries"),
    ],
    ids=["empty_list", "blank_string", "empty_string"],
)
def test_validate_modules_rejects_invalid_module_tags_list(
    make_module_payload,
    make_module_spec,
    tags: list[str],
    error_message: str,
):
    """
    GIVEN a module with invalid tags metadata
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    payload = make_module_payload()
    payload["tags"] = tags
    module = make_module_spec(payload)

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


@pytest.mark.parametrize(
    "tags",
    [
        ["crypto"],
        ["file_id"],
        ["aes-256"],
        ["text-processing", "safe_exec"],
    ],
    ids=["simple", "underscore", "hyphen", "mixed_valid_tokens"],
)
def test_validate_modules_accepts_valid_module_tag_names(
    make_module_payload,
    make_module_spec,
    tags: list[str],
):
    """
    GIVEN a module with valid tag names
    WHEN validate_modules is called
    THEN validation succeeds
    """
    payload = make_module_payload()
    payload["tags"] = tags
    module = make_module_spec(payload)

    validate_modules([module])


@pytest.mark.parametrize(
    "tags",
    [
        ["Crypto"],
        ["123crypto"],
        ["bad tag"],
        ["_bad"],
        ["bad.tag"],
        ["bad/tag"],
    ],
    ids=[
        "uppercase",
        "starts_with_digit",
        "space",
        "leading_underscore",
        "dot",
        "slash",
    ],
)
def test_validate_modules_rejects_invalid_module_tag_names(
    make_module_payload,
    make_module_spec,
    tags: list[str],
):
    """
    GIVEN a module with invalid tag names
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    payload = make_module_payload()
    payload["tags"] = tags
    module = make_module_spec(payload)

    with pytest.raises(ActionSpecsParseError, match="invalid tag name"):
        validate_modules([module])


def test_validate_modules_rejects_module_tags_with_non_string_item(make_valid_module):
    """
    GIVEN a parsed module with a non-string value inside tags
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.tags = cast(list[str], ["alpha", 123])

    with pytest.raises(
        ActionSpecsParseError,
        match="tags must be a non-empty list of strings",
    ):
        validate_modules([module])


# ============================================================================
# Action-Level Validation
# ============================================================================


@pytest.mark.parametrize(
    "name",
    ["", "Invalid", "123abc", "bad-name", "_hidden", "bad name"],
    ids=[
        "empty",
        "uppercase",
        "starts_with_digit",
        "hyphen",
        "leading_underscore",
        "space",
    ],
)
def test_validate_modules_rejects_invalid_action_names(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    name: str,
):
    """
    GIVEN an action whose name violates the DSL naming regex
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_module_spec(
        make_module_payload(actions={name: make_action_payload()})
    )

    with pytest.raises(ActionSpecsParseError, match="invalid action name"):
        validate_modules([module])


# ============================================================================
# Action Tags
# ============================================================================


def test_validate_modules_accepts_valid_action_tags(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action with valid tags metadata
    WHEN validate_modules is called
    THEN validation succeeds
    """
    action = make_action_payload(tags=["aes-256", "encryption", "file_id"])
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


@pytest.mark.parametrize(
    "tags,error_message",
    [
        ([], "tags must be a non-empty list of strings"),
        (["   "], "tags must not contain blank entries"),
        (["alpha", ""], "tags must not contain blank entries"),
    ],
    ids=["empty_list", "blank_string", "empty_string"],
)
def test_validate_modules_rejects_invalid_action_tags_list(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    tags: list[str],
    error_message: str,
):
    """
    GIVEN an action with invalid tags metadata
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(tags=tags)
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


@pytest.mark.parametrize(
    "tags",
    [
        ["Crypto"],
        ["123crypto"],
        ["bad tag"],
        ["_bad"],
        ["bad.tag"],
        ["bad/tag"],
    ],
    ids=[
        "uppercase",
        "starts_with_digit",
        "space",
        "leading_underscore",
        "dot",
        "slash",
    ],
)
def test_validate_modules_rejects_invalid_action_tag_names(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    tags: list[str],
):
    """
    GIVEN an action with invalid tag names
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(tags=tags)
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="invalid tag name"):
        validate_modules([module])


def test_validate_modules_rejects_action_tags_with_non_string_item(make_valid_module):
    """
    GIVEN a parsed action with a non-string value inside tags
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].tags = cast(list[str], ["alpha", 123])

    with pytest.raises(
        ActionSpecsParseError,
        match="tags must be a non-empty list of strings",
    ):
        validate_modules([module])


def test_validate_modules_rejects_empty_command(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action with an empty command list
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(command=[])
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="command must not be empty"):
        validate_modules([module])


@pytest.mark.parametrize(
    ("command", "error_message"),
    [
        ([{"arg": "x"}], "exactly one binary token"),
        ([{"binary": "echo"}, {"binary": "cat"}], "exactly one binary token"),
        (["test", {"binary": "echo"}], "binary must be first command element"),
    ],
    ids=["missing_binary", "multiple_binary", "binary_not_first"],
)
def test_validate_modules_rejects_invalid_binary_rules(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    command,
    error_message: str,
):
    """
    GIVEN an action whose command violates binary token rules
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(command=command)
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


def test_validate_modules_rejects_binary_not_declared_in_module(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action that references a binary not declared by the module
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(command=[{"binary": "cat"}])
    module = make_module_spec(
        make_module_payload(binaries=["echo", "printf"], actions={"ping": action})
    )

    with pytest.raises(
        ActionSpecsParseError,
        match="is not declared in module binaries",
    ):
        validate_modules([module])


# ============================================================================
# Outputs Validation
# ============================================================================


def test_validate_modules_accepts_file_command_output_reference(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action with `file + command` output declaration
    WHEN command references the output exactly once
    THEN validation succeeds
    """
    action = make_action_payload(
        outputs={
            "out_file": {
                "type": "file",
                "source": "command",
                "description": "Command output file",
            }
        },
        command=[{"binary": "echo"}, {"output": "out_file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


def test_validate_modules_rejects_missing_file_command_output_reference(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action declaring `file + command` output
    WHEN command does not reference that output
    THEN validation fails deterministically
    """
    action = make_action_payload(
        outputs={
            "out_file": {
                "type": "file",
                "source": "command",
                "description": "Command output file",
            }
        },
        command=[{"binary": "echo"}, "ok"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="must be referenced exactly once"):
        validate_modules([module])


def test_validate_modules_rejects_reserved_output_name(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a DSL action declaring an output named stdout_file
    WHEN modules are semantically validated
    THEN validation fails because stdout_file is a reserved output name
    """

    action = make_action_payload(
        outputs={
            "stdout_file": {
                "type": "file",
                "source": "command",
                "description": "Reserved output name",
            }
        },
        command=[{"binary": "echo"}, {"output": "stdout_file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError, match="stdout_file.*reserved|reserved.*stdout_file"
    ):
        validate_modules([module])


def test_validate_modules_rejects_stderr_output_source(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an output declaration using stderr source
    WHEN semantic validation runs
    THEN validation rejects the unsupported type/source combination
    """
    action = make_action_payload(
        outputs={
            "err_file": {
                "type": "file",
                "source": "stderr",
                "description": "Unsupported stderr file output",
            }
        },
        command=[{"binary": "echo"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="unsupported type/source"):
        validate_modules([module])


def test_validator__rejects_unknown_output_type(make_valid_module):
    """
    GIVEN an output definition with unknown output type
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """

    class _UnknownOutput:
        """Lightweight output-like object for validator negative tests."""

        type = "unknown"
        source = "stdout"

    module = make_valid_module()
    module.actions["ping"].outputs = {"bad_output": _UnknownOutput()}

    with pytest.raises(ActionSpecsParseError, match="unsupported type/source"):
        validate_modules([module])


def test_validator__rejects_unknown_output_source(make_valid_module):
    """
    GIVEN an output definition with unknown output source
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """

    class _UnknownOutput:
        """Lightweight output-like object for validator negative tests."""

        type = "file"
        source = "unknown"

    module = make_valid_module()
    module.actions["ping"].outputs = {"bad_output": _UnknownOutput()}

    with pytest.raises(ActionSpecsParseError, match="unsupported type/source"):
        validate_modules([module])


@pytest.mark.parametrize(
    "output_type,output_source",
    [
        ("file", "stderr"),
        ("data", "command"),
        ("data", "stdout"),
    ],
    ids=["file_stderr", "data_command", "data_stdout"],
)
def test_validator__rejects_invalid_type_source_combination(
    make_valid_module,
    output_type: str,
    output_source: str,
):
    """
    GIVEN an output definition with invalid type/source combination
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """

    class _InvalidOutput:
        """Lightweight output-like object for validator negative tests."""

        def __init__(self, output_type: str, output_source: str):
            """Initialize output type/source pair for testing.

            Args:
                output_type: Output type value.
                output_source: Output source value.
            """

            self.type = output_type
            self.source = output_source

    module = make_valid_module()
    module.actions["ping"].outputs = {
        "bad_output": _InvalidOutput(output_type, output_source)
    }

    with pytest.raises(ActionSpecsParseError, match="unsupported type/source"):
        validate_modules([module])


def test_validator__file_command_requires_reference(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action with file+command output declaration
    WHEN command does not reference the output
    THEN ActionSpecsParseError is raised
    """

    action = make_action_payload(
        outputs={
            "cmd_out": {
                "type": "file",
                "source": "command",
                "description": "Command output placeholder",
            }
        },
        command=[{"binary": "echo"}, "ok"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="must be referenced exactly once"):
        validate_modules([module])


def test_validator__file_command_rejects_multiple_references(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action with file+command output declaration
    WHEN command references the same output multiple times
    THEN ActionSpecsParseError is raised
    """

    action = make_action_payload(
        outputs={
            "cmd_out": {
                "type": "file",
                "source": "command",
                "description": "Command output placeholder",
            }
        },
        command=[
            {"binary": "echo"},
            {"output": "cmd_out"},
            {"output": "cmd_out"},
        ],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="must be referenced exactly once"):
        validate_modules([module])


def test_validate_modules_rejects_file_stdout_output_combination(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a DSL action declaring a file output sourced from stdout
    WHEN modules are semantically validated
    THEN validation fails because file+stdout outputs are no longer supported
    """

    action = make_action_payload(
        outputs={
            "stdout_text": {
                "type": "file",
                "source": "stdout",
                "description": "Stdout materialized file",
            }
        },
        command=[{"binary": "echo"}, "ok"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="unsupported type/source"):
        validate_modules([module])


def test_validator__rejects_undeclared_output_reference(make_valid_module):
    """
    GIVEN a command containing an output token
    WHEN referenced output is not declared
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [
        BinaryCmd(binary="echo"),
        OutputCmd(output="missing_output"),
    ]

    with pytest.raises(
        ActionSpecsParseError,
        match="output 'missing_output' referenced in command but not defined",
    ):
        validate_modules([module])


# ============================================================================
# Command Elements
# ============================================================================


def test_validate_modules_rejects_unsupported_command_element_type(make_valid_module):
    """
    GIVEN a command containing an unsupported element type
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [BinaryCmd(binary="echo"), 123]

    with pytest.raises(ActionSpecsParseError, match="unsupported element type"):
        validate_modules([module])


@pytest.mark.parametrize(
    ("literal", "error_message"),
    [
        ("", "must not be empty"),
        ("   ", "must not be whitespace-only"),
        ("\x00", "must not contain NULL bytes"),
        ("bad\u0007token", "must not contain control characters"),
    ],
    ids=["empty_literal", "whitespace_only", "null_byte", "control_char"],
)
def test_validate_modules_rejects_invalid_command_literals(
    make_valid_module,
    literal: str,
    error_message: str,
):
    """
    GIVEN a command containing an invalid literal token
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [BinaryCmd(binary="echo"), literal]

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


@pytest.mark.parametrize(
    "literal",
    [
        "/etc/passwd",
        r"C:\Windows\System32",
        "C:/Windows/System32",
        r"\\server\share",
        r"relative\path",
        "../secrets.txt",
        "safe/../secrets.txt",
    ],
    ids=[
        "posix_absolute",
        "windows_drive_backslash",
        "windows_drive_slash",
        "unc_path",
        "backslash_path",
        "parent_traversal",
        "embedded_parent_traversal",
    ],
)
def test_validate_modules_rejects_command_literal_host_paths(
    make_valid_module,
    literal: str,
):
    """
    GIVEN a command containing a host-path-like literal token
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised at startup
    """
    module = make_valid_module()
    module.actions["ping"].command = [BinaryCmd(binary="echo"), literal]

    with pytest.raises(ActionSpecsParseError, match="host paths"):
        validate_modules([module])


def test_validate_modules_allows_core_uuid_command_literal_exception(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN the reviewed core UUID action using the kernel UUID path
    WHEN validate_modules is called
    THEN the command literal exception is accepted
    """
    action = make_action_payload(
        command=[{"binary": "cat"}, "/proc/sys/kernel/random/uuid"],
    )
    module = make_module_spec(
        make_module_payload(
            module_name="random",
            binaries=["cat"],
            actions={"gen_uuid": action},
        )
    ).with_runtime_namespace((), "core")

    validate_modules([module])


@pytest.mark.parametrize(
    ("source", "action_name"),
    [
        ("user", "gen_uuid"),
        ("core", "read_uuid"),
    ],
    ids=["user_source", "wrong_action"],
)
def test_validate_modules_rejects_unreviewed_uuid_path_literal(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    source: str,
    action_name: str,
):
    """
    GIVEN the kernel UUID path outside the exact core allowlist entry
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised at startup
    """
    action = make_action_payload(
        command=[{"binary": "cat"}, "/proc/sys/kernel/random/uuid"],
    )
    module = make_module_spec(
        make_module_payload(
            module_name="random",
            binaries=["cat"],
            actions={action_name: action},
        )
    ).with_runtime_namespace((), source)

    with pytest.raises(ActionSpecsParseError, match="host paths"):
        validate_modules([module])


def test_validate_modules_accepts_command_literal_placeholders_and_marks_args_used(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN args referenced only inside a const literal template
    WHEN validate_modules is called
    THEN semantic validation succeeds without unused-arg errors
    """
    action = make_action_payload(
        args={
            "user": {
                "type": "string",
                "required": True,
                "description": "user name",
            },
            "count": {
                "type": "int",
                "required": True,
                "description": "retry count",
            },
        },
        command=[{"binary": "echo"}, "user_{user}_count_{count}"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


@pytest.mark.parametrize(
    "literal",
    [
        "{}",
        "{arg:-default}",
        "{exec(cmd)}",
        "{arg1_{arg2}}",
        "{missing",
        "missing}",
        "${user}",
    ],
    ids=[
        "empty_placeholder",
        "modifier_syntax",
        "expression_syntax",
        "nested_placeholder",
        "unbalanced_opening",
        "unbalanced_closing",
        "dollar_brace_syntax",
    ],
)
def test_validate_modules_rejects_invalid_command_literal_placeholder_syntax(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    literal: str,
):
    """
    GIVEN a const literal with invalid placeholder syntax
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised at startup
    """
    action = make_action_payload(
        args={
            "user": {
                "type": "string",
                "required": True,
                "description": "user name",
            }
        },
        command=[{"binary": "echo"}, literal],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="invalid placeholder syntax",
    ):
        validate_modules([module])


def test_validate_modules_rejects_command_literal_placeholder_with_undefined_arg(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a const literal placeholder referencing an undefined arg
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "user": {
                "type": "string",
                "required": True,
                "description": "user name",
            }
        },
        command=[{"binary": "echo"}, "hello_{missing}"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="references undefined arg"):
        validate_modules([module])


@pytest.mark.parametrize(
    "arg_spec",
    [
        {
            "type": "bool",
            "required": True,
            "description": "bool arg",
        },
        {
            "type": "file_id",
            "required": True,
            "description": "file arg",
        },
        {
            "type": "list",
            "items": "string",
            "required": True,
            "description": "list arg",
        },
    ],
    ids=["bool", "file_id", "list"],
)
def test_validate_modules_rejects_command_literal_placeholder_with_unsupported_arg_type(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    arg_spec,
):
    """
    GIVEN a const literal placeholder pointing to unsupported arg type
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={"value": arg_spec},
        command=[{"binary": "echo"}, "{value}"],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="unsupported type"):
        validate_modules([module])


# ============================================================================
# References
# ============================================================================


def test_validate_modules_rejects_undefined_arg_reference(make_valid_module):
    """
    GIVEN a command that references an undefined argument
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [BinaryCmd(binary="echo"), ArgCmd(arg="value")]

    with pytest.raises(
        ActionSpecsParseError,
        match="arg 'value' referenced in command but not defined",
    ):
        validate_modules([module])


def test_validate_modules_rejects_undefined_flag_reference(make_valid_module):
    """
    GIVEN a command that references an undefined flag
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [BinaryCmd(binary="echo"), FlagCmd(flag="verbose")]

    with pytest.raises(
        ActionSpecsParseError,
        match="flag 'verbose' referenced in command but not defined",
    ):
        validate_modules([module])


# ============================================================================
# Unused Definitions
# ============================================================================


def test_validate_modules_rejects_unused_arg(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action that declares an argument but never uses it in command
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "value": {
                "type": "string",
                "required": True,
                "description": "input value",
            }
        },
        command=[{"binary": "echo"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="arg 'value' is defined but not used",
    ):
        validate_modules([module])


def test_validate_modules_rejects_unused_flag(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action that declares a flag but never uses it in command
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        flags={
            "verbose": {
                "value": "-v",
                "default": False,
                "description": "verbose flag",
            }
        },
        command=[{"binary": "echo"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="flag 'verbose' is defined but not used",
    ):
        validate_modules([module])


# ============================================================================
# Naming Rules
# ============================================================================


@pytest.mark.parametrize(
    "arg_name",
    ["", "Invalid", "123abc", "bad-name", "_hidden", "bad name"],
    ids=[
        "empty",
        "uppercase",
        "starts_with_digit",
        "hyphen",
        "leading_underscore",
        "space",
    ],
)
def test_validate_modules_rejects_invalid_arg_names(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    arg_name: str,
):
    """
    GIVEN an action whose argument name violates the DSL naming regex
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            arg_name: {
                "type": "string",
                "required": True,
                "description": "input value",
            }
        },
        command=[{"binary": "echo"}, {"arg": arg_name}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="invalid arg name"):
        validate_modules([module])


@pytest.mark.parametrize(
    "flag_name",
    ["", "Invalid", "123abc", "bad-name", "_hidden", "bad name"],
    ids=[
        "empty",
        "uppercase",
        "starts_with_digit",
        "hyphen",
        "leading_underscore",
        "space",
    ],
)
def test_validate_modules_rejects_invalid_flag_names(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    flag_name: str,
):
    """
    GIVEN an action whose flag name violates the DSL naming regex
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        flags={
            flag_name: {
                "value": "-v",
                "default": False,
                "description": "verbose flag",
            }
        },
        command=[{"binary": "echo"}, {"flag": flag_name}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="invalid flag name"):
        validate_modules([module])


def test_validate_modules_rejects_arg_flag_name_collisions(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an action that reuses the same name for an arg and a flag
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "dup": {
                "type": "string",
                "required": True,
                "description": "duplicate arg",
            }
        },
        flags={
            "dup": {
                "value": "-d",
                "default": False,
                "description": "duplicate flag",
            }
        },
        command=[{"binary": "echo"}, {"arg": "dup"}, {"flag": "dup"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="name collision between arg and flag 'dup'",
    ):
        validate_modules([module])


# ============================================================================
# Argument Validation
# ============================================================================


def test_validate_modules_rejects_required_arg_with_default(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a required arg that also defines a default
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": True,
                "default": 1,
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="cannot be required and define a default",
    ):
        validate_modules([module])


def test_validate_modules_rejects_optional_arg_without_default(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an optional arg that omits its default
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": False,
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="must define a default when not required",
    ):
        validate_modules([module])


def test_validate_modules_rejects_list_without_items(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a list arg without items
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "files": {
                "type": "list",
                "required": True,
                "description": "files",
            }
        },
        command=[{"binary": "echo"}, {"arg": "files"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="must define 'items'"):
        validate_modules([module])


def test_validate_modules_rejects_items_on_non_list(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a non-list arg with items defined
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "value": {
                "type": "string",
                "required": True,
                "items": "string",
                "description": "value",
            }
        },
        command=[{"binary": "echo"}, {"arg": "value"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="cannot define 'items'"):
        validate_modules([module])


def test_validate_modules_rejects_invalid_items_type(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a list arg with unsupported items type
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "values": {
                "type": "list",
                "items": "int",
                "required": True,
                "description": "values",
            }
        },
        command=[{"binary": "echo"}, {"arg": "values"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="must be 'string' or 'file_id'"):
        validate_modules([module])


@pytest.mark.parametrize(
    ("arg_type", "default"),
    [("string", 123), ("bool", "true"), ("float", "3.14")],
    ids=["string_from_int", "bool_from_string", "float_from_string"],
)
def test_validate_modules_rejects_incompatible_default_types(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    arg_type: str,
    default,
):
    """
    GIVEN an arg whose default is incompatible with the declared DSL type
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "value": {
                "type": arg_type,
                "required": False,
                "default": default,
                "description": "value",
            }
        },
        command=[{"binary": "echo"}, {"arg": "value"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="default for arg 'value' is incompatible",
    ):
        validate_modules([module])


def test_validate_modules_rejects_non_integer_float_default_for_int(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an int arg with a non-integer float default
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": False,
                "default": 1.5,
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="default for arg 'count' is incompatible",
    ):
        validate_modules([module])


# ============================================================================
# File ID Validation
# ============================================================================


def test_validate_modules_rejects_invalid_file_id_default(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a file_id arg with an invalid UUID4 default
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "file": {
                "type": "file_id",
                "required": False,
                "default": "not-a-uuid4",
                "description": "file id",
            }
        },
        command=[{"binary": "echo"}, {"arg": "file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="default for arg 'file' is incompatible",
    ):
        validate_modules([module])


# ============================================================================
# Constraints Happy Paths
# ============================================================================


def test_validate_modules_accepts_valid_numeric_constraints(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a numeric arg with valid min/max constraints block
    WHEN validate_modules is called
    THEN validation succeeds
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": False,
                "default": 2,
                "constraints": {"min": 1, "max": 5},
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


def test_validate_modules_accepts_valid_string_constraints(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a string arg with valid constraints block
    WHEN validate_modules is called
    THEN validation succeeds
    """
    action = make_action_payload(
        args={
            "value": {
                "type": "string",
                "required": False,
                "default": "alpha",
                "constraints": {
                    "min_length": 1,
                    "max_length": 10,
                    "allowed_values": ["alpha", "beta"],
                },
                "description": "value",
            }
        },
        command=[{"binary": "echo"}, {"arg": "value"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


def test_validate_modules_accepts_valid_file_constraints(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a file_id arg with valid constraints block
    WHEN validate_modules is called
    THEN validation succeeds
    """
    action = make_action_payload(
        args={
            "file": {
                "type": "file_id",
                "required": True,
                "constraints": {
                    "max_size": 1024,
                    "allowed_extensions": ["txt", "csv"],
                    "allowed_mime_types": ["text/plain", "text/csv"],
                },
                "description": "file",
            }
        },
        command=[{"binary": "echo"}, {"arg": "file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    validate_modules([module])


# ============================================================================
# Constraints Rejection Cases
# ============================================================================


def test_validate_modules_rejects_unknown_constraint_key(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN an arg constraints block with an unsupported key
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": False,
                "default": 2,
                "constraints": {"max_length": 4},
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="unsupported constraint key"):
        validate_modules([module])


def test_validate_modules_rejects_numeric_min_greater_than_max(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a numeric arg whose constraints min is greater than max
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "count": {
                "type": "int",
                "required": False,
                "default": 5,
                "constraints": {"min": 10, "max": 1},
                "description": "count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="has min greater than max"):
        validate_modules([module])


@pytest.mark.parametrize(
    ("constraints", "error_message"),
    [
        ({"min": 1.5}, "min must be an integer value"),
        ({"max": 2.5}, "max must be an integer value"),
    ],
    ids=["float_min_for_int", "float_max_for_int"],
)
def test_validate_modules_rejects_non_integer_min_max_for_int(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    constraints,
    error_message: str,
):
    """
    GIVEN an int arg with non-integer numeric bounds
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    arg_payload = {
        "type": "int",
        "required": False,
        "default": 5,
        "description": "count",
        "constraints": constraints,
    }

    action = make_action_payload(
        args={"count": arg_payload},
        command=[{"binary": "echo"}, {"arg": "count"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


@pytest.mark.parametrize(
    "max_size",
    [0, -1],
    ids=["zero", "negative"],
)
def test_validate_modules_rejects_non_positive_max_size(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    max_size: int,
):
    """
    GIVEN a file_id arg with a non-positive max_size
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "file": {
                "type": "file_id",
                "required": True,
                "constraints": {"max_size": max_size},
                "description": "file id",
            }
        },
        command=[{"binary": "echo"}, {"arg": "file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="max_size must be greater than 0"):
        validate_modules([module])


@pytest.mark.parametrize(
    "constraints,error_message",
    [
        ({"allowed_values": []}, "allowed_values must be a non-empty list of strings"),
        ({"allowed_values": ["a", "a"]}, "allowed_values must be unique"),
    ],
    ids=["empty_allowed_values", "duplicate_allowed_values"],
)
def test_validate_modules_rejects_invalid_string_allowed_values(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    constraints,
    error_message: str,
):
    """
    GIVEN a string arg with invalid allowed_values constraint
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "value": {
                "type": "string",
                "required": False,
                "default": "a",
                "constraints": constraints,
                "description": "value",
            }
        },
        command=[{"binary": "echo"}, {"arg": "value"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


def test_validate_modules_rejects_invalid_file_allowed_mime_types(
    make_module_payload,
    make_action_payload,
    make_module_spec,
):
    """
    GIVEN a file_id arg with invalid mime-like constraints
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "file": {
                "type": "file_id",
                "required": True,
                "constraints": {"allowed_mime_types": ["plain"]},
                "description": "file",
            }
        },
        command=[{"binary": "echo"}, {"arg": "file"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(
        ActionSpecsParseError,
        match="allowed_mime_types must contain valid mime-like strings",
    ):
        validate_modules([module])


@pytest.mark.parametrize(
    "constraints,error_message",
    [
        ({"min_items": -1}, "min_items must be >= 0"),
        ({"min_items": 5, "max_items": 2}, "max_items must be >= min_items"),
        ({"max_items": -1}, "max_items must be > 0"),
    ],
    ids=["negative_min_items", "max_items_below_min_items", "negative_max_items"],
)
def test_validate_modules_rejects_invalid_list_constraints(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    constraints,
    error_message: str,
):
    """
    GIVEN a list arg with invalid min_items/max_items constraints
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        args={
            "items": {
                "type": "list",
                "items": "string",
                "required": True,
                "constraints": constraints,
                "description": "items",
            }
        },
        command=[{"binary": "echo"}, {"arg": "items"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


# ============================================================================
# Flags
# ============================================================================


@pytest.mark.parametrize(
    ("flag_value", "error_message"),
    [
        ("", "must not be empty"),
        ("   ", "must not be whitespace-only"),
        ("\x00", "must not contain NULL bytes"),
        ("bad\u0007flag", "must not contain control characters"),
    ],
    ids=["empty", "whitespace_only", "null_byte", "control_char"],
)
def test_validate_modules_rejects_invalid_flag_values(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    flag_value: str,
    error_message: str,
):
    """
    GIVEN a flag whose literal value is invalid
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised
    """
    action = make_action_payload(
        flags={
            "verbose": {
                "value": flag_value,
                "default": False,
                "description": "verbose flag",
            }
        },
        command=[{"binary": "echo"}, {"flag": "verbose"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match=error_message):
        validate_modules([module])


@pytest.mark.parametrize(
    "flag_value",
    [
        "/etc/passwd",
        r"C:\Windows\System32",
        "C:/Windows/System32",
        r"\\server\share",
        r"relative\path",
        "../secrets.txt",
        "safe/../secrets.txt",
    ],
    ids=[
        "posix_absolute",
        "windows_drive_backslash",
        "windows_drive_slash",
        "unc_path",
        "backslash_path",
        "parent_traversal",
        "embedded_parent_traversal",
    ],
)
def test_validate_modules_rejects_flag_value_host_paths(
    make_module_payload,
    make_action_payload,
    make_module_spec,
    flag_value: str,
):
    """
    GIVEN a flag whose literal value contains host-path syntax
    WHEN validate_modules is called
    THEN ActionSpecsParseError is raised at startup
    """
    action = make_action_payload(
        flags={
            "verbose": {
                "value": flag_value,
                "default": False,
                "description": "verbose flag",
            }
        },
        command=[{"binary": "echo"}, {"flag": "verbose"}],
    )
    module = make_module_spec(make_module_payload(actions={"ping": action}))

    with pytest.raises(ActionSpecsParseError, match="host paths"):
        validate_modules([module])
