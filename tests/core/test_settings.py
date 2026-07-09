"""Tests for `star.core.config.Settings`.

These tests check loading from environment, defaults and validation
invariants for STAR configuration.
"""

import pytest
from pydantic import ValidationError

from star.core import config
from star.core.config import (
    Settings,
    get_settings,
    load_star_api_token,
    validate_api_token,
)
from star.core.utils.file_storage import (
    ensure_storage_dirs,
    get_blob_dir,
    get_data_root,
    get_meta_dir,
    get_tmp_dir,
)

# ============================================================================
# Happy Path
# ============================================================================


def test_settings_load_from_env(minimal_safe_env, api_token, star_root_dir):
    """
    GIVEN the minimal required environment variables are set
    WHEN the Settings are validated from the environment
    THEN the resulting `Settings` object contains the expected values
    """
    s = Settings.model_validate({})

    # star_api_token is no longer loaded via environment parsing in Settings.
    assert s.star_api_token == ""
    assert s.star_root_dir == str(star_root_dir.resolve())


def test_settings_defaults_applied(minimal_safe_env):
    """
    GIVEN the minimal required environment variables are set
    WHEN the Settings are validated
    THEN default values are applied for optional integer/string fields
    """
    s = Settings.model_validate({})

    assert s.star_max_file_bytes == 104857600
    assert s.star_max_yml_bytes == 102400
    assert s.star_timeout_ms == 5000
    assert s.star_rate_limit_rps == 10
    assert s.star_app_version == "0.1.2"
    assert s.star_enable_docs is False
    assert s.star_enable_security_headers is True


# ----------------------------------------------------------------------------
# STAR Root Directory Validation
# ----------------------------------------------------------------------------


def test_star_root_dir_must_be_absolute(minimal_safe_env, monkeypatch):
    """
    GIVEN a relative STAR_ROOT_DIR
    WHEN settings are loaded
    THEN validation should fail
    """
    monkeypatch.setenv("STAR_ROOT_DIR", "relative/path")

    with pytest.raises(ValidationError):
        Settings.model_validate({})


def test_data_root_is_derived_from_root(tmp_path, minimal_safe_env, monkeypatch):
    """
    GIVEN a STAR_ROOT_DIR
    WHEN get_data_root is called
    THEN it should resolve to root/data
    """
    root_dir = tmp_path / "star-root"
    monkeypatch.setenv("STAR_ROOT_DIR", str(root_dir))

    settings = Settings.model_validate({})

    assert get_data_root(settings) == root_dir.resolve() / "data"


def test_storage_dirs_created(tmp_path, minimal_safe_env, monkeypatch):
    """
    GIVEN a clean root directory
    WHEN ensure_storage_dirs runs
    THEN all subdirectories must exist
    """
    root_dir = tmp_path / "star-root"
    monkeypatch.setenv("STAR_ROOT_DIR", str(root_dir))

    settings = Settings.model_validate({})
    ensure_storage_dirs(settings)

    assert get_data_root(settings).exists()
    assert get_blob_dir(settings).exists()
    assert get_meta_dir(settings).exists()
    assert get_tmp_dir(settings).exists()


# ============================================================================
# Required Variables
# ============================================================================


@pytest.mark.parametrize(
    "missing_var",
    [
        "STAR_ROOT_DIR",
    ],
    ids=[
        "star_root_dir",
    ],
)
def test_missing_required_env_raises(minimal_safe_env, monkeypatch, missing_var):
    """
    GIVEN one of the required environment variables is empty/missing
    WHEN Settings are validated
    THEN a ValidationError is raised
    """
    # minimal_safe_env provides the baseline, then we simulate missing
    # by setting the variable to an empty string.
    monkeypatch.setenv(missing_var, "")

    with pytest.raises(ValidationError):
        Settings.model_validate({})


# ============================================================================
# Variable Datatype Validation
# ============================================================================


