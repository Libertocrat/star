"""MIME detection and upload type policy for STAR-managed files."""

from __future__ import annotations

import hashlib
from pathlib import Path

import magic

from star.core.files.exceptions import (
    FileExtensionMissingError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
)
from star.core.security.mime_map import EXTENSION_MIME_MAP

_MAGIC = magic.Magic(mime=True)

_DISALLOWED_EXECUTABLE_EXTENSIONS = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".msi",
        ".dll",
        ".ps1",
        ".sh",
    }
)

_DISALLOWED_EXECUTABLE_MIME_PREFIXES = ("application/x-dosexec",)

_DISALLOWED_EXECUTABLE_MIME_EXACT = frozenset(
    {
        "application/vnd.microsoft.portable-executable",
        "application/x-msdownload",
        "application/x-shellscript",
        "text/x-shellscript",
    }
)


def compute_sha256_for_file(path: Path) -> str:
    """Compute SHA-256 digest for a file path.

    Args:
        path: Path to the file.

    Returns:
        Lowercase SHA-256 hex digest.

    Raises:
        OSError: If the file cannot be opened or read.
    """

    hasher = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def detect_mime_for_file(path: Path) -> str:
    """Detect MIME type for a persisted blob file.

    Args:
        path: Path to the file.

    Returns:
        Lowercased MIME type string.

    Raises:
        OSError: If the file cannot be opened or read.
    """

    return _detect_mime(path)


def validate_extension_and_mime(original_filename: str, mime_type: str) -> str:
    """Validate extension and MIME compatibility against trusted mapping.

    Args:
        original_filename: Normalized basename from client upload.
        mime_type: Content-based MIME detected by STAR.

    Returns:
        Normalized extension when validation succeeds.

    Raises:
        FileExtensionMissingError: If extension is missing.
        MimeMappingNotDefinedError: If extension is unknown by policy mapping.
        UnsupportedMediaTypeValidationError: If extension and detected MIME mismatch.
    """

    return _validate_extension_and_mime(original_filename, mime_type)


def _normalize_extension(filename: str | None) -> str:
    """Normalize a filename extension to lowercase with leading dot.

    Args:
        filename: Input filename or `None`.

    Returns:
        Normalized extension or an empty string.
    """

    if not filename:
        return ""
    return Path(filename).suffix.strip().lower()


def _detect_mime(path: Path) -> str:
    """Detect MIME type from file contents.

    Args:
        path: Path of the staged file.

    Returns:
        Lowercased content-based MIME type.

    Raises:
        OSError: If the file cannot be opened or read.
    """

    with path.open("rb") as file_obj:
        sample = file_obj.read(8192)
    return _MAGIC.from_buffer(sample).strip().lower()


def _is_disallowed_executable(extension: str, mime_type: str) -> bool:
    """Return whether a file should be rejected as executable content.

    Args:
        extension: Normalized file extension.
        mime_type: Content-based detected MIME type.

    Returns:
        True if file type is considered executable and disallowed.
    """

    if extension in _DISALLOWED_EXECUTABLE_EXTENSIONS:
        return True
    if mime_type in _DISALLOWED_EXECUTABLE_MIME_EXACT:
        return True
    return mime_type.startswith(_DISALLOWED_EXECUTABLE_MIME_PREFIXES)


def _validate_extension_and_mime(original_filename: str, mime_type: str) -> str:
    """Validate extension and MIME compatibility against trusted mapping.

    Args:
        original_filename: Normalized basename from client upload.
        mime_type: Content-based MIME detected by STAR.

    Returns:
        Normalized extension when validation succeeds.

    Raises:
        FileExtensionMissingError: If extension is missing.
        MimeMappingNotDefinedError: If extension is unknown by policy mapping.
        UnsupportedMediaTypeValidationError: If extension and detected MIME mismatch.
    """

    extension = _normalize_extension(original_filename)
    if not extension:
        raise FileExtensionMissingError()

    allowed_mimes = EXTENSION_MIME_MAP.get(extension)
    if not allowed_mimes:
        raise MimeMappingNotDefinedError(extension=extension)

    if mime_type not in {m.lower() for m in allowed_mimes}:
        raise UnsupportedMediaTypeValidationError(
            extension=extension,
            detected_mime=mime_type,
            message="Uploaded file extension does not match detected MIME type.",
        )

    if _is_disallowed_executable(extension, mime_type):
        raise UnsupportedMediaTypeValidationError(
            extension=extension,
            detected_mime=mime_type,
            message="Executable file types are not allowed.",
        )

    return extension
