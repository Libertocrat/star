"""Unit tests for STAR runtime renderer.

These tests validate deterministic argv generation and strict runtime argument
constraints for the STAR DSL action renderer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from star.actions.exceptions import ActionInvalidArgError, ActionRuntimeRenderError
from star.actions.models.core import (
    ActionSpec,
    ArgDef,
    CommandElement,
    FlagDef,
    OutputDef,
    OutputSource,
    OutputType,
    ParamType,
    SecretDelivery,
)
from star.actions.models.security import BinaryPolicy
from star.actions.runtime import file_manager
from star.actions.runtime.renderer import render_command
from star.actions.runtime.secret_manager import cleanup_secret_files
from star.core.config import Settings
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import (
    get_blob_path,
    get_secret_tmp_dir,
    load_file_metadata,
)


def _make_metadata(file_id, *, size_bytes: int = 10) -> FileMetadata:
    """Build a valid file metadata object for renderer tests.

    Args:
        file_id: File UUID associated with metadata.
        size_bytes: Reported file size in bytes.

    Returns:
        Validated `FileMetadata` instance.
    """

    now = datetime.now(tz=UTC)
    return FileMetadata(
        id=file_id,
        original_filename="input.txt",
        stored_filename=f"file_{file_id}.bin",
        mime_type="text/plain",
        extension=".txt",
        size_bytes=size_bytes,
        sha256="a" * 64,
        created_at=now,
        updated_at=now,
        status="ready",
    )


def _make_metadata_with_status(
    file_id,
    *,
    status: Literal["pending", "unverified", "ready"],
    size_bytes: int = 10,
) -> FileMetadata:
    """Build file metadata object with configurable lifecycle status.

    Args:
        file_id: File UUID associated with metadata.
        status: File lifecycle status.
        size_bytes: Reported file size in bytes.

    Returns:
        Validated `FileMetadata` instance.
    """

    now = datetime.now(tz=UTC)
    return FileMetadata(
        id=file_id,
        original_filename="input.txt",
        stored_filename=f"file_{file_id}.bin",
        mime_type="text/plain",
        extension=".txt",
        size_bytes=size_bytes,
        sha256="a" * 64,
        created_at=now,
        updated_at=now,
        status=status,
    )


def _make_settings(tmp_path: Path) -> Settings:
    """Build deterministic settings for renderer output tests.

    Args:
        tmp_path: Per-test temporary path.

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
    arg_defs: dict[str, ArgDef] | None = None,
    flag_defs: dict[str, FlagDef] | None = None,
    defaults: dict[str, object] | None = None,
    outputs: dict[str, OutputDef] | None = None,
    command_template: tuple[CommandElement, ...] | None = None,
) -> ActionSpec:
    """Build a minimal valid `ActionSpec` with optional overrides.

    Args:
        arg_defs: Optional runtime argument definitions.
        flag_defs: Optional runtime flag definitions.
        defaults: Optional default params mapping.
        outputs: Optional runtime output definitions.
        command_template: Optional command token sequence.

    Returns:
        Minimal valid `ActionSpec` ready for renderer tests.
    """

    template = (
        (cast(CommandElement, {"kind": "binary", "value": "echo"}),)
        if command_template is None
        else command_template
    )

    return ActionSpec(
        name="test.echo",
        namespace=(),
        module="test",
        action="echo",
        version=1,
        params_model=BaseModel,
        binary="echo",
        command_template=template,
        execution_policy=BinaryPolicy(allowed=("echo",), blocked=()),
        arg_defs={} if arg_defs is None else arg_defs,
        flag_defs={} if flag_defs is None else flag_defs,
        defaults={} if defaults is None else defaults,
        outputs={} if outputs is None else outputs,
        authors=None,
        tags=(),
        summary=None,
        description=None,
        deprecated=False,
        params_example=None,
    )


# ============================================================================
# Defaults
# ============================================================================


