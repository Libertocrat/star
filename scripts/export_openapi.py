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


def get_release_version() -> str:
    """Return the normalized release version for documentation assets.

    Returns:
        Semantic version string without a leading `v` prefix.

    Raises:
        ValueError: If `RELEASE_VERSION` is not a valid semantic version.
    """

    raw = os.getenv("RELEASE_VERSION", "0.1.0").strip()
    normalized = raw[1:] if raw.startswith("v") else raw
    if not re.fullmatch(r"\d+\.\d+\.\d+", normalized):
        raise ValueError(
            "RELEASE_VERSION must be in format vX.Y.Z or X.Y.Z (for example: v1.2.3)"
        )
    return normalized


# Minimal valid settings for schema generation; values won't affect the schema but must
# satisfy validation. The runtime keeps docs disabled by default for security, so
# we set `star_enable_docs=True` explicitly here to generate the published schema
# without changing the normal application default.
def build_docs_settings() -> Settings:
    """Create a minimal settings object for documentation generation.

    Returns:
        Valid application settings suitable for OpenAPI export.
    """

    return Settings(
        star_app_version=get_release_version(),
        star_api_token="docs-token",  # noqa: S106 -- fixed token for documentation purposes only
        star_root_dir="/var/lib/star",
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
