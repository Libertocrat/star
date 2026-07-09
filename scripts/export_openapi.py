"""Export STAR OpenAPI schema to a JSON file.

This script builds the application using create_app()
and writes the generated OpenAPI schema to disk.

Intended for CI usage.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from star.app import create_app
from star.core.config import Settings

OPENAPI_OUTPUT_PATH = Path("docs/api-docs/output/openapi.json")


def get_docs_root_dir() -> str:
    """Return a writable STAR root directory for docs/schema generation.

    The application factory creates storage directories during startup.
    For CI/documentation export we must avoid privileged paths like
    `/var/lib/star` and use a writable temporary location instead.

    Returns:
        Absolute path to a writable docs root directory.

    Raises:
        ValueError: If `STAR_DOCS_ROOT_DIR` is not an absolute path.
    """

    default_root = str((Path.cwd() / ".star-docs").resolve())
    raw = os.getenv("STAR_DOCS_ROOT_DIR", default_root).strip()
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("STAR_DOCS_ROOT_DIR must be an absolute path")
    return str(path.resolve(strict=False))


def get_release_version() -> str:
    """Return the normalized release version for documentation assets.

    Returns:
        Semantic version string without a leading `v` prefix.

    Raises:
        ValueError: If `RELEASE_VERSION` is not a valid semantic version.
    """

    raw = os.getenv("RELEASE_VERSION", "0.1.2").strip()
    normalized = raw[1:] if raw.startswith("v") else raw
    if not re.fullmatch(r"\d+\.\d+\.\d+", normalized):
        raise ValueError("RELEASE_VERSION must be in format vX.Y.Z or X.Y.Z")
    return normalized


def build_docs_settings() -> Settings:
    """Create a minimal settings object for documentation generation.

    The selected values satisfy application validation without changing the
    generated schema. Documentation export enables docs explicitly so runtime
    defaults can stay restrictive.

    Returns:
        Valid application settings suitable for OpenAPI export.
    """

    return Settings(
        star_app_version=get_release_version(),
        star_api_token="docs-token",  # noqa: S106 -- fixed token for documentation purposes only
        star_root_dir=get_docs_root_dir(),
        star_enable_docs=True,
        star_max_file_bytes=1048576,
        star_max_yml_bytes=100 * 1024,
        star_max_stdout_bytes=None,
        star_max_stderr_bytes=None,
        star_timeout_ms=5000,
        star_rate_limit_rps=5,
        star_enable_security_headers=True,
        star_blocked_binaries_extra=None,
    )


def main() -> None:
    """Export the generated OpenAPI schema to the docs output path."""

    app = create_app(settings=build_docs_settings())
    schema = app.openapi()

    output_path = OPENAPI_OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
