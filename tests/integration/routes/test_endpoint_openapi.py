"""Integration tests for the generated OpenAPI contract.

These tests validate the runtime OpenAPI projection exposed by STAR.
They ensure security exposure, contract overrides, schema registration,
and error examples are documented as expected.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from star.actions.models.core import ParamType
from star.actions.presentation.contracts import (
    _build_required_arg_example_value,
    _format_arg_type_for_docs,
)
from star.app import create_app

# ============================================================================
# Helpers
# ============================================================================


def _openapi_document(
    minimal_safe_env,
    monkeypatch,
    action_registry=None,
) -> dict:
    """Fetch the generated OpenAPI document.

    Build a docs-enabled test app and return the parsed `/openapi.json` payload.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.

    Returns:
        Parsed OpenAPI JSON document.
    """

    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")
    monkeypatch.setenv("STAR_DOCS_REQUIRE_AUTH", "false")
    app = create_app()
    if action_registry is not None:
        app.state.action_registry = action_registry
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


# ============================================================================
# OpenAPI Spec Validation
# ============================================================================


def test_openapi_is_valid_spec(minimal_safe_env, monkeypatch):
    """Validate generated OpenAPI document against the spec.

    GIVEN docs are explicitly enabled for schema inspection
    WHEN `/openapi.json` is generated
    THEN the payload is a valid OpenAPI schema.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")
    monkeypatch.setenv("STAR_DOCS_REQUIRE_AUTH", "false")
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    validate(schema)


# ============================================================================
# Security contract
# ============================================================================


def test_openapi_sets_security_for_private_and_public_routes(
    minimal_safe_env,
    monkeypatch,
):
    """Validate auth exposure for public vs private endpoints.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN global BearerAuth is enabled
    AND health is explicitly public
    AND metrics inherits protected BearerAuth by default.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    assert schema["security"] == [{"BearerAuth": []}]

    health_get = schema["paths"]["/health"]["get"]
    metrics_get = schema["paths"]["/metrics"]["get"]
    assert health_get["security"] == []
    assert metrics_get.get("security") != []
    assert "401" in metrics_get["responses"]
    assert "429" not in metrics_get["responses"]
    assert "504" not in metrics_get["responses"]


def test_openapi_marks_metrics_public_when_metrics_auth_is_disabled(
    minimal_safe_env,
    monkeypatch,
):
    """Validate optional public metrics auth exposure.

    GIVEN docs are enabled and metrics auth is explicitly disabled
    WHEN generating the OpenAPI schema
    THEN metrics is marked as a public operation.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    monkeypatch.setenv("STAR_METRICS_REQUIRE_AUTH", "false")

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    metrics_get = schema["paths"]["/metrics"]["get"]
    assert metrics_get["security"] == []
    assert "401" not in metrics_get["responses"]