def test_render_command__uses_defaults_when_params_missing():
    """
    GIVEN an action with default argument value
    WHEN render_command is called with empty params
    THEN argv includes the default value
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=False, description="name")
        },
        defaults={"name": "fallback"},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "name"},
        ),
    )

    assert render_command(spec, {}) == ["echo", "fallback"]


def test_render_command__params_override_defaults():
    """
    GIVEN an action with default argument value
    WHEN render_command is called with explicit param
    THEN argv uses param value over default
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=False, description="name")
        },
        defaults={"name": "fallback"},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "name"},
        ),
    )

    assert render_command(spec, {"name": "override"}) == ["echo", "override"]


# ============================================================================
# None Validation
# ============================================================================


def test_render_command__rejects_none_values():
    """
    GIVEN a resolved parameter with None value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=False, description="name")
        },
        defaults={"name": None},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "name"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be None"):
        render_command(spec, {})


def test_render_command__rejects_none_in_params():
    """
    GIVEN a parameter explicitly set to None
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be None"):
        render_command(spec, {"name": None})


# ============================================================================
# String Validation
# ============================================================================


def test_render_command__rejects_non_string_value():
    """
    GIVEN a string argument receiving a non-string value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="must be a string"):
        render_command(spec, {"value": 123})


def test_render_command__rejects_empty_string():
    """
    GIVEN a string argument with empty string
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be empty"):
        render_command(spec, {"value": ""})


def test_render_command__rejects_whitespace_string():
    """
    GIVEN a string argument with whitespace-only value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be empty"):
        render_command(spec, {"value": "   \t"})


@pytest.mark.parametrize(
    "value",
    ["--help", "-v"],
    ids=["double_dash", "single_dash"],
)
def test_render_command__rejects_flag_like_string(value: str):
    """
    GIVEN a string argument with flag-like value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="cannot start with '-'"):
        render_command(spec, {"value": value})


def test_render_command__rejects_flag_like_with_leading_spaces():
    """
    GIVEN a string argument with leading-space flag-like value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="cannot start with '-'"):
        render_command(spec, {"value": "   --danger"})


def test_render_command__accepts_valid_string():
    """
    GIVEN a valid non-empty non-flag string value
    WHEN render_command is called
    THEN argv is rendered successfully
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(type=ParamType.STRING, required=True, description="value")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "value"},
        ),
    )

    assert render_command(spec, {"value": "hello-world"}) == ["echo", "hello-world"]


def test_render_command__enforces_string_min_length():
    """
    GIVEN a string argument with min_length constraint
    WHEN value length is below min_length
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(
                type=ParamType.STRING,
                required=True,
                constraints={"min_length": 3},
                description="value",
            )
        }
    )

    with pytest.raises(ActionInvalidArgError, match="length >= 3"):
        render_command(spec, {"value": "ab"})


def test_render_command__enforces_string_allowed_values():
    """
    GIVEN a string argument with allowed_values constraint
    WHEN value is not in allowed_values
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(
                type=ParamType.STRING,
                required=True,
                constraints={"allowed_values": ["alpha", "beta"]},
                description="value",
            )
        }
    )

    with pytest.raises(ActionInvalidArgError, match="must be one of"):
        render_command(spec, {"value": "gamma"})


# ============================================================================
# Numeric Validation
# ============================================================================


def test_render_command__rejects_non_numeric_value():
    """
    GIVEN a numeric argument receiving a non-numeric value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "count": ArgDef(type=ParamType.INT, required=True, description="count")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="must be numeric"):
        render_command(spec, {"count": "abc"})


def test_render_command__enforces_numeric_min():
    """
    GIVEN a numeric argument with minimum constraint
    WHEN value is below minimum
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "count": ArgDef(
                type=ParamType.INT,
                required=True,
                constraints={"min": 5},
                description="count",
            )
        }
    )

    with pytest.raises(
        ActionInvalidArgError,
        match="must be greater than or equal",
    ):
        render_command(spec, {"count": 4})


