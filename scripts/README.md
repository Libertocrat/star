# Scripts

This directory contains helper scripts used for local development, release artifacts, and documentation publishing.

> [!IMPORTANT]
> Run the commands in this document from the repository root.

## Overview

| Script | Purpose |
| --- | --- |
| `scripts/star-forward.sh` | Forward a localhost port to a running STAR container when Compose port publishing is disabled or not desired |
| `scripts/export_openapi.py` | Build the FastAPI app and write the OpenAPI schema to disk |
| `scripts/build_docs_site.py` | Build a versioned Swagger UI site for GitHub Pages from the exported schema |

## star-forward.sh

Creates a temporary localhost port forward to a running STAR container.

This script is optional when using the default Compose mapping (`STAR_HOST_BIND_ADDRESS` + `STAR_HOST_PORT`), and is mainly useful when host publishing is disabled and access is needed only for temporary local testing.

The script starts an ephemeral `alpine/socat` container on the same Docker network as STAR. The forward binds to `127.0.0.1`, not to all host interfaces.

### Responsibilities

- Resolve the target STAR container
- Choose or validate a local TCP port
- Start a temporary TCP forward to the STAR service port

### Required configuration

Required variables:

- `STAR_SHARED_NETWORK`: Docker network shared with STAR
- `STAR_PORT`: TCP port exposed by the STAR container
- `COMPOSE_PROJECT_NAME`: required only when `--container` is not provided

If `--env-file` is provided, the required variables are loaded from that file. If `--env-file` is not provided, the required variables must already exist in the shell environment.

In normal local use, pass the same `.env` file that was used to start the Compose stack so the network name, container prefix, and service port stay aligned.

### Container resolution

- If `--container <name>` is provided, that running container is used
- Otherwise the script searches for a running container whose name starts with `$COMPOSE_PROJECT_NAME-star`
- Zero matches or multiple matches cause an error

### Local port selection

- `--local-port <port>` forces a specific port
- Without `--local-port`, the script scans ports `8081` through `8099`
- A port is considered unavailable if it is already listening on the host or already published by Docker

### Flags

- `--env-file <path>`: load variables from a file
- `--container <name>`: use a specific STAR container
- `--local-port <port>`: use a specific localhost port
- `--dry-run`: print actions without starting the proxy container
- `-h`, `--help`: show usage and exit

### Example

```bash
docker compose up -d star
./scripts/star-forward.sh --env-file .env
```

After the forward starts, the local URLs printed by the script include:

- `http://localhost:<PORT>/health`
- `http://localhost:<PORT>/docs` when `STAR_ENABLE_DOCS=true`
- `http://localhost:<PORT>/openapi.json` when `STAR_ENABLE_DOCS=true`

The forwarding script does not enable docs endpoints by itself. It only forwards traffic to whatever the running STAR container currently exposes.

### Reference

- [scripts/specs/star-forward.spec.md](scripts/specs/star-forward.spec.md)

## export_openapi.py

Builds the STAR application and writes the generated OpenAPI schema to `docs/api-docs/output/openapi.json`.

### Responsibilities

- Normalize and validate the release version
- Build a documentation-specific `Settings` object
- Create the FastAPI application
- Generate and write the OpenAPI schema as JSON

### Inputs

- `RELEASE_VERSION`: optional environment variable
  - Accepted formats: `vX.Y.Z` or `X.Y.Z`
  - Default: `0.1.0`

### Behavior

- The script strips a leading `v` before storing the version in settings
- The generated settings set `star_enable_docs=True` explicitly so export behavior stays stable regardless of runtime defaults
- The output directory is created automatically if needed
- The JSON file is written with indentation and a trailing newline

### Requirements

- Python dependencies required by STAR must be installed
- The STAR package must be importable in the current environment

### Example

```bash
export RELEASE_VERSION=v0.1.0
python scripts/export_openapi.py
```

Output:

- `docs/api-docs/output/openapi.json`

## build_docs_site.py

Builds a versioned Swagger UI site under `site/api-docs/` for publication to GitHub Pages.

### Responsibilities

- Create `site/api-docs/<RELEASE_VERSION>/`
- Copy Swagger UI static assets into the version directory
- Copy the repository Swagger template as `index.html`
- Copy the exported OpenAPI schema as `openapi.json`
- Create redirects for `site/index.html` and `site/api-docs/index.html`

### Inputs

- `RELEASE_VERSION`: required environment variable used as the version folder
- `docs/api-docs/template/swagger.html`: HTML template copied to the versioned site as `index.html`
- `docs/api-docs/output/openapi.json`: schema file produced by `scripts/export_openapi.py`
- `node_modules/swagger-ui-dist`: Swagger UI distribution copied into the site

### Behavior

- The script preserves existing content in `site/api-docs/` by copying new files into the selected version directory
- `site/api-docs/index.html` redirects to the latest version directory
- `site/index.html` redirects to `./api-docs/`

### Requirements

- `RELEASE_VERSION` must be set in the environment
- Swagger UI assets must already be installed under `node_modules`
- The OpenAPI export must already exist before this script runs

### Example

```bash
export RELEASE_VERSION=v0.1.0
npm init -y
npm install swagger-ui-dist@5.17.14
python scripts/export_openapi.py
python scripts/build_docs_site.py
```

Output:

- `site/api-docs/<RELEASE_VERSION>/index.html`
- `site/api-docs/<RELEASE_VERSION>/openapi.json`
- `site/api-docs/index.html`
- `site/index.html`

---