def test_openapi_actions_list_docs_describe_query_rules(
    minimal_safe_env,
    monkeypatch,
):
    """Validate GET /v1/actions query docs and parameter constraints.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN `/v1/actions` documents query behavior for `q`, `tags`, and `match`
    including `match` allowed values and semantic default behavior.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    actions_get = schema["paths"]["/v1/actions"]["get"]
    description = actions_get.get("description", "")

    assert "Discover registered actions grouped by module." in description
    assert "`q`" in description
    assert "`tags`" in description
    assert "`match`" in description
    assert "logical AND" in description

    parameters = {
        parameter["name"]: parameter for parameter in actions_get.get("parameters", [])
    }

    assert {"q", "tags", "match"}.issubset(parameters)
    assert parameters["q"]["schema"]["example"] == "sha256"
    assert parameters["tags"]["schema"]["example"] == "hashing,checksum"

    match_schema = parameters["match"]["schema"]
    assert match_schema["type"] == "string"
    assert match_schema["enum"] == ["any", "all"]
    assert match_schema["default"] == "any"

    example = actions_get["responses"]["200"]["content"]["application/json"]["example"]
    modules = example["data"]["modules"]
    assert modules

    action_ids = [
        action["action_id"]
        for module in modules
        for action in module.get("actions", [])
    ]
    assert any(action_id.endswith(".sha256_file") for action_id in action_ids)


def test_openapi_action_spec_example_references_a_built_in_action(
    minimal_safe_env,
):
    """Validate the action-spec example references a published CORE action.

    GIVEN the built-in CORE registry
    WHEN the OpenAPI schema is generated
    THEN the GET action-spec example identifies a real no-parameter action.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
    """

    app = create_app()
    schema = app.openapi()
    example = schema["paths"]["/v1/actions/{action_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["example"]

    assert example["data"]["action"] == "gen_uuid"
    assert example["data"]["action_id"] == "base.random.gen_uuid"
    assert example["data"]["args"] == []
    assert example["data"]["flags"] == []


# ============================================================================
# Execute Contract Projection
# ============================================================================


def test_openapi_execute_contract_includes_integrity_and_request_id_headers(
    minimal_safe_env,
    monkeypatch,
):
    """Validate `POST /v1/actions/{action_id}` runtime contract projection.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN `/v1/actions/{action_id}` includes dynamic request/response examples
    and integrity metadata
    AND `X-Request-Id` is documented as UUID on responses.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    post = schema["paths"]["/v1/actions/{action_id}"]["post"]

    integrity = post["x-star-integrity"]
    assert integrity["content_type_required"] == "application/json"
    assert integrity["enforced_by"] == "RequestIntegrityMiddleware"
    assert integrity["body_limit_bytes"] == 1048576

    request_schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"] == "#/components/schemas/ExecuteActionRequest"

    for code in ("200", "400", "401", "429", "500", "504"):
        if code not in post["responses"]:
            continue
        headers = post["responses"][code].get("headers", {})
        assert "X-Request-Id" in headers
        header_schema = headers["X-Request-Id"]["schema"]
        assert header_schema["type"] == "string"
        assert header_schema["format"] == "uuid"

    retry_after_schema = post["responses"]["429"]["headers"]["Retry-After"]["schema"]
    assert retry_after_schema["type"] == "string"
    assert retry_after_schema["pattern"] == "^[0-9]+$"


def test_openapi_request_body_operations_publish_integrity_body_limits(
    minimal_safe_env,
    monkeypatch,
):
    """Validate request-body size metadata across operation types.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN normal request bodies publish STAR_MAX_BODY_BYTES
    AND multipart uploads publish STAR_MAX_FILE_BYTES.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    execute_post = schema["paths"]["/v1/actions/{action_id}"]["post"]
    files_post = schema["paths"]["/v1/files"]["post"]

    assert execute_post["x-star-integrity"]["body_limit_bytes"] == 1048576
    assert files_post["x-star-integrity"]["body_limit_bytes"] == 104857600
    assert "x-star-integrity" not in schema["paths"]["/v1/actions"]["get"]


def test_openapi_execute_examples_include_enriched_markdown_and_params(
    minimal_safe_env,
    monkeypatch,
    valid_registry,
):
    """Validate enriched action docs and params examples for the execute route.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN each action example includes markdown generated from ActionSpec
    AND params examples include required/default values derived at runtime.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(
        minimal_safe_env,
        monkeypatch,
        action_registry=valid_registry,
    )
    post = schema["paths"]["/v1/actions/{action_id}"]["post"]
    examples = post["requestBody"]["content"]["application/json"]["examples"]

    sequence_to_example = examples["test_runtime.sequence_to"]
    sequence_to_description = sequence_to_example["description"]
    sequence_to_params = sequence_to_example["value"]["params"]
    assert "action" not in sequence_to_example["value"]
    assert sequence_to_example["value"]["stdout_as_file"] is False

    assert "#### Args" in sequence_to_description
    assert "#### Flags" in sequence_to_description
    assert "#### Request Options" in sequence_to_description
    assert "#### Outputs" not in sequence_to_description
    assert "`count` (`int`)" in sequence_to_description
    assert "required" in sequence_to_description
    assert (
        "- `equal_width`: Pad values to the same width; default: `false`"
        in sequence_to_description
    )
    assert sequence_to_params["count"] == 1
    assert sequence_to_params["equal_width"] is False

    default_example = examples["test_runtime.default_sequence"]
    default_description = default_example["description"]
    default_params = default_example["value"]["params"]
    assert "action" not in default_example["value"]
    assert default_example["value"]["stdout_as_file"] is False

    assert "`value` (`int`)" in default_description
    assert "default: `5`" in default_description
    assert default_params["value"] == 5

    sequence_one_example = examples["test_runtime.sequence_one"]
    sequence_one_description = sequence_one_example["description"]
    sequence_one_params = sequence_one_example["value"]["params"]
    assert "action" not in sequence_one_example["value"]
    assert sequence_one_example["value"]["stdout_as_file"] is False

    assert "- _No args_" in sequence_one_description
    assert "- _No flags_" in sequence_one_description
    assert sequence_one_params == {}


