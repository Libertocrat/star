"""Tests for STAR output sanitization and truncation layer."""

from __future__ import annotations

import pytest

from star.actions.models.runtime import ActionExecutionResult
from star.actions.runtime.sanitizer import (
    PATH_REDACTION,
    TRUNCATION_MARKER,
    sanitize_output,
    transform_output,
    truncate_output,
)


def _make_result(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> ActionExecutionResult:
    """Build a deterministic ActionExecutionResult for output tests."""

    return ActionExecutionResult(
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        exec_time=0.01,
        pid=123,
    )


@pytest.mark.parametrize(
    "input_bytes,expected",
    [
        (b"\x1b[31mred\x1b[0m", b"red"),
        (b"a\x00b\x1fc\x7f", b"abc"),
        (b"/tmp/star/out.txt", PATH_REDACTION.encode("utf-8")),
    ],
    ids=["ansi_strip", "control_chars", "path_redaction"],
)
def test_sanitize_output_applies_security_pipeline(input_bytes: bytes, expected: bytes):
    """GIVEN raw subprocess bytes with unsafe content
    WHEN sanitize_output is called
    THEN the returned bytes are sanitized deterministically
    """

    assert sanitize_output(input_bytes) == expected


def test_sanitize_output_normalizes_newlines():
    """GIVEN mixed newline conventions
    WHEN sanitize_output is called
    THEN line endings are normalized to LF
    """

    assert sanitize_output(b"a\r\nb\rc\n") == b"a\nb\nc\n"


def test_sanitize_output_does_not_redact_base64_token_with_slashes():
    """GIVEN a base64 token containing slash characters
    WHEN sanitize_output is called
    THEN the token is preserved and no path redaction marker is inserted
    """

    token = b"eUBIM0eZTc8nGfi9/is7sz52KDaEWn16R4xzcBqo5LFgL6x/" b"pZoVeSyu4GaWZ2c8="

    out = sanitize_output(token)

    assert out == token
    assert PATH_REDACTION.encode("utf-8") not in out


def test_sanitize_output_does_not_redact_base64_fragment_between_newlines():
    """GIVEN a base64-like fragment that starts with slash between newlines
    WHEN sanitize_output is called
    THEN the fragment is preserved because it is not under a sensitive prefix
    """

    raw = b"abc\n/T0DUuphNjR8T/hiMuEQeaOxh5kv\nxyz\n"

    out = sanitize_output(raw)

    assert out == raw
    assert PATH_REDACTION.encode("utf-8") not in out


def test_transform_output_does_not_mark_base64_token_as_redacted():
    """GIVEN stdout containing a base64 token with slash characters
    WHEN transform_output is called
    THEN redacted remains false and stdout is preserved
    """

    token = b"eUBIM0eZTc8nGfi9/is7sz52KDaEWn16R4xzcBqo5LFgL6x/" b"pZoVeSyu4GaWZ2c8=\n"
    result = _make_result(stdout=token)

    safe = transform_output(result, max_stdout=1024, max_stderr=1024)

    assert safe.redacted is False
    assert safe.stdout == token


def test_transform_output_redacts_configured_star_root_dir(settings):
    """GIVEN stdout containing the configured STAR root directory
    WHEN transform_output is called with runtime settings
    THEN redacted is true and the configured path is replaced
    """

    custom_settings = settings.model_copy(update={"star_root_dir": "/custom/star/root"})
    result = _make_result(stdout=b"blob=/custom/star/root/blobs/file_123.bin\n")

    safe = transform_output(
        result,
        max_stdout=1024,
        max_stderr=1024,
        settings=custom_settings,
    )

    assert safe.redacted is True
    assert safe.stdout == b"blob=[REDACTED_PATH]\n"


def test_sanitize_output_redacts_absolute_path_with_boundary():
    """GIVEN a sensitive absolute path surrounded by non-token boundaries
    WHEN sanitize_output is called
    THEN the sensitive path is replaced with the path redaction marker
    """

    out = sanitize_output(b"created=/tmp/star/out.txt\n")

    assert out == b"created=[REDACTED_PATH]\n"


def test_sanitize_output_redacts_absolute_path_without_extension():
    """GIVEN a sensitive absolute path without a file extension
    WHEN sanitize_output is called
    THEN the sensitive path is still replaced with the path redaction marker
    """

    out = sanitize_output(b"secret path: /run/secrets/star_api_token\n")

    assert out == b"secret path: [REDACTED_PATH]\n"


def test_sanitize_output_redacts_static_sensitive_path_prefixes():
    """GIVEN output containing known static sensitive filesystem paths
    WHEN sanitize_output is called
    THEN those paths are replaced with the path redaction marker
    """

    raw = (
        b"app=/app/star/app.py\n"
        b"spec=/etc/star/actions.d/custom.yml\n"
        b"secret=/run/secrets/star_api_token\n"
        b"tmp=/tmp/star/out.txt\n"
        b"proc=/proc/self/environ\n"
    )

    out = sanitize_output(raw)

    assert b"/app/star/app.py" not in out
    assert b"/etc/star/actions.d/custom.yml" not in out
    assert b"/run/secrets/star_api_token" not in out
    assert b"/tmp/star/out.txt" not in out
    assert b"/proc/self/environ" not in out
    assert out.count(PATH_REDACTION.encode("utf-8")) == 5


def test_sanitize_output_does_not_redact_non_sensitive_absolute_paths():
    """GIVEN output containing non-sensitive absolute paths
    WHEN sanitize_output is called
    THEN those paths are preserved because they are outside sensitive prefixes
    """

    raw = b"binary=/usr/bin/openssl\nlib=/lib/x86_64-linux-gnu/libc.so.6\n"

    out = sanitize_output(raw)

    assert out == raw
    assert PATH_REDACTION.encode("utf-8") not in out


def test_sanitize_output_redacts_configured_star_root_dir(settings):
    """GIVEN output containing the configured STAR root directory
    WHEN sanitize_output is called with runtime settings
    THEN the configured STAR root path is replaced with the redaction marker
    """

    custom_settings = settings.model_copy(update={"star_root_dir": "/custom/star/root"})

    out = sanitize_output(
        b"blob=/custom/star/root/blobs/file_123.bin\n",
        settings=custom_settings,
    )

    assert out == b"blob=[REDACTED_PATH]\n"


def test_truncate_output_below_limit_keeps_data():
    """GIVEN output smaller than limit
    WHEN truncate_output is called
    THEN bytes are returned unchanged and not truncated
    """

    data = b"hello"
    out, truncated = truncate_output(data, 8)

    assert out == data
    assert truncated is False


def test_truncate_output_above_limit_appends_marker():
    """GIVEN output larger than limit
    WHEN truncate_output is called
    THEN output is truncated and includes truncation marker
    """

    data = b"A" * 128
    limit = 32

    out, truncated = truncate_output(data, limit)

    assert truncated is True
    assert len(out) == limit
    assert out.endswith(TRUNCATION_MARKER)


def test_truncate_output_very_small_limit_returns_marker_slice():
    """GIVEN a limit smaller than marker length
    WHEN truncate_output is called on oversized output
    THEN only a marker slice is returned
    """

    limit = 8
    out, truncated = truncate_output(b"0123456789", limit)

    assert truncated is True
    assert out == TRUNCATION_MARKER[:limit]


def test_truncate_limit_equals_marker():
    """GIVEN limit equal to marker length
    WHEN truncated
    THEN output equals marker slice
    """

    limit = len(TRUNCATION_MARKER)
    out, truncated = truncate_output(b"X" * (limit + 10), limit)

    assert truncated is True
    assert out == TRUNCATION_MARKER[:limit]


def test_postprocess_output_sets_truncated_flag_when_any_stream_is_truncated():
    """GIVEN one stream above truncation limit
    WHEN postprocess_output is called
    THEN truncated flag is true in the aggregated output
    """

    result = _make_result(stdout=b"X" * 128, stderr=b"ok")

    safe = transform_output(result, max_stdout=32, max_stderr=32)

    assert safe.truncated is True


def test_redacted_only_when_no_sensitive_path_present():
    """GIVEN output without sensitive paths but with normalization changes
    WHEN postprocessed
    THEN redacted is False
    """

    result = _make_result(stdout=b"\x1b[31mhello\x1b[0m\r\n", stderr=b"\x00warn")

    safe = transform_output(result, max_stdout=1024, max_stderr=1024)

    assert safe.redacted is False


def test_redacted_when_sensitive_path_present():
    """GIVEN output with a sensitive absolute path
    WHEN postprocessed
    THEN redacted is True
    """

    result = _make_result(stdout=b"\x1b[31m/tmp/star/secret.txt\x1b[0m", stderr=b"")

    safe = transform_output(result, max_stdout=1024, max_stderr=1024)

    assert safe.redacted is True
    assert PATH_REDACTION.encode("utf-8") in safe.stdout


def test_sanitize_handles_invalid_utf8():
    """GIVEN invalid utf-8 bytes
    WHEN sanitized
    THEN no exception is raised
    """

    out = sanitize_output(b"\xff\xfe\xfa")

    assert isinstance(out, bytes)


def test_postprocess_output_processes_stdout_and_stderr_and_aggregates_flags():
    """GIVEN stdout and stderr requiring sanitization and truncation
    WHEN postprocess_output is called
    THEN both streams are transformed and flags are aggregated correctly
    """

    result = _make_result(
        stdout=b"/tmp/very/long/path/that/should/be/redacted\n" + (b"A" * 200),
        stderr=b"\x1b[31mERR\x1b[0m\x00",
    )

    safe = transform_output(result, max_stdout=48, max_stderr=16)

    assert safe.truncated is True
    assert safe.redacted is True
    assert PATH_REDACTION.encode("utf-8") in safe.stdout
    assert b"\x1b" not in safe.stderr
    assert b"\x00" not in safe.stderr


@pytest.mark.parametrize(
    "max_stdout,max_stderr,error_field",
    [
        (0, 1, "max_stdout"),
        (1, 0, "max_stderr"),
    ],
    ids=["zero_limit_stdout", "zero_limit_stderr"],
)
def test_postprocess_rejects_zero_limit(
    max_stdout: int,
    max_stderr: int,
    error_field: str,
):
    """GIVEN zero limits
    WHEN postprocess_output is called
    THEN ValueError is raised
    """

    result = _make_result(stdout=b"ok", stderr=b"ok")

    with pytest.raises(ValueError, match=error_field):
        transform_output(result, max_stdout=max_stdout, max_stderr=max_stderr)
