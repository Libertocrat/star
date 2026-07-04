import json
from pathlib import Path
from typing import Any

import pytest

from scripts import export_openapi

# ============================================================================
# Release Versions
# ============================================================================


@pytest.mark.parametrize(
    ("raw_version", "expected"),
    [
        pytest.param("v1.2.3", "1.2.3", id="prefixed"),
        pytest.param("1.2.3", "1.2.3", id="plain"),
        pytest.param(" v1.2.3 ", "1.2.3", id="whitespace"),
    ],
)
def test_get_release_version_normalizes_supported_semver_formats(
    raw_version: str, expected: str, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN a supported release version in the environment
    WHEN the OpenAPI exporter reads the release version
    THEN it returns a normalized semantic version for application settings
    """
    monkeypatch.setenv("RELEASE_VERSION", raw_version)

    assert export_openapi.get_release_version() == expected


@pytest.mark.parametrize(
    "raw_version",
    [
        pytest.param("", id="empty"),
        pytest.param("latest", id="label"),
        pytest.param("v1.2", id="partial_semver"),
        pytest.param("v1.2.3-beta", id="prerelease"),
        pytest.param("../v1.2.3", id="traversal"),
    ],
)
def test_get_release_version_rejects_invalid_values(
    raw_version: str, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN an invalid release version in the environment
    WHEN the OpenAPI exporter validates the release version
    THEN it raises a focused validation error before settings construction
    """
    monkeypatch.setenv("RELEASE_VERSION", raw_version)

    with pytest.raises(ValueError, match="RELEASE_VERSION must be in format"):
        export_openapi.get_release_version()


# ============================================================================
# Docs Root
# ============================================================================


def test_get_docs_root_dir_defaults_to_absolute_repo_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN no explicit docs root in the environment
    WHEN the OpenAPI exporter resolves its docs root
    THEN it returns an absolute repository-local default path
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STAR_DOCS_ROOT_DIR", raising=False)

    assert export_openapi.get_docs_root_dir() == str(tmp_path / ".star-docs")


def test_get_docs_root_dir_rejects_relative_environment_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    GIVEN a relative docs root in the environment
    WHEN the OpenAPI exporter validates its docs root
    THEN it rejects the path before settings construction
    """
    monkeypatch.setenv("STAR_DOCS_ROOT_DIR", "relative/star-docs")

    with pytest.raises(ValueError, match="STAR_DOCS_ROOT_DIR must be an absolute"):
        export_openapi.get_docs_root_dir()


# ============================================================================
# Settings And Export
# ============================================================================


def test_build_docs_settings_enables_docs_with_normalized_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN valid documentation export environment values
    WHEN documentation settings are built
    THEN docs are enabled with a normalized app version and writable root
    """
    monkeypatch.setenv("RELEASE_VERSION", "v1.2.3")
    monkeypatch.setenv("STAR_DOCS_ROOT_DIR", str(tmp_path / "star-docs"))

    settings = export_openapi.build_docs_settings()

    assert settings.star_app_version == "1.2.3"
    assert settings.star_root_dir == str(tmp_path / "star-docs")
    assert settings.star_enable_docs is True


def test_main_writes_deterministic_openapi_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN a documentation application with a generated OpenAPI schema
    WHEN the OpenAPI exporter runs
    THEN it writes indented UTF-8 JSON with a trailing newline
    """
    output_path = tmp_path / "docs" / "api-docs" / "output" / "openapi.json"
    captured_settings: list[Any] = []
    schema = {
        "openapi": "3.1.0",
        "info": {"title": "STAR", "version": "1.2.3"},
    }

    class DocsApp:
        """Provide a deterministic OpenAPI schema for the export test."""

        def openapi(self) -> dict[str, Any]:
            """Return the synthetic OpenAPI schema."""
            return schema

    def create_docs_app(*, settings: Any) -> DocsApp:
        """Capture documentation settings and return a deterministic app."""
        captured_settings.append(settings)
        return DocsApp()

    monkeypatch.setenv("RELEASE_VERSION", "v1.2.3")
    monkeypatch.setenv("STAR_DOCS_ROOT_DIR", str(tmp_path / "star-docs"))
    monkeypatch.setattr(export_openapi, "OPENAPI_OUTPUT_PATH", output_path)
    monkeypatch.setattr(export_openapi, "create_app", create_docs_app)

    export_openapi.main()

    assert len(captured_settings) == 1
    assert captured_settings[0].star_app_version == "1.2.3"
    assert json.loads(output_path.read_text(encoding="utf-8")) == schema
    assert output_path.read_text(encoding="utf-8").endswith("\n")
    assert output_path.read_text(encoding="utf-8").startswith("{\n  ")