def test_openapi_execute_examples_omit_stdout_as_file_when_disallowed(
    minimal_safe_env,
    monkeypatch,
    valid_registry,
):
    """
    GIVEN docs are enabled
    WHEN generating the OpenAPI schema for an action that disallows stdout file
        materialization
    THEN the request example omits stdout_as_file entirely
    AND the markdown description does not show Request Options.
    """

    del minimal_safe_env
    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")
    monkeypatch.setenv("STAR_DOCS_REQUIRE_AUTH", "false")

    app = create_app()
    app.state.action_registry = valid_registry

    schema = app.openapi()
    post = schema["paths"]["/v1/actions/{action_id}"]["post"]
    examples = post["requestBody"]["content"]["application/json"]["examples"]

    blocked_example = examples["test_runtime.random_token_no_stdout_file"]
    blocked_description = blocked_example["description"]
    blocked_value = blocked_example["value"]

    assert "#### Request Options" not in blocked_description
    assert "stdout_as_file" not in blocked_value
    assert blocked_value["params"] == {}


def test_openapi_execute_response_examples_include_outputs_when_declared(
    minimal_safe_env,
    monkeypatch,
    valid_registry,
):
    """Validate response examples document declared action outputs.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN response examples for actions with outputs include `data.outputs`
    AND actions allowing stdout_as_file document reserved `stdout_file` output
    without forcing it into examples when `stdout_as_file` is false.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
        valid_registry: Shared action registry with representative outputs.
    """

    del minimal_safe_env
    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")
    monkeypatch.setenv("STAR_DOCS_REQUIRE_AUTH", "false")

    app = create_app()
    app.state.action_registry = valid_registry

    schema = app.openapi()
    post = schema["paths"]["/v1/actions/{action_id}"]["post"]
    examples = post["responses"]["200"]["content"]["application/json"]["examples"]

    outputs_description = examples["test_runtime.generate_random_output"]["description"]
    assert "#### Outputs" in outputs_description
    assert (
        "Command output placeholder containing generated bytes" in outputs_description
    )
    assert "`cmd_out` (`FileMetadata`)" in outputs_description

    outputs_example = examples["test_runtime.generate_random_output"]["value"]["data"]
    assert "outputs" in outputs_example

    copied_file = outputs_example["outputs"]["cmd_out"]
    assert copied_file["original_filename"] == "action.cmd_out.bin"
    assert copied_file["mime_type"] == "application/octet-stream"
    assert copied_file["status"] == "ready"

    stdout_description = examples["test_runtime.sequence_one"]["description"]
    assert "`stdout_file` (`FileMetadata`)" in stdout_description

    stdout_example = examples["test_runtime.sequence_one"]["value"]["data"]
    assert stdout_example["outputs"] == {}


def test_openapi_helpers_support_list_arg_docs_and_examples():
    """Validate OpenAPI helper behavior for list-based action args.

    GIVEN required list argument metadata
    WHEN helper values are generated for docs/examples
    THEN list docs labels and example payloads match supported list item types.
    """

    assert _format_arg_type_for_docs(ParamType.LIST, ParamType.STRING) == "list[string]"
    assert (
        _format_arg_type_for_docs(ParamType.LIST, ParamType.FILE_ID) == "list[file_id]"
    )

    assert _build_required_arg_example_value(
        "inputs", ParamType.LIST, ParamType.STRING
    ) == [
        "inputs_item_1",
        "inputs_item_2",
    ]
    assert _build_required_arg_example_value(
        "files", ParamType.LIST, ParamType.FILE_ID
    ) == [
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "98e56387-3364-4ce2-9c66-44d23ec4e23a",
    ]


# ============================================================================
# Global metadata
# ============================================================================


