"""Build versioned Swagger documentation site for GitHub Pages.

This script:
- Receives the current release version (e.g. v0.1.0)
- Copies Swagger UI assets and the OpenAPI schema into /api-docs/<version>/
- Publishes a global STAR social-preview image under /assets/
- Generates metadata-aware redirect pages for the root and /api-docs/
- Injects matching social metadata into each versioned Swagger page
"""

from __future__ import annotations

import os
import shutil
from html import escape
from pathlib import Path

TEMPLATE_DIR = Path("docs/api-docs/template")
TEMPLATE_NAME = "swagger.html"
OPENAPI_OUTPUT_PATH = Path("docs/api-docs/output/openapi.json")
SOCIAL_PREVIEW_SOURCE = Path("docs/assets/star-gh-social-preview.png")
FAVICON_LIGHT_SOURCE = Path("docs/assets/libertocrat-favicon-light-32.png")
FAVICON_DARK_SOURCE = Path("docs/assets/libertocrat-favicon-dark-32.png")
SITE_ROOT = Path("site")

DOCS_PUBLIC_BASE_URL = os.environ.get(
    "DOCS_PUBLIC_BASE_URL",
    "https://libertocrat.github.io/star",
).rstrip("/")

SITE_TITLE = "STAR API Reference | Secure Templated Actions Runtime"

SITE_DESCRIPTION = (
    "Reference documentation for STAR's authenticated API: predefined, "
    "validated system actions for workflows and AI agents, without arbitrary "
    "shell execution."
)

SOCIAL_PREVIEW_ALT = "STAR - Secure Templated Actions Runtime"

METADATA_PLACEHOLDER = "{{STAR_DOCS_METADATA}}"


def public_url(path: str) -> str:
    """Build an absolute public URL from the configured Pages base URL."""

    return f"{DOCS_PUBLIC_BASE_URL}/{path.lstrip('/')}"


def render_social_metadata(page_url: str) -> str:
    """Render shared metadata for social cards, crawlers, and browsers."""

    title = escape(SITE_TITLE, quote=True)
    description = escape(SITE_DESCRIPTION, quote=True)
    page_url = escape(page_url, quote=True)

    social_preview_url = escape(
        public_url(f"assets/{SOCIAL_PREVIEW_SOURCE.name}"),
        quote=True,
    )

    social_preview_alt = escape(SOCIAL_PREVIEW_ALT, quote=True)

    favicon_light_url = escape(
        public_url(f"assets/{FAVICON_LIGHT_SOURCE.name}"),
        quote=True,
    )

    favicon_dark_url = escape(
        public_url(f"assets/{FAVICON_DARK_SOURCE.name}"),
        quote=True,
    )

    return f"""\
<meta charset="utf-8" />
<title>{title}</title>

<meta name="description" content="{description}" />
<link rel="canonical" href="{page_url}" />

<link
  rel="icon"
  type="image/png"
  sizes="32x32"
  media="(prefers-color-scheme: light)"
  href="{favicon_light_url}"
/>

<link
  rel="icon"
  type="image/png"
  sizes="32x32"
  media="(prefers-color-scheme: dark)"
  href="{favicon_dark_url}"
/>

<meta property="og:type" content="website" />
<meta property="og:site_name" content="STAR" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{description}" />
<meta property="og:url" content="{page_url}" />
<meta property="og:image" content="{social_preview_url}" />
<meta property="og:image:alt" content="{social_preview_alt}" />

<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{title}" />
<meta name="twitter:description" content="{description}" />
<meta name="twitter:image" content="{social_preview_url}" />
<meta name="twitter:image:alt" content="{social_preview_alt}" />"""


def render_redirect_page(*, page_url: str, redirect_target: str) -> str:
    """Build a valid HTML redirect page with full social metadata."""

    metadata = render_social_metadata(page_url)

    escaped_target = escape(redirect_target, quote=True)

    return f"""\
<!doctype html>
<html lang="en">
<head>
  {metadata}
  <meta http-equiv="refresh" content="0; url={escaped_target}" />
</head>
<body>
  <p>
    Redirecting to
    <a href="{escaped_target}">{escaped_target}</a>.
  </p>
</body>
</html>
"""


