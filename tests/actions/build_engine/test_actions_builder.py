"""Unit tests for the STAR DSL specs builder.

These tests freeze builder-layer invariants:
- validated `ModuleSpec` input compiles into runtime `ActionSpec`
- runtime contract fields are normalized deterministically
- defensive builder failures are surfaced with `ActionSpecsBuildError`
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import UUID4, SecretStr, ValidationError

from star.actions.build_engine.builder import build_actions
from star.actions.exceptions import ActionSpecsBuildError
from star.actions.models import ActionSpec, ParamType
from star.core.config import Settings

# ============================================================================
# Fixtures and helpers
# ============================================================================


def _test_settings(
    *,
    blocked_extra: str | None = None,
) -> Settings:
    """Build minimal runtime settings for builder tests.

    Args:
        blocked_extra: Optional CSV blocklist extra entries.

    Returns:
        Validated Settings instance for build_actions.
    """

    return Settings.model_validate(
        {
            "star_root_dir": "/tmp/star-test",  # noqa: S108 -- fixed path for testing purposes
            "star_blocked_binaries_extra": blocked_extra,
        }
    )


# ============================================================================
# build_actions: happy path
# ============================================================================


def test_build_actions_returns_dict(make_valid_module):
    """
    GIVEN a valid module specification
    WHEN build_actions is called
    THEN a dictionary of ActionSpec values is returned
    """
    module = make_valid_module()

    result = build_actions([module], _test_settings())

    assert isinstance(result, dict)
    assert all(isinstance(key, str) for key in result)
    assert all(isinstance(value, ActionSpec) for value in result.values())


# ============================================================================
# namespacing
# ============================================================================


def test_action_names_are_namespaced(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a module with runtime namespace metadata
    WHEN build_actions is called
    THEN the resulting key uses `namespace.module.action`
    """
    action = make_action_spec_input()
    module = make_module_spec(
        make_module_payload(module_name="random_gen", actions={"token_hex": action})
    )
    module.with_runtime_namespace(("file",), "core")

    result = build_actions([module], _test_settings())

    assert "file.random_gen.token_hex" in result