def test_openapi_includes_global_metadata_from_app_settings(
    minimal_safe_env,
    monkeypatch,
):
    """Validate global OpenAPI metadata projected from app initialization.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN title/version/description come from app configuration
    AND contact/license/externalDocs are exposed as configured.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    info = schema["info"]
    assert info["title"] == "Secure Templated Actions Runtime (STAR)"
    assert info["version"] == "0.2.0"
    assert "Runtime-aware OpenAPI contract generation" in info["description"]

    assert info["contact"]["name"] == "Libertocrat"
    assert info["contact"]["url"] == "https://github.com/Libertocrat/"
    assert info["contact"]["email"] == "libertocrat@proton.me"

    assert info["license"]["name"] == "GNU Affero General Public License v3.0"
    assert info["license"]["url"] == "https://www.gnu.org/licenses/agpl-3.0.html"

    assert schema["externalDocs"]["url"] == "https://github.com/Libertocrat/star"


# ============================================================================
# Public Endpoint Overrides
# ============================================================================


def test_openapi_applies_public_endpoint_response_overrides(
    minimal_safe_env,
    monkeypatch,
):
    """Validate explicit response-contract overrides for public endpoints.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN `/metrics` advertises `text/plain` metrics payload
    AND `/health` advertises canonical envelope example.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    metrics_get = schema["paths"]["/metrics"]["get"]
    metrics_200 = metrics_get["responses"]["200"]
    assert metrics_200["description"] == "Prometheus metrics snapshot"
    assert "text/plain" in metrics_200["content"]
    assert metrics_200["content"]["text/plain"]["schema"]["type"] == "string"

    health_get = schema["paths"]["/health"]["get"]
    health_200 = health_get["responses"]["200"]
    assert health_200["description"] == "Health success response"
    health_example = health_200["content"]["application/json"]["schema"]["example"]
    assert health_example["success"] is True
    assert health_example["error"] is None
    assert health_example["data"]["status"] == "ok"


# ============================================================================
# Component schemas
# ============================================================================


