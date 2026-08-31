"""Metadata persistence helpers for STAR-managed files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from star.core.config import Settings, get_settings
from star.core.files.filesystem import unlink_managed_blob, unlink_managed_metadata
from star.core.files.layout import (
    ensure_storage_dirs,
    get_blob_filename,
    get_blob_path,
    get_meta_lock_path,
    get_meta_path,
)
from star.core.schemas.files import FileMetadata

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_METADATA_ETAG_PATTERN = re.compile(r'^"[a-f0-9]{64}"$')


def metadata_etag(metadata: FileMetadata) -> str:
    """Return a strong opaque ETag for one canonical metadata representation.

    Args:
        metadata: Validated metadata record.

    Returns:
        Quoted SHA-256 entity tag suitable for an HTTP `ETag` header.
    """

    serialized = json.dumps(
        metadata.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f'"{hashlib.sha256(serialized).hexdigest()}"'


def is_metadata_etag(value: str) -> bool:
    """Return whether a header value is a single strong STAR metadata ETag.

    Args:
        value: Candidate `If-Match` header value.

    Returns:
        True only for one quoted SHA-256 metadata entity tag.
    """

    return _METADATA_ETAG_PATTERN.fullmatch(value) is not None


@contextmanager
def metadata_lock(
    file_id: UUID,
    settings: Settings | None = None,
) -> Iterator[None]:
    """Acquire a process-shared advisory lock for one metadata sidecar.

    Args:
        file_id: UUID whose server-derived metadata lock to acquire.
        settings: Optional pre-loaded runtime settings.

    Yields:
        None while the caller exclusively owns the metadata mutation lock.

    Raises:
        OSError: If the lock cannot be created or safely opened.
    """

    import fcntl

    ensure_storage_dirs(settings)
    lock_path = get_meta_lock_path(file_id, settings)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Metadata lock path is not a regular file.")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
    payload = metadata.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=meta_path.parent,
        prefix=f".{meta_path.name}.",
        suffix=".tmp",
        text=True,
    )
    tmp_meta_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(serialized)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_meta_path, meta_path)
    finally:
        try:
            tmp_meta_path.unlink()
        except FileNotFoundError:
            pass


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
        file_name=original_filename,
        tags=[],
        stored_filename=get_blob_filename(file_id),
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

    cfg = settings if settings is not None else get_settings()
    unlink_managed_blob(file_id, cfg)
    blob_path = get_blob_path(file_id, cfg)
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

    cfg = settings if settings is not None else get_settings()
    unlink_managed_metadata(file_id, cfg)
    meta_path = get_meta_path(file_id, cfg)
    return meta_path
