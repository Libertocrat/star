"""Internal configuration for the STAR actions engine.

This module defines fixed, non-user-configurable parameters that control
how the actions DSL is discovered, validated, and processed.

These values are part of the system design and must remain deterministic.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from star.actions.models.core import ParamType, SpecProvenance
from star.core.security.mime_map import EXTENSION_MIME_MAP

# ------------------------------------------------------------------
# DSL SPEC DIRECTORIES
# ------------------------------------------------------------------

# Core specs directory (resolved dynamically relative to this module)
CORE_SPECS_DIR: Path = Path(__file__).resolve().parent / "specs"

# Extension specs directory (mounted via Docker volume)
EXTENSION_SPECS_DIR: Path = Path("/etc/star/actions.d")

# Ordered list of spec directories (deterministic loading order)
SPEC_DIRS: tuple[Path, ...] = (
    CORE_SPECS_DIR,
    EXTENSION_SPECS_DIR,
)

# Mounted modules retain the stable public `user.*` namespace.
USER_NAMESPACE_PREFIX = "user"

# ------------------------------------------------------------------
# DSL FILE VALIDATION CONSTANTS
# ------------------------------------------------------------------

# Allowed YAML file extensions
ALLOWED_SPEC_EXTENSIONS: tuple[str, ...] = (".yml", ".yaml")

# Allowed control characters inside YAML files
# (others below ASCII 32 are rejected)
ALLOWED_CONTROL_CHARS: tuple[str, ...] = ("\n", "\r", "\t")

# Disallowed YAML patterns (defense-in-depth)
DISALLOWED_YAML_PATTERNS: tuple[str, ...] = (
    "!!python",
    "!!binary",
)

# ------------------------------------------------------------------
# DSL BEHAVIOR CONSTANTS
# ------------------------------------------------------------------

# Shared identifier pattern for module, action, arg, flag, and output names.
IDENTIFIER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Shared tag token pattern for module-level and action-level tags.
TAG_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

# MIME-like token pattern used by file constraints validation.
MIME_LIKE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)

# Exact MIME values permitted in extension-authored static argv values. This
# derives from the canonical managed-file MIME policy rather than accepting an
# arbitrary MIME-like string that could also be a relative host path.
EXTENSION_STATIC_MIME_TYPES: frozenset[str] = frozenset(
    mime_type for mime_types in EXTENSION_MIME_MAP.values() for mime_type in mime_types
)

# Placeholder matcher for validated const-template interpolation: {arg_name}.
CONST_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}")

# Allowed arg types for const-template interpolation.
CONST_TEMPLATE_ALLOWED_ARG_TYPES: tuple[ParamType, ...] = (
    ParamType.STRING,
    ParamType.INT,
    ParamType.FLOAT,
)

# Upper bound for secret arguments delivered through sensitive sinks.
MAX_SECRET_LENGTH = 4096

# Reserved output names used by runtime-generated outputs.
RESERVED_OUTPUT_NAMES: tuple[str, ...] = ("stdout_file",)

# ------------------------------------------------------------------
# DSL COMMAND LITERAL PATH POLICY
# ------------------------------------------------------------------

# Windows absolute path matcher used by build-time command literal validation.
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_DRIVE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z]:")

# Encoded separators and environment-style references can cause a program to
# reconstruct a host path even when a literal has no raw separator.
PERCENT_ENCODED_PATH_SEPARATOR_PATTERN = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
ENVIRONMENT_PATH_REFERENCE_PATTERN = re.compile(
    r"(?:\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)|%[A-Za-z_][A-Za-z0-9_]*%)"
)
FILE_URI_PATTERN = re.compile(r"^file:", re.IGNORECASE)

# Reviewed core command literal exceptions. Extension specs never receive a
# host-path exception.
REVIEWED_COMMAND_LITERAL_PATH_ALLOWLIST: frozenset[
    tuple[SpecProvenance, str, str, str]
] = frozenset(
    {
        (
            SpecProvenance.CORE,
            "random",
            "gen_uuid",
            "/proc/sys/kernel/random/uuid",
        )
    }
)

# ------------------------------------------------------------------
# RUNTIME OUTPUT SANITIZATION CONSTANTS
# ------------------------------------------------------------------

# Maximum default stdout/stderr sizes before truncation.
DEFAULT_MAX_STDOUT_BYTES = 64 * 1024
DEFAULT_MAX_STDERR_BYTES = 64 * 1024

# Marker appended when sanitized output is truncated.
TRUNCATION_MARKER = b"\n[STAR OUTPUT TRUNCATED]\n"

# Marker used when sensitive filesystem paths are redacted from output.
PATH_REDACTION = "[REDACTED_PATH]"

# Marker used when invocation-provided secrets are redacted from output.
SECRET_REDACTION = "[REDACTED_SECRET]"  # noqa: S105

# Token characters that should prevent path matching when adjacent to `/`.
# This avoids false positives inside compact tokens such as base64/base64url.
PATH_BOUNDARY_CHARS = r"A-Za-z0-9+/=_-"

# Static sensitive path prefixes that should be redacted from subprocess output.
# The runtime STAR_ROOT_DIR value is added dynamically by the sanitizer layer.
STATIC_SENSITIVE_PATH_PREFIXES: tuple[str, ...] = (
    "/app",
    "/etc/star",
    "/var/lib/star",
    "/run/secrets",
    # The path below, resolves to /tmp or similar, to prevent DevSecOps pipeline errors
    Path(tempfile.gettempdir()).as_posix(),
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/home",
)

# ANSI escape sequence matcher used to strip terminal formatting from output.
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# Unsafe control characters removed from subprocess output.
UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ------------------------------------------------------------------
# ERROR MASKING LABELS
# ------------------------------------------------------------------

CORE_MASK_PREFIX = "CORE"
EXTENSION_MASK_PREFIX = "EXTENSION"
