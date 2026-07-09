"""Runtime configuration loading and validation for STAR."""

from __future__ import annotations

import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from star.core.utils.parsing import parse_csv, parse_csv_set

logger = logging.getLogger(__name__)

STAR_API_TOKEN_SECRET_PATH = Path("/run/secrets/star_api_token")


def _is_simple_binary_name(binary: str) -> bool:
    """Return whether a token is a simple binary name.

    Args:
        binary: Raw binary token to validate.

    Returns:
        True when token is non-empty and has no path separators.
    """
    stripped = binary.strip()
    return stripped != "" and "/" not in stripped and "\\" not in stripped


def validate_api_token(token: str) -> str:
    """Validate and sanitize STAR_API_TOKEN.

    Rules:
        - Trim surrounding whitespace.
        - Minimum length: 32 characters.
        - Require at least two character classes among lowercase, uppercase,
          digits and symbols.

    Args:
        token: Raw token value from Docker secret or development fallback.

    Returns:
        Sanitized token string.

    Raises:
        ValueError: If the token does not satisfy security constraints.
    """

    sanitized = token.strip()

    if len(sanitized) < 32:
        raise ValueError("STAR_API_TOKEN must be at least 32 characters long")

    classes = 0
    classes += int(any(c.islower() for c in sanitized))
    classes += int(any(c.isupper() for c in sanitized))
    classes += int(any(c.isdigit() for c in sanitized))
    classes += int(any(not c.isalnum() for c in sanitized))

    if classes < 2:
        raise ValueError(
            "STAR_API_TOKEN must contain characters from at least two character classes"
        )

    return sanitized


def load_star_api_token() -> str:
    """Load STAR_API_TOKEN from Docker secret with development fallback.

    Priority:
        1) /run/secrets/star_api_token
        2) STAR_API_TOKEN_DEV (only when secret file is missing)

    Returns:
        The trimmed raw token.

    Raises:
        RuntimeError: If secret is missing/empty and fallback is not available.
    """

    try:
        raw_secret = STAR_API_TOKEN_SECRET_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        dev_token = os.getenv("STAR_API_TOKEN_DEV", "").strip()
        if dev_token:
            return dev_token
        raise RuntimeError(
            "STAR_API_TOKEN Docker secret not found at /run/secrets/star_api_token"
        ) from None

    token = raw_secret.strip()
    if token == "":
        raise RuntimeError("STAR_API_TOKEN Docker secret is empty")

    logger.info("Loaded STAR_API_TOKEN from Docker secret")
    return token


