"""
Global pytest fixtures for STAR test suite.

This module defines shared fixtures used across unit, integration, and
smoke tests. The primary goals are:

- Ensure full isolation from the local environment (.env, shell variables).
- Provide minimal, valid defaults for Settings-dependent tests.
- Enable authenticated HTTP requests against the FastAPI app.
"""

from __future__ import annotations

import gzip
import os
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from star.core.config import Settings

# ============================================================================
# Environment and registry isolation
# ============================================================================


@pytest.fixture(autouse=True)
def clean_star_environment(monkeypatch):
    """Ensure test-only isolation from local configuration sources.

    This fixture enforces *strict configuration isolation* for all tests by:

    - Removing any `STAR_*` variables from the process environment.
    - Disabling `.env` file loading in `Settings`.
    - Clearing the cached Settings instance (`get_settings`) so each test
      observes only the environment prepared by its fixtures.

    Rationale:
        STAR settings are lazily loaded and cached via `get_settings()`.
        Without clearing the cache, changes to environment variables
        performed by fixtures would not take effect consistently.

        This fixture guarantees that:
        - No test depends on developer or CI `.env` files.
        - No test depends on execution order.
        - Every test sees a fresh Settings resolution.

    Scope:
        Test-only fixture. MUST NOT be used in production code.

    Args:
        monkeypatch: Pytest helper to mutate process environment safely.

    Yields:
        None. Runs setup before each test and restores settings afterward.
    """
    # ------------------------------------------------------------------
    # 1. Remove all STAR_* variables from the environment
    # ------------------------------------------------------------------
    for key in list(os.environ.keys()):
        if key.startswith("STAR_"):
            monkeypatch.delenv(key, raising=False)

    # ------------------------------------------------------------------
    # 2. Disable `.env` loading for Settings during tests
    # ------------------------------------------------------------------
    original_env_file = None
    try:
        from star.core.config import Settings

        original_env_file = Settings.model_config.get("env_file", None)
        monkeypatch.setitem(Settings.model_config, "env_file", None)
    except Exception:  # noqa: S110
        # Never fail tests due to configuration import issues
        pass

    # ------------------------------------------------------------------
    # 3. Clear cached settings to ensure fresh resolution per test
    # ------------------------------------------------------------------
    try:
        from star.core.config import get_settings

        get_settings.cache_clear()
    except Exception:  # noqa: S110
        pass

    # Run the test
    yield

    # ------------------------------------------------------------------
    # 4. Restore Settings configuration after the test
    # ------------------------------------------------------------------
    try:
        if original_env_file is not None:
            Settings.model_config["env_file"] = original_env_file
        else:
            Settings.model_config.pop("env_file", None)
    except Exception:  # noqa: S110
        pass


@pytest.fixture
def clean_action_registry():
    """Isolate the global action registry for each test.

    Yields:
        None. Runs each test with an empty registry and restores baseline.
    """
    from star.actions import registry

    # Use the public registry API: take a snapshot, replace with an empty
    # registry for the duration of the test, and restore the snapshot after.
    snapshot = registry.get_registry_snapshot()
    registry.replace_registry({})
    try:
        yield
    finally:
        registry.restore_registry(snapshot)


# ============================================================================
# DSL runtime fixtures
# ============================================================================


