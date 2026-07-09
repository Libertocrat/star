"""Unit tests for STAR runtime outputs builder."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from star.actions.exceptions import ActionRuntimeOutputError
from star.actions.models.core import (
    ActionSpec,
    CommandElement,
    OutputDef,
    OutputSource,
    OutputType,
)
from star.actions.models.runtime import (
    ActionExecutionOutput,
    ActionExecutionResult,
    RenderedAction,
)
from star.actions.models.security import BinaryPolicy
from star.actions.runtime import file_manager
from star.actions.runtime.outputs_builder import _cleanup_known_outputs, build_outputs
from star.core.config import Settings
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import (
    EMPTY_SHA256,
    compute_sha256_for_file,
    create_placeholder_file_metadata,
    get_blob_path,
    get_meta_path,
    load_file_metadata,
)


def _make_settings(tmp_path: Path) -> Settings:
    """Build deterministic settings for output storage tests.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        Validated Settings instance.
    """

    return Settings.model_validate(
        {
            "star_api_token": "a" * 64,
            "star_root_dir": str(tmp_path),
        }
    )


def _make_spec(
    *,
    action: str = "test_action",
    outputs: dict[str, OutputDef] | None = None,
) -> ActionSpec:
    """Build a minimal runtime ActionSpec for outputs tests.

    Args:
        action: Action short name.
        outputs: Optional outputs mapping.

    Returns:
        Minimal ActionSpec suitable for build_outputs.
    """

    return ActionSpec(
        name=f"test.{action}",
        namespace=(),
        module="test",
        action=action,
        version=1,
        params_model=BaseModel,
        binary="echo",
        command_template=(cast(CommandElement, {"kind": "binary", "value": "echo"}),),
        execution_policy=BinaryPolicy(allowed=("echo", "cp"), blocked=()),
        arg_defs={},
        flag_defs={},
        defaults={},
        outputs={} if outputs is None else outputs,
        authors=None,
        tags=(),
        summary=None,
        description=None,
        deprecated=False,
        params_example=None,
    )


def _make_execution_result(*, returncode: int = 0) -> ActionExecutionResult:
    """Build deterministic ActionExecutionResult for outputs tests.

    Args:
        returncode: Process return code.

    Returns:
        ActionExecutionResult instance.
    """

    return ActionExecutionResult(
        returncode=returncode,
        stdout=b"",
        stderr=b"",
        exec_time=0.01,
        pid=123,
    )


def _make_sanitized_output(*, stdout: bytes = b"") -> ActionExecutionOutput:
    """Build deterministic sanitized output model.

    Args:
        stdout: Sanitized stdout bytes.

    Returns:
        ActionExecutionOutput instance.
    """

    return ActionExecutionOutput(
        returncode=0,
        stdout=stdout,
        stderr=b"",
        exec_time=0.01,
        pid=123,
        truncated=False,
        redacted=False,
    )


def _patch_storage_to_settings(monkeypatch: pytest.MonkeyPatch, cfg: Settings) -> None:
    """Patch outputs_builder storage operations to use explicit settings.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        cfg: Test settings instance.
    """

    monkeypatch.setattr(
        "star.actions.runtime.outputs_builder.finalize_command_output_file",
        lambda **kwargs: file_manager.finalize_command_output_file(
            settings=cfg,
            **{k: v for k, v in kwargs.items() if k != "settings"},
        ),
    )
    monkeypatch.setattr(
        "star.actions.runtime.outputs_builder.create_ready_file_from_bytes",
        lambda **kwargs: file_manager.create_ready_file_from_bytes(
            settings=cfg,
            **{k: v for k, v in kwargs.items() if k != "settings"},
        ),
    )
    monkeypatch.setattr(
        "star.actions.runtime.outputs_builder.cleanup_output_file",
        lambda file_id, settings=None: file_manager.cleanup_output_file(
            file_id, settings=cfg
        ),
    )


def _create_placeholder(
    *,
    cfg: Settings,
    original_filename: str = "test.out.bin",
) -> FileMetadata:
    """Create command placeholder metadata in pending state.

    Args:
        cfg: Test settings instance.
        original_filename: Placeholder original filename.

    Returns:
        Persisted pending FileMetadata.
    """

    return create_placeholder_file_metadata(
        original_filename=original_filename,
        settings=cfg,
    )


def _minimal_png_bytes() -> bytes:
    """Return deterministic minimal PNG bytes.

    Returns:
        Binary content for a minimal PNG file.
    """

    return (
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


# ============================================================================
# File Command Success Path
# ============================================================================


def test_outputs_builder__file_command_creates_ready_metadata(tmp_path, monkeypatch):
    """
    GIVEN a command output with existing blob
    WHEN build_outputs is called
    THEN metadata is updated to status "ready"
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(_minimal_png_bytes())

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    assert result["cmd_out"] is not None
    assert result["cmd_out"].status == "ready"