@pytest.mark.parametrize(
    "env_field",
    [
        "STAR_MAX_FILE_BYTES",
        "STAR_MAX_YML_BYTES",
        "STAR_TIMEOUT_MS",
        "STAR_RATE_LIMIT_RPS",
    ],
    ids=[
        "star_max_file_bytes",
        "star_max_yml_bytes",
        "star_timeout_ms",
        "star_rate_limit_rps",
    ],
)
def test_int_fields_invalid_string(minimal_safe_env, monkeypatch, env_field):
    """
    GIVEN any integer-backed environment field set to a non-integer string
    WHEN Settings are validated
    THEN a ValidationError is raised for that field
    """
    # Provide a clearly non-integer string
    monkeypatch.setenv(env_field, "123abc")

    with pytest.raises(ValidationError):
        Settings.model_validate({})


def test_star_app_version_invalid_format_raises(minimal_safe_env, monkeypatch):
    """
    GIVEN STAR_APP_VERSION is set with a non-semver value
    WHEN Settings are validated
    THEN validation fails with ValidationError
    """
    monkeypatch.setenv("STAR_APP_VERSION", "v1.2.3")

    with pytest.raises(ValidationError):
        Settings.model_validate({})


# ============================================================================
# Blocked binary extra settings
# ============================================================================


def test_blocked_binary_extra_valid_csv(minimal_safe_env, monkeypatch):
    """
    GIVEN a valid CSV value for blocked binary extra entries
    WHEN Settings are validated
    THEN blocked extra field is accepted as configured
    """
    monkeypatch.setenv("STAR_BLOCKED_BINARIES_EXTRA", "bash,sh")

    s = Settings.model_validate({})

    assert s.star_blocked_binaries_extra == "bash,sh"


def test_blocked_binary_extra_blank_value_becomes_none(minimal_safe_env, monkeypatch):
    """
    GIVEN a blank blocked binary extra value
    WHEN Settings are validated
    THEN the value is normalized to None
    """
    monkeypatch.setenv("STAR_BLOCKED_BINARIES_EXTRA", "   ")

    settings = Settings.model_validate({})

    assert settings.star_blocked_binaries_extra is None


def test_blocked_binary_extra_rejects_path_entries(minimal_safe_env, monkeypatch):
    """
    GIVEN blocked binary extra entries containing a path-like token
    WHEN Settings are validated
    THEN ValidationError is raised
    """
    monkeypatch.setenv("STAR_BLOCKED_BINARIES_EXTRA", "echo,/bin/echo")

    with pytest.raises(ValidationError):
        Settings.model_validate({})


def test_blocked_binary_extra_accepts_duplicate_values(minimal_safe_env, monkeypatch):
    """
    GIVEN duplicate binary names in blocked extra CSV values
    WHEN Settings are validated
    THEN validation succeeds without errors
    """
    monkeypatch.setenv("STAR_BLOCKED_BINARIES_EXTRA", "bash,bash")

    s = Settings.model_validate({})

    assert s.star_blocked_binaries_extra == "bash,bash"


# ============================================================================
# API Docs and OpenAPI
# ============================================================================


def test_docs_endpoints_disabled_by_default(client):
    """
    GIVEN the application created with default settings
    WHEN requesting `/openapi.json` and `/docs`
    THEN the endpoints are not exposed
    """
    resp_openapi = client.get("/openapi.json")
    assert resp_openapi.status_code == 401

    resp_docs = client.get("/docs")
    assert resp_docs.status_code == 401


def test_docs_endpoints_enabled_when_flag_true(minimal_safe_env, monkeypatch):
    """
    GIVEN the required STAR environment variables and `STAR_ENABLE_DOCS=true`
    WHEN the application is created via the factory
    THEN `/openapi.json` and `/docs` are exposed
    """
    # minimal_safe_env ensures required vars are present; enable docs explicitly
    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")

    from fastapi.testclient import TestClient

    from star.app import create_app

    app = create_app()  # will read settings from the environment
    client = TestClient(app)

    resp_openapi = client.get("/openapi.json")
    assert resp_openapi.status_code == 200

    resp_docs = client.get("/docs")
    assert resp_docs.status_code == 200