@pytest.fixture
def valid_registry(tmp_path, monkeypatch):
    """Build a deterministic DSL runtime registry for tests.

    The fixture writes a minimal but valid STAR DSL module to a temporary
    specs directory and compiles it through the public registry builder.

    Args:
            tmp_path: Per-test temporary root provided by pytest.

    Returns:
            ActionRegistry: Immutable registry with representative
                    `test_runtime.*` actions for params, defaults, and command
                    outputs.
    """
    import star.actions.registry as registry_module

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec_file = specs_dir / "test_runtime.yml"
    spec_file.write_text(
        """
version: 1
module: test_runtime
description: "Test runtime module"
tags: [test, runtime]

binaries:
    - echo
    - openssl

actions:

    ping:
        description: "Return deterministic hello output"
        summary: "Ping"
        tags: [health, smoke_test]
        command:
            - binary: echo
            - "hello"

    repeat:
        description: "Echo one integer argument"
        summary: "Repeat"
        tags: [echo, repeatable]
        args:
            count:
                type: int
                required: true
                description: "Number to echo"
        command:
            - binary: echo
            - arg: count

    range_test:
        description: "Test numeric constraints"
        tags: [validation, numeric-range]
        args:
            value:
                type: int
                required: true
                constraints:
                    min: 1
                    max: 10
                description: "Value in range"
        command:
            - binary: echo
            - arg: value

    default_test:
        description: "Test default value"
        tags: [defaults, optional-input]
        args:
            value:
                type: int
                required: false
                default: 5
                description: "Optional value"
        command:
            - binary: echo
            - arg: value

    write_output:
        description: "Generate bytes into one command output placeholder"
        summary: "Write output"
        tags: [outputs, runtime, openssl]
        outputs:
            cmd_out:
                type: file
                source: command
                description: "Command output placeholder containing generated bytes"
        command:
            - binary: openssl
            - "rand"
            - "-out"
            - output: cmd_out
            - "16"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.model_validate(
        {
            "star_root_dir": str(tmp_path),
        }
    )

    monkeypatch.setattr(registry_module, "SPEC_DIRS", (specs_dir,))

    return registry_module.build_registry_from_specs(settings)


# ============================================================================
# Base data fixtures
# ============================================================================


@pytest.fixture
def api_token() -> str:
    """Return a deterministic API token for authenticated tests.

    Returns:
        str: API token used in Authorization headers.
    """
    return "66350e905a79c0d0213876cc837624c4a53b2bed2380133a6d27c3e50c40047f"


@pytest.fixture
def star_root_dir(tmp_path):
    """Create a temporary STAR root directory for persistent storage tests.

    Args:
        tmp_path: Pytest-provided temporary directory unique to the test.

    Returns:
        Path: Path to the STAR root directory.
    """
    d = tmp_path / "star-root"
    d.mkdir()
    return d


@pytest.fixture
def minimal_safe_env(monkeypatch, star_root_dir, api_token):
    """Provide a minimal, safe environment for Settings-based tests.

    This fixture sets required STAR variables to deterministic
    values so tests don't need to repeat the same `monkeypatch.setenv`
    calls. Tests that need to vary one of these values should accept
    `minimal_safe_env` and then call `monkeypatch.setenv(...)` to
    override the specific variable.

    Args:
        monkeypatch: Pytest helper to set environment variables.
        star_root_dir: Root directory fixture.
        api_token: Deterministic API token fixture.

    Returns:
        Mapping of the environment variables configured for the test.
    """
    monkeypatch.setenv("STAR_API_TOKEN_DEV", api_token)
    monkeypatch.setenv("STAR_ROOT_DIR", str(star_root_dir))
    (star_root_dir / "tmp").mkdir(parents=True, exist_ok=True)
    return {
        "STAR_API_TOKEN_DEV": api_token,
        "STAR_ROOT_DIR": str(star_root_dir),
    }


# ============================================================================
# Settings fixture
# ============================================================================


@pytest.fixture
def settings(api_token, star_root_dir) -> Settings:
    """Return a minimal, valid Settings object for tests.

    This fixture constructs Settings explicitly via `model_validate`,
    ensuring no configuration is read from the environment or `.env`.

    Args:
        api_token: API token fixture.
        star_root_dir: Root directory fixture.
    Returns:
        Settings: Fully validated Settings instance.
    """
    return Settings.model_validate(
        {
            "star_api_token": api_token,
            "star_root_dir": str(star_root_dir),
        }
    )


# ============================================================================
# FastAPI app & client fixtures
# ============================================================================


@pytest.fixture
def app(settings):
    """Create a FastAPI application instance configured for tests.

    Args:
        settings: Valid Settings instance injected into the app.

    Returns:
        FastAPI: Configured application instance.
    """
    from star.app import create_app

    return create_app(settings)


@pytest.fixture
def client(app):
    """Return a TestClient bound to the configured FastAPI app.

    Args:
        app: FastAPI application fixture.

    Returns:
        TestClient: HTTP client for integration tests.
    """
    return TestClient(app)


# ============================================================================
# Integration app factories
# ============================================================================


@pytest.fixture
def create_upload_app(
    minimal_safe_env,
    monkeypatch,
    tmp_path,
) -> Callable[..., object]:
    """Return a factory for app instances with isolated STAR data storage.

    Args:
        minimal_safe_env: Fixture that provides required STAR environment vars.
        monkeypatch: Pytest helper used to set test-only environment values.
        tmp_path: Per-test temporary directory.

    Returns:
        Callable that builds a configured FastAPI app. Supports an optional
        keyword argument `max_file_bytes` to override `STAR_MAX_FILE_BYTES`.
    """

    del minimal_safe_env  # fixture ensures baseline STAR env values

    root_dir = tmp_path
    monkeypatch.setenv("STAR_ROOT_DIR", str(root_dir))

    def _create(*, max_file_bytes: int | None = None):
        """Create an application instance configured for upload-route testing.

        Args:
            max_file_bytes: Optional STAR_MAX_FILE_BYTES override.

        Returns:
            Configured FastAPI app instance.
        """

        if max_file_bytes is not None:
            monkeypatch.setenv("STAR_MAX_FILE_BYTES", str(max_file_bytes))
        else:
            monkeypatch.delenv("STAR_MAX_FILE_BYTES", raising=False)

        from star.app import create_app

        return create_app()

    return _create


# ============================================================================
# HTTP headers
# ============================================================================


@pytest.fixture
def auth_headers(api_token) -> dict[str, str]:
    """Return Authorization headers for authenticated requests.

    Args:
        api_token: API token fixture.

    Returns:
        dict[str, str]: Headers containing a Bearer token.
    """
    return {
        "Authorization": f"Bearer {api_token}",
    }


@pytest.fixture
def upload_file_id(client, auth_headers):
    """Return a factory that uploads files via POST `/v1/files`.

    This fixture helps tests use the same upload mechanism as production,
    returning the persisted `file_id` generated by the service.

    Args:
        client: FastAPI TestClient fixture.
        auth_headers: Authorization headers fixture.

    Returns:
        Callable that uploads one file and returns its UUID.
    """

    def _upload(
        *,
        name: str = "file.txt",
        content: bytes = b"hello world",
        content_type: str = "text/plain",
    ) -> UUID:
        """Upload one file through the API and return its persisted UUID.

        Args:
            name: Uploaded filename.
            content: Binary file content.
            content_type: Uploaded content type header value.

        Returns:
            Persisted file UUID returned by API.
        """

        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": (name, content, content_type)},
        )
        assert response.status_code == 201

        body = response.json()
        file_id = body["data"]["file"]["id"]
        return UUID(file_id)

    return _upload


# ============================================================================
# Filesystem fixtures
# ============================================================================


@dataclass(frozen=True)
class SandboxFile:
    """
    Value object representing a file created inside the STAR sandbox for tests.

    This object intentionally exposes multiple path representations to avoid
    leaking sandbox layout logic into individual tests.

    Attributes:
        abs_path:
            Absolute filesystem path to the file on disk.
            Intended for assertions that require direct filesystem access
            (existence checks, debugging, etc.).

        rel_path:
            Path relative to the sandbox root.
            This is the form expected by STAR actions and MUST be used when
            constructing execute request payloads.

        subdir:
            The sandbox subdirectory in which the file was created.
            Provided for clarity and debugging; tests should rarely need it.
    """

    abs_path: Path
    rel_path: Path
    subdir: str


@pytest.fixture
def sandbox_file_factory(minimal_safe_env):
    """
    Factory fixture to create files inside the STAR sandbox for tests.

    This fixture encapsulates all sandbox layout knowledge and returns a
    SandboxFile value object exposing both absolute and sandbox-relative paths.

    Tests MUST use `SandboxFile.rel_path` when passing paths to STAR actions,
    and SHOULD avoid performing manual path manipulation.

    Args:
        minimal_safe_env: Environment mapping fixture with sandbox metadata.

    Returns:
        Callable[[name: str, content: bytes, subdir: str | None], SandboxFile]:
            Factory function to create files in the sandbox.
    """

    root = Path(minimal_safe_env["STAR_ROOT_DIR"])
    sandbox = root

    def _create(
        name: str,
        content: bytes,
        subdir: str | None = None,
    ) -> SandboxFile:
        """Create one sandbox file and return its path metadata object.

        Args:
            name: Filename to create.
            content: Binary file content.
            subdir: Optional allowed sandbox subdirectory.

        Returns:
            SandboxFile with absolute and relative path variants.
        """

        chosen = subdir or "tmp"
        base = sandbox / chosen
        base.mkdir(parents=True, exist_ok=True)

        abs_path = base / name
        abs_path.write_bytes(content)

        rel_path = abs_path.relative_to(sandbox)
        return SandboxFile(
            abs_path=abs_path,
            rel_path=rel_path,
            subdir=chosen,
        )

    return _create


# ============================================================================
# Higher-level file factory for MIME tests
# ============================================================================


@pytest.fixture
def file_factory(sandbox_file_factory):
    """
    High-level factory to generate realistic files by logical type.

    This fixture builds real, structurally valid files for common MIME types
    without introducing external dependencies.

    Supported types:
        - "text"
        - "md"
        - "csv"
        - "png"
        - "pdf"
        - "zip"
        - "tar"
        - "gzip"
        - "exe"
        - "elf"
        - "shell"
        - "python"
        - "javascript"

    Args:
        sandbox_file_factory: Low-level sandbox file creation fixture.

    Returns:
        Callable[[str, str], SandboxFile]: Type-based file builder.
    """

    def create(file_type: str, name: str) -> SandboxFile:
        """Create a realistic file payload by logical file type.

        Args:
            file_type: Logical fixture file type key.
            name: Filename to create.

        Returns:
            SandboxFile for the generated payload.
        """

        file_type = file_type.lower()

        # ------------------------------------------------------------------
        # TEXT
        # ------------------------------------------------------------------
        if file_type == "text":
            return sandbox_file_factory(
                name=name,
                content=b"Hello STAR\n",
            )

        # ------------------------------------------------------------------
        # MD
        # ------------------------------------------------------------------
        if file_type == "md":
            md_bytes = (
                b"# STAR Test Document\n\n"
                b"This is a markdown file.\n\n"
                b"- item 1\n"
                b"- item 2\n"
            )
            return sandbox_file_factory(name=name, content=md_bytes)

        # ------------------------------------------------------------------
        # CSV
        # ------------------------------------------------------------------
        if file_type == "csv":
            csv_bytes = b"id,name,value\n1,alpha,100\n2,beta,200\n"
            return sandbox_file_factory(name=name, content=csv_bytes)

        # ------------------------------------------------------------------
        # PNG (minimal valid PNG)
        # ------------------------------------------------------------------
        if file_type == "png":
            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01"
                b"\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00"
                b"\x90wS\xde"
                b"\x00\x00\x00\x0aIDAT"
                b"\x08\xd7c\xf8\x0f\x00\x01\x01\x01\x00"
                b"\x18\xdd\x8d\x18"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            return sandbox_file_factory(name=name, content=png_bytes)

        # ------------------------------------------------------------------
        # PDF (minimal valid PDF)
        # ------------------------------------------------------------------
        if file_type == "pdf":
            pdf_bytes = (
                b"%PDF-1.4\n"
                b"1 0 obj\n"
                b"<< /Type /Catalog >>\n"
                b"endobj\n"
                b"trailer\n"
                b"<< /Root 1 0 R >>\n"
                b"%%EOF"
            )
            return sandbox_file_factory(name=name, content=pdf_bytes)

        # ------------------------------------------------------------------
        # ZIP
        # ------------------------------------------------------------------
        if file_type == "zip":
            # First create empty placeholder
            sf = sandbox_file_factory(name=name, content=b"")
            with zipfile.ZipFile(sf.abs_path, "w") as z:
                z.writestr("data.bin", os.urandom(256))
            return sf

        # ------------------------------------------------------------------
        # TAR
        # ------------------------------------------------------------------
        if file_type == "tar":
            sf = sandbox_file_factory(name=name, content=b"")
            temp_file = sf.abs_path.parent / "data.bin"
            temp_file.write_bytes(os.urandom(256))
            with tarfile.open(sf.abs_path, "w") as t:
                t.add(temp_file, arcname="data.bin")
            temp_file.unlink()
            return sf

        # ------------------------------------------------------------------
        # GZIP
        # ------------------------------------------------------------------
        if file_type == "gzip":
            sf = sandbox_file_factory(name=name, content=b"")
            with gzip.open(sf.abs_path, "wb") as g:
                g.write(os.urandom(256))
            return sf

        # ------------------------------------------------------------------
        # EXE (Windows PE / DOS MZ header)
        # ------------------------------------------------------------------
        if file_type == "exe":
            exe_bytes = b"MZ" + b"\x00" * 512
            return sandbox_file_factory(name=name, content=exe_bytes)

        # ------------------------------------------------------------------
        # ELF (Linux executable)
        # ------------------------------------------------------------------
        if file_type == "elf":
            elf_bytes = (
                b"\x7fELF"  # Magic
                + b"\x02"  # EI_CLASS (64-bit)
                + b"\x01"  # EI_DATA (little endian)
                + b"\x01"  # EI_VERSION
                + (b"\x00" * 9)  # EI_PAD
                + b"\x02\x00"  # e_type (EXEC)
                + b"\x3e\x00"  # e_machine (x86-64)
                + b"\x01\x00\x00\x00"  # e_version
                + (b"\x00" * 52)
            )
            return sandbox_file_factory(name=name, content=elf_bytes)

        # ------------------------------------------------------------------
        # SHELL SCRIPT
        # ------------------------------------------------------------------
        if file_type == "shell":
            shell_bytes = b"#!/bin/bash\n echo STAR\n"
            return sandbox_file_factory(name=name, content=shell_bytes)

        # ------------------------------------------------------------------
        # PYTHON SCRIPT
        # ------------------------------------------------------------------
        if file_type == "python":
            py_bytes = (
                b"#!/usr/bin/env python3\n"
                b"import sys\n"
                b"sys.stdout.write('STAR\\n')\n"
            )
            return sandbox_file_factory(name=name, content=py_bytes)

        # ------------------------------------------------------------------
        # JAVASCRIPT
        # ------------------------------------------------------------------
        if file_type == "javascript":
            js_bytes = b"#!/usr/bin/env node\nconsole.log('STAR');\n"
            return sandbox_file_factory(name=name, content=js_bytes)

        raise ValueError(f"Unsupported file type: {file_type}")

    return create
