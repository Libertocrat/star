"""Managed file storage API for STAR.

This package owns the lifecycle for STAR-managed local files and exposes
transport-neutral storage operations used by routes, actions, and future
storage backends.
"""

from star.core.files.descriptors import (
    FileContentDescriptor,
    FileListPage,
    FileMetadataUpdateResult,
    UploadChecksum,
)
from star.core.files.exceptions import (
    ChecksumMismatchError,
    EmptyManagedFileError,
    FileExtensionMissingError,
    InvalidChecksumAlgorithmError,
    InvalidManagedFileMetadataError,
    ManagedFileError,
    ManagedFileNotFoundError,
    ManagedFilePreconditionFailedError,
    ManagedFileStorageError,
    ManagedFileTooLargeError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
)
from star.core.files.layout import (
    ensure_storage_dirs,
    get_blob_dir,
    get_blob_path,
    get_data_root,
    get_files_root,
    get_meta_dir,
    get_meta_lock_path,
    get_meta_path,
    get_runtime_root,
    get_secret_tmp_dir,
    get_tmp_dir,
)
from star.core.files.listing import (
    apply_filters,
    apply_pagination,
    apply_sort,
    decode_cursor,
    encode_cursor,
)
from star.core.files.metadata import (
    EMPTY_SHA256,
    create_placeholder_file_metadata,
    delete_blob_file,
    delete_metadata_file,
    is_metadata_etag,
    load_file_metadata,
    metadata_etag,
    metadata_lock,
    save_file_metadata,
)
from star.core.files.metadata_validation import (
    MAX_FILE_NAME_BYTES,
    MAX_FILE_TAG_LENGTH,
    MAX_FILE_TAGS,
    canonicalize_tags,
    validate_file_name,
)
from star.core.files.mime import (
    compute_sha256_for_file,
    detect_mime_for_file,
    validate_extension_and_mime,
)
from star.core.files.storage import (
    LocalManagedFileStore,
    ManagedFileStore,
    iter_file_chunks,
    sanitize_download_filename,
)

__all__ = [
    "ChecksumMismatchError",
    "EMPTY_SHA256",
    "EmptyManagedFileError",
    "FileContentDescriptor",
    "FileExtensionMissingError",
    "FileListPage",
    "FileMetadataUpdateResult",
    "InvalidChecksumAlgorithmError",
    "InvalidManagedFileMetadataError",
    "LocalManagedFileStore",
    "ManagedFileError",
    "ManagedFileNotFoundError",
    "ManagedFilePreconditionFailedError",
    "ManagedFileStorageError",
    "ManagedFileStore",
    "ManagedFileTooLargeError",
    "MAX_FILE_NAME_BYTES",
    "MAX_FILE_TAG_LENGTH",
    "MAX_FILE_TAGS",
    "MimeMappingNotDefinedError",
    "UnsupportedMediaTypeValidationError",
    "UploadChecksum",
    "apply_filters",
    "apply_pagination",
    "apply_sort",
    "canonicalize_tags",
    "compute_sha256_for_file",
    "create_placeholder_file_metadata",
    "decode_cursor",
    "delete_blob_file",
    "delete_metadata_file",
    "detect_mime_for_file",
    "encode_cursor",
    "ensure_storage_dirs",
    "get_blob_dir",
    "get_blob_path",
    "get_data_root",
    "get_files_root",
    "get_meta_dir",
    "get_meta_lock_path",
    "get_meta_path",
    "get_runtime_root",
    "get_secret_tmp_dir",
    "get_tmp_dir",
    "iter_file_chunks",
    "is_metadata_etag",
    "load_file_metadata",
    "metadata_etag",
    "metadata_lock",
    "sanitize_download_filename",
    "save_file_metadata",
    "validate_extension_and_mime",
    "validate_file_name",
]