def test_build_actions_uses_module_action_for_empty_namespace(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a module without runtime namespace metadata
    WHEN build_actions is called
    THEN the action key remains module.action
    """
    action = make_action_spec_input()
    module = make_module_spec(
        make_module_payload(module_name="test_module", actions={"ping": action})
    )

    result = build_actions([module], _test_settings())

    assert "test_module.ping" in result


def test_build_actions_includes_core_directory_namespace(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a module with a core directory namespace
    WHEN build_actions is called
    THEN the final action key includes namespace.module.action
    """
    module = make_module_spec(
        make_module_payload(
            module_name="crypto", actions={"encrypt_file": make_action_spec_input()}
        )
    )
    module.with_runtime_namespace(("file",), "core")

    result = build_actions([module], _test_settings())

    assert "file.crypto.encrypt_file" in result


def test_build_actions_includes_user_namespace_prefix(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a user module with runtime namespace metadata
    WHEN build_actions is called
    THEN the final action key starts with user
    """
    module = make_module_spec(
        make_module_payload(
            module_name="my_module", actions={"some_action": make_action_spec_input()}
        )
    )
    module.with_runtime_namespace(("user", "custom"), "user")

    result = build_actions([module], _test_settings())

    assert "user.custom.my_module.some_action" in result


def test_build_actions_sets_action_spec_namespace(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a module with runtime namespace metadata
    WHEN build_actions is called
    THEN ActionSpec exposes the namespace tuple
    """
    module = make_module_spec(
        make_module_payload(
            module_name="crypto", actions={"encrypt_file": make_action_spec_input()}
        )
    )
    module.with_runtime_namespace(("file", "crypto"), "core")

    spec = build_actions([module], _test_settings())["file.crypto.crypto.encrypt_file"]

    assert spec.namespace == ("file", "crypto")


# ============================================================================
# command_template
# ============================================================================


def test_command_template_structure(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action command with binary, arg, and flag tokens
    WHEN build_actions is called
    THEN command_template is a tuple preserving normalized token ordering
    """
    action = make_action_spec_input(
        args={
            "value": {
                "type": "string",
                "required": True,
                "description": "value",
            }
        },
        flags={
            "verbose": {
                "value": "-v",
                "default": False,
                "description": "verbose",
            }
        },
        command=[
            {"binary": "echo"},
            {"flag": "verbose"},
            {"arg": "value"},
        ],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert isinstance(spec.command_template, tuple)
    assert spec.command_template == (
        {"kind": "binary", "value": "echo"},
        {"kind": "flag", "name": "verbose"},
        {"kind": "arg", "name": "value"},
    )


def test_command_template_normalizes_literal_as_const_token(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action command containing a literal element
    WHEN build_actions is called
    THEN the literal is compiled as a `kind='const'` token
    """
    action = make_action_spec_input(command=[{"binary": "echo"}, "-hex"])
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.command_template[1] == {"kind": "const", "value": "-hex"}


def test_command_template_normalizes_output_token(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action command containing an output token
    WHEN build_actions is called
    THEN the output token is compiled as `kind='output'`
    """
    action = make_action_spec_input(
        outputs={
            "out_file": {
                "type": "file",
                "source": "command",
                "description": "Command materialized file output",
            }
        },
        command=[{"binary": "echo"}, {"output": "out_file"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.command_template[1] == {"kind": "output", "name": "out_file"}


def test_build_actions_compiles_output_definitions(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with declared outputs
    WHEN build_actions is called
    THEN ActionSpec outputs are compiled into runtime output definitions
    """
    action = make_action_spec_input(
        outputs={
            "cmd_file": {
                "type": "file",
                "source": "command",
                "description": "Command output file",
            },
        },
        command=[{"binary": "echo"}, {"output": "cmd_file"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert set(spec.outputs.keys()) == {"cmd_file"}
    assert spec.outputs["cmd_file"].type.value == "file"
    assert spec.outputs["cmd_file"].source.value == "command"
    assert spec.outputs["cmd_file"].description == "Command output file"


def test_build_action_defaults_allow_stdout_as_file_to_true(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a valid DSL action without allow_stdout_as_file
    WHEN the runtime action spec is built
    THEN the action allows stdout file materialization by default
    """

    action = make_action_spec_input(command=[{"binary": "echo"}, "ok"])
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.allow_stdout_as_file is True


def test_build_action_preserves_allow_stdout_as_file_false(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a valid DSL action with allow_stdout_as_file set to false
    WHEN the runtime action spec is built
    THEN the compiled action spec disables stdout file materialization
    """

    action = make_action_spec_input(
        allow_stdout_as_file=False,
        command=[{"binary": "echo"}, "ok"],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.allow_stdout_as_file is False


# ============================================================================
# binary extraction
# ============================================================================


def test_binary_extracted_from_command(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action command with a binary token
    WHEN build_actions is called
    THEN ActionSpec.binary matches that token value
    """
    action = make_action_spec_input(command=[{"binary": "openssl"}, "rand", "-hex"])
    module = make_module_spec(
        make_module_payload(binaries=["openssl"], actions={"token_hex": action})
    )

    spec = build_actions([module], _test_settings())["test_module.token_hex"]

    assert spec.binary == "openssl"


# ============================================================================
# arg_defs
# ============================================================================


def test_arg_defs_compiled_correctly(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with argument metadata and constraints
    WHEN build_actions is called
    THEN arg_defs preserve the validated DSL definition
    """
    action = make_action_spec_input(
        args={
            "bytes": {
                "type": "int",
                "required": False,
                "default": 16,
                "constraints": {"min": 1, "max": 64},
                "description": "bytes count",
            }
        },
        command=[{"binary": "echo"}, {"arg": "bytes"}],
    )
    module = make_module_spec(make_module_payload(actions={"token_hex": action}))

    spec = build_actions([module], _test_settings())["test_module.token_hex"]
    arg_def = spec.arg_defs["bytes"]

    assert arg_def.type == ParamType.INT
    assert arg_def.required is False
    assert arg_def.default == 16
    assert arg_def.constraints == {"min": 1, "max": 64}


def test_arg_defs_compile_secret_delivery(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with a secret arg and stdin delivery
    WHEN build_actions is called
    THEN arg_defs preserve the internal secret delivery policy
    """
    action = make_action_spec_input(
        args={
            "password": {
                "type": "secret",
                "required": True,
                "delivery": {"type": "stdin"},
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "password",
            }
        },
        command=[{"binary": "echo"}],
    )
    module = make_module_spec(make_module_payload(actions={"encrypt": action}))

    spec = build_actions([module], _test_settings())["test_module.encrypt"]
    arg_def = spec.arg_defs["password"]

    assert arg_def.type == ParamType.SECRET
    assert arg_def.required is True
    assert arg_def.delivery is not None
    assert arg_def.delivery.type == "stdin"
    assert arg_def.delivery.append_newline is True
    assert arg_def.constraints == {"min_length": 1, "max_length": 64}


def test_arg_defs_compile_secret_file_delivery(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with a secret arg and file delivery
    WHEN build_actions is called
    THEN arg_defs preserve file delivery type and newline policy
    """
    action = make_action_spec_input(
        args={
            "password": {
                "type": "secret",
                "required": True,
                "delivery": {"type": "file"},
                "constraints": {"min_length": 1, "max_length": 64},
                "description": "password",
            }
        },
        command=[{"binary": "echo"}, "file:{password}"],
    )
    module = make_module_spec(make_module_payload(actions={"encrypt": action}))

    spec = build_actions([module], _test_settings())["test_module.encrypt"]
    arg_def = spec.arg_defs["password"]

    assert arg_def.type == ParamType.SECRET
    assert arg_def.delivery is not None
    assert arg_def.delivery.type == "file"
    assert arg_def.delivery.append_newline is False
    assert arg_def.constraints == {"min_length": 1, "max_length": 64}


# ============================================================================
# flag_defs
# ============================================================================


def test_flag_defs_compiled_correctly(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with a flag definition
    WHEN build_actions is called
    THEN flag_defs preserve literal and default values
    """
    action = make_action_spec_input(
        flags={
            "verbose": {
                "value": "-v",
                "default": False,
                "description": "verbose",
            }
        },
        command=[{"binary": "echo"}, {"flag": "verbose"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]
    flag_def = spec.flag_defs["verbose"]

    assert flag_def.value == "-v"
    assert flag_def.default is False


# ============================================================================
# defaults
# ============================================================================


def test_defaults_include_optional_args_and_flags(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN required and optional args plus flags
    WHEN build_actions is called
    THEN defaults include optional args and all flags, but not required args
    """
    action = make_action_spec_input(
        args={
            "required_arg": {
                "type": "string",
                "required": True,
                "description": "required",
            },
            "optional_arg": {
                "type": "int",
                "required": False,
                "default": 10,
                "description": "optional",
            },
        },
        flags={
            "verbose": {
                "value": "-v",
                "default": True,
                "description": "verbose",
            }
        },
        command=[
            {"binary": "echo"},
            {"arg": "required_arg"},
            {"arg": "optional_arg"},
            {"flag": "verbose"},
        ],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.defaults == {"optional_arg": 10, "verbose": True}


# ============================================================================
# params_model
# ============================================================================


def test_params_model_fields(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with args and flags
    WHEN build_actions is called
    THEN params_model exposes matching field names and annotations
    """
    action = make_action_spec_input(
        args={
            "bytes": {
                "type": "int",
                "required": False,
                "default": 16,
                "description": "bytes",
            },
            "name": {
                "type": "string",
                "required": True,
                "description": "name",
            },
        },
        flags={
            "verbose": {
                "value": "-v",
                "default": False,
                "description": "verbose",
            }
        },
        command=[
            {"binary": "echo"},
            {"arg": "bytes"},
            {"arg": "name"},
            {"flag": "verbose"},
        ],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    model = build_actions([module], _test_settings())[
        "test_module.test_action"
    ].params_model

    assert set(model.model_fields.keys()) == {"bytes", "name", "verbose"}
    assert model.model_fields["bytes"].annotation is int
    assert model.model_fields["name"].annotation is str
    assert model.model_fields["verbose"].annotation is bool


def test_params_model_required_behavior(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN required and optional params in one action
    WHEN validating input through params_model
    THEN missing required params fail and optional params get defaults
    """
    action = make_action_spec_input(
        args={
            "name": {
                "type": "string",
                "required": True,
                "description": "name",
            },
            "count": {
                "type": "int",
                "required": False,
                "default": 2,
                "description": "count",
            },
        },
        flags={
            "verbose": {
                "value": "-v",
                "default": False,
                "description": "verbose",
            }
        },
        command=[
            {"binary": "echo"},
            {"arg": "name"},
            {"arg": "count"},
            {"flag": "verbose"},
        ],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))
    model = build_actions([module], _test_settings())[
        "test_module.test_action"
    ].params_model

    with pytest.raises(ValidationError):
        model.model_validate({})

    validated = model.model_validate({"name": "alice"})
    assert validated.count == 2
    assert validated.verbose is False


def test_params_model_uses_secretstr_for_secret_args(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN an action with a secret arg
    WHEN build_actions creates the params_model
    THEN the field uses SecretStr and redacts its repr/schema metadata
    """
    action = make_action_spec_input(
        args={
            "password": {
                "type": "secret",
                "required": True,
                "delivery": {"type": "stdin"},
                "description": "password",
            }
        },
        command=[{"binary": "echo"}],
    )
    module = make_module_spec(make_module_payload(actions={"encrypt": action}))
    model = build_actions([module], _test_settings())[
        "test_module.encrypt"
    ].params_model

    field = model.model_fields["password"]
    schema = model.model_json_schema()["properties"]["password"]
    validated = model.model_validate({"password": "topsecret"})

    assert field.annotation is SecretStr
    assert field.repr is False
    assert schema["format"] == "password"
    assert schema["writeOnly"] is True
    assert schema["sensitive"] is True
    assert isinstance(validated.password, SecretStr)
    assert "topsecret" not in repr(validated)


def test_file_id_validates_uuid4(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a file_id parameter
    WHEN validating request params
    THEN invalid UUID values fail and valid UUID4 values pass
    """
    action = make_action_spec_input(
        args={
            "file": {
                "type": "file_id",
                "required": True,
                "description": "file id",
            }
        },
        command=[{"binary": "echo"}, {"arg": "file"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))
    model = build_actions([module], _test_settings())[
        "test_module.test_action"
    ].params_model

    with pytest.raises(ValidationError):
        model.model_validate({"file": "not-a-uuid"})

    valid = model.model_validate({"file": str(uuid4())})
    assert valid.file is not None


def test_builder_list_string_annotation(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a list[string] argument
    WHEN build_actions is called
    THEN params_model field type is list[str]
    """
    action = make_action_spec_input(
        args={
            "files": {
                "type": "list",
                "items": "string",
                "required": True,
                "description": "files",
            }
        },
        command=[{"binary": "echo"}, {"arg": "files"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.params_model.model_fields["files"].annotation == list[str]
    assert spec.arg_defs["files"].items == ParamType.STRING


def test_builder_list_file_id_annotation(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a list[file_id] argument
    WHEN build_actions is called
    THEN params_model field type is list[UUID4]
    """
    action = make_action_spec_input(
        args={
            "files": {
                "type": "list",
                "items": "file_id",
                "required": True,
                "description": "files",
            }
        },
        command=[{"binary": "echo"}, {"arg": "files"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.params_model.model_fields["files"].annotation == list[UUID4]
    assert spec.arg_defs["files"].items == ParamType.FILE_ID


def test_builder_forces_list_required_true_when_omitted(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a list argument without explicit required declaration
    WHEN build_actions is called
    THEN runtime arg_def.required is forced to True
    """
    action = make_action_spec_input(
        args={
            "items": {
                "type": "list",
                "items": "string",
                "description": "items",
            }
        },
        command=[{"binary": "echo"}, {"arg": "items"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    spec = build_actions([module], _test_settings())["test_module.test_action"]

    assert spec.arg_defs["items"].required is True
    assert "items" not in spec.defaults


# ============================================================================
# params_model naming
# ============================================================================


def test_params_model_name_generation(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a namespaced action identifier with directory and underscore segments
    WHEN build_actions is called
    THEN params_model name follows CamelCase + Params suffix
    """
    action = make_action_spec_input(command=[{"binary": "openssl"}, "rand", "-hex"])
    module = make_module_spec(
        make_module_payload(
            module_name="random_gen",
            binaries=["openssl"],
            actions={"token_hex": action},
        )
    )
    module.with_runtime_namespace(("file",), "core")

    spec = build_actions([module], _test_settings())["file.random_gen.token_hex"]

    assert spec.params_model.__name__ == "FileRandomGenTokenHexParams"


# ============================================================================
# tags normalization
# ============================================================================


def test_build_actions_normalizes_module_tags(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with mixed-case and duplicate YAML list tags
    WHEN build_actions is called
    THEN tags are lowercased, deduplicated, and order-preserving
    """
    payload = make_module_payload()
    payload["tags"] = ["A", "b", "a", "C"]
    module = make_module_spec(payload)

    spec = build_actions([module], _test_settings())["test_module.ping"]

    assert spec.tags == ("a", "b", "c")


def test_build_actions_merges_module_and_action_tags(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN module tags and action-specific tags with duplicates
    WHEN build_actions is called
    THEN ActionSpec tags contain effective deduplicated tags
    """
    action = make_action_spec_input(tags=["files", "aes-256", "encryption"])
    payload = make_module_payload(actions={"encrypt_file": action})
    payload["tags"] = ["crypto", "files", "security"]
    module = make_module_spec(payload)

    spec = build_actions([module], _test_settings())["test_module.encrypt_file"]

    assert spec.tags == ("crypto", "files", "security", "aes-256", "encryption")


def test_build_actions_uses_action_tags_without_module_tags(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN action-specific tags and no module tags
    WHEN build_actions is called
    THEN ActionSpec tags include normalized action tags
    """
    action = make_action_spec_input(tags=["aes-256", "encryption"])
    module = make_module_spec(make_module_payload(actions={"encrypt_file": action}))

    spec = build_actions([module], _test_settings())["test_module.encrypt_file"]

    assert spec.tags == ("aes-256", "encryption")


# ============================================================================
# authors normalization
# ============================================================================


def test_authors_normalization(
    make_module_payload,
    make_module_spec,
):
    """
    GIVEN a module with authors metadata
    WHEN build_actions is called
    THEN authors are exposed as a tuple in runtime ActionSpec
    """
    payload = make_module_payload()
    payload["authors"] = ["Alice", "Bob"]
    module = make_module_spec(payload)

    spec = build_actions([module], _test_settings())["test_module.ping"]

    assert spec.authors == ("Alice", "Bob")


# ============================================================================
# duplicate detection
# ============================================================================


def test_duplicate_action_names_raise_error(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN two modules producing the same fully qualified action name
    WHEN build_actions is called
    THEN ActionSpecsBuildError is raised defensively
    """
    action = make_action_spec_input()
    first = make_module_spec(
        make_module_payload(module_name="dup", actions={"ping": action})
    )
    second = make_module_spec(
        make_module_payload(module_name="dup", actions={"ping": action})
    )

    with pytest.raises(ActionSpecsBuildError, match="duplicate fully qualified"):
        build_actions([first, second], _test_settings())


def test_build_actions_rejects_duplicate_final_action_fqdn(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN two modules producing the same final action FQDN
    WHEN build_actions is called
    THEN ActionSpecsBuildError is raised
    """
    action = make_action_spec_input()
    first = make_module_spec(
        make_module_payload(module_name="crypto", actions={"encrypt_file": action})
    ).with_runtime_namespace(("file",), "core")
    second = make_module_spec(
        make_module_payload(module_name="crypto", actions={"encrypt_file": action})
    ).with_runtime_namespace(("file",), "core")

    with pytest.raises(
        ActionSpecsBuildError,
        match="duplicate fully qualified action name",
    ):
        build_actions([first, second], _test_settings())


def test_build_actions_allows_same_module_action_in_different_namespaces(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN two modules with the same base module and action names
    in different namespaces
    WHEN build_actions is called
    THEN both final action names are compiled
    """
    action = make_action_spec_input()
    first = make_module_spec(
        make_module_payload(module_name="crypto", actions={"encrypt_file": action})
    ).with_runtime_namespace(("file",), "core")
    second = make_module_spec(
        make_module_payload(module_name="crypto", actions={"encrypt_file": action})
    ).with_runtime_namespace(("security",), "core")

    result = build_actions([first, second], _test_settings())

    assert "file.crypto.encrypt_file" in result
    assert "security.crypto.encrypt_file" in result


# ============================================================================
# defensive errors
# ============================================================================


def test_missing_binary_raises_error(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a command template without a binary token
    WHEN build_actions is called
    THEN ActionSpecsBuildError is raised during binary extraction
    """
    action = make_action_spec_input(
        args={
            "value": {
                "type": "string",
                "required": True,
                "description": "value",
            }
        },
        command=[{"arg": "value"}],
    )
    module = make_module_spec(make_module_payload(actions={"test_action": action}))

    with pytest.raises(ActionSpecsBuildError, match="no binary token found"):
        build_actions([module], _test_settings())


def test_invalid_command_element_raises_error(make_valid_module):
    """
    GIVEN a validated module mutated with an unsupported command element
    WHEN build_actions is called
    THEN ActionSpecsBuildError is raised
    """
    module = make_valid_module()
    module.actions["ping"].command = [123]

    with pytest.raises(ActionSpecsBuildError, match="unsupported type"):
        build_actions([module], _test_settings())


# ============================================================================
# immutability
# ============================================================================


def test_input_not_mutated(
    make_module_payload,
    make_module_spec,
    make_action_spec_input,
):
    """
    GIVEN a validated ModuleSpec used as builder input
    WHEN build_actions is called
    THEN the original input module remains unchanged
    """
    action = make_action_spec_input(command=[{"binary": "echo"}, "-n"])
    module = make_module_spec(make_module_payload(actions={"test_action": action}))
    before = module.model_dump(mode="python")

    _ = build_actions([module], _test_settings())

    after = module.model_dump(mode="python")
    assert after == before


def test_build_actions_attaches_execution_policy(make_valid_module):
    """
    GIVEN a valid module and default runtime settings
    WHEN build_actions is called
    THEN each ActionSpec includes an execution_policy
    """
    module = make_valid_module()

    spec = build_actions([module], _test_settings())["test_module.ping"]

    assert spec.execution_policy.allowed == ("echo",)
    assert "bash" in spec.execution_policy.blocked


def test_build_actions_merges_blocked_extra(make_valid_module):
    """
    GIVEN a valid module and blocked extra values
    WHEN build_actions is called
    THEN extra binaries are merged into the effective blocklist
    """
    module = make_valid_module()

    spec = build_actions(
        [module],
        _test_settings(blocked_extra="openssl"),
    )["test_module.ping"]

    assert "openssl" in spec.execution_policy.blocked


def test_build_actions_fails_when_primary_binary_is_blocked_extra(
    make_valid_module,
):
    """
    GIVEN a valid module and blocked extra matching action binary
    WHEN build_actions is called
    THEN ActionSpecsBuildError is raised fail-closed
    """
    module = make_valid_module()

    with pytest.raises(ActionSpecsBuildError, match="is not allowed by effective"):
        build_actions([module], _test_settings(blocked_extra="echo"))


def test_build_actions_blocklist_wins_module_allowlist(make_valid_module):
    """
    GIVEN a module-allowed binary also present in blocked extra entries
    WHEN build_actions is called
    THEN the binary is excluded from effective allowlist
    """
    module = make_valid_module()

    with pytest.raises(ActionSpecsBuildError, match="is not allowed by effective"):
        build_actions(
            [module],
            _test_settings(blocked_extra="echo"),
        )
