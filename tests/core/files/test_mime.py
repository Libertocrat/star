"""Tests for managed file MIME and extension policy helpers."""

from __future__ import annotations

import hashlib

import pytest

from star.core.files import (
    FileExtensionMissingError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
    compute_sha256_for_file,
    validate_extension_and_mime,
)


def test_compute_sha256_for_file_reads_complete_content(tmp_path):
    """
    GIVEN a local file with multiple chunks of content
    WHEN the managed file checksum helper computes SHA-256
    THEN the returned digest matches the digest of the complete byte stream
    """

    path = tmp_path / "content.bin"
    content = b"abc" * 70000
    path.write_bytes(content)

    digest = compute_sha256_for_file(path)

    assert digest == hashlib.sha256(content).hexdigest()


def test_validate_extension_and_mime_accepts_known_case_insensitive_extension():
    """
    GIVEN an upload filename with a known uppercase extension
    WHEN extension and detected MIME policy are validated
    THEN the normalized lowercase extension is returned
    """

    extension = validate_extension_and_mime("REPORT.TXT", "text/plain")

    assert extension == ".txt"


@pytest.mark.parametrize(
    ("filename", "mime_type", "expected_error"),
    [
        pytest.param(
            "README",
            "text/plain",
            FileExtensionMissingError,
            id="missing_extension",
        ),
        pytest.param(
            "archive.unknown",
            "application/octet-stream",
            MimeMappingNotDefinedError,
            id="unknown_extension",
        ),
        pytest.param(
            "image.png",
            "text/plain",
            UnsupportedMediaTypeValidationError,
            id="mime_mismatch",
        ),
        pytest.param(
            "script.sh",
            "text/x-shellscript",
            UnsupportedMediaTypeValidationError,
            id="disallowed_executable",
        ),
    ],
)
def test_validate_extension_and_mime_rejects_invalid_policy_cases(
    filename,
    mime_type,
    expected_error,
):
    """
    GIVEN upload filename and MIME combinations outside managed file policy
    WHEN extension and detected MIME policy are validated
    THEN the focused policy exception is raised
    """

    with pytest.raises(expected_error):
        validate_extension_and_mime(filename, mime_type)