def test_render_command__enforces_numeric_max():
    """
    GIVEN a numeric argument with maximum constraint
    WHEN value is above maximum
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "count": ArgDef(
                type=ParamType.FLOAT,
                required=True,
                constraints={"max": 2.5},
                description="count",
            )
        }
    )

    with pytest.raises(ActionInvalidArgError, match="must be less than or equal"):
        render_command(spec, {"count": 3.0})


def test_render_command__accepts_valid_float_value():
    """
    GIVEN a float argument within allowed range
    WHEN render_command is called
    THEN argv is rendered successfully
    """

    spec = _make_spec(
        arg_defs={
            "value": ArgDef(
                type=ParamType.FLOAT,
                required=True,
                constraints={"min": 1.0, "max": 5.0},
                description="value",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "value"},
        ),
    )

    assert render_command(spec, {"value": 3.5}) == ["echo", "3.5"]


# ============================================================================
# Secret Delivery
# ============================================================================


def test_render_command__delivers_secret_to_stdin_without_argv_leak():
    """
    GIVEN a secret argument with stdin delivery
    WHEN render_command is called
    THEN argv omits the secret and stdin_data receives the secret bytes
    """

    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="stdin", append_newline=True),
                description="password",
            )
        },
        command_template=({"kind": "binary", "value": "cat"},),
    )

    rendered = render_command(spec, {"password": "topsecret"})

    assert rendered.argv == ["cat"]
    assert "topsecret" not in rendered.argv
    assert rendered.stdin_data == b"topsecret\n"
    assert rendered.secret_redactions == ("topsecret",)
    assert "topsecret" not in repr(rendered)


def test_render_command__honors_secret_delivery_without_newline():
    """
    GIVEN a secret argument with append_newline disabled
    WHEN render_command is called
    THEN stdin_data contains the exact secret bytes
    """

    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="stdin", append_newline=False),
                description="password",
            )
        },
        command_template=({"kind": "binary", "value": "cat"},),
    )

    rendered = render_command(spec, {"password": "topsecret"})

    assert rendered.stdin_data == b"topsecret"


def test_render_command__delivers_secret_to_file_without_argv_leak(tmp_path: Path):
    """
    GIVEN a secret argument with file delivery
    WHEN render_command is called
    THEN argv receives a file reference and the secret file contains the secret
    """

    settings = _make_settings(tmp_path)
    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file"),
                description="password",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "file:{password}"},
        ),
    )

    rendered = render_command(spec, {"password": "topsecret"}, settings=settings)

    try:
        assert rendered.argv[0] == "echo"
        assert rendered.argv[1].startswith("file:")
        secret_path = Path(rendered.argv[1].removeprefix("file:"))
        assert secret_path.parent == get_secret_tmp_dir(settings)
        assert secret_path.read_text(encoding="utf-8") == "topsecret"
        assert secret_path.stat().st_mode & 0o777 == 0o600
        assert rendered.secret_files == (secret_path,)
        assert rendered.secret_redactions == ("topsecret",)
        assert rendered.stdin_data is None
        assert "topsecret" not in rendered.argv[1]
        assert "topsecret" not in repr(rendered)
    finally:
        cleanup_secret_files(rendered.secret_files, settings=settings)


def test_render_command__file_secret_appends_newline_when_configured(
    tmp_path: Path,
):
    """
    GIVEN a file-delivered secret with append_newline enabled
    WHEN render_command is called
    THEN the materialized secret file ends with one newline
    """

    settings = _make_settings(tmp_path)
    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file", append_newline=True),
                description="password",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "password"},
        ),
    )

    rendered = render_command(spec, {"password": "topsecret"}, settings=settings)

    try:
        secret_path = Path(rendered.argv[1])
        assert secret_path.read_text(encoding="utf-8") == "topsecret\n"
    finally:
        cleanup_secret_files(rendered.secret_files, settings=settings)


def test_render_command__supports_multiple_file_secret_deliveries(tmp_path: Path):
    """
    GIVEN two secret arguments with file delivery
    WHEN render_command is called
    THEN each secret receives a separate owned temp file
    """

    settings = _make_settings(tmp_path)
    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file"),
                description="password",
            ),
            "token": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file"),
                description="token",
            ),
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "password"},
            {"kind": "arg", "name": "token"},
        ),
    )

    rendered = render_command(
        spec,
        {"password": "topsecret", "token": "othertopsecret"},
        settings=settings,
    )

    try:
        first_path = Path(rendered.argv[1])
        second_path = Path(rendered.argv[2])
        assert first_path != second_path
        assert first_path.read_text(encoding="utf-8") == "topsecret"
        assert second_path.read_text(encoding="utf-8") == "othertopsecret"
        assert rendered.secret_files == (first_path, second_path)
        assert rendered.secret_redactions == ("topsecret", "othertopsecret")
    finally:
        cleanup_secret_files(rendered.secret_files, settings=settings)


def test_render_command__cleans_file_secret_when_render_later_fails(
    tmp_path: Path,
):
    """
    GIVEN a file-delivered secret and an invalid later command token
    WHEN render_command fails after creating the secret file
    THEN the owned secret file is cleaned up
    """

    settings = _make_settings(tmp_path)
    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="file"),
                description="password",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "password"},
            cast(CommandElement, {"kind": "unsupported"}),
        ),
    )

    with pytest.raises(ActionRuntimeRenderError, match="Unsupported command token"):
        render_command(spec, {"password": "topsecret"}, settings=settings)

    assert list(get_secret_tmp_dir(settings).glob("secret_*.tmp")) == []


def test_render_command__rejects_secret_arg_token_at_runtime():
    """
    GIVEN a runtime ActionSpec that references a secret as an argv arg token
    WHEN render_command is called
    THEN ActionInvalidArgError is raised as defense in depth
    """

    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="stdin"),
                description="password",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "password"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be rendered as argv"):
        render_command(spec, {"password": "topsecret"})


def test_render_command__rejects_secret_const_placeholder_at_runtime():
    """
    GIVEN a runtime ActionSpec that interpolates a secret in a const token
    WHEN render_command is called
    THEN ActionInvalidArgError is raised as defense in depth
    """

    spec = _make_spec(
        arg_defs={
            "password": ArgDef(
                type=ParamType.SECRET,
                required=True,
                delivery=SecretDelivery(type="stdin"),
                description="password",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "secret:{password}"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="cannot be rendered as argv"):
        render_command(spec, {"password": "topsecret"})


# ============================================================================
# Managed File Resolution
# ============================================================================


def test_render_command__resolves_file_id_to_blob_path(
    monkeypatch,
    tmp_path: Path,
):
    """
    GIVEN a valid file_id argument
    WHEN metadata and blob are available
    THEN argv uses the resolved blob path
    """

    file_id = uuid4()
    blob_path = tmp_path / f"file_{file_id}.bin"
    blob_path.write_bytes(b"ok")

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata(file_id, size_bytes=2),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "file"},
        ),
    )

    assert render_command(spec, {"file": file_id}) == ["cat", str(blob_path)]


def test_render_command__resolves_file_id_with_explicit_settings(tmp_path: Path):
    """
    GIVEN a managed file persisted under an injected storage root
    WHEN render_command resolves a file_id argument with explicit settings
    THEN argv uses the blob path from that settings snapshot
    """

    cfg = _make_settings(tmp_path)
    metadata = file_manager.create_ready_file_from_bytes(
        original_filename="input.txt",
        content=b"ok",
        extension=".txt",
        mime_type="text/plain",
        settings=cfg,
    )
    blob_path = get_blob_path(metadata.id, cfg)

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "file"},
        ),
    )

    assert render_command(spec, {"file": metadata.id}, settings=cfg) == [
        "cat",
        str(blob_path),
    ]


def test_render_command__fails_when_file_metadata_is_missing(monkeypatch):
    """
    GIVEN a file_id argument
    WHEN metadata cannot be loaded
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: None,
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: Path("/unused"),
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="was not found"):
        render_command(spec, {"file": file_id})


