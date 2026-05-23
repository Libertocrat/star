# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-17

☘️ First release shipped on St. Patrick's Day: may it keep mischievous commands away.

### Project

- Initial public release of the Secure Templated Actions Runtime (STAR)
- Hardened FastAPI microservice for controlled execution of allowlisted filesystem actions

### Added

- FastAPI application factory with runtime-configurable documentation endpoints and custom OpenAPI generation
- Unified action execution API exposed at `/v1/execute` for typed action execution with a standardized response envelope
- Dynamic action discovery and explicit in-memory registry for allowlisted operations
- Stable machine-readable error taxonomy for dispatcher, middleware, and route failures
- Runtime-generated OpenAPI contract that projects registered actions, examples, response headers, and auth exposure rules
- Sandboxed file actions for checksum calculation, MIME detection, deletion, move, and composite file verification
- Extension-to-MIME policy mapping for content validation workflows in `file_verify`
- Prometheus metrics endpoint and request correlation support through `X-Request-Id`
- Health endpoint for readiness checks in containerized deployments

### Security

- Bearer token authentication with Docker secret loading and API token strength validation
- ASGI request integrity enforcement for malformed paths and headers, conflicting transport headers, unsupported content types, and oversized bodies
- Process-local rate limiting and per-request timeout enforcement for protected execution traffic
- Filesystem sandbox enforcement with allowed subdirectory controls, traversal rejection, absolute path blocking, and backslash rejection
- Symlink-safe file access with `O_NOFOLLOW`, regular-file validation, and reduced TOCTOU exposure in file operations
- Extension-preserving move policy and conflict-aware destination validation for sandboxed file lifecycle operations
- Composite file verification with MIME checks, extension allowlists, optional MIME allowlists, and optional checksum validation
- Optional baseline response security headers with fingerprinting header removal
- Rootless container runtime with a non-root user and minimal production dependency set

### DevSecOps

- GitHub Actions quality pipeline for formatting, linting, type checking, tests, Bandit, pip-audit, and Docker build validation
- Dedicated security workflow for Semgrep analysis and Trivy filesystem and image scanning
- Release workflow for strict semantic version tags, GHCR image publishing, pre-publish container scanning, and GitHub release assets
- Documentation publishing workflow for OpenAPI export, schema validation, Swagger UI site generation, and `gh-pages` publication
- Makefile-driven local workflow for dependency setup, CI execution, container builds, and deep security checks
- Pre-commit hooks for whitespace hygiene, YAML validation, formatting, linting, typing, security scanning, Dockerfile linting, and test execution
- Deterministic pytest suite with smoke, unit, integration, and security-focused coverage for middleware, routes, OpenAPI, settings, and file actions

### Documentation

- Comprehensive README covering architecture, security model, configuration, API usage, observability, and deployment
- Architecture reference describing application layers, middleware order, action dispatch, filesystem controls, and OpenAPI design
- Threat model documenting trust boundaries, attack surfaces, mitigations, and residual risks
- Testing guide covering unit, integration, smoke, and security-focused validation strategy
- CI and release guide describing workflows, Makefile targets, dependency sets, and release automation
- Development and contributing guides for local setup, tooling, workflow conventions, and project participation
- Security policy with private disclosure guidance and PGP key reference
- Scripts reference for shared-volume initialization, local port forwarding, OpenAPI export, and static docs publication

### Infrastructure

- Hardened Python 3.12 slim container image with OS package updates and runtime-only dependency installation
- Deterministic non-root UID and GID configuration for shared-volume compatibility
- Docker Compose deployment model with external network attachment, Docker secret mounting, and shared volume integration
- Container healthcheck using the internal `/health` endpoint for runtime readiness
- Environment-driven runtime configuration for sandbox boundaries, request limits, logging, docs exposure, and version metadata
- Separated runtime, testing, linting, security, and development dependency sets
- Helper scripts for OpenAPI export, versioned documentation site generation, shared-volume initialization, and localhost-only service forwarding

---

[0.1.0]: https://github.com/Libertocrat/star/releases/tag/v0.1.0