def test_openapi_registers_action_models_and_prunes_internal_schemas(
    minimal_safe_env,
    monkeypatch,
    valid_registry,
):
    """Validate component schema registration/pruning for runtime OpenAPI.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN action-related models are registered in components
    AND internal FastAPI/Pydantic helper schemas are pruned.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(
        minimal_safe_env,
        monkeypatch,
        action_registry=valid_registry,
    )

    components = schema["components"]["schemas"]
    for model_name in (
        "ExecuteActionRequest",
        "ExecuteActionData",
        "TestRuntimeSequenceOneParams",
        "TestRuntimeSequenceToParams",
        "TestRuntimeBoundedSequenceParams",
        "TestRuntimeDefaultSequenceParams",
    ):
        assert model_name in components

    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    assert "HealthResult" not in components
    assert "ResponseEnvelope_HealthResult_" not in components


# ============================================================================
# Error Examples
# ============================================================================


def test_openapi_error_contract_replaces_default_422_with_public_error_examples(
    minimal_safe_env,
    monkeypatch,
):
    """Validate dynamic error response generation for action execution endpoint.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN FastAPI's generic 422 is replaced
    AND centralized public error examples are published.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """
    schema = _openapi_document(minimal_safe_env, monkeypatch)

    post = schema["paths"]["/v1/actions/{action_id}"]["post"]
    responses = post["responses"]

    assert "422" in responses
    examples_422 = responses["422"]["content"]["application/json"]["examples"]
    assert "UNPROCESSABLE_ENTITY" in examples_422

    # The generic FastAPI validation wrapper should not leak through examples.
    assert "ValidationError" not in str(examples_422)


# ============================================================================
# Files Endpoints Contracts
# ============================================================================


def test_openapi_documents_files_upload_contract(
    minimal_safe_env,
    monkeypatch,
):
    """Validate OpenAPI examples for `POST /v1/files`.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN upload operation documents canonical success and handler-level errors.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    upload = schema["paths"]["/v1/files"]["post"]
    responses = upload["responses"]

    success_example = responses["201"]["content"]["application/json"]["example"]
    assert success_example["success"] is True
    assert success_example["error"] is None
    assert success_example["data"]["file"]["status"] == "ready"

    for status in ("400", "401", "413", "415", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples


def test_openapi_documents_files_metadata_contract(
    minimal_safe_env,
    monkeypatch,
):
    """Validate OpenAPI examples for `GET /v1/files/{id}`.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN metadata operation documents canonical success and handler-level errors.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    metadata_get = schema["paths"]["/v1/files/{id}"]["get"]
    responses = metadata_get["responses"]

    success_example = responses["200"]["content"]["application/json"]["example"]
    assert success_example["success"] is True
    assert success_example["error"] is None
    assert success_example["data"]["file"]["status"] == "ready"

    for status in ("400", "401", "404", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples

    assert "ETag" in responses["200"]["headers"]


def test_openapi_documents_files_metadata_update_contract(
    minimal_safe_env,
    monkeypatch,
):
    """
    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN conditional metadata replacement documents its ETag and stable errors

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    metadata_put = schema["paths"]["/v1/files/{id}"]["put"]
    responses = metadata_put["responses"]
    if_match = next(
        parameter
        for parameter in metadata_put["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "If-Match"
    )

    assert if_match["required"] is True
    assert if_match["schema"]["pattern"] == '^"[a-f0-9]{64}"$'
    assert "ETag" in responses["200"]["headers"]
    assert responses["200"]["content"]["application/json"]["example"]["data"]["file"][
        "tags"
    ] == ["finance", "q3"]

    for status in ("400", "401", "404", "412", "422", "428", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples


def test_openapi_documents_files_content_contract(
    minimal_safe_env,
    monkeypatch,
):
    """Validate OpenAPI contract for `GET /v1/files/{id}/content`.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN content operation documents binary success response and handler-level errors.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    content_get = schema["paths"]["/v1/files/{id}/content"]["get"]
    responses = content_get["responses"]

    assert "200" in responses
    response_200 = responses["200"]
    assert response_200["description"] == "Streamed file content."
    binary_schema = response_200["content"]["application/octet-stream"]["schema"]
    assert binary_schema["type"] == "string"
    assert binary_schema["format"] == "binary"

    for status in ("400", "401", "404", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples


def test_openapi_documents_files_delete_contract(
    minimal_safe_env,
    monkeypatch,
):
    """Validate OpenAPI examples for `DELETE /v1/files/{id}`.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN delete operation documents canonical success and handler-level errors.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    delete = schema["paths"]["/v1/files/{id}"]["delete"]
    responses = delete["responses"]

    success_example = responses["200"]["content"]["application/json"]["example"]
    assert success_example["success"] is True
    assert success_example["error"] is None
    assert success_example["data"]["file"]["deleted"] is True

    for status in ("400", "401", "404", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples


def test_openapi_documents_files_list_contract(
    minimal_safe_env,
    monkeypatch,
):
    """Validate OpenAPI examples for `GET /v1/files`.

    GIVEN docs are enabled
    WHEN generating the OpenAPI schema
    THEN list operation documents canonical success and handler-level errors.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
    """

    schema = _openapi_document(minimal_safe_env, monkeypatch)

    list_get = schema["paths"]["/v1/files"]["get"]
    responses = list_get["responses"]
    cursor_parameter = next(
        parameter
        for parameter in list_get["parameters"]
        if parameter["in"] == "query" and parameter["name"] == "cursor"
    )

    assert "Opaque continuation token" in cursor_parameter["description"]
    assert (
        "bound to the effective filters and ordering" in cursor_parameter["description"]
    )
    assert "4096 characters" in cursor_parameter["description"]

    parameters = {
        parameter["name"]: parameter
        for parameter in list_get["parameters"]
        if parameter["in"] == "query"
    }
    assert {"file_name", "tags"}.issubset(parameters)
    assert "case-insensitive substring" in parameters["file_name"]["description"]
    assert "Every listed tag" in parameters["tags"]["description"]
    assert parameters["status"]["schema"]["anyOf"][0]["enum"] == [
        "pending",
        "unverified",
        "ready",
    ]
    assert "all-of CSV `tags` filter" in list_get["description"]
    assert "Unknown, repeated, and empty query parameters are rejected." in (
        list_get["description"]
    )

    success_example = responses["200"]["content"]["application/json"]["example"]
    assert success_example["success"] is True
    assert success_example["error"] is None
    assert success_example["data"]["files"] == []
    assert success_example["data"]["pagination"]["count"] == 0
    assert success_example["data"]["pagination"]["next_cursor"] is None

    for status in ("400", "401", "422", "500"):
        assert status in responses
        examples = responses[status]["content"]["application/json"]["examples"]
        assert isinstance(examples, dict)
        assert examples