def test_renderer__rejects_non_ready_file_input(monkeypatch, tmp_path: Path):
    """
    GIVEN file_id with status != ready
    WHEN render_command runs
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()
    blob_path = tmp_path / f"file_{file_id}.bin"
    blob_path.write_bytes(b"ok")

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata_with_status(file_id, status="pending"),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "file"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="not ready for use"):
        render_command(spec, {"file": file_id})


def test_render_command__fails_when_blob_path_is_missing(monkeypatch, tmp_path: Path):
    """
    GIVEN a file_id argument with existing metadata
    WHEN blob file is missing on disk
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()
    missing_blob_path = tmp_path / "missing.bin"

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata(file_id, size_bytes=10),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: missing_blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="blob"):
        render_command(spec, {"file": file_id})


def test_render_command__fails_when_file_size_exceeds_max_size(
    monkeypatch,
    tmp_path: Path,
):
    """
    GIVEN a file_id argument with max_size constraint
    WHEN metadata size exceeds max_size
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()
    blob_path = tmp_path / f"file_{file_id}.bin"
    blob_path.write_bytes(b"ok")

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata(file_id, size_bytes=999),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(
                type=ParamType.FILE_ID,
                required=True,
                constraints={"max_size": 100},
                description="file",
            )
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "file"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="file size must be"):
        render_command(spec, {"file": file_id})


def test_render_command__fails_when_file_extension_not_allowed(
    monkeypatch,
    tmp_path: Path,
):
    """
    GIVEN a file_id argument with allowed_extensions constraint
    WHEN metadata extension is not allowed
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()
    blob_path = tmp_path / f"file_{file_id}.bin"
    blob_path.write_bytes(b"ok")

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata(file_id, size_bytes=10),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(
                type=ParamType.FILE_ID,
                required=True,
                constraints={"allowed_extensions": ["csv", "json"]},
                description="file",
            )
        }
    )

    with pytest.raises(ActionInvalidArgError, match="extension"):
        render_command(spec, {"file": file_id})


