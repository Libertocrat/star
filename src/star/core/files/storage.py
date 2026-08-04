"""Local managed file store implementation for STAR."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Protocol

from star.core.config import Settings, get_settings
from star.core.files.descriptors import (
    FileContentDescriptor,
    FileListPage,
    UploadChecksum,
)
from star.core.files.exceptions import (
    ChecksumMismatchError,
    EmptyManagedFileError,
    InvalidChecksumAlgorithmError,
    InvalidManagedFileMetadataError,
    ManagedFileNotFoundError,
    ManagedFileStorageError,
    ManagedFileTooLargeError,
)
from star.core.files.layout import (
    ensure_storage_dirs,
    get_blob_path,
    get_meta_dir,
    get_meta_path,
    get_tmp_dir,
)
from star.core.files.listing import (
    apply_filters,
    apply_pagination,
    apply_sort,
)
from star.core.files.metadata import (
    EMPTY_SHA256,
    create_placeholder_file_metadata,
    delete_blob_file,
    delete_metadata_file,
    load_file_metadata,
    save_file_metadata,
)
from star.core.files.mime import (
    compute_sha256_for_file,
    detect_mime_for_file,
    validate_extension_and_mime,
)
from star.core.schemas.files import FileMetadata

logger = logging.getLogger("star.core.files")


class AsyncChunkReader(Protocol):
    """Minimal async byte stream contract consumed by file uploads.

    Implementations may be FastAPI `UploadFile` objects or test doubles. The
    protocol intentionally requires only async byte reads so `core/files` stays
    independent from FastAPI transport types.
    """

    async def read(self, size: int = -1) -> bytes:
        """Read up to `size` bytes from the stream.

        Args:
            size: Maximum bytes to read, or -1 for implementation default.

        Returns:
            Bytes read from the stream, or empty bytes at EOF.
        """


class ManagedFileStore(Protocol):
    """Storage-neutral API for STAR-managed file lifecycle operations.

    This Protocol defines the operations that routes and action runtime code
    need from managed storage. Local-only capabilities remain explicitly named
    so future object-store implementations do not imply host-path support.
    """

    async def upload_stream(
        self,
        stream: AsyncChunkReader,
        *,
        original_filename: str | None,
        checksum: UploadChecksum | None = None,
    ) -> FileMetadata:
        """Validate, persist, and publish an uploaded stream.

        Args:
            stream: Async byte stream to consume.
            original_filename: Client-provided display filename, treated as
                untrusted metadata and normalized to a basename by the store.
            checksum: Optional checksum expectation.

        Returns:
            Ready persisted file metadata.
        """

    def require_metadata(self, file_id: uuid.UUID) -> FileMetadata:
        """Load one metadata record or raise a domain failure.

        Args:
            file_id: File UUID to load.

        Returns:
            Validated metadata record.
        """

    def resolve_content(self, file_id: uuid.UUID) -> FileContentDescriptor:
        """Resolve one ready managed file for local content streaming.

        Args:
            file_id: File UUID to resolve.

        Returns:
            Descriptor with local blob path and safe response metadata.
        """

    def list_files(
        self,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None,
        order: str,
        status: str | None = None,
        mime_type: str | None = None,
        extension: str | None = None,
    ) -> FileListPage:
        """List metadata records with filters and cursor pagination.

        Args:
            limit: Maximum records in the returned page.
            cursor: Optional decoded cursor from a previous page.
            order: Sort order used for deterministic pagination.
            status: Optional lifecycle status filter.
            mime_type: Optional MIME type filter.
            extension: Optional extension filter.

        Returns:
            Storage-level page containing records and optional next cursor.
        """

    def delete_file(self, file_id: uuid.UUID) -> bool:
        """Delete one ready managed file and cleanup its blob best-effort.

        Args:
            file_id: File UUID to delete.

        Returns:
            True when metadata deletion publishes successfully.
        """

    def create_pending_output(self, *, original_filename: str) -> FileMetadata:
        """Create pending metadata for a generated output file.

        Args:
            original_filename: Public filename to persist on the placeholder.

        Returns:
            Persisted pending metadata.
        """

    def finalize_generated_file(
        self,
        *,
        file_id: uuid.UUID,
        original_filename: str,
    ) -> FileMetadata:
        """Finalize a generated output blob into ready metadata.

        Args:
            file_id: Pending output file UUID.
            original_filename: Public filename to persist in final metadata.

        Returns:
            Ready metadata for the generated output.
        """

    def create_ready_file_from_bytes(
        self,
        *,
        original_filename: str,
        content: bytes,
        extension: str,
        mime_type: str,
    ) -> FileMetadata:
        """Create a ready managed file from trusted producer bytes.

        Args:
            original_filename: Public filename for the generated file.
            content: Blob bytes from a trusted internal producer.
            extension: Public extension metadata.
            mime_type: Public MIME metadata.

        Returns:
            Ready persisted file metadata.
        """

    def resolve_local_blob_path(self, file_id: uuid.UUID) -> Path:
        """Resolve the local blob path for filesystem-backed consumers.

        Args:
            file_id: File UUID to resolve.

        Returns:
            Local filesystem path derived from STAR-managed layout.
        """


class LocalManagedFileStore:
    """Local filesystem implementation of the STAR managed file store.

    Attributes:
        settings: Runtime settings used to derive STAR storage paths and limits.
    """

    def __init__(self, settings: Settings | None = None):
        """Initialize a local store from explicit or cached settings.

        Args:
            settings: Optional pre-loaded runtime settings.
        """

        self.settings = settings if settings is not None else get_settings()

    async def upload_stream(
        self,
        stream: AsyncChunkReader,
        *,
        original_filename: str | None,
        checksum: UploadChecksum | None = None,
    ) -> FileMetadata:
        """Validate and persist an uploaded byte stream.

        Args:
            stream: Async byte stream to consume.
            original_filename: Client-provided display filename.
            checksum: Optional checksum expectation.

        Returns:
            Ready persisted file metadata.

        Raises:
            ManagedFileTooLargeError: If the stream exceeds `star_max_file_bytes`.
            EmptyManagedFileError: If the stream has no content.
            InvalidChecksumAlgorithmError: If checksum uses an unsupported algorithm.
            ChecksumMismatchError: If checksum verification fails.
            FileExtensionMissingError: If the filename extension is missing.
            MimeMappingNotDefinedError: If extension policy has no mapping.
            UnsupportedMediaTypeValidationError: If MIME validation fails.
            ManagedFileStorageError: If blob promotion or metadata publication fails.
        """

        ensure_storage_dirs(self.settings)
        file_id = uuid.uuid4()
        tmp_path = get_tmp_dir(self.settings) / f"upload_{file_id}.tmp"
        blob_path = get_blob_path(file_id, self.settings)

        hasher = hashlib.sha256()
        size_bytes = 0
        max_bytes = self.settings.star_max_file_bytes
        moved_to_blob = False

        try:
            with tmp_path.open("wb") as temp_f:
                while True:
                    chunk = await stream.read(1024 * 1024)
                    if not chunk:
                        break

                    size_bytes += len(chunk)
                    if max_bytes is not None and size_bytes > max_bytes:
                        raise ManagedFileTooLargeError()

                    hasher.update(chunk)
                    temp_f.write(chunk)

                if size_bytes == 0:
                    raise EmptyManagedFileError()

            sha256 = hasher.hexdigest()
            if checksum is not None:
                if checksum.algorithm != "sha256":
                    raise InvalidChecksumAlgorithmError()
                if sha256.lower() != checksum.expected.strip().lower():
                    raise ChecksumMismatchError(
                        algorithm="sha256",
                        expected=checksum.expected,
                        actual=sha256,
                    )

            detected_mime = detect_mime_for_file(tmp_path)
            safe_original_filename = Path(original_filename or "uploaded_file").name
            extension = validate_extension_and_mime(
                safe_original_filename,
                detected_mime,
            )

            # Blob promotion happens before metadata publication so metadata
            # remains the availability boundary for public file discovery.
            os.replace(tmp_path, blob_path)
            moved_to_blob = True

            now_utc = datetime.now(UTC)
            metadata = FileMetadata(
                id=file_id,
                original_filename=safe_original_filename,
                stored_filename=blob_path.name,
                mime_type=detected_mime,
                extension=extension,
                size_bytes=size_bytes,
                sha256=sha256,
                created_at=now_utc,
                updated_at=now_utc,
                status="ready",
            )

            try:
                # If metadata publication fails, the promoted blob is still
                # operation-owned and must be cleaned before surfacing failure.
                save_file_metadata(metadata, self.settings)
            except Exception as exc:
                if moved_to_blob and blob_path.exists():
                    try:
                        blob_path.unlink()
                    except OSError:
                        logger.exception(
                            "Failed to cleanup blob after metadata write error"
                        )
                raise ManagedFileStorageError(
                    "Failed to persist file metadata."
                ) from exc

            logger.info(
                "File stored",
                extra={
                    "file_id": str(file_id),
                    "size": size_bytes,
                    "mime": detected_mime,
                    "original_filename": safe_original_filename,
                },
            )
            return metadata
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    logger.exception("Failed to cleanup temporary upload file")

    def require_metadata(self, file_id: uuid.UUID) -> FileMetadata:
        """Load and validate metadata for a managed file id.

        Args:
            file_id: File UUID.

        Returns:
            Validated metadata record.

        Raises:
            ManagedFileNotFoundError: If metadata does not exist.
            InvalidManagedFileMetadataError: If metadata is corrupted or invalid.
            ManagedFileStorageError: If storage cannot be read.
        """

        try:
            metadata = load_file_metadata(file_id, self.settings)
        except OSError as exc:
            logger.exception(
                "file.metadata.prepare_failed",
                extra={"file_id": str(file_id)},
            )
            raise ManagedFileStorageError("Failed to read file metadata.") from exc
        except Exception as exc:
            logger.warning(
                "file.metadata.invalid",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError("Invalid file metadata.") from exc

        if metadata is None:
            logger.warning(
                "file.metadata.not_found",
                extra={"file_id": str(file_id)},
            )
            raise ManagedFileNotFoundError()

        return metadata

    def resolve_content(self, file_id: uuid.UUID) -> FileContentDescriptor:
        """Resolve and validate one ready local blob for streaming.

        Args:
            file_id: File UUID.

        Returns:
            Descriptor containing streaming metadata and local blob path.

        Raises:
            ManagedFileError: If metadata or blob state is invalid.
        """

        metadata = self.require_metadata(file_id)
        self._validate_ready_metadata(file_id, metadata)

        blob_path = get_blob_path(file_id, self.settings)
        self._validate_metadata_blob_reference(file_id, metadata, blob_path)

        if not blob_path.exists():
            logger.warning(
                "file.content.blob_not_found",
                extra={"file_id": str(file_id), "blob_path": str(blob_path)},
            )
            raise ManagedFileNotFoundError()

        if not blob_path.is_file():
            logger.warning(
                "file.content.invalid_metadata",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError(
                "Stored file path is not a regular file."
            )

        try:
            size_bytes = blob_path.stat().st_size
        except OSError as exc:
            logger.exception(
                "file.content.prepare_failed",
                extra={"file_id": str(file_id)},
            )
            raise ManagedFileStorageError(
                "Failed to prepare file content for streaming."
            ) from exc

        mime_type = metadata.mime_type.strip().lower() if metadata.mime_type else ""
        if "/" not in mime_type:
            mime_type = "application/octet-stream"

        filename = sanitize_download_filename(metadata.original_filename, file_id)

        logger.info(
            "file.content.resolved",
            extra={
                "file_id": str(file_id),
                "blob_path": str(blob_path),
                "mime_type": mime_type,
                "filename": filename,
                "size_bytes": size_bytes,
            },
        )

        return FileContentDescriptor(
            file_id=file_id,
            blob_path=blob_path,
            mime_type=mime_type,
            filename=filename,
            size_bytes=size_bytes,
        )

    def list_files(
        self,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None,
        order: str,
        status: str | None = None,
        mime_type: str | None = None,
        extension: str | None = None,
    ) -> FileListPage:
        """List persisted metadata records with deterministic ordering.

        Args:
            limit: Maximum records to return.
            cursor: Optional decoded pagination cursor.
            order: Sort order, asc or desc.
            status: Optional status filter.
            mime_type: Optional MIME type filter.
            extension: Optional extension filter.

        Returns:
            Storage-level file list page.

        Raises:
            ManagedFileStorageError: If listing cannot be completed.
        """

        try:
            meta_dir = get_meta_dir(self.settings)
            items: list[FileMetadata] = []
            for meta_path in sorted(meta_dir.glob("file_*.json")):
                stem = meta_path.stem
                prefix = "file_"
                if not stem.startswith(prefix):
                    continue

                raw_id = stem[len(prefix) :]
                try:
                    file_id = uuid.UUID(raw_id)
                except ValueError:
                    logger.warning(
                        "file.list.skipped_metadata",
                        extra={
                            "reason": "invalid_filename",
                            "meta_path": str(meta_path),
                        },
                    )
                    continue

                try:
                    metadata = self.require_metadata(file_id)
                except ManagedFileNotFoundError:
                    continue
                except InvalidManagedFileMetadataError:
                    logger.warning(
                        "file.list.skipped_metadata",
                        extra={"reason": "invalid_metadata", "file_id": str(file_id)},
                    )
                    continue

                items.append(metadata)

            filtered = apply_filters(
                items,
                status=status,
                mime_type=mime_type,
                extension=extension,
            )
            sorted_items = apply_sort(filtered, order=order)
            page, next_cursor = apply_pagination(
                sorted_items,
                limit=limit,
                cursor=cursor,
                order=order,
            )
            return FileListPage(files=page, next_cursor=next_cursor)
        except ManagedFileStorageError:
            raise
        except Exception as exc:
            logger.exception("file.list.failed")
            raise ManagedFileStorageError("Failed to list files.") from exc

    def delete_file(self, file_id: uuid.UUID) -> bool:
        """Delete file metadata and cleanup the blob best-effort.

        Args:
            file_id: File UUID.

        Returns:
            True when deletion publishes successfully by removing metadata.

        Raises:
            ManagedFileError: If metadata validation or metadata deletion fails.
        """

        metadata = self.require_metadata(file_id)
        self._validate_ready_metadata(file_id, metadata, action="delete")

        blob_path = get_blob_path(file_id, self.settings)
        meta_path = get_meta_path(file_id, self.settings)
        self._validate_metadata_blob_reference(file_id, metadata, blob_path)

        blob_exists = blob_path.exists()
        if not blob_exists:
            logger.warning(
                "file.delete.blob_missing_before_cleanup",
                extra={"file_id": str(file_id), "blob_path": str(blob_path)},
            )

        if blob_exists and not blob_path.is_file():
            logger.warning(
                "file.delete.invalid_metadata",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError(
                "Stored file path is not a regular file."
            )

        try:
            # Metadata deletion is the public deletion boundary; blob cleanup
            # after this point is best-effort and does not fail the request.
            delete_metadata_file(file_id, self.settings)
        except (FileNotFoundError, OSError) as exc:
            logger.exception(
                "file.delete.metadata_delete_failed",
                extra={"file_id": str(file_id), "meta_path": str(meta_path)},
            )
            raise ManagedFileStorageError("Failed to delete file metadata.") from exc

        try:
            delete_blob_file(file_id, self.settings)
        except FileNotFoundError:
            logger.warning(
                "file.delete.blob_cleanup_missing",
                extra={"file_id": str(file_id), "blob_path": str(blob_path)},
            )
        except OSError:
            logger.exception(
                "file.delete.blob_cleanup_failed",
                extra={"file_id": str(file_id), "blob_path": str(blob_path)},
            )

        logger.info(
            "file.delete.succeeded",
            extra={
                "file_id": str(file_id),
                "blob_path": str(blob_path),
                "meta_path": str(meta_path),
                "original_filename": metadata.original_filename,
                "stored_filename": metadata.stored_filename,
                "mime_type": metadata.mime_type,
                "size_bytes": metadata.size_bytes,
            },
        )
        return True

    def create_pending_output(self, *, original_filename: str) -> FileMetadata:
        """Create pending metadata for a generated output file.

        Args:
            original_filename: Public original filename.

        Returns:
            Persisted pending metadata.
        """

        return create_placeholder_file_metadata(
            original_filename=original_filename,
            settings=self.settings,
        )

    def finalize_generated_file(
        self,
        *,
        file_id: uuid.UUID,
        original_filename: str,
    ) -> FileMetadata:
        """Finalize a generated output file into ready metadata.

        Args:
            file_id: File UUID for the pending output placeholder.
            original_filename: Public original filename to persist.

        Returns:
            Finalized ready metadata.

        Raises:
            FileNotFoundError: If the pending metadata is missing.
            OSError: If storage operations fail.
            ValueError: If metadata payload becomes invalid.
        """

        metadata = load_file_metadata(file_id, self.settings)
        if metadata is None:
            raise FileNotFoundError(f"Output metadata '{file_id}' was not found")

        blob_path = get_blob_path(file_id, self.settings)
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
        save_file_metadata(unverified, self.settings)

        size_bytes = blob_path.stat().st_size
        sha256 = compute_sha256_for_file(blob_path)
        mime_type = "application/octet-stream"
        if size_bytes > 0:
            mime_type = detect_mime_for_file(blob_path)

        final_metadata = unverified.model_copy(
            update={
                "original_filename": original_filename,
                "mime_type": mime_type,
                "extension": ".bin",
                "size_bytes": size_bytes,
                "sha256": sha256 if size_bytes > 0 else EMPTY_SHA256,
                "status": "ready",
                "updated_at": datetime.now(UTC),
            }
        )
        save_file_metadata(final_metadata, self.settings)
        return final_metadata

    def create_ready_file_from_bytes(
        self,
        *,
        original_filename: str,
        content: bytes,
        extension: str,
        mime_type: str,
    ) -> FileMetadata:
        """Create a ready managed file from trusted producer bytes.

        Args:
            original_filename: Public original filename.
            content: Blob content to persist.
            extension: Public extension metadata.
            mime_type: Public MIME metadata.

        Returns:
            Persisted ready metadata.

        Raises:
            OSError: If the local blob or metadata cannot be written.
            ValueError: If generated metadata violates `FileMetadata` validation.
        """

        ensure_storage_dirs(self.settings)

        file_id = uuid.uuid4()
        blob_path = get_blob_path(file_id, self.settings)
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
        save_file_metadata(metadata, self.settings)
        return metadata

    def resolve_local_blob_path(self, file_id: uuid.UUID) -> Path:
        """Resolve the local filesystem blob path for a managed file id.

        Args:
            file_id: File UUID.

        Returns:
            Local blob path derived from fixed STAR layout.
        """

        return get_blob_path(file_id, self.settings)

    def blob_exists(self, file_id: uuid.UUID) -> bool:
        """Return whether a local blob exists for a managed file id.

        Args:
            file_id: File UUID.

        Returns:
            True if the blob path exists.
        """

        return self.resolve_local_blob_path(file_id).exists()

    def create_empty_blob(self, file_id: uuid.UUID) -> Path:
        """Create an empty local blob for a managed file id if missing.

        Args:
            file_id: File UUID.

        Returns:
            Local blob path.
        """

        blob_path = self.resolve_local_blob_path(file_id)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            blob_path.write_bytes(b"")
        return blob_path

    @staticmethod
    def _validate_ready_metadata(
        file_id: uuid.UUID,
        metadata: FileMetadata,
        *,
        action: str = "content",
    ) -> None:
        """Validate shared ready-state metadata invariants.

        Args:
            file_id: Requested file UUID.
            metadata: Loaded metadata record.
            action: Log namespace suffix for the caller.

        Raises:
            InvalidManagedFileMetadataError: If metadata is inconsistent.
        """

        if metadata.id != file_id:
            logger.warning(
                f"file.{action}.invalid_metadata",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError(
                "File metadata does not match requested file id."
            )

        if metadata.status != "ready":
            logger.warning(
                f"file.{action}.not_ready",
                extra={"file_id": str(file_id), "status": metadata.status},
            )
            message = (
                "File is not in deletable state."
                if action == "delete"
                else "File is not available for download."
            )
            raise InvalidManagedFileMetadataError(message)

        if not metadata.stored_filename or not metadata.stored_filename.strip():
            logger.warning(
                f"file.{action}.invalid_metadata",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError(
                "Stored file reference is missing from metadata."
            )

    @staticmethod
    def _validate_metadata_blob_reference(
        file_id: uuid.UUID,
        metadata: FileMetadata,
        blob_path: Path,
    ) -> None:
        """Validate that metadata references the expected local blob name.

        Args:
            file_id: Requested file UUID.
            metadata: Loaded metadata record.
            blob_path: Expected local blob path.

        Raises:
            InvalidManagedFileMetadataError: If metadata references another blob.
        """

        if metadata.stored_filename != blob_path.name:
            logger.warning(
                "file.content.invalid_metadata",
                extra={"file_id": str(file_id)},
            )
            raise InvalidManagedFileMetadataError(
                "Stored file reference does not match expected blob path."
            )


def sanitize_download_filename(
    original_filename: str | None,
    file_id: uuid.UUID,
) -> str:
    """Return a safe filename for Content-Disposition download responses.

    Args:
        original_filename: Filename persisted in metadata.
        file_id: UUID used for deterministic fallback naming.

    Returns:
        Sanitized filename, or `file_<uuid>.bin` fallback.
    """

    fallback = f"file_{file_id}.bin"
    candidate = Path(original_filename or "").name

    if not candidate:
        return fallback

    sanitized = "".join(ch for ch in candidate if ch >= " " and ch != "\x7f")
    sanitized = sanitized.replace("/", "").replace("\\", "").strip().strip(".")

    return sanitized or fallback


def iter_file_chunks(
    path: Path,
    chunk_size: int = 65536,
) -> Iterator[bytes]:
    """Yield file bytes in fixed-size chunks.

    Args:
        path: File path to stream.
        chunk_size: Bytes per chunk.

    Yields:
        Binary chunks until EOF.

    Raises:
        ValueError: If `chunk_size` is not positive.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk
