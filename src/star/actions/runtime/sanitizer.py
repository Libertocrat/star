"""Output sanitization and truncation layer for STAR runtime execution."""

from __future__ import annotations

import re
from pathlib import Path

from star.actions import engine_config
from star.actions.models import ActionExecutionOutput, ActionExecutionResult
from star.core.config import Settings

# Re-export sanitizer constants from engine_config.
ANSI_ESCAPE_RE = engine_config.ANSI_ESCAPE_RE
PATH_BOUNDARY_CHARS = engine_config.PATH_BOUNDARY_CHARS
PATH_REDACTION = engine_config.PATH_REDACTION
SECRET_REDACTION = engine_config.SECRET_REDACTION
STATIC_SENSITIVE_PATH_PREFIXES = engine_config.STATIC_SENSITIVE_PATH_PREFIXES
TRUNCATION_MARKER = engine_config.TRUNCATION_MARKER
UNSAFE_CONTROL_RE = engine_config.UNSAFE_CONTROL_RE

# Re-export default output limits.
DEFAULT_MAX_STDOUT_BYTES = engine_config.DEFAULT_MAX_STDOUT_BYTES
DEFAULT_MAX_STDERR_BYTES = engine_config.DEFAULT_MAX_STDERR_BYTES


def _normalize_sensitive_prefixes(prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize sensitive absolute path prefixes for regex construction.

    Args:
        prefixes: Candidate absolute path prefixes.

    Returns:
        Deduplicated absolute prefixes without trailing slashes, sorted by
        descending length so more specific prefixes are matched first.
    """

    normalized: list[str] = []
    seen: set[str] = set()

    for prefix in prefixes:
        raw_value = str(Path(prefix))
        if raw_value == "/":
            continue

        value = raw_value.rstrip("/")
        if value == "" or not value.startswith("/"):
            continue
        if value in seen:
            continue

        seen.add(value)
        normalized.append(value)

    return tuple(sorted(normalized, key=len, reverse=True))


def _build_sensitive_path_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """Build effective sensitive path prefixes for output redaction.

    Args:
        settings: Optional runtime settings.

    Returns:
        Normalized sensitive prefixes including the runtime STAR root dir.
    """

    prefixes = STATIC_SENSITIVE_PATH_PREFIXES
    if settings is not None:
        prefixes = (*prefixes, settings.star_root_dir)

    return _normalize_sensitive_prefixes(prefixes)


def _build_sensitive_path_regex(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    """Build a regex that redacts only sensitive absolute path prefixes.

    Args:
        prefixes: Normalized absolute path prefixes.

    Returns:
        Compiled regex matching sensitive paths with token-aware boundaries.
    """

    escaped_prefixes = "|".join(re.escape(prefix) for prefix in prefixes)
    return re.compile(
        rf"(?:(?<![{PATH_BOUNDARY_CHARS}])|(?<==))"
        rf"(?:{escaped_prefixes})"
        r"(?:/[A-Za-z0-9._-]+)*"
        rf"(?![{PATH_BOUNDARY_CHARS}])"
    )


def sanitize_output(
    data: bytes,
    settings: Settings | None = None,
    secret_redactions: tuple[str, ...] = (),
) -> bytes:
    """Sanitize subprocess output.

    Steps:
        - Decode using UTF-8 with replacement.
        - Remove ANSI escape sequences.
        - Strip unsafe control characters.
        - Normalize line endings.
        - Redact sensitive internal absolute paths.
        - Re-encode to UTF-8.

    Args:
        data: Raw output bytes.
        settings: Optional runtime settings used to include the configured
            STAR root directory in sensitive path redaction.
        secret_redactions: Exact secret values to redact from this invocation.

    Returns:
        Sanitized bytes.
    """

    if not data:
        return b""

    path_regex = _build_sensitive_path_regex(_build_sensitive_path_prefixes(settings))

    text = data.decode("utf-8", errors="replace")
    text = ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = UNSAFE_CONTROL_RE.sub("", text)
    text = path_regex.sub(PATH_REDACTION, text)
    text = _redact_secret_values(text, secret_redactions)
    return text.encode("utf-8", errors="replace")


def _redact_secret_values(text: str, secret_redactions: tuple[str, ...]) -> str:
    """Redact exact secret values from sanitized output text.

    Args:
        text: Output text after structural sanitization.
        secret_redactions: Exact secret values for one invocation.

    Returns:
        Text with exact secret occurrences replaced.
    """

    redacted = text
    for secret in sorted(set(secret_redactions), key=len, reverse=True):
        if secret == "":
            continue
        redacted = redacted.replace(secret, SECRET_REDACTION)
    return redacted


def truncate_output(data: bytes, limit: int) -> tuple[bytes, bool]:
    """Truncate output safely with marker.

    Args:
        data: Input bytes.
        limit: Maximum allowed size.

    Returns:
        Tuple of (truncated_data, was_truncated).
    """

    if len(data) <= limit:
        return data, False

    marker_len = len(TRUNCATION_MARKER)
    if limit <= marker_len:
        return TRUNCATION_MARKER[:limit], True

    keep = limit - marker_len
    return data[:keep] + TRUNCATION_MARKER, True


def transform_output(
    result: ActionExecutionResult,
    *,
    max_stdout: int,
    max_stderr: int,
    settings: Settings | None = None,
    secret_redactions: tuple[str, ...] = (),
) -> ActionExecutionOutput:
    """Transform raw execution output into a sanitized and bounded result.

    Args:
        result: Raw execution result from executor.
        max_stdout: Maximum allowed stdout size in bytes.
        max_stderr: Maximum allowed stderr size in bytes.
        settings: Optional runtime settings used for sensitive path redaction.
        secret_redactions: Exact secret values to redact from this invocation.

    Returns:
        Sanitized and truncated execution output.

    Raises:
        ValueError: If limits are invalid.
    """

    if max_stdout <= 0:
        raise ValueError("max_stdout must be greater than 0")
    if max_stderr <= 0:
        raise ValueError("max_stderr must be greater than 0")

    stdout_sanitized = sanitize_output(
        result.stdout,
        settings=settings,
        secret_redactions=secret_redactions,
    )
    stderr_sanitized = sanitize_output(
        result.stderr,
        settings=settings,
        secret_redactions=secret_redactions,
    )

    stdout_safe, stdout_truncated = truncate_output(stdout_sanitized, max_stdout)
    stderr_safe, stderr_truncated = truncate_output(stderr_sanitized, max_stderr)

    marker_bytes = PATH_REDACTION.encode()
    secret_marker_bytes = SECRET_REDACTION.encode()
    stdout_redacted = marker_bytes in stdout_sanitized
    stderr_redacted = marker_bytes in stderr_sanitized
    stdout_secret_redacted = secret_marker_bytes in stdout_sanitized
    stderr_secret_redacted = secret_marker_bytes in stderr_sanitized

    return ActionExecutionOutput(
        returncode=result.returncode,
        stdout=stdout_safe,
        stderr=stderr_safe,
        exec_time=result.exec_time,
        pid=result.pid,
        truncated=stdout_truncated or stderr_truncated,
        redacted=(
            stdout_redacted
            or stderr_redacted
            or stdout_secret_redacted
            or stderr_secret_redacted
        ),
    )