def test_render_command__fails_when_file_mime_type_not_allowed(
    monkeypatch,
    tmp_path: Path,
):
    """
    GIVEN a file_id argument with allowed_mime_types constraint
    WHEN metadata mime_type is not allowed
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()
    blob_path = tmp_path / f"file_{file_id}.bin"
    blob_path.write_bytes(b"ok")

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: _make_metadata(file_id, size_bytes=10),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: blob_path,
    )

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(
                type=ParamType.FILE_ID,
                required=True,
                constraints={"allowed_mime_types": ["application/pdf"]},
                description="file",
            )
        }
    )

    with pytest.raises(ActionInvalidArgError, match="mime type"):
        render_command(spec, {"file": file_id})


def test_render_command__enforces_list_min_items():
    """
    GIVEN a list argument with min_items constraint
    WHEN list value has fewer items than min_items
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "items": ArgDef(
                type=ParamType.LIST,
                items=ParamType.STRING,
                required=True,
                constraints={"min_items": 2},
                description="items",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "items"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="at least 2"):
        render_command(spec, {"items": ["one"]})


def test_render_command__enforces_list_max_items():
    """
    GIVEN a list argument with max_items constraint
    WHEN list value has more items than max_items
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "items": ArgDef(
                type=ParamType.LIST,
                items=ParamType.STRING,
                required=True,
                constraints={"max_items": 2},
                description="items",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "items"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="at most 2"):
        render_command(spec, {"items": ["one", "two", "three"]})


def test_render_command_list_string_expands():
    """
    GIVEN a list[string] argument
    WHEN render_command is called
    THEN argv contains all elements expanded in order
    """

    spec = _make_spec(
        arg_defs={
            "items": ArgDef(
                type=ParamType.LIST,
                items=ParamType.STRING,
                required=True,
                description="items",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "items"},
        ),
    )

    assert render_command(spec, {"items": ["one", "two", "three"]}) == [
        "echo",
        "one",
        "two",
        "three",
    ]


def test_render_command_list_file_id_resolves_paths(monkeypatch, tmp_path: Path):
    """
    GIVEN a list[file_id] argument
    WHEN render_command is called
    THEN argv contains resolved file paths
    """

    first_id = uuid4()
    second_id = uuid4()
    first_blob = tmp_path / f"file_{first_id}.bin"
    second_blob = tmp_path / f"file_{second_id}.bin"
    first_blob.write_bytes(b"a")
    second_blob.write_bytes(b"b")

    blobs = {first_id: first_blob, second_id: second_blob}

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda file_id, settings=None: _make_metadata(file_id),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda file_id, settings=None: blobs[file_id],
    )

    spec = _make_spec(
        arg_defs={
            "files": ArgDef(
                type=ParamType.LIST,
                items=ParamType.FILE_ID,
                required=True,
                description="files",
            )
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "files"},
        ),
    )

    assert render_command(spec, {"files": [first_id, second_id]}) == [
        "cat",
        str(first_blob),
        str(second_blob),
    ]


def test_render_command_list_file_id_uses_explicit_settings(tmp_path: Path):
    """
    GIVEN managed files persisted under an injected storage root
    WHEN render_command resolves a list[file_id] argument with explicit settings
    THEN argv uses ordered blob paths from that settings snapshot
    """

    cfg = _make_settings(tmp_path)
    first_metadata = file_manager.create_ready_file_from_bytes(
        original_filename="first.txt",
        content=b"a",
        extension=".txt",
        mime_type="text/plain",
        settings=cfg,
    )
    second_metadata = file_manager.create_ready_file_from_bytes(
        original_filename="second.txt",
        content=b"b",
        extension=".txt",
        mime_type="text/plain",
        settings=cfg,
    )
    first_blob = get_blob_path(first_metadata.id, cfg)
    second_blob = get_blob_path(second_metadata.id, cfg)

    spec = _make_spec(
        arg_defs={
            "files": ArgDef(
                type=ParamType.LIST,
                items=ParamType.FILE_ID,
                required=True,
                description="files",
            )
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "files"},
        ),
    )

    assert render_command(
        spec,
        {"files": [first_metadata.id, second_metadata.id]},
        settings=cfg,
    ) == [
        "cat",
        str(first_blob),
        str(second_blob),
    ]


def test_render_command_list_preserves_order():
    """
    GIVEN a list argument
    WHEN render_command is called
    THEN element order is preserved in argv
    """

    spec = _make_spec(
        arg_defs={
            "items": ArgDef(
                type=ParamType.LIST,
                items=ParamType.STRING,
                required=True,
                description="items",
            )
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "items"},
        ),
    )

    assert render_command(spec, {"items": ["b", "a", "c"]}) == ["echo", "b", "a", "c"]


def test_render_command_list_invalid_uuid():
    """
    GIVEN a list[file_id] with invalid UUID
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "files": ArgDef(
                type=ParamType.LIST,
                items=ParamType.FILE_ID,
                required=True,
                description="files",
            )
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "files"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="valid file_id"):
        render_command(spec, {"files": ["not-a-uuid"]})


