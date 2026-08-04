"""Runtime file lifecycle helpers for STAR action outputs."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Mapping

from star.actions.models.core import ActionSpec, OutputSource, OutputType
from star.core.config import Settings
from star.core.files import (
    LocalManagedFileStore,
    delete_blob_file,
    delete_metadata_file,
)
from star.core.schemas.files import FileMetadata

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
    store: LocalManagedFileStore | None = None

    for output_name, output_def in spec.outputs.items():
        if output_def.type != OutputType.FILE:
            continue
        if output_def.source != OutputSource.COMMAND:
            continue

        if store is None:
            store = LocalManagedFileStore(settings)
        metadata = store.create_pending_output(
            original_filename=f"{spec.action}.{output_name}.bin",
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

    blob_path = LocalManagedFileStore(settings).resolve_local_blob_path(file_id)
    return str(blob_path.resolve())


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

    return LocalManagedFileStore(settings).finalize_generated_file(
        file_id=file_id,
        original_filename=f"{action_name}.{output_name}.bin",
    )


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
        extension: Public extension metadata.
        mime_type: Public MIME metadata.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Persisted ready file metadata.
    """

    return LocalManagedFileStore(settings).create_ready_file_from_bytes(
        original_filename=original_filename,
        content=content,
        extension=extension,
        mime_type=mime_type,
    )


def blob_exists_for_file(file_id: uuid.UUID, settings: Settings | None = None) -> bool:
    """Return whether the blob exists for a file id.

    Args:
        file_id: File UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        True if the blob path exists.
    """

    return LocalManagedFileStore(settings).blob_exists(file_id)


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

    return LocalManagedFileStore(settings).create_empty_blob(file_id)