class Settings(BaseSettings):
    """Application settings loaded from environment (Pydantic v2).

    Pydantic-settings maps environment variables from field names by
    default for all settings except `star_api_token`, which is injected from
    Docker secret during settings initialization.

    Attributes:
        star_api_token: API token required for Bearer authentication.
        star_root_dir: Root directory acting as the single STAR filesystem sandbox.
        star_max_file_bytes: Maximum allowed bytes for file operations.
        star_max_yml_bytes: Maximum allowed bytes per DSL YAML spec file.
        star_max_stdout_bytes: Optional max bytes kept from sanitized stdout.
        star_max_stderr_bytes: Optional max bytes kept from sanitized stderr.
        star_timeout_ms: Per-request timeout (milliseconds).
        star_rate_limit_rps: Rate limit in requests-per-second.
        star_app_version: Application semantic version (x.y.z).
        star_enable_docs: Enable OpenAPI docs endpoints. Disabled by default
            for security; enable only for local development or testing.
        star_enable_security_headers: Enable baseline response security headers.
        star_blocked_binaries_extra: Optional CSV string with extra blocked
            binaries merged into the default blocklist.
    """

    # Loaded from Docker secret in `get_settings`, not from environment.
    star_api_token: str = Field("")
    star_root_dir: str = Field("/var/lib/star")
    star_max_file_bytes: int = Field(104857600)
    star_max_yml_bytes: int = Field(100 * 1024)
    star_max_stdout_bytes: int | None = Field(None)
    star_max_stderr_bytes: int | None = Field(None)
    star_timeout_ms: int = Field(5000)
    star_rate_limit_rps: int = Field(10)
    star_app_version: str = Field("0.1.2")
    star_enable_docs: bool = Field(False)
    star_enable_security_headers: bool = Field(True)
    star_blocked_binaries_extra: str | None = Field(None)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        # Ignore unrelated environment variables (e.g. Docker compose metadata)
        # to avoid Pydantic `extra_forbidden` errors when a full .env contains
        # variables that are not part of this Settings model.
        "extra": "ignore",
    }

    @field_validator("star_root_dir", mode="before")
    def _validate_required_non_empty(cls, v, info):
        """Reject missing or blank values for required string settings."""

        # Ensure required env values exists and are not empty/whitespace.
        if v is None:
            raise ValueError(f"{info.field_name} must be set and non-empty")
        if isinstance(v, str) and v.strip() == "":
            raise ValueError(f"{info.field_name} must be set and non-empty")
        return v

    @field_validator("star_root_dir", mode="before")
    def _validate_star_root_dir(cls, v):
        """Validate STAR_ROOT_DIR as a safe absolute directory path."""

        p = Path(str(v).strip())

        if not p.is_absolute():
            raise ValueError("STAR_ROOT_DIR must be an absolute path")

        if str(p) == "/":
            raise ValueError("STAR_ROOT_DIR cannot be root '/'")

        return str(p.resolve(strict=False))

    @field_validator("star_max_stdout_bytes", "star_max_stderr_bytes")
    # Avoid using mode="before" to prevent string to int casting errors
    def _validate_output_limits(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("must be greater than 0")
        return v

    @field_validator("star_app_version", mode="before")
    def _validate_star_app_version(cls, v):
        """Validate application version format as semantic version `x.y.z`."""

        s = str(v).strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", s):
            raise ValueError("star_app_version must use semantic version format x.y.z")
        return s

    @field_validator("star_blocked_binaries_extra", mode="before")
    def _validate_blocked_binary_extra(cls, v):
        """Validate optional extra blocked binary CSV values."""

        if v is None:
            return None

        s = str(v)
        if s.strip() == "":
            return None

        parsed = list(parse_csv(s))
        raw_parts = [part.strip() for part in s.split(",")]
        if len(parsed) != len(raw_parts):
            raise ValueError(
                "blocked binaries extra must not contain empty CSV entries"
            )

        for binary in parse_csv_set(s):
            if not _is_simple_binary_name(binary):
                raise ValueError(
                    (
                        "invalid blocked binaries extra entry "
                        f"'{binary}': paths are not allowed"
                    )
                )

        return s


def abort_config(message: str) -> NoReturn:
    """Log a fatal configuration error and terminate the process."""

    logger.error("Configuration error: %s", message)
    sys.exit(1)


@lru_cache
def get_settings() -> Settings:
    """
    Lazily load and cache application settings from environment sources.

    This accessor intentionally instantiates `Settings` via
    `Settings.model_validate({})` instead of calling `Settings()` directly.

    Rationale:
        - In Pydantic v2, `BaseSettings` loads configuration from its configured
          sources (environment variables, `.env`, secrets, etc.) during
          validation, not during object construction.
        - Calling `model_validate({})` preserves the full runtime behavior of
          environment-based configuration while avoiding mypy false-positives
          about missing required constructor arguments.
        - Deferring settings instantiation avoids loading configuration at
          import time, which is critical for test isolation and for preventing
          failures when required environment variables are not yet defined.

    Design considerations:
        - Settings are loaded lazily and cached to provide a single source of
          truth at runtime.
        - Tests can fully control configuration by setting environment
          variables before invoking this function.
        - Importing application modules never implicitly depends on the
          presence of environment configuration.

    Returns:
        Settings: A fully validated Settings instance loaded from the current
        environment and Docker secret sources.
    """
    try:
        settings = Settings.model_validate({})
        token = load_star_api_token()
        settings.star_api_token = validate_api_token(token)
        return settings
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        abort_config(str(exc))