def test_render_command_list_missing_file(monkeypatch):
    """
    GIVEN a list[file_id] with missing metadata
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    file_id = uuid4()

    monkeypatch.setattr(
        "star.actions.runtime.renderer.load_file_metadata",
        lambda _, settings=None: None,
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.get_blob_path",
        lambda _, settings=None: Path("/unused"),
    )

    spec = _make_spec(
        arg_defs={
            "files": ArgDef(
                type=ParamType.LIST,
                items=ParamType.FILE_ID,
                required=True,
                description="files",
            )
        },
        command_template=(
            {"kind": "binary", "value": "cat"},
            {"kind": "arg", "name": "files"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="was not found"):
        render_command(spec, {"files": [file_id]})


def test_render_command__fails_when_file_id_is_invalid_uuid():
    """
    GIVEN a file_id argument with malformed UUID value
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "file": ArgDef(type=ParamType.FILE_ID, required=True, description="file")
        }
    )

    with pytest.raises(ActionInvalidArgError, match="valid file_id"):
        render_command(spec, {"file": "not-a-uuid"})


# ============================================================================
# Flags
# ============================================================================


@pytest.mark.parametrize(
    "value",
    [1, "true", object()],
    ids=["int_one", "string_true", "object_instance"],
)
def test_render_command__flag_requires_strict_true(value: object):
    """
    GIVEN a flag parameter with truthy non-True value
    WHEN render_command is called
    THEN flag token is not included
    """

    spec = _make_spec(
        flag_defs={"verbose": FlagDef(value="-v", default=False, description="v")},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "flag", "name": "verbose"},
        ),
    )

    assert render_command(spec, {"verbose": value}) == ["echo"]