def test_outputs_builder__file_command_updates_metadata_fields(tmp_path, monkeypatch):
    """
    GIVEN a command output with existing blob
    WHEN build_outputs is called
    THEN metadata fields are recalculated
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    content = _minimal_png_bytes()
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    metadata = result["cmd_out"]
    assert metadata is not None
    assert metadata.size_bytes == len(content)
    assert metadata.sha256 == compute_sha256_for_file(blob)
    assert metadata.mime_type == "image/png"


def test_outputs_builder__file_command_sets_unverified_before_ready(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a command output
    WHEN build_outputs runs
    THEN metadata transitions through "unverified"
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(_minimal_png_bytes())

    statuses: list[str] = []
    original_save = file_manager.save_file_metadata

    def _capture_status(metadata: FileMetadata, settings=None):
        """Capture status transitions while persisting metadata."""

        if metadata.id == placeholder.id:
            statuses.append(metadata.status)
        return original_save(metadata, settings)

    monkeypatch.setattr(
        "star.actions.runtime.file_manager.save_file_metadata", _capture_status
    )

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    assert "unverified" in statuses
    assert "ready" in statuses
    assert statuses.index("unverified") < statuses.index("ready")


def test_outputs_builder__file_command_uses_existing_blob(tmp_path, monkeypatch):
    """
    GIVEN blob exists
    WHEN build_outputs runs
    THEN blob is not recreated
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    content = b"existing-blob-content"
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)
    before_mtime = blob.stat().st_mtime

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    assert blob.read_bytes() == content
    assert blob.stat().st_mtime == before_mtime


# ============================================================================
# File Command Empty Output
# ============================================================================


def test_outputs_builder__file_command_creates_empty_blob_if_missing(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN command did not create blob
    WHEN build_outputs runs
    THEN empty blob is created
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    assert not blob.exists()

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    assert blob.exists()
    assert blob.read_bytes() == b""


def test_outputs_builder__file_command_empty_blob_metadata_is_valid(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN empty blob case
    WHEN build_outputs runs
    THEN metadata reflects empty content correctly
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(),
    )

    metadata = result["cmd_out"]
    assert metadata is not None
    assert metadata.status == "ready"
    assert metadata.size_bytes == 0
    assert metadata.sha256 == EMPTY_SHA256
    assert metadata.mime_type == "application/octet-stream"
    assert metadata.extension == ".bin"


# ============================================================================
# File Command Failure Path
# ============================================================================


def test_outputs_builder__file_command_failure_returns_none(tmp_path, monkeypatch):
    """
    GIVEN command exit_code != 0
    WHEN build_outputs runs
    THEN output value is None
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"partial")

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=1),
        _make_sanitized_output(),
    )

    assert result["cmd_out"] is None


def test_outputs_builder__file_command_failure_triggers_cleanup(tmp_path, monkeypatch):
    """
    GIVEN command failure
    WHEN build_outputs runs
    THEN metadata and blob are deleted
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"partial")

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=2),
        _make_sanitized_output(),
    )

    assert not get_meta_path(placeholder.id, cfg).exists()
    assert not get_blob_path(placeholder.id, cfg).exists()


