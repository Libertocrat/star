"""Tests for HTTP structural validation helpers.

These tests freeze pure, deterministic invariants for content-type,
content-length parsing, and path character hygiene.
"""

import pytest

from star.core.security.http_validation import (
    is_supported_json_content_type,
    normalize_content_type,
    parse_content_length_strict,
    path_has_disallowed_characters,
)

# ============================================================================
# Content-Type Normalization
# ============================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none-remains-none"),
        pytest.param("application/json", "application/json", id="plain-json-kept"),
        pytest.param(
            "application/json; charset=utf-8",
            "application/json",
            id="json-with-params-stripped",
        ),
        pytest.param(
            "Application/Json; Charset=UTF-8",
            "application/json",
            id="mixed-case-normalized",
        ),
        pytest.param(
            "  application/json  ; charset=utf-8",
            "application/json",
            id="whitespace-trimmed",
        ),
    ],
)
def test_normalize_content_type(value, expected):
    """
    GIVEN a raw Content-Type header value
    WHEN normalize_content_type is called
    THEN it returns the normalized base media type
    """
    normalized = normalize_content_type(value)

    assert normalized == expected


# ============================================================================
# JSON Policy Support
# ============================================================================


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        pytest.param("application/json", True, id="json-accepted"),
        pytest.param(
            "application/json; charset=utf-8",
            True,
            id="json-with-charset-accepted",
        ),
        pytest.param(
            "Application/Json; Charset=UTF-8",
            True,
            id="mixed-case-json-accepted",
        ),
        pytest.param(None, False, id="none-rejected"),
        pytest.param("", False, id="empty-rejected"),
        pytest.param("text/plain", False, id="text-plain-rejected"),
        pytest.param("application/xml", False, id="xml-rejected"),
        pytest.param("multipart/form-data", False, id="multipart-rejected"),
    ],
)
def test_is_supported_json_content_type(content_type, expected):
    """
    GIVEN a Content-Type header value
    WHEN JSON support is evaluated
    THEN it returns whether application/json is present
    """
    supported = is_supported_json_content_type(content_type)

    assert supported is expected


# ============================================================================
# Content-Length Parsing
# ============================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0", 0, id="zero-valid"),
        pytest.param("10", 10, id="ten-valid"),
        pytest.param("123", 123, id="three-digits-valid"),
    ],
)
def test_parse_content_length_strict_valid_values(value, expected):
    """
    GIVEN a digits-only non-negative Content-Length value
    WHEN parse_content_length_strict is called
    THEN it returns the parsed integer
    """
    parsed = parse_content_length_strict(value)

    assert parsed == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty-rejected"),
        pytest.param(" ", id="blank-space-rejected"),
        pytest.param("abc", id="alpha-rejected"),
        pytest.param("-1", id="negative-rejected"),
        pytest.param("+1", id="signed-positive-rejected"),
        pytest.param("1.0", id="decimal-rejected"),
        pytest.param(" 10", id="leading-space-rejected"),
        pytest.param("10 ", id="trailing-space-rejected"),
    ],
)
def test_parse_content_length_strict_invalid_values_raise_value_error(value):
    """
    GIVEN an invalid Content-Length representation
    WHEN parse_content_length_strict is called
    THEN ValueError is raised naturally
    """
    with pytest.raises(ValueError):
        parse_content_length_strict(value)


# ============================================================================
# path_has_disallowed_characters: path hygiene policy
# ============================================================================


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("tmp/ab\x00cd.txt", True, id="nul-byte-rejected"),
        pytest.param("tmp\\file.txt", True, id="backslash-rejected"),
        pytest.param("tmp/file\x1f.txt", True, id="unit-separator-rejected"),
        pytest.param("tmp/file\x01.txt", True, id="soh-rejected"),
        pytest.param("uploads/file.txt", False, id="normal-path-allowed"),
        pytest.param("uploads/with\ttab.txt", False, id="tab-allowed"),
        pytest.param("tmp/a1_b2-c3/file99.log", False, id="safe-alnum-path-allowed"),
    ],
)
def test_path_has_disallowed_characters(path, expected):
    """
    GIVEN a candidate request path
    WHEN path hygiene rules are evaluated
    THEN it reports whether disallowed characters are present
    """
    has_disallowed = path_has_disallowed_characters(path)

    assert has_disallowed is expected