def test_render_command__default_false_flag_is_excluded():
    """
    GIVEN a flag with default False
    WHEN params omit the flag
    THEN flag token is excluded from argv
    """

    spec = _make_spec(
        flag_defs={"verbose": FlagDef(value="-v", default=False, description="v")},
        defaults={"verbose": False},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "flag", "name": "verbose"},
        ),
    )

    assert render_command(spec, {}) == ["echo"]


def test_render_command__default_true_flag_is_included():
    """
    GIVEN a flag with default True
    WHEN params omit the flag
    THEN flag token is included in argv
    """

    spec = _make_spec(
        flag_defs={"verbose": FlagDef(value="-v", default=True, description="v")},
        defaults={"verbose": True},
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "flag", "name": "verbose"},
        ),
    )

    assert render_command(spec, {}) == ["echo", "-v"]


# ============================================================================
# Command Template
# ============================================================================


def test_render_command__preserves_command_token_order():
    """
    GIVEN mixed binary/const/arg tokens
    WHEN render_command is called
    THEN argv preserves template token order
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "-n"},
            {"kind": "arg", "name": "name"},
            {"kind": "const", "value": "!"},
        ),
    )

    assert render_command(spec, {"name": "star"}) == ["echo", "-n", "star", "!"]


def test_render_command__interpolates_const_placeholders_for_supported_types():
    """
    GIVEN const literals containing string/int/float placeholders
    WHEN render_command is called
    THEN placeholders are interpolated into argv tokens deterministically
    """

    spec = _make_spec(
        arg_defs={
            "user": ArgDef(type=ParamType.STRING, required=True, description="user"),
            "count": ArgDef(type=ParamType.INT, required=True, description="count"),
            "ratio": ArgDef(type=ParamType.FLOAT, required=True, description="ratio"),
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "u:{user}"},
            {"kind": "const", "value": "c:{count}"},
            {"kind": "const", "value": "r:{ratio}"},
            {"kind": "const", "value": "mix:{user}_{count}"},
        ),
    )

    assert render_command(
        spec,
        {"user": "alice", "count": 3, "ratio": 2.5},
    ) == ["echo", "u:alice", "c:3", "r:2.5", "mix:alice_3"]


def test_render_command__interpolates_repeated_const_placeholder():
    """
    GIVEN a const literal with repeated placeholder name
    WHEN render_command is called
    THEN each placeholder occurrence is replaced with the same arg value
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "{name}:{name}"},
        ),
    )

    assert render_command(spec, {"name": "star"}) == ["echo", "star:star"]


def test_render_command__rejects_const_template_value_with_whitespace():
    """
    GIVEN a string arg used by const placeholder containing whitespace
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "user:{name}"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="cannot contain whitespace"):
        render_command(spec, {"name": "bad value"})


def test_render_command__rejects_const_template_value_with_control_characters():
    """
    GIVEN a string arg used by const placeholder containing control characters
    WHEN render_command is called
    THEN ActionInvalidArgError is raised
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "user:{name}"},
        ),
    )

    with pytest.raises(ActionInvalidArgError, match="control characters"):
        render_command(spec, {"name": "bad\u0007value"})


def test_render_command__supports_multiple_args_and_flags():
    """
    GIVEN a command template with multiple args and flags
    WHEN render_command is called
    THEN argv includes all resolved values in template order
    """

    spec = _make_spec(
        arg_defs={
            "first": ArgDef(type=ParamType.STRING, required=True, description="first"),
            "second": ArgDef(type=ParamType.INT, required=True, description="second"),
        },
        flag_defs={
            "verbose": FlagDef(value="-v", default=False, description="v"),
            "debug": FlagDef(value="--debug", default=False, description="d"),
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "flag", "name": "verbose"},
            {"kind": "arg", "name": "first"},
            {"kind": "flag", "name": "debug"},
            {"kind": "arg", "name": "second"},
        ),
    )

    assert render_command(
        spec,
        {"first": "abc", "second": 7, "verbose": True, "debug": False},
    ) == ["echo", "-v", "abc", "7"]


# ============================================================================
# Output File Handling
# ============================================================================


