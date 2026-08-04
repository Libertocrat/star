"""Metadata persistence helpers for STAR-managed files."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from star.core.config import Settings, get_settings
from star.core.files.layout import ensure_storage_dirs, get_blob_path, get_meta_path
from star.core.schemas.files import FileMetadata

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def save_file_metadata(
    metadata: FileMetadata,
    settings: Settings | None = None,
) -> None:
    """Persist typed file metadata to JSON using an atomic replace.

    Args:
        metadata: Metadata model to persist.
        settings: Optional pre-loaded runtime settings.

    Raises:
        OSError: If the temporary sidecar cannot be written or atomically promoted.
    """

    meta_path = get_meta_path(metadata.id, settings)
    tmp_meta_path = meta_path.with_suffix(".json.tmp")
    payload = metadata.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    tmp_meta_path.write_text(serialized, encoding="utf-8")
    os.replace(tmp_meta_path, meta_path)


def load_file_metadata(
    file_id: UUID,
    settings: Settings | None = None,
) -> FileMetadata | None:
    """Load typed file metadata JSON for the given file id.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Parsed and validated file metadata model, or None if not found.

    Raises:
        OSError: If file cannot be read.
        json.JSONDecodeError: If metadata is invalid JSON.
        ValidationError: If schema validation fails.
    """

    meta_path = get_meta_path(file_id, settings)

    if not meta_path.exists():
        return None

    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    return FileMetadata.model_validate(payload)


def create_placeholder_file_metadata(
    *,
    original_filename: str,
    settings: Settings | None = None,
) -> FileMetadata:
    """Create and persist placeholder metadata for generated output files.

    Args:
        original_filename: Public original filename to store in metadata.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Persisted placeholder metadata in `pending` state.

    Raises:
        OSError: If storage directories or metadata sidecar cannot be written.
        ValueError: If generated metadata violates the persisted schema.
    """

    cfg = settings if settings is not None else get_settings()
    ensure_storage_dirs(cfg)

    file_id = uuid.uuid4()
    now_utc = datetime.now(UTC)
    metadata = FileMetadata(
        id=file_id,
        original_filename=original_filename,
        stored_filename=f"file_{file_id}.bin",
        mime_type="application/octet-stream",
        extension=".bin",
        size_bytes=0,
        sha256=EMPTY_SHA256,
        created_at=now_utc,
        updated_at=now_utc,
        status="pending",
    )
    save_file_metadata(metadata, cfg)
    return metadata


def delete_blob_file(
    file_id: UUID,
    settings: Settings | None = None,
) -> Path:
    """Delete the persisted blob file for a file id.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Deleted blob file path.

    Raises:
        FileNotFoundError: If the blob file does not exist.
        OSError: If deletion fails due to OS-level error.
    """

    blob_path = get_blob_path(file_id, settings)
    blob_path.unlink()
    return blob_path


def delete_metadata_file(
    file_id: UUID,
    settings: Settings | None = None,
) -> Path:
    """Delete the persisted metadata file for a file id.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Deleted metadata file path.

    Raises:
        FileNotFoundError: If the metadata file does not exist.
        OSError: If deletion fails due to OS-level error.
    """

    meta_path = get_meta_path(file_id, settings)
    meta_path.unlink()
    return meta_path
