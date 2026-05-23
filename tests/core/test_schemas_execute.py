# tests/test_schemas_execute.py
"""
Tests for ExecuteActionRequest schema.

These tests define and freeze the input contract for execution requests
handled by STAR. They focus on schema invariants, not action behavior.
"""

import pytest
from pydantic import ValidationError

from star.routes.actions.schemas import ExecuteActionRequest

# ============================================================================
# Success Cases
# ============================================================================


def test_execute_request_valid_minimal_payload():
    """
    GIVEN a minimal valid execution request payload
    WHEN the ExecuteActionRequest schema is validated
    THEN the model is created successfully with expected values
    """
    req = ExecuteActionRequest()

    assert req.params == {}
    assert req.stdout_as_file is False


def test_execute_request_accepts_params_dict():
    """
    GIVEN a valid execution request with params
    WHEN the ExecuteActionRequest schema is validated
    THEN params are preserved as-is
    """
    params = {"path": "/uploads/file.txt", "algorithm": "sha256"}

    req = ExecuteActionRequest(params=params)

    assert req.params == params


def test_execute_request_accepts_empty_params_by_default():
    """
    GIVEN a valid execution request without params
    WHEN the ExecuteActionRequest schema is validated
    THEN params defaults to an empty dictionary
    """
    req = ExecuteActionRequest()

    assert req.params == {}


def test_execute_action_request_defaults_stdout_as_file_to_false():
    """
    GIVEN an execute action request without stdout_as_file
    WHEN the request model is validated
    THEN stdout_as_file defaults to false
    """

    req = ExecuteActionRequest()

    assert req.stdout_as_file is False


def test_execute_request_accepts_stdout_as_file_true():
    """
    GIVEN an execution request with stdout_as_file enabled
    WHEN the ExecuteActionRequest schema is validated
    THEN stdout_as_file is preserved as true
    """

    req = ExecuteActionRequest(stdout_as_file=True)

    assert req.stdout_as_file is True


# ============================================================================
# Required fields
# ============================================================================


def test_execute_request_accepts_unknown_fields_without_breaking_contract():
    """
    GIVEN an execution payload with unknown fields
    WHEN the ExecuteActionRequest schema is validated
    THEN unknown fields are ignored and params contract remains stable
    """
    req = ExecuteActionRequest(action="noop")

    assert req.params == {}
    assert not hasattr(req, "action")


# ============================================================================
# Field type validation
# ============================================================================


def test_execute_request_params_must_be_dict():
    """
    GIVEN an execution request where 'params' is not a dict
    WHEN the ExecuteActionRequest schema is validated
    THEN a ValidationError is raised
    """
    with pytest.raises(ValidationError):
        ExecuteActionRequest(params=["not", "a", "dict"])


# ============================================================================
# Contract shape invariants
# ============================================================================


def test_execute_request_has_stable_shape():
    """
    GIVEN a valid ExecuteActionRequest
    WHEN the model is instantiated
    THEN the 'params' field always exists
    """
    req = ExecuteActionRequest()

    assert hasattr(req, "params")
    assert hasattr(req, "stdout_as_file")
