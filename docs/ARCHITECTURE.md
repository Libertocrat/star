# STAR Architecture

## Table of Contents

- [1. System Overview](#1-system-overview)
- [2. Repository Structure](#2-repository-structure)
- [3. FastAPI Application Layer](#3-fastapi-application-layer)
- [4. Middleware Security Layer](#4-middleware-security-layer)
- [5. Action Execution Model](#5-action-execution-model)
- [6. Managed File and Filesystem Security Model](#6-managed-file-and-filesystem-security-model)
- [7. Configuration System](#7-configuration-system)
- [8. Observability and Metrics](#8-observability-and-metrics)
- [9. API Documentation System](#9-api-documentation-system)
- [10. Container Runtime Model](#10-container-runtime-model)
- [11. Testing Architecture](#11-testing-architecture)

## 1. System Overview

Secure Templated Actions Runtime (STAR) is a FastAPI-based secure automation runtime and constrained tool-execution boundary for workflows, AI agents, and low-code automations. It is typically deployed as an internal service and exposes a small authenticated execution surface together with a STAR-managed file service accessible through `/v1/files`.

The service is not a generic shell gateway. At startup, STAR discovers YAML-based Action DSL specifications, validates them, compiles them into immutable runtime `ActionSpec` objects, and stores them in an in-memory registry. At request time, clients can only execute those predeclared actions through `/v1/actions/{action_id}`.

An action is therefore best understood as predefined command execution, but with STAR controls around it:

- only DSL-declared binaries, args, flags, and outputs are accepted
- request params are validated against generated Pydantic models
- command rendering is deterministic and template-constrained
- sensitive action params use dedicated delivery handling instead of being rendered into argv
- binary policy checks are enforced both at build time and at execution time
- stdout and stderr are sanitized before they are returned, and file outputs may be materialized either from declared command placeholders or from sanitized stdout when `stdout_as_file` is `true` and allowed

STAR also exposes `/v1/files`, which provides the supported external lifecycle for uploaded and generated files. Storage is rooted under `STAR_ROOT_DIR`, and callers interact with UUID-based file identifiers rather than raw filesystem paths.

```mermaid
flowchart TD

subgraph Startup["Startup Phase (Build-Time)"]
    ActionDSLSpecs["Action DSL Specs"] --> DSLBuildEngine["DSL Build Engine"]
    DSLBuildEngine --> ActionRegistry["Action Registry"]
end

subgraph Runtime["Request Flow (Runtime)"]
    Client --> MiddlewareStack["Middleware Stack"]
    MiddlewareStack --> APIRoutes["API Routes"]
    APIRoutes --> ActionRegistry
    APIRoutes --> FileService["File Service"]
    ActionRegistry --> CommandRenderer["Command Renderer"]
    CommandRenderer --> ProcessExecutor["Process Executor"]
    ProcessExecutor --> OutputProcessor["Output Processor"]
end

FileService --> ManagedStorage["Managed Storage"]
OutputProcessor --> ManagedStorage
ManagedStorage --> STARRootDir["STAR_ROOT_DIR"]
```

## 2. Repository Structure

The main implementation lives under `src/star`.

| Path | Role |
| --- | --- |
| `src/star` | Application package containing the app factory, routes, middleware, shared core helpers, and the DSL-backed action system. |
| `src/star/actions/build_engine` | DSL spec discovery, YAML safety checks, semantic validation, and runtime action compilation. |
| `src/star/actions/runtime` | Runtime command rendering, subprocess execution, stdout/stderr sanitization, output handling, and file placeholder management. |
| `src/star/actions/presentation` | Public action catalog, request/response contract generation, and OpenAPI-facing serializers. |
| `src/star/actions/specs` | Built-in YAML action modules that define the shipped action catalog. |
| `src/star/middleware` | Authentication, request integrity, request ID, observability, rate limiting, timeout, and optional security headers. |
| `src/star/core` | Settings, errors, OpenAPI generation, storage utilities, security helpers, and shared response/file schemas. |
| `src/star/routes` | Thin HTTP handlers for `/v1/actions`, `/v1/files`, `/health`, and `/metrics`. |
| `tests` | Smoke, unit, and integration tests covering startup, settings, middleware, action build/runtime layers, file APIs, and OpenAPI behavior. |
| `scripts` | Helper scripts for OpenAPI export, docs site generation, and local port forwarding. |

Within `src/star/actions/runtime`, ownership stays split by execution phase. `renderer.py` resolves params and command templates, `secret_manager.py` creates and cleans invocation-owned secret files for file-delivered `secret` args, `executor.py` runs the rendered argv without a shell, `sanitizer.py` bounds and redacts subprocess output, and `outputs_builder.py` shapes declared outputs into response payloads or managed files.

## 3. FastAPI Application Layer

The application is built in `src/star/app.py` by `create_app()`.

### Application initialization

Key startup behaviors are:

- load `Settings` through `get_settings()` unless a test provides one explicitly
- create storage directories through `ensure_storage_dirs(settings)`
- register `/docs`, `/redoc`, and `/openapi.json` only when `star_enable_docs` is true
- build the immutable runtime action registry through `build_registry_from_specs(settings)`
- attach typed runtime dependencies (`settings` and `action_registry`) to `app.state`
- register middleware, exception handlers, and routers

The runtime uses a custom `FastAPI` subclass that overrides `openapi()` so the application can lazily build and cache a runtime-aware schema through `build_openapi_schema()`.

### Router registration

The app includes four route modules:

- `/v1/actions`: authenticated discovery, contract retrieval, and execution for DSL-defined actions
- `/v1/files`: STAR-managed upload, metadata retrieval, listing, content streaming, and deletion
- `/health`: readiness endpoint that returns `{"status": "ok"}` in the standard response envelope
- `/metrics`: Prometheus exposition endpoint

### Exception handling

Two global handlers are installed:

- `http_exception_handler` maps Starlette HTTP exceptions into STAR envelopes while preserving `X-Request-Id`
- `generic_exception_handler` logs unhandled exceptions and returns a generic structured 500 response

The route layer is intentionally thin. It resolves and type-validates runtime dependencies from application state, delegates to runtime or storage handlers, and maps domain exceptions to stable STAR error codes.

## 4. Middleware Security Layer

STAR applies several middleware layers in `src/star/middleware`. In `app.py`, middleware is added in reverse of the runtime execution order because Starlette runs the last added middleware first.

Actual runtime order:

1. `SecurityHeadersMiddleware` when `star_enable_security_headers` is enabled
2. `RequestIDMiddleware`
3. `ObservabilityMiddleware`
4. `RateLimitMiddleware`
5. `TimeoutMiddleware`
6. `RequestIntegrityMiddleware`
7. `AuthMiddleware`
8. Router handler

```mermaid
flowchart TD
Client --> SecurityHeadersOptional[SecurityHeaders optional]
SecurityHeadersOptional --> RequestID
RequestID --> Observability
Observability --> RateLimit
RateLimit --> Timeout
Timeout --> RequestIntegrity
RequestIntegrity --> Auth
Auth --> Router
```

If security headers are disabled, the pipeline starts at `RequestIDMiddleware`.

### `AuthMiddleware`

- Requires `Authorization: Bearer <token>` for protected endpoints.
- Uses `hmac.compare_digest()` for token comparison.
- Exempts `/health` and `/metrics`.
- Also exempts `/docs`, `/redoc`, and `/openapi.json` when runtime docs are enabled.
- Returns a 401 response envelope with `WWW-Authenticate: Bearer` on failure.

### `RequestIntegrityMiddleware`

- Operates at ASGI level.
- Rejects malformed request paths containing NUL bytes, backslashes, or disallowed control characters.
- Rejects malformed raw headers, including duplicate `Authorization` headers, whitespace in header names, and control characters in names or values.
- Rejects requests that contain both `Content-Length` and `Transfer-Encoding`.
- Enforces `application/json` for `POST /v1/actions/{action_id}` and `multipart/form-data` for `POST /v1/files`.
- Enforces maximum body size through strict `Content-Length` parsing or streaming body counting when the header is absent.
- Emits rejection metrics through `star_request_integrity_rejections_total`.

### `RateLimitMiddleware`

- Uses in-memory async-safe token buckets keyed by `request.client.host`.
- Enforces a process-local per-client requests-per-second limit from `star_rate_limit_rps`.
- Exempts `/metrics` and, when docs are enabled, the docs endpoints.
- Returns a structured 429 response with `Retry-After` when the bucket is empty.
- Emits `star_rate_limited_total` without client identity labels to keep metric cardinality bounded.

### `TimeoutMiddleware`

- Wraps downstream execution with `asyncio.wait_for()`.
- Uses `star_timeout_ms`, clamped to a minimum of 100 ms.
- Exempts `/health` and `/metrics`.
- Converts timeouts and cancellations to a standardized 504 response.
- Emits `star_timeouts_total`.

### `RequestIDMiddleware`

- Accepts a client-supplied `X-Request-Id` when it is a valid UUID.
- Otherwise generates a new UUID4.
- Stores the value on `request.state.request_id` for downstream consumers.
- Adds `X-Request-Id` to every response.

### `ObservabilityMiddleware`

- Records request telemetry without changing request or response behavior.
- Excludes `/metrics` from instrumentation by default.
- Tracks total requests, duration, inflight requests, and error-class totals.
- Wraps the ASGI `send` callable to capture the final HTTP status code.

### `SecurityHeadersMiddleware`

- Removes `Server` and `X-Powered-By` response headers.
- Sets baseline headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy`.
- Runs only when `star_enable_security_headers` is true.

## 5. Action Execution Model

The action system lives in `src/star/actions` and is split into build-time, presentation, and runtime layers.

### Build-time pipeline

At startup, `build_registry_from_specs()` performs the following steps:

1. `load_module_specs()` discovers YAML-based Action DSL specifications from the configured spec directories.
2. Loader safety checks reject invalid file sizes, invalid extensions, NUL bytes, disallowed control characters, and dangerous YAML patterns.
3. `validate_modules()` enforces semantic DSL rules such as module uniqueness, supported DSL version, binary declarations, identifier format, action structure, and command literal path policy.
4. `build_actions()` compiles validated modules into immutable runtime `ActionSpec` objects with generated `params_model` classes, command templates, defaults, output declarations, stdout file policy, and binary execution policy.
5. `ActionRegistry` stores the final action mapping and precomputes presentation summaries.

This is the allowlist boundary. If a spec is invalid, the registry is not built and the application fails to start.

### Runtime execution path

`POST /v1/actions/{action_id}` eventually reaches `dispatch_action()`, which performs the runtime flow:

1. Resolve the action from `ActionRegistry`.
2. Validate request params and execution options, including `stdout_as_file` policy checks.
3. Render the final argv list and any internal sensitive delivery payloads with `render_command()`.
4. Resolve `file_id` args and output placeholders through the managed file layer.
5. Re-check binary policy and execute the argv with `asyncio.create_subprocess_exec()` in `execute_command()`, using the configured runtime timeout and POSIX process-group cleanup where supported.
6. Process stdout and stderr through the output pipeline, including sanitization, declared command-output handling, and optional `stdout_file` materialization from sanitized stdout.

```mermaid
flowchart LR

subgraph BuildTime["Build-Time Pipeline"]
    Specs["Action DSL Specs"] --> Loader
    Loader --> Validator
    Validator --> Builder
    Builder --> Registry["Action Registry"]
    Registry --> Presentation["Presentation Layer"]
end

subgraph Runtime["Runtime Execution"]
    Registry --> Dispatcher
    Dispatcher --> CommandRenderer["Command Renderer"]
    CommandRenderer --> ProcessExecutor["Process Executor"]
    ProcessExecutor --> OutputProcessor["Output Processor"]
end

OutputProcessor --> ManagedStorage["Managed Storage"]
```

### What an action means in STAR

An action is not arbitrary shell submitted by the client.

An action is a predeclared command template whose binary, accepted params, flag mapping, output declarations, and public contract are all defined in YAML and compiled before the service accepts traffic. Clients only provide values for the declared parameter surface.

In STAR, an action is safer than direct command execution because the command shape is frozen by the DSL and enforced by validation, rendering, policy checks, and response sanitization.

### Sensitive action parameters

The DSL supports `secret` args for request values such as passphrases that must remain strings at the API boundary but must not be treated as ordinary argv text. A `secret` arg is required, cannot define a default, and must declare an internal delivery policy. Supported internal delivery sinks write the secret to subprocess stdin or materialize it as an invocation-owned temporary file reference, while keeping delivery details out of public action contracts.

Build-time validation rejects unsafe `secret` usage, including direct argv rendering unless the arg uses file delivery. For `delivery: file`, direct arg references and command-literal placeholders such as `file:{password}` expand to the temporary file path, not the secret value. Runtime rendering repeats those checks as defense in depth, produces argv without the secret value, and stores only invocation-local stdin bytes, temporary secret-file ownership, and redaction values for the output sanitizer. The executor still runs the command without a shell; `stdin=PIPE` is used only when the rendered action has stdin data, otherwise stdin is connected to `DEVNULL`.

Public action contracts expose the parameter as `type: secret` with password-oriented metadata and a safe example placeholder. They do not expose the internal delivery policy.

## 6. Managed File and Filesystem Security Model

STAR supports two related file surfaces:

- the external managed file API under `/v1/files`
- internal filesystem security helpers used by runtime storage and security-sensitive operations

### Managed file API

`src/star/routes/files/router.py` exposes the supported external file lifecycle:

- `POST /v1/files` uploads a file, validates it, and persists blob plus metadata
- `GET /v1/files` lists files with cursor pagination and filtering
- `GET /v1/files/{id}` returns metadata only
- `GET /v1/files/{id}/content` streams persisted blob content
- `DELETE /v1/files/{id}` deletes a managed file

The file API is UUID-based. Clients do not provide raw filesystem paths to retrieve stored content. Uploaded files are persisted as immutable blobs with metadata sidecars under storage rooted at `STAR_ROOT_DIR`.

`FileMetadata` is owned by `src/star/core/schemas/files.py` because the same validated model crosses persistence, storage, action-runtime, and public response boundaries. File route schemas re-export that model for compatibility, but reusable core helpers and action runtime code import it from `core`.

Action outputs can also be materialized into STAR-managed storage. Declared `file + command` outputs use runtime placeholders created before subprocess execution and finalized into managed file records after successful output handling. Sanitized stdout can also be materialized into the reserved `outputs.stdout_file` entry when the client requests `stdout_as_file=true` and the selected action allows it.

### Storage layout

STAR keeps service-owned runtime data under `STAR_ROOT_DIR/data/` and separates persistent managed files from invocation-local runtime artifacts:

| Path | Owner | Purpose |
| --- | --- | --- |
| `data/files/blobs/` | Managed file storage | Immutable uploaded files and materialized action-output blobs exposed through `/v1/files` by UUID. |
| `data/files/meta/` | Managed file storage | Metadata sidecars for managed file records. |
| `data/files/tmp/` | Managed file storage | Staging area for upload and managed-file write workflows before atomic promotion. |
| `data/runtime/secrets/` | Action runtime | Invocation-owned temporary files used only for file-delivered `secret` args, cleaned after render failure, success, subprocess failure, timeout, or cancellation. |

### Filesystem security primitives

Lower-level path protections exist in `src/star/core/security/paths.py` and related helpers.

`sanitize_rel_path()` rejects:

- NUL bytes
- backslashes
- control characters
- empty paths
- absolute paths
- `..` traversal segments
- excessively long paths

`resolve_in_sandbox()` then:

- resolves the configured sandbox root strictly
- rejects symlinks in any existing path component
- normalizes the candidate path with `os.path.normpath()`
- verifies the final candidate stays inside the sandbox with `os.path.commonpath()`

`safe_open_no_follow()` opens the final component with `O_NOFOLLOW` when the platform supports it and verifies that the target is a regular file.

These helpers remain relevant because STAR still treats `STAR_ROOT_DIR` as a hardened storage boundary, even though the public API prefers managed `file_id` references over direct path exposure.

## 7. Configuration System

Configuration is defined in `src/star/core/config.py` with a Pydantic `BaseSettings` model.

> [!IMPORTANT]
> `STAR_ROOT_DIR` must be configured before STAR can start. If this value is missing or invalid, configuration loading aborts the process. Its default and recommended value is `/var/lib/star`

### Loading behavior

- Settings are loaded lazily through `get_settings()` and cached with `lru_cache`.
- `.env` is used as an environment file source.
- Environment variable matching is case-insensitive.
- Unrelated environment variables are ignored.

### Required and validated settings

Required settings include:

- `STAR_ROOT_DIR`

Validated runtime controls include:

- `STAR_MAX_FILE_BYTES`
- `STAR_MAX_YML_BYTES`
- `STAR_TIMEOUT_MS`
- `STAR_RATE_LIMIT_RPS`
- `STAR_APP_VERSION`
- `STAR_ENABLE_DOCS`
- `STAR_ENABLE_SECURITY_HEADERS`
- `STAR_BLOCKED_BINARIES_EXTRA`

### API token loading

The API token is not read directly from the settings model. `get_settings()` calls `load_star_api_token()`, which loads the token from `/run/secrets/star_api_token`. If that secret file is missing, the code falls back to `STAR_API_TOKEN_DEV` for development use.

`validate_api_token()` trims the token and enforces:

- minimum length of 32 characters
- at least two character classes among lowercase, uppercase, digits, and symbols

### `.env.example`

`.env.example` documents the expected runtime configuration for:

- non-root container identity
- Docker and Compose integration
- strict sandboxed storage root location
- body size, timeout, and rate-limit controls
- logging and application version
- docs toggle and security-header toggle

## 8. Observability and Metrics

Observability is implemented in `src/star/middleware/observability.py` and exposed by `src/star/routes/metrics.py`.

### Prometheus exposure

`/metrics` returns the output of `prometheus_client.generate_latest()` with Prometheus's content type. The route is intentionally small and does not build metrics itself.

### Request instrumentation

The observability middleware exports:

- `star_http_requests_total` labeled by method, normalized path, and status code
- `star_http_request_duration_seconds` labeled by method, normalized path, and status class
- `star_http_inflight_requests`
- `star_http_errors_total` labeled by status class

Additional middleware-specific metrics are also part of the exported registry:

- `star_request_integrity_rejections_total`
- `star_rate_limited_total`
- `star_timeouts_total`

Paths are normalized before labeling so the metrics layer can aggregate traffic consistently and reduce cardinality.

```mermaid
flowchart TD
Request --> ObservabilityMiddleware
ObservabilityMiddleware --> PrometheusRegistry
PrometheusRegistry --> MetricsRoute
MetricsRoute --> Scraper
```

## 9. API Documentation System

STAR generates OpenAPI dynamically from the live application, the runtime action registry, and the file route contracts.

### Runtime schema generation

`src/star/core/openapi.py` starts with FastAPI's `get_openapi()` output and then patches it to match STAR runtime behavior. The builder:

- adds tags and external documentation
- injects a global bearer authentication scheme
- marks `/health` and `/metrics` as public in the OpenAPI document
- registers shared schemas such as `ResponseEnvelope` and `ErrorInfo`
- enriches `POST /v1/actions/{action_id}` with action-specific examples and runtime response variants
- marks secret params as sensitive password inputs while keeping internal delivery policy out of public examples and schemas
- documents the public contracts for `GET /v1/actions` and `GET /v1/actions/{action_id}`
- applies explicit `/v1/files` contract overrides for upload, metadata retrieval, listing, content streaming, and delete operations
- adds STAR response headers such as `X-Request-Id` and `Retry-After`
- removes internal-only schemas from the published document
- overrides the generated contracts for `/health` and `/metrics`

The docs endpoints `/docs`, `/redoc`, and `/openapi.json` are controlled by `star_enable_docs` in `app.py`. Source-tree contributor deployments commonly leave them disabled unless they are actively needed, while the packaged `deploy/star` flow enables them by default for local exploration and `--production` flips that generated default for new runtime configs.

### Export pipeline

`scripts/export_openapi.py` creates a documentation-specific `Settings` object, enables docs, builds the app, generates the schema, and writes `docs/api-docs/output/openapi.json`.

`scripts/build_docs_site.py` takes that exported schema, copies a Swagger UI distribution into a versioned site directory, installs the project template as `index.html`, and creates redirects for the latest published version.

### CI publication

The repository contains a dedicated GitHub Actions workflow in `.github/workflows/release-docs.yml` that runs on version tags. It exports the OpenAPI schema, validates it, builds the versioned documentation site, and publishes it to the `gh-pages` branch.

## 10. Container Runtime Model

The source-tree contributor runtime is defined by `Dockerfile` and `docker-compose.yml`. Release packages also ship the guided deploy wrapper at `deploy/star` together with runtime assets under `deploy/star-runtime/`.

### Docker image

The image:

- uses `python:3.12-slim`
- installs `ca-certificates`, `curl`, and `libmagic1`
- creates a deterministic non-root user and group from build args
- installs runtime Python dependencies from `requirements/runtime.txt`
- copies the application source into `/app`
- removes group and other write permissions from `/app`
- starts Uvicorn with `uvicorn --factory star.app:create_app`
- exposes `STAR_PORT`
- runs a healthcheck against `http://localhost:${STAR_PORT}/health`

### Compose service model

The Compose service:

- runs the ephemeral `star-init` helper service before `star` starts
- builds the image from the repository Dockerfile
- passes container identity build args (`STAR_CONTAINER_USER`, `STAR_CONTAINER_GROUP`, `STAR_CONTAINER_UID`, `STAR_CONTAINER_GID`)
- loads environment variables from `.env`
- mounts the persistent volume at `${STAR_ROOT_DIR}`
- injects the API token through the `star_api_token` Docker secret backed by `./secrets/star_api_token.txt`
- attaches the service to an external Docker network named by `STAR_SHARED_NETWORK`
- publishes STAR to the host using `STAR_HOST_BIND_ADDRESS:STAR_HOST_PORT:STAR_PORT`
- restarts with `unless-stopped`

`star-init` creates the root directory, assigns ownership to the non-root runtime user, and normalizes directory and file permissions on the mounted storage volume before the API service starts.

This matches the intended internal-service deployment model: STAR is meant to be reachable from other trusted containers on the shared network, not from a public edge.

## 11. Testing Architecture

Testing is organized by scope:

- `tests/test_app_smoke.py` covers basic application startup and health behavior.
- `tests/actions` covers registry construction, DSL loader and validator behavior, runtime dispatch, presentation helpers, and execution-related slices.
- `tests/core` covers schemas, settings, OpenAPI helpers, and security utilities.
- `tests/integration/middleware` exercises middleware behavior end to end.
- `tests/integration/routes` exercises route-level behavior for `/v1/actions`, `/v1/files`, `/health`, `/metrics`, and `/openapi.json`.

The test layout separates unit-level validation of the DSL and security primitives from integration-level checks of the HTTP surface and generated documentation.
