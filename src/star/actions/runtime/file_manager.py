"""Runtime file lifecycle helpers for STAR action outputs."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from star.actions.models.core import ActionSpec, OutputSource, OutputType
from star.core.config import Settings
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import (
    EMPTY_SHA256,
    compute_sha256_for_file,
    create_placeholder_file_metadata,
    delete_blob_file,
    delete_metadata_file,
    detect_mime_for_file,
    ensure_storage_dirs,
    get_blob_path,
    save_file_metadata,
)

logger = logging.getLogger("star.actions.runtime.file_manager")


def create_command_output_placeholders(
    spec: ActionSpec,
    settings: Settings | None = None,
) -> dict[str, uuid.UUID]:
    """Create pending placeholder metadata for `file + command` outputs.

    Args:
        spec: Runtime action specification.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Mapping of output name to created placeholder file id.
    """

    output_files: dict[str, uuid.UUID] = {}

    for output_name, output_def in spec.outputs.items():
        if output_def.type != OutputType.FILE:
            continue
        if output_def.source != OutputSource.COMMAND:
            continue

        metadata = create_placeholder_file_metadata(
            original_filename=f"{spec.action}.{output_name}.bin",
            settings=settings,
        )
        output_files[output_name] = metadata.id

    return output_files


def resolve_output_blob_path(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> str:
    """Resolve absolute blob path for one output placeholder file id.

    Args:
        file_id: Placeholder file UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Absolute blob path string.
    """

    return str(get_blob_path(file_id, settings).resolve())


def cleanup_output_placeholders(
    output_files: Mapping[str, uuid.UUID],
    settings: Settings | None = None,
) -> None:
    """Delete placeholder metadata/blob artifacts best-effort.

    Args:
        output_files: Mapping of output names to placeholder file ids.
        settings: Optional pre-loaded runtime settings.
    """

    for file_id in output_files.values():
        cleanup_output_file(file_id, settings=settings)


def cleanup_output_file(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> None:
    """Delete one output file metadata/blob pair best-effort.

    Args:
        file_id: File UUID to cleanup.
        settings: Optional pre-loaded runtime settings.
    """

    try:
        delete_blob_file(file_id, settings)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Failed to delete output blob during cleanup")

    try:
        delete_metadata_file(file_id, settings)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Failed to delete output metadata during cleanup")


def finalize_command_output_file(
    *,
    file_id: uuid.UUID,
    action_name: str,
    output_name: str,
    settings: Settings | None = None,
) -> FileMetadata:
    """Finalize one command-generated output file into ready metadata.

    Args:
        file_id: Placeholder file UUID.
        action_name: Action short name.
        output_name: Output key name.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Finalized `ready` FileMetadata.

    Raises:
        FileNotFoundError: If metadata is missing.
        OSError: If storage operations fail.
        ValueError: If metadata payload becomes invalid.
    """

    from star.core.utils.file_storage import load_file_metadata

    metadata = load_file_metadata(file_id, settings)
    if metadata is None:
        raise FileNotFoundError(f"Output metadata '{file_id}' was not found")

    blob_path = get_blob_path(file_id, settings)
    if not blob_path.exists():
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(b"")

    now_utc = datetime.now(UTC)
    unverified = metadata.model_copy(
        update={
            "status": "unverified",
            "updated_at": now_utc,
        }
    )
    save_file_metadata(unverified, settings)

    size_bytes = blob_path.stat().st_size
    sha256 = compute_sha256_for_file(blob_path)
    mime_type = "application/octet-stream"
    if size_bytes > 0:
        mime_type = detect_mime_for_file(blob_path)

    final_metadata = unverified.model_copy(
        update={
            "original_filename": f"{action_name}.{output_name}.bin",
            "mime_type": mime_type,
            "extension": ".bin",
            "size_bytes": size_bytes,
            "sha256": sha256 if size_bytes > 0 else EMPTY_SHA256,
            "status": "ready",
            "updated_at": datetime.now(UTC),
        }
    )
    save_file_metadata(final_metadata, settings)
    return final_metadata


def create_ready_file_from_bytes(
    *,
    original_filename: str,
    content: bytes,
    extension: str,
    mime_type: str,
    settings: Settings | None = None,
) -> FileMetadata:
    """Create a ready STAR-managed file from provided content bytes.

    Args:
        original_filename: Public original filename.
        content: Blob content to persist.
        extension: Public extension metadata (e.g. `.txt`).
        mime_type: Public MIME metadata.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Persisted ready file metadata.
    """

    cfg = settings
    ensure_storage_dirs(cfg)

    file_id = uuid.uuid4()
    blob_path = get_blob_path(file_id, cfg)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(content)

    now_utc = datetime.now(UTC)
    metadata = FileMetadata(
        id=file_id,
        original_filename=original_filename,
        stored_filename=f"file_{file_id}.bin",
        mime_type=mime_type,
        extension=extension,
        size_bytes=len(content),
        sha256=(compute_sha256_for_file(blob_path) if content else EMPTY_SHA256),
        created_at=now_utc,
        updated_at=now_utc,
        status="ready",
    )
    save_file_metadata(metadata, cfg)
    return metadata


def blob_exists_for_file(file_id: uuid.UUID, settings: Settings | None = None) -> bool:
    """Return whether the blob exists for a file id.

    Args:
        file_id: File UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        True if the blob path exists.
    """

    return get_blob_path(file_id, settings).exists()


def create_empty_blob_for_file(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> Path:
    """Create an empty blob for a file id if missing.

    Args:
        file_id: File UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Blob path.
    """

    blob_path = get_blob_path(file_id, settings)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    if not blob_path.exists():
        blob_path.write_bytes(b"")
    return blob_path
