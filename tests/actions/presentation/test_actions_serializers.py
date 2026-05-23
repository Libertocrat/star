"""Unit tests for STAR action presentation serializers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from star.actions.models.core import (
    ActionSpec,
    ArgDef,
    FlagDef,
    OutputDef,
    OutputSource,
    OutputType,
    ParamType,
)
from star.actions.models.presentation import (
    ActionPublicSpec,
    ActionSummary,
    ModuleSummary,
)
from star.actions.models.security import BinaryPolicy
from star.actions.presentation.serializers import (
    module_summary_to_dict,
    modules_to_response,
    to_action_public_spec,
    to_action_summary,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_action_spec() -> ActionSpec:
    """Build a minimal ActionSpec for serializer tests."""

    class Params(BaseModel):
        """Test params model used by serializer fixtures.

        Attributes:
            value: Integer input used by the sample action.
        """

        value: int

    return ActionSpec(
        name="crypto.hash.encrypt",
        namespace=("crypto", "hash"),
        module="hash",
        action="encrypt",
        version=1,
        params_model=Params,
        binary="echo",
        command_template=(),
        execution_policy=BinaryPolicy(allowed=("echo",), blocked=()),
        arg_defs={
            "value": ArgDef(
                type=ParamType.INT,
                required=False,
                default=10,
                constraints={"min": 1, "max": 10},
                description="value",
            )
        },
        flag_defs={
            "verbose": FlagDef(value="--verbose", default=False, description="flag")
        },
        outputs={
            "result": OutputDef(
                type=OutputType.FILE,
                source=OutputSource.COMMAND,
                description="result",
            )
        },
        defaults={"value": 10, "verbose": False},
        allow_stdout_as_file=True,
        tags=("crypto", "aes-256"),
        summary="summary",
        description="description",
    )


@pytest.fixture
def sample_module_summary(sample_action_spec: ActionSpec) -> ModuleSummary:
    """Build ModuleSummary with one action."""

    action = ActionSummary(
        action="encrypt",
        action_id=sample_action_spec.name,
        summary="summary",
        description="description",
        tags=("crypto", "aes-256"),
    )

    return ModuleSummary(
        module="hash",
        module_id="crypto.hash",
        namespace="crypto.hash",
        namespace_path=("crypto", "hash"),
        description="module desc",
        tags=("crypto",),
        authors=("author",),
        actions=[action],
    )


# ============================================================================
# ACTION SUMMARY
# ============================================================================


def test_to_action_summary_maps_fields(sample_action_spec: ActionSpec) -> None:
    """
    GIVEN a valid ActionSpec
    WHEN converting to ActionSummary
    THEN fields must be mapped deterministically
    """

    result = to_action_summary(sample_action_spec)

    assert isinstance(result, ActionSummary)
    assert result.action == "encrypt"
    assert result.action_id == "crypto.hash.encrypt"
    assert result.summary == "summary"
    assert result.description == "description"
    assert result.tags == ("crypto", "aes-256")


# ============================================================================
# ACTION PUBLIC SPEC
# ============================================================================


def test_to_action_public_spec_maps_fields(sample_action_spec: ActionSpec) -> None:
    """
    GIVEN a valid ActionSpec
    WHEN converting to ActionPublicSpec
    THEN args, flags, outputs must be exposed
    """

    result = to_action_public_spec(sample_action_spec)

    assert isinstance(result, ActionPublicSpec)
    assert result.action == "encrypt"
    assert result.action_id == "crypto.hash.encrypt"
    assert len(result.args) == 1
    assert len(result.flags) == 1
    assert len(result.outputs) == 1
    assert result.allow_stdout_as_file is True
    assert result.tags == ("crypto", "aes-256")
    assert result.args[0]["name"] == "value"
    assert result.flags[0]["name"] == "verbose"
    assert "value" not in result.flags[0]
    assert result.outputs[0]["name"] == "result"


def test_to_action_public_spec_includes_contracts(
    sample_action_spec: ActionSpec,
) -> None:
    """
    GIVEN a valid ActionSpec
    WHEN converting to ActionPublicSpec
    THEN contracts and examples must be included
    """

    result = to_action_public_spec(sample_action_spec)

    assert isinstance(result.params_contract, dict)
    assert isinstance(result.params_example, dict)
    assert isinstance(result.response_contract, dict)
    assert isinstance(result.response_example, dict)
    assert "params" in result.params_contract
    assert "data" in result.response_contract


def test_to_action_public_spec_defaults(sample_action_spec: ActionSpec) -> None:
    """
    GIVEN ActionSpec with defaults
    WHEN serialized
    THEN defaults must be preserved in args
    """

    result = to_action_public_spec(sample_action_spec)

    arg = result.args[0]

    assert arg["default"] == 10


def test_to_action_public_spec_constraints(sample_action_spec: ActionSpec) -> None:
    """
    GIVEN ActionSpec with constraints
    WHEN serialized
    THEN constraints must be exposed
    """

    result = to_action_public_spec(sample_action_spec)

    assert "constraints" in result.args[0]
    assert result.args[0]["constraints"] == {"min": 1, "max": 10}


def test_to_action_public_spec_defaults_from_valid_registry(valid_registry) -> None:
    """
    GIVEN a build-time registry action with defaults
    WHEN serialized
    THEN default values from the builder must be preserved
    """

    spec = valid_registry.get("test_runtime.default_test")

    result = to_action_public_spec(spec)

    assert result.args[0]["name"] == "value"
    assert result.args[0]["default"] == 5


def test_to_action_public_spec_constraints_from_valid_registry(valid_registry) -> None:
    """
    GIVEN a build-time registry action with constraints
    WHEN serialized
    THEN constraints from the builder must be preserved
    """

    spec = valid_registry.get("test_runtime.range_test")

    result = to_action_public_spec(spec)

    assert result.args[0]["name"] == "value"
    assert result.args[0]["constraints"] == {"min": 1, "max": 10}


# ============================================================================
# MODULE SERIALIZATION
# ============================================================================


def test_module_summary_to_dict(sample_module_summary: ModuleSummary) -> None:
    """
    GIVEN a ModuleSummary
    WHEN serialized
    THEN output must match API format
    """

    result = module_summary_to_dict(sample_module_summary)

    assert result["module_id"] == "crypto.hash"
    assert result["namespace_path"] == ["crypto", "hash"]
    assert result["authors"] == ["author"]
    assert result["actions"][0]["action_id"] == "crypto.hash.encrypt"
    assert result["actions"][0]["tags"] == ["crypto", "aes-256"]


def test_modules_to_response(sample_module_summary: ModuleSummary) -> None:
    """
    GIVEN module summaries
    WHEN building response
    THEN result must wrap modules list
    """

    result = modules_to_response([sample_module_summary])

    assert "modules" in result
    assert len(result["modules"]) == 1