def test_outputs_builder__cleanup_is_not_duplicated(monkeypatch):
    """
    GIVEN same file appears in outputs and rendered.output_files
    WHEN cleanup runs
    THEN file is deleted only once
    """

    file_id = uuid4()
    now = datetime.now(UTC)
    metadata = FileMetadata(
        id=file_id,
        original_filename="x.bin",
        stored_filename=f"file_{file_id}.bin",
        mime_type="application/octet-stream",
        extension=".bin",
        size_bytes=1,
        sha256="a" * 64,
        created_at=now,
        updated_at=now,
        status="ready",
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": file_id})

    calls: list[UUID] = []
    monkeypatch.setattr(
        "star.actions.runtime.outputs_builder.cleanup_output_file",
        lambda target_file_id, settings=None: calls.append(target_file_id),
    )

    _cleanup_known_outputs({"cmd_out": metadata}, rendered)

    assert calls == [file_id]


# ============================================================================
# Stdout As File Option
# ============================================================================


def test_build_outputs_omits_stdout_file_when_not_requested(tmp_path, monkeypatch):
    """
    GIVEN an action that allows stdout file materialization
    WHEN outputs are built without stdout_as_file
    THEN no stdout_file output is returned
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    spec = _make_spec(outputs={})
    rendered = RenderedAction(argv=["echo"], output_files={})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(stdout=b"HELLO\n"),
    )

    assert result == {}


def test_build_outputs_creates_stdout_file_when_requested(tmp_path, monkeypatch):
    """
    GIVEN a successful action execution with sanitized stdout
    WHEN outputs are built with stdout_as_file enabled
    THEN sanitized stdout is stored as a managed text file output
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    stdout_bytes = b"STAR_STDOUT_BYTES\n"
    result = build_outputs(
        _make_spec(outputs={}),
        RenderedAction(argv=["echo"], output_files={}),
        _make_execution_result(returncode=0),
        _make_sanitized_output(stdout=stdout_bytes),
        stdout_as_file=True,
    )

    metadata = result["stdout_file"]
    assert metadata is not None
    assert metadata.status == "ready"
    assert metadata.extension == ".txt"
    assert metadata.mime_type == "text/plain"
    assert get_blob_path(metadata.id, cfg).read_bytes() == stdout_bytes


def test_build_outputs_creates_empty_stdout_file_for_empty_stdout(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a successful action execution with empty sanitized stdout
    WHEN outputs are built with stdout_as_file enabled
    THEN an empty managed text file is created
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    result = build_outputs(
        _make_spec(outputs={}),
        RenderedAction(argv=["echo"], output_files={}),
        _make_execution_result(returncode=0),
        _make_sanitized_output(stdout=b""),
        stdout_as_file=True,
    )

    metadata = result["stdout_file"]
    assert metadata is not None
    assert metadata.size_bytes == 0
    assert metadata.sha256 == EMPTY_SHA256


def test_build_outputs_returns_null_stdout_file_on_failed_command(
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a failed action execution
    WHEN outputs are built with stdout_as_file enabled
    THEN stdout_file is returned as null
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    result = build_outputs(
        _make_spec(outputs={}),
        RenderedAction(argv=["echo"], output_files={}),
        _make_execution_result(returncode=1),
        _make_sanitized_output(stdout=b"HELLO\n"),
        stdout_as_file=True,
    )

    assert result == {"stdout_file": None}


# ============================================================================
# Order Preservation
# ============================================================================


def test_outputs_builder__preserves_output_order(tmp_path, monkeypatch):
    """
    GIVEN multiple outputs in DSL
    WHEN build_outputs runs
    THEN outputs dict preserves declaration order
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"x")

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND),
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    result = build_outputs(
        spec,
        rendered,
        _make_execution_result(returncode=0),
        _make_sanitized_output(stdout=b"x"),
        stdout_as_file=True,
    )

    assert list(result.keys()) == ["cmd_out", "stdout_file"]


# ============================================================================
# Error Handling
# ============================================================================


def test_outputs_builder__raises_on_internal_failure(tmp_path, monkeypatch):
    """
    GIVEN unexpected error during output processing
    WHEN build_outputs runs
    THEN runtime error is raised
    """

    cfg = _make_settings(tmp_path)
    _patch_storage_to_settings(monkeypatch, cfg)

    placeholder = _create_placeholder(cfg=cfg)
    blob = get_blob_path(placeholder.id, cfg)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"x")

    monkeypatch.setattr(
        "star.actions.runtime.outputs_builder.finalize_command_output_file",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        }
    )
    rendered = RenderedAction(argv=["echo"], output_files={"cmd_out": placeholder.id})

    with pytest.raises(ActionRuntimeOutputError, match="Failed to materialize"):
        build_outputs(
            spec,
            rendered,
            _make_execution_result(returncode=0),
            _make_sanitized_output(),
        )

    assert load_file_metadata(placeholder.id, cfg) is None