def test_openapi_version_reflects_star_app_version(minimal_safe_env, monkeypatch):
    """
    GIVEN STAR_APP_VERSION is provided in the environment and docs are enabled
    WHEN the application OpenAPI document is requested
    THEN info.version matches the configured STAR_APP_VERSION value
    """
    monkeypatch.setenv("STAR_APP_VERSION", "7.8.9")
    monkeypatch.setenv("STAR_ENABLE_DOCS", "true")

    from fastapi.testclient import TestClient

    from star.app import create_app

    app = create_app()
    client = TestClient(app)

    resp_openapi = client.get("/openapi.json")
    assert resp_openapi.status_code == 200
    data = resp_openapi.json()
    assert data["info"]["version"] == "7.8.9"


# ============================================================================
# API Token Loading (Secrets and Fallback)
# ============================================================================


def test_get_settings_loads_token_from_dev_fallback(
    minimal_safe_env, monkeypatch, tmp_path
):
    """
    GIVEN no Docker secret file
    WHEN fallback env is set
    THEN token is injected.
    """
    missing_secret = tmp_path / "does-not-exist-star-api-token"
    monkeypatch.setattr(config, "STAR_API_TOKEN_SECRET_PATH", missing_secret)
    get_settings.cache_clear()

    s = get_settings()

    assert s.star_api_token != ""
    assert s.star_api_token == validate_api_token(s.star_api_token)


def test_load_star_api_token_missing_secret_and_no_fallback_raises(
    monkeypatch, tmp_path
):
    """
    GIVEN no Docker secret and no STAR_API_TOKEN_DEV.
    WHEN loading token
    THEN fail fast.
    """
    monkeypatch.setenv("STAR_API_TOKEN_DEV", "")
    missing_secret = tmp_path / "does-not-exist-star-api-token"
    monkeypatch.setattr(config, "STAR_API_TOKEN_SECRET_PATH", missing_secret)

    with pytest.raises(RuntimeError, match="STAR_API_TOKEN Docker secret not found"):
        load_star_api_token()


def test_load_star_api_token_empty_secret_raises(tmp_path, monkeypatch):
    """
    GIVEN empty Docker secret file
    WHEN loading token
    THEN fail fast.
    """
    secret_file = tmp_path / "star_api_token"
    secret_file.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(config, "STAR_API_TOKEN_SECRET_PATH", secret_file)

    with pytest.raises(RuntimeError, match="STAR_API_TOKEN Docker secret is empty"):
        load_star_api_token()


# ============================================================================
# API Token Validation Logic
# ============================================================================


@pytest.mark.parametrize(
    "token,error_message",
    [
        ("sh0rtt0k3n", "STAR_API_TOKEN must be at least 32 characters long"),
        (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "STAR_API_TOKEN must contain characters from at least two "
            "character classes",
        ),
        (
            "11111111111111111111111111111111",
            "STAR_API_TOKEN must contain characters from at least two "
            "character classes",
        ),
    ],
    ids=[
        "too_short",
        "only_chars",
        "only_digits",
    ],
)
def test_validate_api_token_rejects_weak_values(token, error_message):
    """
    GIVEN weak/placeholder values
    WHEN validating token
    THEN ValueError is raised.
    """
    with pytest.raises(ValueError, match=error_message):
        validate_api_token(token)


def test_get_settings_reads_token_from_secret_file(
    minimal_safe_env, monkeypatch, tmp_path
):
    """
    GIVEN a valid Docker secret file containing a strong API token
    WHEN `get_settings()` resolves configuration
    THEN the API token is loaded from the Docker secret and injected
    into the resulting Settings instance
    """

    # Create a simulated Docker secrets directory
    secrets_dir = tmp_path / "run" / "secrets"
    secrets_dir.mkdir(parents=True)

    token = "A1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # noqa: S105 - test token

    secret_file = secrets_dir / "star_api_token"
    secret_file.write_text(token, encoding="utf-8")

    # Redirect the secret path used by the configuration loader
    monkeypatch.setattr(config, "STAR_API_TOKEN_SECRET_PATH", secret_file)

    # Ensure no dev fallback is used
    monkeypatch.delenv("STAR_API_TOKEN_DEV", raising=False)

    # Clear cached settings so the new secret path is used
    get_settings.cache_clear()

    # Resolve settings via the normal configuration entrypoint
    s = get_settings()

    # Validate token was loaded and validated correctly
    assert s.star_api_token == token
    assert s.star_api_token == validate_api_token(token)
