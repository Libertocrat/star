"""Tests for raw header integrity security helpers.

These tests freeze structural invariants for low-level header hygiene.
They validate byte-level rules without any ASGI or middleware wiring.
"""

import pytest

from star.core.security.headers import (
    _has_illegal_ctrl_bytes,
    find_header_integrity_violation,
)

# ============================================================================
# Control Bytes Policy
# ============================================================================


@pytest.mark.parametrize(
    ("data", "allow_tab", "expected"),
    [
        pytest.param(b"normal", False, False, id="normal-bytes-allowed"),
        pytest.param(b"value-with-tab\t", True, False, id="tab-allowed-in-value"),
        pytest.param(b"\t", True, False, id="single-tab-allowed-when-enabled"),
        pytest.param(b"\x00", False, True, id="nul-byte-rejected"),
        pytest.param(b"\x1f", False, True, id="unit-separator-rejected"),
        pytest.param(b"\t", False, True, id="tab-rejected-when-disabled"),
    ],
)
def test_has_illegal_ctrl_bytes_policy(data, allow_tab, expected):
    """
    GIVEN raw header bytes and a TAB policy
    WHEN _has_illegal_ctrl_bytes is evaluated
    THEN it returns whether disallowed control bytes are present
    """
    result = _has_illegal_ctrl_bytes(data, allow_tab=allow_tab)

    assert result is expected


# ============================================================================
# Header Integrity Violations
# ============================================================================


def test_find_header_integrity_violation_duplicate_authorization():
    """
    GIVEN two Authorization headers in one request
    WHEN headers are inspected for integrity
    THEN duplicate_authorization is returned
    """
    raw_headers = [
        (b"authorization", b"Bearer first"),
        (b"authorization", b"Bearer second"),
    ]
    violation = find_header_integrity_violation(raw_headers)

    assert violation == "duplicate_authorization"


@pytest.mark.parametrize(
    "raw_headers",
    [
        pytest.param([(b"bad name", b"value")], id="header-name-space-rejected"),
        pytest.param([(b"bad\tname", b"value")], id="header-name-tab-rejected"),
    ],
)
def test_find_header_integrity_violation_header_name_whitespace(raw_headers):
    """
    GIVEN a header name containing whitespace
    WHEN headers are inspected for integrity
    THEN header_name_whitespace is returned
    """
    violation = find_header_integrity_violation(raw_headers)

    assert violation == "header_name_whitespace"


@pytest.mark.parametrize(
    "raw_headers",
    [
        pytest.param([(b"bad\x00name", b"value")], id="header-name-nul-rejected"),
        pytest.param(
            [(b"bad\x1fname", b"value")], id="header-name-control-char-rejected"
        ),
    ],
)
def test_find_header_integrity_violation_header_name_control_char(raw_headers):
    """
    GIVEN a header name containing control characters
    WHEN headers are inspected for integrity
    THEN header_name_control_char is returned
    """
    violation = find_header_integrity_violation(raw_headers)

    assert violation == "header_name_control_char"


@pytest.mark.parametrize(
    "raw_headers",
    [
        pytest.param([(b"x-test", b"bad\x00value")], id="header-value-nul-rejected"),
        pytest.param(
            [(b"x-test", b"bad\x1fvalue")], id="header-value-control-char-rejected"
        ),
    ],
)
def test_find_header_integrity_violation_header_value_control_char(raw_headers):
    """
    GIVEN a header value containing disallowed control characters
    WHEN headers are inspected for integrity
    THEN header_value_control_char is returned
    """
    violation = find_header_integrity_violation(raw_headers)

    assert violation == "header_value_control_char"


@pytest.mark.parametrize(
    ("raw_headers", "expected"),
    [
        pytest.param(
            [
                (b"x-test", b"value\twith-tab"),
                (b"content-type", b"application/json"),
            ],
            None,
            id="tab-allowed-in-value",
        ),
        pytest.param(
            [
                (b"x\tname", b"value"),
            ],
            "header_name_whitespace",
            id="tab-rejected-in-name",
        ),
    ],
)
def test_find_header_integrity_violation_tab_handling(raw_headers, expected):
    """
    GIVEN TAB bytes in either header value or name
    WHEN headers are inspected for integrity
    THEN TAB is accepted in values and rejected in names
    """
    violation = find_header_integrity_violation(raw_headers)

    assert violation == expected


# ============================================================================
# find_header_integrity_violation: happy path
# ============================================================================


@pytest.mark.parametrize(
    "raw_headers",
    [
        pytest.param(
            [(b"authorization", b"Bearer token")], id="single-authorization-allowed"
        ),
        pytest.param(
            [
                (b"content-type", b"application/json"),
                (b"x-request-id", b"abc123"),
            ],
            id="normal-headers-allowed",
        ),
        pytest.param(
            [
                (b"authorization", b"Bearer token"),
                (b"content-length", b"10"),
                (b"accept", b"application/json"),
            ],
            id="mixed-valid-headers-allowed",
        ),
    ],
)
def test_find_header_integrity_violation_valid_headers_return_none(raw_headers):
    """
    GIVEN headers with no structural violations
    WHEN headers are inspected for integrity
    THEN no violation is returned
    """
    violation = find_header_integrity_violation(raw_headers)

    assert violation is None
