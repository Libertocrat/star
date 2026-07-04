from pathlib import Path

import pytest

from scripts import build_docs_site

# ============================================================================
# Helpers
# ============================================================================


def _write_docs_inputs(root: Path) -> None:
    """Create the smallest docs tree required by the site builder."""

    template_dir = root / "docs" / "api-docs" / "template"
    output_dir = root / "docs" / "api-docs" / "output"
    assets_dir = root / "docs" / "assets"
    swagger_dist = root / "node_modules" / "swagger-ui-dist"

    template_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)
    swagger_dist.mkdir(parents=True)

    (template_dir / "swagger.html").write_text(
        """<!doctype html>
<html>
<head>
  {{STAR_DOCS_METADATA}}
  <link rel="stylesheet" href="./swagger-ui.css" />
</head>
<body>
  <div id="swagger-ui"></div>
</body>
</html>
""",
        encoding="utf-8",
    )
    (output_dir / "openapi.json").write_text('{"openapi":"3.1.0"}\n', encoding="utf-8")
    (swagger_dist / "swagger-ui.css").write_text("body{}\n", encoding="utf-8")
    (swagger_dist / "swagger-ui-bundle.js").write_text("// bundle\n", encoding="utf-8")
    (assets_dir / "star-gh-social-preview.png").write_bytes(b"social-preview")
    (assets_dir / "libertocrat-favicon-light-32.png").write_bytes(b"light")
    (assets_dir / "libertocrat-favicon-dark-32.png").write_bytes(b"dark")


# ============================================================================
# Tracked Assets
# ============================================================================


def test_declared_docs_assets_exist():
    """
    GIVEN the repository docs builder asset constants
    WHEN the tracked docs asset paths are checked
    THEN every asset required by the release docs pipeline exists
    """
    assert (build_docs_site.TEMPLATE_DIR / build_docs_site.TEMPLATE_NAME).is_file()
    assert build_docs_site.SOCIAL_PREVIEW_SOURCE.is_file()
    assert build_docs_site.FAVICON_LIGHT_SOURCE.is_file()
    assert build_docs_site.FAVICON_DARK_SOURCE.is_file()


# ============================================================================
# Release Versions
# ============================================================================


@pytest.mark.parametrize(
    ("raw_version", "expected"),
    [
        pytest.param("v1.2.3", "v1.2.3", id="prefixed"),
        pytest.param("1.2.3", "1.2.3", id="plain"),
        pytest.param(" v1.2.3 ", "v1.2.3", id="whitespace"),
    ],
)
def test_get_release_version_accepts_supported_semver_formats(
    raw_version: str, expected: str, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN a supported release version in the environment
    WHEN the docs builder reads the release version
    THEN it returns the validated version for use as the site path
    """
    monkeypatch.setenv("RELEASE_VERSION", raw_version)

    assert build_docs_site.get_release_version() == expected


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
    WHEN the docs builder validates the release version
    THEN it raises a focused validation error before path construction
    """
    monkeypatch.setenv("RELEASE_VERSION", raw_version)

    with pytest.raises(ValueError, match="RELEASE_VERSION must be in format"):
        build_docs_site.get_release_version()


# ============================================================================
# Template Rendering
# ============================================================================


def test_render_swagger_template_rejects_missing_metadata_placeholder(tmp_path: Path):
    """
    GIVEN a Swagger template without the required metadata placeholder
    WHEN the template is rendered for a versioned docs page
    THEN a focused validation error explains the missing placeholder
    """
    template_path = tmp_path / "swagger.html"
    template_path.write_text("<html></html>\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing required placeholder"):
        build_docs_site.render_swagger_template(
            template_path=template_path,
            page_url="https://docs.example.test/star/api-docs/v1.2.3/",
        )


# ============================================================================
# Site Generation
# ============================================================================


def test_build_docs_site_generates_metadata_and_shared_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN exported OpenAPI, Swagger UI assets, and social preview assets
    WHEN the docs site builder runs for a release version
    THEN the versioned site contains metadata, redirects, schema, and assets
    """
    _write_docs_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_VERSION", "v1.2.3")
    monkeypatch.setattr(
        build_docs_site,
        "DOCS_PUBLIC_BASE_URL",
        "https://docs.example.test/star",
    )

    build_docs_site.main()

    version_index = tmp_path / "site" / "api-docs" / "v1.2.3" / "index.html"
    api_docs_index = tmp_path / "site" / "api-docs" / "index.html"
    root_index = tmp_path / "site" / "index.html"

    version_html = version_index.read_text(encoding="utf-8")
    assert "{{STAR_DOCS_METADATA}}" not in version_html
    assert "<title>STAR API Reference | Secure Templated Actions Runtime</title>" in (
        version_html
    )
    expected_canonical = (
        '<link rel="canonical" '
        'href="https://docs.example.test/star/api-docs/v1.2.3/" />'
    )
    assert expected_canonical in version_html
    assert (
        'content="https://docs.example.test/star/assets/star-gh-social-preview.png"'
        in version_html
    )
    assert "libertocrat-favicon-light-32.png" in version_html
    assert "libertocrat-favicon-dark-32.png" in version_html

    assert (tmp_path / "site" / "api-docs" / "v1.2.3" / "openapi.json").is_file()
    assert (tmp_path / "site" / "api-docs" / "v1.2.3" / "swagger-ui.css").is_file()
    assert (tmp_path / "site" / "assets" / "star-gh-social-preview.png").is_file()
    assert (tmp_path / "site" / "assets" / "libertocrat-favicon-light-32.png").is_file()
    assert (tmp_path / "site" / "assets" / "libertocrat-favicon-dark-32.png").is_file()
    assert "url=./v1.2.3/" in api_docs_index.read_text(encoding="utf-8")
    assert "url=./api-docs/" in root_index.read_text(encoding="utf-8")


def test_build_docs_site_rejects_invalid_release_version_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN valid docs inputs but an invalid release version
    WHEN the docs site builder runs
    THEN it rejects the version before creating generated site files
    """
    _write_docs_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_VERSION", "../v1.2.3")

    with pytest.raises(ValueError, match="RELEASE_VERSION must be in format"):
        build_docs_site.main()

    assert not (tmp_path / "site").exists()


def test_build_docs_site_requires_exported_openapi_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    GIVEN docs inputs without an exported OpenAPI schema
    WHEN the docs site builder runs for a valid release version
    THEN it fails before creating generated site files
    """
    _write_docs_inputs(tmp_path)
    (tmp_path / build_docs_site.OPENAPI_OUTPUT_PATH).unlink()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_VERSION", "v1.2.3")

    with pytest.raises(FileNotFoundError, match="OpenAPI schema not found"):
        build_docs_site.main()

    assert not (tmp_path / "site").exists()
