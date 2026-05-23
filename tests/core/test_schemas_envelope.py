# tests/test_schemas_envelope.py
"""
Tests for ResponseEnvelope schema.

These tests freeze the public response contract used across STAR.
They validate invariants and intended usage, not Pydantic internals.
"""

import pytest
from pydantic import ValidationError

from star.core.schemas.envelope import ErrorInfo, ResponseEnvelope

# ============================================================================
# ErrorInfo schema
# ============================================================================


def test_error_info_requires_code_and_message():
    """
    GIVEN an ErrorInfo
    WHEN required fields are missing
    THEN validation must fail
    """
    with pytest.raises(ValidationError):
        ErrorInfo(message="Something went wrong")

    with pytest.raises(ValidationError):
        ErrorInfo(code="ERR_SOMETHING")


def test_error_info_accepts_optional_details():
    """
    GIVEN an ErrorInfo with details
    WHEN the model is instantiated
    THEN details must be preserved as-is
    """
    details = {"path": "/etc", "reason": "permission denied"}

    err = ErrorInfo(
        code="ERR_FORBIDDEN",
        message="Access denied",
        details=details,
    )

    assert err.code == "ERR_FORBIDDEN"
    assert err.message == "Access denied"
    assert err.details == details


# ============================================================================
# ResponseEnvelope: Success cases
# ============================================================================


def test_success_response_factory_sets_success_true():
    """
    GIVEN ResponseEnvelope.success_response(data)
    WHEN the factory method is called
    THEN success=True, data is set, error is None
    """
    payload = {"result": "ok"}

    env = ResponseEnvelope.success_response(payload)

    assert env.success is True
    assert env.data == payload
    assert env.error is None


# ============================================================================
# ResponseEnvelope: Error cases
# ============================================================================


def test_failure_response_factory_sets_success_false():
    """
    GIVEN ResponseEnvelope.failure(code, message)
    WHEN the factory method is called
    THEN success=False, error is populated, data is None
    """
    env = ResponseEnvelope.failure(
        code="ERR_INVALID_INPUT",
        message="Invalid input provided",
    )

    assert env.success is False
    assert env.data is None
    assert env.error is not None
    assert env.error.code == "ERR_INVALID_INPUT"
    assert env.error.message == "Invalid input provided"


def test_failure_response_includes_error_details():
    """
    GIVEN ResponseEnvelope.failure(code, message, details)
    WHEN the factory method is called with details
    THEN error.details is preserved
    """
    details = {"field": "path", "reason": "traversal detected"}

    env = ResponseEnvelope.failure(
        code="ERR_SECURITY",
        message="Security violation",
        details=details,
    )

    assert env.success is False
    assert env.data is None
    assert env.error is not None
    assert env.error.details == details


# ============================================================================
# ResponseEnvelope: shape invariants
# ============================================================================


def test_response_envelope_has_stable_shape():
    """
    GIVEN any ResponseEnvelope
    WHEN instances are created from success and failure factories
    THEN fields success, data, and error always exist
    """
    success_env = ResponseEnvelope.success_response(data={"ok": True})
    failure_env = ResponseEnvelope.failure(
        code="ERR_TEST",
        message="Test error",
    )

    for env in (success_env, failure_env):
        assert hasattr(env, "success")
        assert hasattr(env, "data")
        assert hasattr(env, "error")
