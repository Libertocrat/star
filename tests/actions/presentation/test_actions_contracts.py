"""Unit tests for STAR action contracts builders."""

from __future__ import annotations

import json

import pytest

from star.actions.presentation.contracts import (
    build_action_contracts,
    build_params_contract,
    build_params_example,
    build_response_contract,
    build_response_example,
)


def _build_contracts_registry(
    *,
    tmp_path,
    monkeypatch,
    settings,
):
    """Build a deterministic DSL registry for contracts edge-case coverage."""

    import star.actions.registry as registry_module

    specs_dir = tmp_path / "specs_contracts"
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec_file = specs_dir / "contracts_runtime.yml"
    spec_file.write_text(
        """
version: 1
module: contracts_runtime
description: "Contracts runtime test module"

binaries:
    - echo

actions:
    flagged_action:
        description: "Action with required/optional args and one flag"
        args:
            required_value:
                type: int
                required: true
                description: "Required integer"
            optional_value:
                type: string
                required: false
                default: "fallback"
                description: "Optional string"
        flags:
            verbose:
                value: --verbose
                default: true
                description: "Verbose mode"
        command:
            - binary: echo
            - arg: required_value
            - arg: optional_value
            - flag: verbose

    file_input_action:
        description: "Action requiring file_id"
        args:
            file:
                type: file_id
                required: true
                description: "Input file id"
        command:
            - binary: echo
            - arg: file

    list_string_action:
        description: "Action requiring list of strings"
        args:
            inputs:
                type: list
                items: string
                required: true
                description: "String list"
        command:
            - binary: echo
            - arg: inputs

    list_file_id_action:
        description: "Action requiring list of file ids"
        args:
            files:
                type: list
                items: file_id
                required: true
                description: "File id list"
        command:
            - binary: echo
            - arg: files

    outputs_dynamic_action:
        description: "Action with dynamic output contracts"
        allow_stdout_as_file: true
        outputs:
            cmd_file:
                type: file
                source: command
                description: "Output materialized from command placeholder"
        command:
            - binary: echo
            - "outputs"
            - output: cmd_file

    outputs_without_stdout_option:
        description: "Action with command output but stdout file disabled"
        allow_stdout_as_file: false
        outputs:
            cmd_file:
                type: file
                source: command
                description: "Output materialized from command placeholder"
        command:
            - binary: echo
            - "outputs"
            - output: cmd_file
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))
    return registry_module.build_registry_from_specs(settings)


@pytest.fixture
def contracts_special_registry(tmp_path, monkeypatch, settings):
    """Build a registry with actions required for contracts edge cases."""

    return _build_contracts_registry(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        settings=settings,
    )


@pytest.fixture
def sample_action_spec(contracts_special_registry):
    """Return an ActionSpec with dynamic outputs for response tests."""

    return contracts_special_registry.get("contracts_runtime.outputs_dynamic_action")


# ============================================================================
# Params Contract
# ============================================================================


def test_build_params_contract_shape(valid_registry) -> None:
    """
    GIVEN a valid action spec
    WHEN building params contract
    THEN the result must contain params and required fields
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_params_contract(spec)

    assert set(result.keys()) == {"params", "stdout_as_file", "required"}
    assert isinstance(result["params"], dict)
    assert isinstance(result["required"], list)


def test_build_params_contract_maps_args(valid_registry) -> None:
    """
    GIVEN an action with argument definitions
    WHEN building params contract
    THEN args must be correctly mapped into contract fields
    """

    spec = valid_registry.get("test_runtime.repeat")

    result = build_params_contract(spec)

    assert "count" in result["params"]
    assert result["params"]["count"]["type"] == "int"
    assert result["params"]["count"]["required"] is True
    assert result["params"]["count"]["default"] is None
    assert result["params"]["count"]["description"] == "Number to echo"


def test_action_params_contract_includes_stdout_as_file_request_option(
    valid_registry,
) -> None:
    """
    GIVEN a registered action
    WHEN its params contract is built
    THEN stdout_as_file appears as a request-level option outside params
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_params_contract(spec)

    assert "stdout_as_file" in result
    assert "stdout_as_file" not in result["params"]
    assert result["stdout_as_file"]["type"] == "bool"
    assert result["stdout_as_file"]["default"] is False
    assert result["stdout_as_file"]["allowed"] is True


def test_build_params_contract_maps_flags(contracts_special_registry) -> None:
    """
    GIVEN an action with flags
    WHEN building params contract
    THEN flags must be mapped as boolean params without exposing internal fields
    """

    spec = contracts_special_registry.get("contracts_runtime.flagged_action")

    result = build_params_contract(spec)
    verbose = result["params"]["verbose"]

    assert verbose["type"] == "bool"
    assert verbose["required"] is False
    assert verbose["default"] is True
    assert verbose["description"] == "Verbose mode"
    assert verbose["constraints"] is None
    assert "value" not in verbose


def test_build_params_contract_required_list(contracts_special_registry) -> None:
    """
    GIVEN an action with required and optional params
    WHEN building params contract
    THEN required list must include only required args and exclude flags
    """

    spec = contracts_special_registry.get("contracts_runtime.flagged_action")

    result = build_params_contract(spec)

    assert "required_value" in result["required"]
    assert "optional_value" not in result["required"]
    assert "verbose" not in result["required"]


def test_build_params_contract_file_id_format(contracts_special_registry) -> None:
    """
    GIVEN an action with file_id params
    WHEN building params contract
    THEN format uuid4 must be included
    """

    spec = contracts_special_registry.get("contracts_runtime.file_input_action")

    result = build_params_contract(spec)

    assert result["params"]["file"]["type"] == "file_id"
    assert result["params"]["file"]["format"] == "uuid4"


def test_build_params_contract_list_string(contracts_special_registry) -> None:
    """
    GIVEN an action with list[string] params
    WHEN building params contract
    THEN type must be formatted as list[string] without format field
    """

    spec = contracts_special_registry.get("contracts_runtime.list_string_action")

    result = build_params_contract(spec)

    assert result["params"]["inputs"]["type"] == "list[string]"
    assert "format" not in result["params"]["inputs"]


def test_build_params_contract_list_file_id_format(contracts_special_registry) -> None:
    """
    GIVEN an action with list[file_id] params
    WHEN building params contract
    THEN type must be list[file_id] and include uuid4 format
    """

    spec = contracts_special_registry.get("contracts_runtime.list_file_id_action")

    result = build_params_contract(spec)

    assert result["params"]["files"]["type"] == "list[file_id]"
    assert result["params"]["files"]["format"] == "uuid4"


def test_build_params_contract_constraints_always_present(
    valid_registry,
    contracts_special_registry,
) -> None:
    """
    GIVEN actions with and without constraints
    WHEN building params contract
    THEN constraints field must always be present
    """

    constrained = build_params_contract(valid_registry.get("test_runtime.range_test"))
    unconstrained = build_params_contract(valid_registry.get("test_runtime.repeat"))
    flag_action = build_params_contract(
        contracts_special_registry.get("contracts_runtime.flagged_action")
    )

    assert "constraints" in constrained["params"]["value"]
    assert constrained["params"]["value"]["constraints"] == {"min": 1, "max": 10}
    assert "constraints" in unconstrained["params"]["count"]
    assert unconstrained["params"]["count"]["constraints"] is None
    assert "constraints" in flag_action["params"]["verbose"]
    assert flag_action["params"]["verbose"]["constraints"] is None


# ============================================================================
# Params Example
# ============================================================================


def test_build_params_example_required_args(valid_registry) -> None:
    """
    GIVEN an action with required args
    WHEN building params example
    THEN required args must have deterministic example values
    """

    spec = valid_registry.get("test_runtime.repeat")

    result = build_params_example(spec)

    assert result["count"] == 1


def test_build_params_example_defaults(valid_registry) -> None:
    """
    GIVEN an action with default values
    WHEN building params example
    THEN defaults must be used
    """

    spec = valid_registry.get("test_runtime.default_test")

    result = build_params_example(spec)

    assert result["value"] == 5


def test_build_params_example_flags(contracts_special_registry) -> None:
    """
    GIVEN an action with flags
    WHEN building params example
    THEN flags must use default values
    """

    spec = contracts_special_registry.get("contracts_runtime.flagged_action")

    result = build_params_example(spec)

    assert result["verbose"] is True


def test_build_params_example_list_string(contracts_special_registry) -> None:
    """
    GIVEN an action with list[string]
    WHEN building params example
    THEN example must contain a list with deterministic values
    """

    spec = contracts_special_registry.get("contracts_runtime.list_string_action")

    result = build_params_example(spec)

    assert result["inputs"] == ["inputs_item_1", "inputs_item_2"]


def test_build_params_example_list_file_id(contracts_special_registry) -> None:
    """
    GIVEN an action with list[file_id]
    WHEN building params example
    THEN example must contain valid uuid strings
    """

    spec = contracts_special_registry.get("contracts_runtime.list_file_id_action")

    result = build_params_example(spec)

    assert result["files"] == [
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "98e56387-3364-4ce2-9c66-44d23ec4e23a",
    ]


# ============================================================================
# Response Contract
# ============================================================================


def test_build_response_contract_structure(valid_registry) -> None:
    """
    GIVEN a valid action
    WHEN building response contract
    THEN envelope fields must exist
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_response_contract(spec)

    assert set(result.keys()) == {"success", "error", "data"}


def test_build_response_contract_data_fields(valid_registry) -> None:
    """
    GIVEN a valid action
    WHEN building response contract
    THEN data fields must match execution schema
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_response_contract(spec)
    data = result["data"]

    assert data["exit_code"]["type"] == "int"
    assert data["stdout"]["type"] == "string"
    assert data["stdout_encoding"]["type"] == "string"
    assert data["stderr"]["type"] == "string"
    assert data["stderr_encoding"]["type"] == "string"
    assert data["exec_time"]["type"] == "float"
    assert data["pid"]["type"] == "int"
    assert data["pid"]["nullable"] is True
    assert data["truncated"]["type"] == "bool"
    assert data["redacted"]["type"] == "bool"


def test_build_response_contract_outputs_null(valid_registry) -> None:
    """
    GIVEN an action without outputs
    WHEN building response contract
    THEN outputs must be null
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_response_contract(spec)

    outputs = result["data"]["outputs"]
    assert outputs is not None
    assert set(outputs.keys()) == {"stdout_file"}


def test_build_response_contract_outputs_dynamic(sample_action_spec) -> None:
    """
    GIVEN an action with outputs
    WHEN building response contract
    THEN outputs must be dynamically generated per action
    """

    result = build_response_contract(sample_action_spec)

    outputs = result["data"]["outputs"]
    assert isinstance(outputs, dict)
    assert set(outputs.keys()) == {"cmd_file", "stdout_file"}


def test_action_response_contract_includes_stdout_file_when_allowed(
    sample_action_spec,
) -> None:
    """
    GIVEN an action that allows stdout file materialization
    WHEN its response contract is built
    THEN outputs includes the reserved stdout_file contract
    """

    result = build_response_contract(sample_action_spec)

    outputs = result["data"]["outputs"]
    assert outputs is not None
    assert "stdout_file" in outputs
    assert outputs["stdout_file"]["source"] == "stdout"


def test_build_response_contract_output_fields(sample_action_spec) -> None:
    """
    GIVEN an action with outputs
    WHEN building response contract
    THEN each output must include type, source, description, and nullable
    """

    result = build_response_contract(sample_action_spec)

    outputs = result["data"]["outputs"]
    assert outputs is not None

    cmd_file = outputs["cmd_file"]
    stdout_file = outputs["stdout_file"]

    assert set(cmd_file.keys()) == {"type", "source", "description", "nullable"}
    assert cmd_file["type"] == "FileMetadata"
    assert cmd_file["nullable"] is True

    assert set(stdout_file.keys()) == {
        "type",
        "source",
        "description",
        "nullable",
        "reserved",
    }
    assert stdout_file["type"] == "FileMetadata"
    assert stdout_file["nullable"] is True
    assert stdout_file["reserved"] is True


def test_action_response_contract_omits_stdout_file_when_disallowed(
    contracts_special_registry,
) -> None:
    """
    GIVEN an action that disables stdout file materialization
    WHEN its response contract is built
    THEN outputs does not include stdout_file
    """

    spec = contracts_special_registry.get(
        "contracts_runtime.outputs_without_stdout_option"
    )

    result = build_response_contract(spec)

    outputs = result["data"]["outputs"]
    assert outputs is not None
    assert set(outputs.keys()) == {"cmd_file"}


# ============================================================================
# Response Example
# ============================================================================


def test_build_response_example_structure(valid_registry) -> None:
    """
    GIVEN a valid action
    WHEN building response example
    THEN response must match public envelope structure
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_response_example(spec)

    assert set(result.keys()) == {"success", "error", "data"}
    assert result["success"] is True
    assert result["error"] is None
    assert result["data"]["exit_code"] == 0
    assert result["data"]["stdout_encoding"] == "utf-8"


def test_build_response_example_outputs_null(valid_registry) -> None:
    """
    GIVEN an action without outputs
    WHEN building response example
    THEN outputs must be an empty mapping
    """

    spec = valid_registry.get("test_runtime.ping")

    result = build_response_example(spec)

    outputs = result["data"]["outputs"]
    assert outputs == {}


def test_build_response_example_outputs_present(sample_action_spec) -> None:
    """
    GIVEN an action with outputs
    WHEN building response example
    THEN outputs must include file metadata structure
    """

    result = build_response_example(sample_action_spec)

    outputs = result["data"]["outputs"]
    assert outputs is not None
    assert set(outputs.keys()) == {"cmd_file"}

    cmd_file = outputs["cmd_file"]
    assert set(cmd_file.keys()) == {
        "id",
        "original_filename",
        "stored_filename",
        "mime_type",
        "extension",
        "size_bytes",
        "sha256",
        "created_at",
        "updated_at",
        "status",
    }


# ============================================================================
# Integration
# ============================================================================


def test_build_action_contracts_integration(valid_registry) -> None:
    """
    GIVEN a valid action spec
    WHEN building full contracts
    THEN all contract sections must be present
    """

    spec = valid_registry.get("test_runtime.repeat")

    result = build_action_contracts(spec)

    assert set(result.keys()) == {
        "params_contract",
        "params_example",
        "response_contract",
        "response_example",
    }


# ============================================================================
# Edge Cases
# ============================================================================


def test_contracts_are_deterministic(valid_registry) -> None:
    """
    GIVEN a valid action spec
    WHEN building contracts multiple times
    THEN results must be identical
    """

    spec = valid_registry.get("test_runtime.repeat")

    first = build_action_contracts(spec)
    second = build_action_contracts(spec)

    assert first == second


def test_contracts_do_not_include_json_schema_keywords(valid_registry) -> None:
    """
    GIVEN generated contracts
    WHEN inspecting serialized output
    THEN JSON schema keywords must not be present
    """

    spec = valid_registry.get("test_runtime.repeat")

    dumped = json.dumps(build_action_contracts(spec), sort_keys=True)

    forbidden_keywords = (
        "$schema",
        "$defs",
        "$ref",
        "properties",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
    )

    for keyword in forbidden_keywords:
        assert f'"{keyword}"' not in dumped


def test_contracts_do_not_expose_internal_fields(contracts_special_registry) -> None:
    """
    GIVEN generated contracts
    WHEN inspecting serialized output
    THEN internal fields such as flag.value must not be exposed
    """

    spec = contracts_special_registry.get("contracts_runtime.flagged_action")

    contracts = build_action_contracts(spec)
    dumped = json.dumps(contracts, sort_keys=True)

    assert "value" not in contracts["params_contract"]["params"]["verbose"]
    assert "--verbose" not in dumped
