from pathlib import Path

import pytest

from scripts import build_docs_site


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


def test_declared_docs_assets_exist():
    """
    GIVEN the repository docs builder asset constants
    WHEN the tracked docs asset paths are checked
    THEN every asset required by the release docs pipeline exists
    """
    assert build_docs_site.SOCIAL_PREVIEW_SOURCE.is_file()
    assert build_docs_site.FAVICON_LIGHT_SOURCE.is_file()
    assert build_docs_site.FAVICON_DARK_SOURCE.is_file()


def test_build_docs_site_generates_metadata_and_shared_assets(
    tmp_path, monkeypatch: pytest.MonkeyPatch
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
