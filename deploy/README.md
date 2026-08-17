# STAR Runtime Package

Safe actions. No raw shell.

This package contains the deployable STAR runtime control surface.

## Download from Releases

The runtime package is published as GitHub Release assets:

- `star-deploy-vX.Y.Z.tar.gz`
- `star-deploy-vX.Y.Z.zip`
- `star-deploy.tar.gz`
- `star-deploy.zip`
- `star-image-vX.Y.Z.spdx.json`
- `SHA256SUMS`
- `SHA256SUMS.sigstore.json`

Latest stable download URL:

```bash
curl -fsSL https://github.com/Libertocrat/star/releases/latest/download/star-deploy.tar.gz -o star-deploy.tar.gz
```

Checksum verification:

```bash
curl -fsSL https://github.com/Libertocrat/star/releases/latest/download/SHA256SUMS -o SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

For cryptographic origin verification, install [Cosign](https://docs.sigstore.dev/cosign/system_config/installation/), select the release tag, and verify the signed manifest before trusting its checksums:

```bash
VERSION=vX.Y.Z
BASE_URL="https://github.com/Libertocrat/star/releases/download/${VERSION}"
curl -fsSL "${BASE_URL}/SHA256SUMS" -o SHA256SUMS
curl -fsSL "${BASE_URL}/SHA256SUMS.sigstore.json" -o SHA256SUMS.sigstore.json
cosign verify-blob SHA256SUMS \
  --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "https://github.com/Libertocrat/star/.github/workflows/release.yml@refs/tags/${VERSION}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS --ignore-missing
```

Release images are signed and have GitHub provenance and SPDX SBOM attestations. With the GitHub CLI, verify the image selected by a version tag with `gh attestation verify oci://ghcr.io/libertocrat/star:${VERSION} -R Libertocrat/star`. Tags are convenient selectors; use the resolved `@sha256:...` image reference for immutable deployment policy.

## What is included

- `./star` - the top-level lifecycle command for configure, startup, demos, status, logs, and shutdown
- `./star-runtime/` - Docker Compose runtime assets, runtime scripts, secrets directory, and local configuration files

> [!IMPORTANT]
> Most users should manage STAR from the directory that contains `./star`.
>
> You usually do not need to enter `star-runtime/` unless you want to adjust `.env`, inspect `secrets/star_api_token.txt`, or add custom YAML specs under `user-specs/`.

In the default deploy flow, the generated STAR configuration enables public Swagger / OpenAPI docs for local testing and demos. Metrics remain protected by bearer authentication by default.

`--production` changes those defaults when STAR generates a new configuration, but an existing `star-runtime/.env` remains authoritative until you overwrite it or edit `STAR_ENABLE_DOCS`, `STAR_DOCS_REQUIRE_AUTH`, or `STAR_METRICS_REQUIRE_AUTH` manually.

## Start

Run the guided flow:

```bash
./star
```

This checks whether STAR is configured, offers to run the configuration wizard, starts the runtime, and points you to the next commands.

## Fast deploy

Run the non-interactive default flow:

```bash
./star --auto
```

Run configuration, startup, and a guided demo in one sequence:

```bash
./star --auto --demo
```

> [!NOTE]
> `./star --silent` is also available for minimal-output automation, and `./star --production` applies production-oriented configure/start behavior.

Built-in demos use `curl` and `jq`. If they are missing, the demo flow can prompt to install them automatically when possible.

## Useful commands

```bash
./star status
./star demo
./star demo --demo encrypt --auto
./star logs -f
./star down
```

Useful explicit subcommands:

```bash
./star configure --auto
./star up --pull
./star logs --tail 200
./star down --docker-cleanup --force
```

## Swagger / OpenAPI docs

Swagger / OpenAPI docs are enabled by default in the standard local deploy flow so STAR is easier to explore and test from a browser. That local flow sets `STAR_DOCS_REQUIRE_AUTH=false`; source-tree and production-oriented defaults keep docs protected when they are mounted.

`/metrics` remains protected by default in all generated modes. Prometheus can scrape it by sending `Authorization: Bearer <STAR_API_TOKEN>` through scrape configuration such as `authorization.credentials_file`.

> [!WARNING]
> For production-oriented deployments, prefer `./star configure --force --production` when you want to regenerate configuration with production-oriented defaults, or set `STAR_ENABLE_DOCS=false` and keep `STAR_METRICS_REQUIRE_AUTH=true` manually in `.env`.

Default local startup keeps the token host-owned and uses the least permissive mode that remains readable by Docker Compose file-based secrets: `0640` when the token group already matches `STAR_CONTAINER_GID`, otherwise `0644`. Production startup explicitly prepares `0640` with the container group before starting.

## Runtime hardening

The long-running STAR container uses Docker's init process for child-process reaping, disallows privilege escalation, drops all Linux capabilities, and limits the application service to 256 PIDs. `STAR_CONTAINER_MEMORY_LIMIT` and `STAR_CONTAINER_CPUS_LIMIT` configure hard memory and CPU limits collectively for the API process and its allowlisted action subprocesses; they default to `1g` and `1.0`, respectively. The supported ranges are `512m` through `8g` for memory and `0.5` through `8.0` CPU cores. The short-lived root `star-init` helper remains limited to preparing named-volume ownership.

The runtime root filesystem remains writable in this release because STAR has not yet proven every required temporary write path under an explicit volume or tmpfs. Capability, privilege, PID, and shutdown controls remain fixed in `0.1.3`.

If you use `--production`, configure with production-oriented settings, or manually disable docs in `.env`, you can re-check STAR status as follows:

1. Safely stop STAR with `./star down`
2. Restart with `./star up`
3. Run `./star status` to verify config and runtime

## Customize

You may want to edit or inspect:

- `star-runtime/.env`
- `star-runtime/secrets/star_api_token.txt`
- `star-runtime/user-specs/`

> [!WARNING]
> Keep the API token secret. Do not commit real secrets or environment files to version control.

## Full docs

For the full project overview and deeper documentation, use the public repository:

- [STAR repository](https://github.com/Libertocrat/star)
- [Main README](https://github.com/Libertocrat/star/blob/main/README.md)
- [Development guide](https://github.com/Libertocrat/star/blob/main/DEVELOPMENT.md)
- [Architecture guide](https://github.com/Libertocrat/star/blob/main/docs/ARCHITECTURE.md)
- [Threat model](https://github.com/Libertocrat/star/blob/main/docs/THREAT_MODEL.md)
- [Security policy](https://github.com/Libertocrat/star/blob/main/SECURITY.md)
- [Hosted OpenAPI docs (not interactive)](https://libertocrat.github.io/star/api-docs/)
