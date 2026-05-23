"""OpenAPI schema construction helpers for runtime-generated STAR contracts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

import star.core.errors as errors
from star.actions.models.presentation import ActionPublicSpec
from star.actions.presentation.catalog import filter_modules
from star.actions.presentation.serializers import (
    modules_to_response,
    to_action_public_spec,
)
from star.actions.registry import ActionRegistry
from star.core.errors import PUBLIC_HTTP_ERRORS, ErrorDef
from star.core.schemas.envelope import ErrorInfo, ResponseEnvelope
from star.routes.actions.schemas import ExecuteActionData, ExecuteActionRequest

# Define explicit response contract overrides for endpoints that cannot be correctly
# inferred from FastAPI's default schema generation
RESPONSE_CONTRACT_OVERRIDES = {
    "/metrics": {
        "method": "get",
        "responses": {
            "200": {
                "description": "Prometheus metrics snapshot",
                "content": {
                    "text/plain": {
                        "schema": {
                            "type": "string",
                            "example": (
                                "# HELP http_requests_total ...\n"
                                "# TYPE http_requests_total counter\n"
                                "http_requests_total 42\n"
                                "# HELP ...\n"
                            ),
                        }
                    }
                },
            }
        },
    },
    "/health": {
        "method": "get",
        "responses": {
            "200": {
                "description": "Health success response",
                "content": {
                    "application/json": {
                        "schema": {
                            "example": {
                                "success": True,
                                "error": None,
                                "data": {"status": "ok"},
                            },
                        }
                    }
                },
            }
        },
    },
}

# Defines the set of STAR error conditions that may be returned by
# global middleware layers (e.g. authentication, rate limiting, timeout).
#
# These errors are not tied to specific route handlers and must be
# injected into all protected operations in the OpenAPI schema.
#
# IMPORTANT:
# - Do not include handler-specific errors here.
# - Errors listed here should originate exclusively from middleware.
# - Public endpoints (e.g. `/health`, `/metrics`) are excluded at runtime.
MIDDLEWARE_ERROR_MAP = [
    errors.UNAUTHORIZED,
    errors.RATE_LIMITED,
    errors.TIMEOUT,
    errors.FILE_TOO_LARGE,
    errors.INVALID_REQUEST,
]


def build_openapi_schema(app: FastAPI) -> dict[str, Any]:
    """Build the STAR OpenAPI document with runtime-aware patches.

    This function generates the base OpenAPI schema from FastAPI routes
    and then enriches it with STAR-specific runtime constraints derived
    from middleware behavior and the dynamic action registry.

    The function is fully self-contained and depends only on the `app`
    instance passed as argument. It does not rely on any global state.

    Args:
        app: FastAPI (or STARApp) instance.

    Returns:
        Cached OpenAPI schema dictionary.
    """

    # If already generated and cached, reuse it
    if app.openapi_schema:
        return app.openapi_schema

    # Generate base schema from FastAPI's route inspection
    schema: dict[str, Any] = get_openapi(
        title=app.title,
        version=app.version,
        description=getattr(app, "description", None),
        routes=app.routes,
    )

    info = schema.setdefault("info", {})
    info["contact"] = getattr(app, "contact", None)
    info["license"] = getattr(app, "license_info", None)

    schema["tags"] = [
        {
            "name": "Actions",
            "description": "Discover and execute DSL-defined actions.",
        },
        {
            "name": "Files",
            "description": "Upload and manage persisted files.",
        },
        {
            "name": "Observability",
            "description": "System health checks and Prometheus metrics endpoints.",
            "externalDocs": {
                "description": "Prometheus official documentation",
                "url": "https://prometheus.io/docs/",
            },
        },
    ]

    schema["externalDocs"] = {
        "description": "Project repository and architectural documentation",
        "url": "https://github.com/Libertocrat/star",
    }

    # Apply STAR-specific patches in stable order.
    # Order matters: security first, then endpoint-level patches,
    # then response header enrichment.
    _patch_custom_schemas(schema)
    _inject_security(schema)
    _patch_public_endpoints(schema)
    _patch_execute_contract(schema, app)
    _patch_files_contract(schema)
    _patch_actions_list_contract(schema, app)
    _patch_actions_get_contract(schema)
    _inject_middleware_errors(schema, MIDDLEWARE_ERROR_MAP)
    _replace_default_422(schema)
    _inject_response_headers(schema)
    _prune_internal_schemas(schema)
    _apply_response_contract_overrides(schema, RESPONSE_CONTRACT_OVERRIDES)

    # Cache on the same app instance (no globals involved)
    app.openapi_schema = schema
    return schema


def _register_model(
    model: type[BaseModel],
    schemas: dict[str, Any],
    nested: bool = False,
) -> None:
    """Register a Pydantic model and optionally register nested schemas.

    Args:
        model: Pydantic model to register under components.schemas.
        schemas: Components schema registry dictionary.
        nested: Set to True to recurse through `$defs`.
    """

    name = model.__name__

    if name in schemas:
        return

    model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    nested_defs = model_schema.pop("$defs", {})

    schemas[name] = model_schema

    if not nested or not nested_defs:
        return

    def _register_defs(defs: dict[str, Any]) -> None:
        """Recursively register nested `$defs` under components schemas."""

        for nested_name, nested_schema in defs.items():
            if nested_name in schemas:
                continue

            schemas[nested_name] = nested_schema
            child_defs = nested_schema.get("$defs", {})
            if child_defs:
                _register_defs(child_defs)

    _register_defs(nested_defs)


# ---------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------


def _inject_security(schema: dict[str, Any]) -> None:
    """Inject global bearer-auth requirements into the OpenAPI schema.

    Args:
        schema: Mutable OpenAPI schema document.
    """

    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})

    security_schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "Opaque token",
        "description": "Send Authorization: Bearer <STAR_API_TOKEN>",
    }

    # Set secure-by-default contract once, then carve out explicit public
    # endpoints in `_patch_public_endpoints` to match middleware behavior.
    schema["security"] = [{"BearerAuth": []}]


def _patch_public_endpoints(schema: dict[str, Any]) -> None:
    """Mark public endpoints as unauthenticated in OpenAPI.

    Args:
        schema: Mutable OpenAPI schema document.
    """

    paths = schema.get("paths", {})
    for path, methods in paths.items():
        if path.startswith("/health") or path.startswith("/metrics"):
            for op in methods.values():
                op["security"] = []
                op["tags"] = ["Observability"]

        if path.startswith("/metrics"):
            op["externalDocs"] = {
                "description": "Prometheus scraping documentation",
                "url": (
                    "https://prometheus.io/docs/prometheus/latest/"
                    "configuration/configuration/#scrape_config"
                ),
            }


# ---------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------


def _inject_response_headers(schema: dict[str, Any]) -> None:
    """Inject standard STAR response headers into every HTTP operation.

    Args:
        schema: Mutable OpenAPI schema document.
    """

    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            responses = operation.setdefault("responses", {})

            for code, response in responses.items():
                headers = response.setdefault("headers", {})

                headers["X-Request-Id"] = {
                    "description": "Request correlation identifier.",
                    "schema": {"type": "string", "format": "uuid"},
                }

                if code == "429":
                    headers["Retry-After"] = {
                        "description": "Seconds to wait before retrying.",
                        "schema": {"type": "string", "pattern": "^[0-9]+$"},
                    }


# ---------------------------------------------------------------------
# /v1/files contracts
# ---------------------------------------------------------------------


def _patch_files_contract(schema: dict[str, Any]) -> None:
    """Apply STAR OpenAPI contract overrides for `/v1/files` endpoints.

    This function defines and injects the OpenAPI response contract for
    file-related operations under the `/v1/files` path. It encapsulates
    all domain-specific knowledge for this endpoint, including:

    - The set of STAR error conditions that may be raised by the handler
      (`ingest_uploaded_file`)
    - A canonical success response example aligned with the
      `ResponseEnvelope[FileMetadata]` structure

    The function delegates the actual schema mutation to
    `_patch_operation_contract`, ensuring consistent behavior across
    all endpoints while keeping domain configuration localized.

    Notes:
        - Only handler-level errors are included here. Middleware-derived
          errors (e.g. authentication, rate limiting) are injected separately
          via `_inject_middleware_errors(...)`.
        - The success example overrides FastAPI-generated examples to provide
          deterministic and meaningful documentation.
        - This function is designed to scale as additional methods
          (GET, DELETE, etc.) are added to `/v1/files`.

    Args:
        schema: Mutable OpenAPI schema document to be patched in-place.

    Returns:
        None.
    """

    FILES_POST_ERRORS = [
        errors.INVALID_REQUEST,
        errors.INVALID_ALGORITHM,
        errors.FILE_EXTENSION_MISSING,
        errors.MIME_MAPPING_NOT_DEFINED,
        errors.FILE_TOO_LARGE,
        errors.UNSUPPORTED_MEDIA_TYPE,
        errors.INTERNAL_ERROR,
    ]

    FILES_POST_SUCCESS_EXAMPLE = {
        "success": True,
        "error": None,
        "data": {
            "file": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "original_filename": "document.pdf",
                "stored_filename": "file_<uuid>.bin",
                "mime_type": "application/pdf",
                "extension": ".pdf",
                "size_bytes": 1024,
                "sha256": "abc123...",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "status": "ready",
            }
        },
    }

    _patch_operation_contract(
        schema,
        path="/v1/files",
        method="post",
        errors=FILES_POST_ERRORS,
        success_example=FILES_POST_SUCCESS_EXAMPLE,
    )

    FILES_LIST_ERRORS = [
        errors.INVALID_REQUEST,
        errors.INTERNAL_ERROR,
    ]

    FILES_LIST_SUCCESS_EXAMPLE = {
        "success": True,
        "error": None,
        "data": {
            "files": [],
            "pagination": {
                "count": 0,
                "next_cursor": None,
            },
        },
    }

    _patch_operation_contract(
        schema,
        path="/v1/files",
        method="get",
        errors=FILES_LIST_ERRORS,
        success_example=FILES_LIST_SUCCESS_EXAMPLE,
    )

    FILES_GET_ERRORS = [
        errors.FILE_NOT_FOUND,
        errors.INVALID_REQUEST,
        errors.INTERNAL_ERROR,
    ]

    FILES_GET_SUCCESS_EXAMPLE = {
        "success": True,
        "error": None,
        "data": {
            "file": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "original_filename": "example.txt",
                "stored_filename": "file_<uuid>.bin",
                "mime_type": "text/plain",
                "extension": ".txt",
                "size_bytes": 123,
                "sha256": (
                    "8e9aa02fb68dfb526d787f6b66adda7b651dd3f9f3b4a03e266d466161f4c39e"
                ),
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "status": "ready",
            }
        },
    }

    _patch_operation_contract(
        schema,
        path="/v1/files/{id}",
        method="get",
        errors=FILES_GET_ERRORS,
        success_example=FILES_GET_SUCCESS_EXAMPLE,
    )

    FILES_DELETE_ERRORS = [
        errors.FILE_NOT_FOUND,
        errors.INVALID_REQUEST,
        errors.INTERNAL_ERROR,
    ]

    FILES_DELETE_SUCCESS_EXAMPLE = {
        "success": True,
        "error": None,
        "data": {
            "file": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "deleted": True,
            }
        },
    }

    _patch_operation_contract(
        schema,
        path="/v1/files/{id}",
        method="delete",
        errors=FILES_DELETE_ERRORS,
        success_example=FILES_DELETE_SUCCESS_EXAMPLE,
    )

    FILES_CONTENT_GET_ERRORS = [
        errors.FILE_NOT_FOUND,
        errors.INVALID_REQUEST,
        errors.INTERNAL_ERROR,
    ]

    _patch_operation_contract(
        schema,
        path="/v1/files/{id}/content",
        method="get",
        errors=FILES_CONTENT_GET_ERRORS,
    )

    files_content_operation = (
        schema.get("paths", {}).get("/v1/files/{id}/content", {}).get("get")
    )
    if files_content_operation:
        files_content_responses = files_content_operation.setdefault("responses", {})
        files_content_responses["200"] = {
            "description": "Streamed file content.",
            "content": {
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                }
            },
        }


def _patch_actions_get_contract(schema: dict[str, Any]) -> None:
    """Enhance GET /v1/actions/{action_id} contract."""

    actions_get_errors = [
        errors.ACTION_NOT_FOUND,
        errors.INVALID_PARAMS,
        errors.INTERNAL_ERROR,
    ]

    actions_get_success_example = {
        "success": True,
        "error": None,
        "data": {
            "action": "ping",
            "action_id": "test_runtime.ping",
            "summary": "Ping",
            "description": "Return deterministic hello output",
            "tags": ["test", "runtime", "health", "smoke_test"],
            "allow_stdout_as_file": True,
            "args": [],
            "flags": [],
            "outputs": [],
            "params_contract": {},
            "params_example": {},
            "response_contract": {},
            "response_example": {},
        },
    }

    _patch_operation_contract(
        schema,
        path="/v1/actions/{action_id}",
        method="get",
        errors=actions_get_errors,
        success_example=actions_get_success_example,
    )


def _patch_actions_list_contract(schema: dict[str, Any], app: FastAPI) -> None:
    """Enhance GET /v1/actions contract and query parameter docs."""

    actions_list_errors: list[ErrorDef] = [
        errors.INVALID_PARAMS,
        errors.INTERNAL_ERROR,
    ]

    actions_list_success_example = cast(
        dict[str, Any],
        {
            "success": True,
            "error": None,
            "data": {
                "modules": [],
            },
        },
    )

    registry = getattr(app.state, "action_registry", None)
    if isinstance(registry, ActionRegistry):
        module_summaries = registry.module_summaries

        # Prefer a realistic crypto/hash discovery slice for docs examples.
        sampled = filter_modules(module_summaries, q="sha256")
        if not sampled:
            sampled = module_summaries[:1]

        actions_list_success_example["data"] = modules_to_response(sampled)

    _patch_operation_contract(
        schema,
        path="/v1/actions",
        method="get",
        errors=actions_list_errors,
        success_example=actions_list_success_example,
    )

    operation = schema.get("paths", {}).get("/v1/actions", {}).get("get")
    if not operation:
        return

    operation["description"] = (
        "Discover registered actions grouped by module.\n\n"
        "Query parameters:\n"
        "- `q`: Optional free-text search over action name, summary, "
        "description, and effective tags.\n"
        "- `tags`: Optional CSV tag filter, for example "
        "`hashing,checksum`. Tokens are trimmed, normalized to lowercase, "
        "and deduplicated.\n"
        "- `match`: Optional tag matching mode: `any` or `all`.\n\n"
        "Behavior:\n"
        "- If `tags` is provided and `match` is omitted, matching defaults "
        "to `any`.\n"
        "- Providing `match` without `tags` returns `INVALID_PARAMS`.\n"
        "- When `q` and `tags` are both present, filters are combined with "
        "logical AND."
    )

    parameters = operation.get("parameters", [])
    parameters_by_name = {
        parameter.get("name"): parameter
        for parameter in parameters
        if isinstance(parameter, dict)
    }

    q_param = parameters_by_name.get("q")
    if q_param is not None:
        q_param["description"] = (
            "Optional free-text filter over action name, summary, "
            "description, and effective tags."
        )
        q_schema = q_param.setdefault("schema", {"type": "string"})
        q_schema["type"] = "string"
        q_schema["example"] = "sha256"

    tags_param = parameters_by_name.get("tags")
    if tags_param is not None:
        tags_param["description"] = (
            "Optional CSV tags filter. Example: `hashing,checksum`. "
            "Tokens are trimmed, lowercased, and deduplicated."
        )
        tags_schema = tags_param.setdefault("schema", {"type": "string"})
        tags_schema["type"] = "string"
        tags_schema["example"] = "hashing,checksum"

    match_param = parameters_by_name.get("match")
    if match_param is not None:
        match_param["description"] = (
            "Optional tag match mode. Allowed: `any` or `all`. "
            "Requires `tags`; defaults to `any` when omitted."
        )
        match_schema = match_param.setdefault("schema", {"type": "string"})
        match_schema["type"] = "string"
        match_schema["enum"] = ["any", "all"]
        match_schema["default"] = "any"
        match_schema["example"] = "all"


# ---------------------------------------------------------------------
# /v1/actions/{action_id} dynamic contract
# ---------------------------------------------------------------------


def _build_action_request_markdown(public_spec: ActionPublicSpec) -> str:
    """Build a markdown description block for one action request example.

    Args:
        public_spec: Public action spec containing rendered contracts/examples.

    Returns:
        Markdown section describing args and flags.
    """

    description = (
        public_spec.description or public_spec.summary or "No description provided."
    )
    params_contract = public_spec.params_contract.get("params", {})

    lines = [
        "",
        description,
        "",
        "#### Args",
        "",
    ]

    if public_spec.args:
        for arg in public_spec.args:
            arg_name = str(arg.get("name", ""))
            contract_arg = params_contract.get(arg_name, {})
            arg_type_display = contract_arg.get("type", arg.get("type", "unknown"))
            arg_line = f"- `{arg_name}` (`{arg_type_display}`): "
            details: list[str] = []

            description_value = arg.get("description")
            if description_value:
                details.append(str(description_value))

            if bool(arg.get("required", False)):
                details.append("**\\*required**")
            else:
                details.append(
                    "default: "
                    f"`{_format_openapi_markdown_value(arg.get('default'))}`"
                )

            arg_line += "; ".join(details) if details else "No details."
            lines.append(arg_line)
    else:
        lines.append("- _No args_")

    lines.extend(["", "#### Flags", ""])
    if public_spec.flags:
        for flag in public_spec.flags:
            flag_name = str(flag.get("name", ""))
            flag_description = str(flag.get("description") or "")
            flag_default = _format_openapi_markdown_value(flag.get("default"))
            lines.append(
                f"- `{flag_name}`: {flag_description}; default: `{flag_default}`"
            )
    else:
        lines.append("- _No flags_")

    if public_spec.allow_stdout_as_file:
        stdout_as_file_spec = public_spec.params_contract["stdout_as_file"]
        lines.extend(["", "#### Request Options", ""])
        lines.append(
            "- `stdout_as_file` (`bool`): Store sanitized stdout as "
            "`outputs.stdout_file` when enabled and allowed by the action; "
            "default: "
            f"`{str(stdout_as_file_spec.get("default", False)).lower()}`;"
        )

    return "\n".join(lines)


def _build_action_response_markdown(public_spec: ActionPublicSpec) -> str:
    """Build a markdown description block for one action response example.

    Args:
        public_spec: Public action spec containing rendered contracts/examples.

    Returns:
        Markdown section describing outputs.
    """

    lines = [
        "",
        "#### Outputs",
        "",
    ]

    if public_spec.outputs:
        for output in public_spec.outputs:
            output_name = str(output.get("name", ""))
            output_type = str(output.get("type", "unknown"))
            output_type = "FileMetadata" if output_type == "file" else output_type
            output_description = str(output.get("description") or "")
            output_source = str(output.get("source") or "unknown")
            lines.append(
                f"- `{output_name}` (`{output_type}`): "
                f"{output_description}; source: `{output_source}`"
            )

    if public_spec.allow_stdout_as_file:
        lines.append(
            "- `stdout_file` (`FileMetadata`): Managed text file created from "
            "sanitized stdout when `stdout_as_file=true`; source: `stdout`; "
            "reserved output name"
        )

    if not public_spec.outputs and not public_spec.allow_stdout_as_file:
        lines.append("- _No outputs_")

    return "\n".join(lines)


def _format_openapi_markdown_value(value: Any) -> str:
    """Format a value as markdown-friendly literal text.

    Args:
        value: Runtime value to serialize for docs.

    Returns:
        String representation suitable for markdown inline code.
    """

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def _patch_execute_contract(schema: dict[str, Any], app: FastAPI) -> None:
    """Patch `POST /v1/actions/{action_id}` runtime OpenAPI contract.

    This function dynamically enriches the `/v1/actions/{action_id}` operation using
    the action registry by generating request/response variants, providing
    rich examples, and annotating middleware-driven behavior.

    Args:
        schema: Mutable OpenAPI schema document.
        app: FastAPI application used to derive runtime metadata.
    """

    paths = schema.get("paths", {})
    execute = paths.get("/v1/actions/{action_id}")
    if not execute:
        return

    post = execute.get("post")
    if not post:
        return

    registry = getattr(app.state, "action_registry", None)
    if not isinstance(registry, ActionRegistry):
        return

    components = schema.setdefault("components", {})
    schemas_section = components.setdefault("schemas", {})

    # ------------------------------------------------------------------
    # 1. Ensure all action models are registered in components.schemas
    # ------------------------------------------------------------------
    _register_model(ExecuteActionRequest, schemas_section, nested=True)
    _register_model(ExecuteActionData, schemas_section, nested=True)

    public_specs: dict[str, ActionPublicSpec] = {}
    for name in registry.list_names():
        spec = registry.get(name)
        _register_model(spec.params_model, schemas_section, nested=True)
        public_specs[name] = to_action_public_spec(spec)

    # ------------------------------------------------------------------
    # 2. Build request examples (selected action now comes from path)
    # ------------------------------------------------------------------

    request_examples = {}

    for name in registry.list_names():
        public_spec = public_specs[name]
        request_markdown = _build_action_request_markdown(public_spec)
        action_summary = (
            public_spec.summary or public_spec.description or "Execute action"
        )

        request_value: dict[str, Any] = {"params": public_spec.params_example}
        if public_spec.allow_stdout_as_file:
            request_value["stdout_as_file"] = False

        request_examples[name] = {
            "summary": f"{name}: {action_summary}",
            "description": request_markdown,
            "value": request_value,
        }

    request_body = post.setdefault("requestBody", {})
    content = request_body.setdefault("content", {})
    app_json = content.setdefault("application/json", {})

    app_json["schema"] = {
        "$ref": "#/components/schemas/ExecuteActionRequest",
    }

    request_body["description"] = (
        "Request body containing action-specific `params` and request-level "
        "execution options.\n\n"
        "The target action is selected via the `action_id` path parameter. "
        "Use `stdout_as_file=true` to store sanitized stdout as "
        "`outputs.stdout_file` when the selected action allows it. "
        "Select an example below to inspect parameter contracts per action."
    )

    app_json["examples"] = request_examples

    # ------------------------------------------------------------------
    # 3. Build response 200 with dynamic result oneOf
    # ------------------------------------------------------------------

    response_examples = {}

    for name in registry.list_names():
        public_spec = public_specs[name]
        response_markdown = _build_action_response_markdown(public_spec)

        response_examples[name] = {
            "summary": f"Response for: {name}",
            "description": response_markdown,
            "value": public_spec.response_example,
        }

    responses = post.setdefault("responses", {})
    response_200 = responses.setdefault("200", {})
    response_200_content = response_200.setdefault("content", {})
    response_200_json = response_200_content.setdefault("application/json", {})

    response_200_json["examples"] = response_examples

    # ------------------------------------------------------------------
    # 4. Explicit error responses
    # ------------------------------------------------------------------

    errors_by_status: dict[int, list[ErrorDef]] = defaultdict(list)

    # Dynamically build error responses from centralized error definitions.
    for err in PUBLIC_HTTP_ERRORS:
        errors_by_status[err.http_status].append(err)

    for status, error_defs in errors_by_status.items():
        response = responses.setdefault(str(status), {"description": f"{status} error"})

        content = response.setdefault("content", {})
        json_content = content.setdefault("application/json", {})

        error_examples = {}

        for err in error_defs:
            error_examples[err.code] = {
                "summary": err.code,
                "value": {
                    "success": False,
                    "error": {
                        "code": err.code,
                        "message": err.default_message,
                    },
                    "data": None,
                },
            }

        json_content["examples"] = error_examples

    # ------------------------------------------------------------------
    # 5. Inject dynamic description listing actions
    # ------------------------------------------------------------------

    action_lines = []
    for name in registry.list_names():
        public_spec = public_specs[name]
        label = f"- `{name}`"
        if public_spec.summary:
            label += f": {public_spec.summary}"
        action_lines.append(label)

    dynamic_description = (
        "Executes a registered STAR action within the secure sandbox environment.\n\n"
        "Set the target action using the `action_id` path parameter.\n\n"
        "### Supported Actions\n\n" + "\n".join(action_lines)
    )

    post["description"] = dynamic_description

    # ------------------------------------------------------------------
    # 6. Middleware metadata (vendor extension)
    # ------------------------------------------------------------------

    post["x-star-integrity"] = {
        "content_type_required": "application/json",
        "body_limit_bytes": getattr(app.state.settings, "star_max_file_bytes", None),
        "enforced_by": "RequestIntegrityMiddleware",
    }


def _patch_operation_contract(
    schema: dict[str, Any],
    *,
    path: str,
    method: str,
    errors: list[ErrorDef] | None = None,
    success_example: dict[str, Any] | None = None,
) -> None:
    """Apply STAR OpenAPI contract overrides to a single operation.

    An operation is defined as a combination of path + HTTP method.

    This function:
    - Injects STAR error examples grouped by HTTP status
    - Removes FastAPI-generated schemas when necessary
    - Optionally overrides the success response example

    Args:
        schema: Mutable OpenAPI schema document.
        path: API path (e.g. `/v1/files`).
        method: HTTP method (e.g. `post`, `get`).
        errors: Optional list of ErrorDef objects to expose.
        success_example: Optional success response example payload.
    """

    paths = schema.get("paths", {})
    path_item = paths.get(path)
    if not path_item:
        return

    operation = path_item.get(method)
    if not operation:
        return

    responses = operation.setdefault("responses", {})

    # ------------------------------------------------------------------
    # 1. Patch error responses
    # ------------------------------------------------------------------

    if errors:
        grouped: dict[int, list[ErrorDef]] = {}
        for err in errors:
            grouped.setdefault(err.http_status, []).append(err)

        for status, errs in grouped.items():
            response = responses.setdefault(
                str(status),
                {"description": f"{status} error"},
            )

            content = response.setdefault("content", {})
            json_content = content.setdefault("application/json", {})

            json_content["examples"] = {
                err.code: {
                    "summary": err.code,
                    "value": {
                        "success": False,
                        "error": {
                            "code": err.code,
                            "message": err.default_message,
                        },
                        "data": None,
                    },
                }
                for err in errs
            }

            json_content.pop("schema", None)

    # ------------------------------------------------------------------
    # 2. Patch success example
    # ------------------------------------------------------------------

    if success_example:
        for code in ("200", "201"):
            if code in responses:
                content = responses[code].setdefault("content", {})
                json_content = content.setdefault("application/json", {})
                json_content["example"] = success_example
                break


def _build_422_examples() -> dict[str, Any]:
    """Build standardized 422 error examples from public STAR error definitions.

    This helper constructs OpenAPI-compatible example payloads for all
    public errors with HTTP status 422 (Unprocessable Entity). Each example
    follows the canonical STAR response envelope format, ensuring consistency
    across all endpoints.

    The generated examples are used to replace FastAPI's default validation
    error schema, which does not align with STAR's error contract.

    Returns:
        Dictionary mapping error codes to OpenAPI example objects, where each
        example includes a summary and a fully structured response payload.
    """

    return {
        err.code: {
            "summary": err.code,
            "value": {
                "success": False,
                "error": {
                    "code": err.code,
                    "message": err.default_message,
                },
                "data": None,
            },
        }
        for err in PUBLIC_HTTP_ERRORS
        if err.http_status == 422
    }


def _replace_default_422(schema: dict[str, Any]) -> None:
    """Replace all default FastAPI 422 responses with STAR error contract.

    This function iterates over all registered API operations and replaces
    any existing HTTP 422 response definitions with a standardized STAR
    error contract. The replacement removes references to FastAPI's internal
    validation schemas (e.g. HTTPValidationError) and injects consistent
    example-based responses derived from centralized error definitions.

    This ensures:
    - Full alignment with STAR's ResponseEnvelope structure
    - Elimination of framework-specific validation artifacts
    - A deterministic and stable OpenAPI contract across all endpoints

    Args:
        schema: Mutable OpenAPI schema document to be patched in-place.
    """
    paths = schema.get("paths", {})

    for _path, path_item in paths.items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            responses = operation.get("responses", {})
            if "422" not in responses:
                continue

            responses["422"] = {
                "description": "422 error",
                "content": {"application/json": {"examples": _build_422_examples()}},
            }


def _inject_middleware_errors(
    schema: dict[str, Any],
    middleware_error_map: list[ErrorDef],
) -> None:
    """Inject middleware-level error responses into protected endpoints.

    This function adds standardized STAR error responses that originate from
    global middleware layers, such as authentication, rate limiting, and
    timeout enforcement. The injected responses are applied to all protected
    operations and skipped for explicitly public endpoints.

    The function expects a list of `ErrorDef` values representing middleware
    failures that may be returned before a request reaches the route handler.

    Notes:
        - Public operations are identified by `security=[]`.
        - Any existing framework-generated JSON schema for the injected
          status code is removed to avoid leaking FastAPI-specific contracts.
        - Response headers such as `Retry-After` for HTTP 429 are expected
          to be added later by `_inject_response_headers(...)`.

    Args:
        schema: Mutable OpenAPI schema document to patch in-place.
        middleware_error_map: List of STAR public error definitions that may
            be returned by middleware.

    Returns:
        None.
    """

    paths = schema.get("paths", {})

    errors_by_status: dict[int, list[ErrorDef]] = {}
    for err in middleware_error_map:
        errors_by_status.setdefault(err.http_status, []).append(err)

    for path_item in paths.values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            # Public endpoints are explicitly marked with empty security.
            if operation.get("security") == []:
                continue

            responses = operation.setdefault("responses", {})

            for status, error_defs in errors_by_status.items():
                response = responses.setdefault(
                    str(status),
                    {"description": f"{status} error"},
                )

                content = response.setdefault("content", {})
                json_content = content.setdefault("application/json", {})

                json_content["examples"] = {
                    err.code: {
                        "summary": err.code,
                        "value": {
                            "success": False,
                            "error": {
                                "code": err.code,
                                "message": err.default_message,
                            },
                            "data": None,
                        },
                    }
                    for err in error_defs
                }

                json_content.pop("schema", None)


def _patch_custom_schemas(schema: dict[str, Any]) -> None:
    """Ensure STAR-specific models and metadata appear in components.

    Args:
        schema: Mutable OpenAPI schema document.
    """
    # Register models that are reused by multiple patches and define the
    # `ErrorInfo.code` enum based on centralized public errors.
    components = schema.get("components", {})
    schemas = components.get("schemas", {})

    _register_model(ResponseEnvelope, schemas, nested=True)
    _register_model(ErrorInfo, schemas, nested=True)

    # Define enum for ErrorInfo.code based on PUBLIC_HTTP_ERRORS
    error_codes = [err.code for err in PUBLIC_HTTP_ERRORS]
    if "ErrorInfo" in schemas:
        error_info_schema = schemas["ErrorInfo"]
        properties = error_info_schema.setdefault("properties", {})
        code_prop = properties.setdefault("code", {})
        code_prop["enum"] = error_codes


def _prune_internal_schemas(schema: dict[str, Any]) -> None:
    """Remove internal-only schema definitions from the OpenAPI document.

    Args:
        schema: Mutable OpenAPI schema document.
    """
    components = schema.get("components", {})
    schemas = components.get("schemas", {})

    # Delete internal-only schemas that should not be exposed
    # in the public OpenAPI document.
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    schemas.pop("HealthResult", None)
    schemas.pop("ResponseEnvelope_HealthResult_", None)
    schemas.pop("ResponseEnvelope_Any_", None)


def _apply_response_contract_overrides(
    schema: dict[str, Any],
    response_contract_overrides: dict[str, dict[str, Any]],
) -> None:
    """Apply explicit response contract overrides to selected operations.

    This function allows declarative replacement of automatically generated
    OpenAPI response contracts. It is intended for endpoints whose runtime
    behavior (e.g., non-JSON media types, streaming responses, binary output)
    cannot be correctly inferred from FastAPI's default schema generation.

    The overrides dictionary must follow this structure:

        {
            "/path": {
                "method": "get" | "post" | ...,
                "responses": {
                    "200": {
                        "description": "...",
                        "content": {
                            "<media-type>": {
                                "schema": {...},
                                "example": ...
                            }
                        }
                    },
                    ...
                }
            }
        }

    Existing response definitions for the specified path + method
    will be replaced with the provided structure.

    Args:
        schema: Mutable OpenAPI schema document.
        response_contract_overrides: Declarative response definitions
            that override the auto-generated contracts.
    """

    paths = schema.get("paths", {})

    for path, override in response_contract_overrides.items():
        method = override.get("method")
        responses_override = override.get("responses")

        if not method or not responses_override:
            continue

        operation = paths.get(path, {}).get(method.lower())
        if not operation:
            continue

        # Replace entire responses section for determinism
        operation["responses"] = responses_override
