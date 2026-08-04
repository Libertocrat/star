"""Domain exceptions for STAR-managed file storage.

The exceptions in this module are transport-neutral. Route handlers are
responsible for mapping them to stable public `StarError` responses.
"""

from __future__ import annotations


class ManagedFileError(Exception):
    """Base class for transport-neutral managed file failures.

    Callers can catch this type when they need to map any managed-file domain
    failure without depending on the local filesystem implementation.
    """


class ManagedFileNotFoundError(ManagedFileError):
    """Raised when a managed file metadata record or required blob is missing.

    This failure is intentionally generic for callers: metadata absence and
    blob absence both mean the managed file cannot be served or operated on.
    """


class InvalidManagedFileMetadataError(ManagedFileError):
    """Raised when persisted file metadata is corrupted or policy-invalid.

    The stored sidecar is treated as an untrusted serialization boundary even
    though STAR wrote it originally.
    """


class ManagedFileStorageError(ManagedFileError):
    """Raised when managed file storage cannot complete an I/O operation.

    Route handlers map this to a generic public failure so host paths and raw
    operating-system details stay internal.
    """


class ManagedFileTooLargeError(ManagedFileError):
    """Raised when an uploaded file exceeds the configured byte limit.

    The store raises this while streaming so oversized uploads can be rejected
    without reading the entire body into memory.
    """


class EmptyManagedFileError(ManagedFileError):
    """Raised when an uploaded file contains no bytes.

    Empty uploads are rejected before MIME detection and metadata publication.
    """


class InvalidChecksumAlgorithmError(ManagedFileError):
    """Raised when a checksum expectation uses an unsupported algorithm.

    STAR currently validates upload checksums only with SHA-256.
    """


class ChecksumMismatchError(ManagedFileError):
    """Raised when an uploaded file checksum does not match expectation.

    Attributes:
        algorithm: Checksum algorithm used for verification.
        expected: Client-provided checksum value.
        actual: Checksum computed from the uploaded content.
    """

    def __init__(self, *, algorithm: str, expected: str, actual: str):
        """Initialize checksum mismatch details.

        Args:
            algorithm: Checksum algorithm used for verification.
            expected: Client-provided checksum value.
            actual: Checksum computed from the uploaded content.
        """

        self.algorithm = algorithm
        self.expected = expected
        self.actual = actual
        super().__init__("Checksum mismatch.")


class FileExtensionMissingError(ValueError):
    """Raised when an uploaded filename has no extension for MIME policy.

    The extension is display metadata, not a storage key, but it is required to
    evaluate STAR's explicit extension-to-MIME allowlist.
    """


class MimeMappingNotDefinedError(ValueError):
    """Raised when STAR has no MIME mapping for the file extension.

    Attributes:
        extension: File extension missing from trusted MIME map.
    """

    def __init__(self, extension: str):
        """Initialize error for an unknown extension mapping.

        Args:
            extension: File extension missing from trusted MIME map.
        """

        self.extension = extension
        super().__init__(f"No MIME mapping defined for extension: {extension}")


class UnsupportedMediaTypeValidationError(ValueError):
    """Raised when uploaded extension and detected MIME are incompatible.

    Attributes:
        extension: Normalized extension declared by uploaded filename.
        detected_mime: MIME type detected from file content.
    """

    def __init__(self, extension: str, detected_mime: str, message: str):
        """Initialize media-type validation error details.

        Args:
            extension: Normalized extension declared by uploaded filename.
            detected_mime: MIME type detected from file content.
            message: Human-readable validation failure message.
        """

        self.extension = extension
        self.detected_mime = detected_mime
        super().__init__(message)
