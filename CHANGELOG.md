# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Adjusted the Semgrep gate so mutable GitHub Actions major-tag findings remain advisory hardening work instead of blocking local and CI security scans.

### Fixed

- Ensured Files API endpoints use the FastAPI app runtime settings snapshot when resolving managed storage.
- Ensured action `file_id` arguments resolve managed files with the runtime settings snapshot.
- Ensured action subprocess execution uses configured runtime timeouts and cleans owned process groups on timeout or cancellation.
- Rejected host-path-like DSL command literals by default while preserving reviewed core exceptions.

### Documentation

- Enhanced the versioned OpenAPI docs site with metadata-aware Swagger pages, social preview imagery, and light/dark favicons for GitHub Pages publication.
- Hardened the versioned OpenAPI docs builder to validate release versions before writing generated site outputs.

## [0.1.1] - 2026-06-22

### Security

- Updated `pydantic-settings` to `2.14.2` to remediate `GHSA-4xgf-cpjx-pc3j`.
- Updated `python-multipart` to `0.0.31` to remediate `CVE-2026-53540`.
- Refreshed the Debian runtime packages to OpenSSL `3.5.6-1~deb13u2`, remediating `CVE-2026-45447` in `libssl3t64`, `openssl`, and `openssl-provider-legacy`.

## [0.1.0] - 2026-06-01

### Project

- First official public STAR release (`v0.1.0`) as the canonical project baseline.
- Completed repository-wide migration and rebranding from SEG to STAR across code, container, documentation, and release surfaces.
- Established STAR as a hardened FastAPI runtime for deterministic execution of predefined, allowlisted actions.

### Added

- Unified actions API under `/v1/actions` with authenticated discovery, action contract retrieval, and typed execution.
- Managed files API under `/v1/files` for upload, metadata retrieval, listing, content streaming, and deletion.
- Typed request and response model with standardized envelopes and stable error taxonomy.
- YAML DSL build engine (loader, validator, builder) with immutable runtime action registry.
- Runtime OpenAPI generation aligned with registered actions and route contracts.
- Built-in safe file and utility actions, including hashing, MIME detection, verification, move, delete, encryption, and analysis flows.
- User-spec extensibility for custom action modules with validated startup loading.
- Observability primitives including `/health`, `/metrics`, request IDs, structured middleware signals, and Prometheus metrics.
- Deploy runtime bundle with `deploy/star` lifecycle orchestration for configure, up, demo, status, logs, and down flows.

### Security

- Bearer token authentication with Docker secret loading and API token strength validation.
- Request-integrity middleware enforcing malformed path/header rejection, duplicate `Authorization` rejection, content-type policy, body-size controls, and transport-header consistency checks.
- Rate limiting and request timeout middleware for bounded execution under abuse conditions.
- DSL security controls that reject unsafe YAML patterns, invalid identifiers, malformed declarations, and blocked binaries.
- Runtime execution hardening with deterministic argv rendering, no shell execution path, binary path rejection, blocked-binary policy, and action-level allowlists.
- Managed file security model using UUID references instead of raw user-provided file paths.
- Path traversal and unsafe path primitive protections, including absolute path, backslash, control-char, and symlink protections, plus safe file-open checks (`O_NOFOLLOW` where available).
- Output sanitization with control-sequence stripping, sensitive path redaction, and bounded stdout/stderr handling.
- Optional security headers and fingerprinting header removal controls.
- Security applicability guidance mapped to OWASP GenAI/LLM and MITRE ATLAS in project security documentation.

### DevSecOps

- GitHub Actions workflow suite for quality, security analysis, release automation, and documentation publication.
- Quality gate automation with formatting, linting, typing, tests, and Docker build validation.
- Security automation with Semgrep, Bandit, pip-audit, Trivy, Hadolint, ShellCheck, shfmt, and actionlint across local and CI paths.
- Release workflow with strict semver tag validation, GHCR publishing, pre-publish image scanning, OpenAPI export, deploy bundle packaging, and checksum generation.
- Release assets automation for OpenAPI artifacts, deploy bundle archives (`star-deploy` tar/zip), and `SHA256SUMS`.
- Makefile-first local DevSecOps workflow aligned with CI jobs and pre-commit hooks.
- Expanded shell and workflow quality gates through shell formatting validation, shell linting, and GitHub Actions linting.

### Documentation

- README refocused for deploy-first onboarding and practical usage with the STAR runtime package.
- Dedicated architecture, threat model, AI security, testing, CI/release, development, and contributing guides.
- Security policy and disclosure process with PGP support for sensitive reports.
- Scripts reference covering OpenAPI export, docs-site generation, local forwarding, and helper tooling.
- Versioned OpenAPI documentation publishing pipeline and hosted docs integration.
- Demo-oriented deploy documentation and assets for faster local adoption and verification.

### Infrastructure

- Hardened Python 3.12-slim container baseline with rootless runtime execution.
- Deterministic UID and GID handling and shared-volume ownership initialization.
- Docker Compose runtime model with external network attachment, secret injection, and healthcheck integration.
- Environment-driven configuration for storage boundaries, auth, limits, observability, docs exposure, and app versioning.
- Dependency set separation across runtime, testing, linting, security, and development requirements.
- GHCR image naming and release metadata aligned with STAR identity.
- GitHub Pages OpenAPI site publishing under the STAR docs path.

### Release

- Published the first official STAR release as `v0.1.0`.
- Established a clean STAR release baseline and changelog history for public versioning.
- Automated release publication for GHCR images plus GitHub release artifacts.
- Published OpenAPI assets and deploy bundle assets (`star-deploy-vX.Y.Z.tar.gz`, `star-deploy-vX.Y.Z.zip`, `star-deploy.tar.gz`, `star-deploy.zip`) with `SHA256SUMS`.
- Finalized STAR release references, docs links, and workflows for ongoing semantic version releases.

---

[Unreleased]: https://github.com/Libertocrat/star/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Libertocrat/star/releases/tag/v0.1.1
[0.1.0]: https://github.com/Libertocrat/star/releases/tag/v0.1.0