def test_renderer__file_command_creates_placeholder_metadata(tmp_path, monkeypatch):
    """
    GIVEN file+command output
    WHEN render_command runs
    THEN metadata is created with status "pending"
    """

    cfg = _make_settings(tmp_path)
    monkeypatch.setattr(
        "star.actions.runtime.renderer.create_command_output_placeholders",
        lambda spec, settings=None: file_manager.create_command_output_placeholders(
            spec, settings=cfg
        ),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.resolve_output_blob_path",
        lambda file_id, settings=None: file_manager.resolve_output_blob_path(
            file_id, settings=cfg
        ),
    )

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "output", "name": "cmd_out"},
        ),
    )

    rendered = render_command(spec, {})
    file_id = rendered.output_files["cmd_out"]
    metadata = load_file_metadata(file_id, cfg)

    assert metadata is not None
    assert metadata.status == "pending"


def test_renderer__file_command_injects_blob_path(tmp_path, monkeypatch):
    """
    GIVEN output token in command
    WHEN render_command runs
    THEN argv includes blob path
    """

    cfg = _make_settings(tmp_path)
    monkeypatch.setattr(
        "star.actions.runtime.renderer.create_command_output_placeholders",
        lambda spec, settings=None: file_manager.create_command_output_placeholders(
            spec, settings=cfg
        ),
    )
    monkeypatch.setattr(
        "star.actions.runtime.renderer.resolve_output_blob_path",
        lambda file_id, settings=None: file_manager.resolve_output_blob_path(
            file_id, settings=cfg
        ),
    )

    spec = _make_spec(
        outputs={
            "cmd_out": OutputDef(type=OutputType.FILE, source=OutputSource.COMMAND)
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "output", "name": "cmd_out"},
        ),
    )

    rendered = render_command(spec, {})
    file_id = rendered.output_files["cmd_out"]
    assert rendered.argv == ["echo", str(get_blob_path(file_id, cfg).resolve())]


def test_renderer__file_stdout_does_not_create_metadata(tmp_path, monkeypatch):
    """
    GIVEN a file output sourced from stdout
    WHEN render_command runs
    THEN no metadata is created
    """

    cfg = _make_settings(tmp_path)
    monkeypatch.setattr(
        "star.actions.runtime.renderer.create_command_output_placeholders",
        lambda spec, settings=None: file_manager.create_command_output_placeholders(
            spec, settings=cfg
        ),
    )

    spec = _make_spec(
        outputs={
            "stdout_file": OutputDef(type=OutputType.FILE, source=OutputSource.STDOUT)
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "hello"},
        ),
    )

    rendered = render_command(spec, {})

    assert rendered.output_files == {}


def test_renderer__file_stdout_does_not_modify_argv(tmp_path, monkeypatch):
    """
    GIVEN a file output sourced from stdout
    WHEN render_command runs
    THEN argv unchanged
    """

    cfg = _make_settings(tmp_path)
    monkeypatch.setattr(
        "star.actions.runtime.renderer.create_command_output_placeholders",
        lambda spec, settings=None: file_manager.create_command_output_placeholders(
            spec, settings=cfg
        ),
    )

    spec = _make_spec(
        outputs={
            "stdout_file": OutputDef(type=OutputType.FILE, source=OutputSource.STDOUT)
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "const", "value": "hello"},
        ),
    )

    rendered = render_command(spec, {})

    assert rendered.argv == ["echo", "hello"]


# ============================================================================
# Edge Cases
# ============================================================================


def test_render_command__wraps_unexpected_errors(monkeypatch):
    """
    GIVEN an unexpected internal runtime exception
    WHEN render_command is called
    THEN ActionRuntimeRenderError is raised
    """

    spec = _make_spec(
        arg_defs={
            "name": ArgDef(type=ParamType.STRING, required=True, description="name")
        },
        command_template=(
            {"kind": "binary", "value": "echo"},
            {"kind": "arg", "name": "name"},
        ),
    )

    def _explode(*_args, **_kwargs):
        """Raise a deterministic runtime failure for error-wrapping tests."""
        raise RuntimeError("boom")

    monkeypatch.setattr("star.actions.runtime.renderer._resolve_arg", _explode)

    with pytest.raises(ActionRuntimeRenderError, match="Unexpected failure"):
        render_command(spec, {"name": "safe"})
