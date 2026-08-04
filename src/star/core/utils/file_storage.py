"""Compatibility exports for STAR managed file storage.

The owning implementation now lives in `star.core.files`. This module remains
as a temporary compatibility layer for callers that have not migrated yet.
"""

from __future__ import annotations

from star.core.files import (
    EMPTY_SHA256,
    FileContentDescriptor,
    FileExtensionMissingError,
    FileListPage,
    LocalManagedFileStore,
    ManagedFileError,
    ManagedFileStore,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
    UploadChecksum,
    compute_sha256_for_file,
    create_placeholder_file_metadata,
    delete_blob_file,
    delete_metadata_file,
    detect_mime_for_file,
    ensure_storage_dirs,
    get_blob_dir,
    get_blob_path,
    get_data_root,
    get_files_root,
    get_meta_dir,
    get_meta_path,
    get_runtime_root,
    get_secret_tmp_dir,
    get_tmp_dir,
    iter_file_chunks,
    load_file_metadata,
    sanitize_download_filename,
    save_file_metadata,
    validate_extension_and_mime,
)
from star.core.files.layout import logger
from star.core.files.mime import _detect_mime, _validate_extension_and_mime

__all__ = [
    "EMPTY_SHA256",
    "FileContentDescriptor",
    "FileExtensionMissingError",
    "FileListPage",
    "LocalManagedFileStore",
    "ManagedFileError",
    "ManagedFileStore",
    "MimeMappingNotDefinedError",
    "UnsupportedMediaTypeValidationError",
    "UploadChecksum",
    "_detect_mime",
    "_validate_extension_and_mime",
    "compute_sha256_for_file",
    "create_placeholder_file_metadata",
    "delete_blob_file",
    "delete_metadata_file",
    "detect_mime_for_file",
    "ensure_storage_dirs",
    "get_blob_dir",
    "get_blob_path",
    "get_data_root",
    "get_files_root",
    "get_meta_dir",
    "get_meta_path",
    "get_runtime_root",
    "get_secret_tmp_dir",
    "get_tmp_dir",
    "iter_file_chunks",
    "load_file_metadata",
    "logger",
    "sanitize_download_filename",
    "save_file_metadata",
    "validate_extension_and_mime",
]