def render_swagger_template(*, template_path: Path, page_url: str) -> str:
    """Inject shared social metadata into the Swagger HTML template."""

    template = template_path.read_text(encoding="utf-8")

    if METADATA_PLACEHOLDER not in template:
        raise ValueError(
            f"Missing required placeholder {METADATA_PLACEHOLDER!r} "
            f"in {template_path}."
        )

    return template.replace(
        METADATA_PLACEHOLDER,
        render_social_metadata(page_url),
        1,
    )


def main() -> None:
    """Build the static Swagger UI site for the requested release version."""

    version = os.environ["RELEASE_VERSION"]

    site_root = SITE_ROOT
    api_docs_root = site_root / "api-docs"
    version_dir = api_docs_root / version
    assets_dir = site_root / "assets"

    template_path = TEMPLATE_DIR / TEMPLATE_NAME
    openapi_path = OPENAPI_OUTPUT_PATH
    swagger_dist = Path("node_modules/swagger-ui-dist")

    if not template_path.is_file():
        raise FileNotFoundError(f"Swagger template not found: {template_path}")

    if not openapi_path.is_file():
        raise FileNotFoundError(f"OpenAPI schema not found: {openapi_path}")

    if not SOCIAL_PREVIEW_SOURCE.is_file():
        raise FileNotFoundError(
            f"Social preview image not found: {SOCIAL_PREVIEW_SOURCE}"
        )

    if not FAVICON_LIGHT_SOURCE.is_file():
        raise FileNotFoundError(
            f"Light favicon image not found: {FAVICON_LIGHT_SOURCE}"
        )
    if not FAVICON_DARK_SOURCE.is_file():
        raise FileNotFoundError(f"Dark favicon image not found: {FAVICON_DARK_SOURCE}")

    if not swagger_dist.is_dir():
        raise FileNotFoundError(f"Swagger UI distribution not found: {swagger_dist}")

    api_docs_root.mkdir(parents=True, exist_ok=True)
    version_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Copy Swagger UI static assets into the versioned documentation directory.
    shutil.copytree(swagger_dist, version_dir, dirs_exist_ok=True)

    # Copy the OpenAPI schema for this specific version.
    shutil.copy2(openapi_path, version_dir / "openapi.json")

    # Publish the STAR social preview once at the root of the Pages site.
    shutil.copy2(
        SOCIAL_PREVIEW_SOURCE,
        assets_dir / SOCIAL_PREVIEW_SOURCE.name,
    )

    # Publish the light and dark favicon images once at the root of the Pages site.
    shutil.copy2(
        FAVICON_LIGHT_SOURCE,
        assets_dir / FAVICON_LIGHT_SOURCE.name,
    )
    shutil.copy2(
        FAVICON_DARK_SOURCE,
        assets_dir / FAVICON_DARK_SOURCE.name,
    )

    root_page_url = public_url("")
    api_docs_page_url = public_url("api-docs/")
    version_page_url = public_url(f"api-docs/{version}/")

    # Render the version-specific Swagger page with social metadata.
    version_index = version_dir / "index.html"
    version_index.write_text(
        render_swagger_template(
            template_path=template_path,
            page_url=version_page_url,
        ),
        encoding="utf-8",
    )

    # /api-docs/ redirects to the current release while retaining metadata.
    latest_index = api_docs_root / "index.html"
    latest_index.write_text(
        render_redirect_page(
            page_url=api_docs_page_url,
            redirect_target=f"./{version}/",
        ),
        encoding="utf-8",
    )

    # Site root redirects to /api-docs/ while retaining metadata.
    root_index = site_root / "index.html"
    root_index.write_text(
        render_redirect_page(
            page_url=root_page_url,
            redirect_target="./api-docs/",
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
