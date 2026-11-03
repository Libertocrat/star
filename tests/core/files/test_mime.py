"""Tests for managed file MIME and extension policy helpers."""

from __future__ import annotations

import hashlib
import os

import pytest

from star.core.files import (
    FileExtensionMissingError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
    compute_sha256_for_file,
    detect_mime_for_file,
    validate_extension_and_mime,
)


@pytest.mark.parametrize(
    ("file_type", "filename", "expected_mime"),
    [
        ("text", "sample.txt", "text/plain"),
        ("pdf", "sample.pdf", "application/pdf"),
        ("png", "sample.png", "image/png"),
        ("zip", "sample.zip", "application/zip"),
        ("tar", "sample.tar", "application/x-tar"),
        ("gzip", "sample.gz", "application/gzip"),
    ],
    ids=["text", "pdf", "png", "zip", "tar", "gzip"],
)
def test_detect_mime_for_file_classifies_real_content(
    file_factory, file_type, filename, expected_mime
):
    """
    GIVEN a real managed file payload, including a nonempty ZIP archive
    WHEN MIME detection inspects the file
    THEN it returns the content MIME independently of a client declaration
    """

    sample = file_factory(file_type, filename)

    assert detect_mime_for_file(sample.abs_path) == expected_mime


@pytest.mark.parametrize("detector_fails", [False, True], ids=["success", "failure"])
def test_detect_mime_for_file_owns_descriptor(tmp_path, monkeypatch, detector_fails):
    """
    GIVEN a detector consuming an open descriptor that may fail
    WHEN MIME detection completes or raises
    THEN the descriptor reads the intended content and is always closed
    """

    from star.core.files import mime

    path = tmp_path / "sample.txt"
    path.write_bytes(b"descriptor content")
    descriptors: list[int] = []

    def inspect_descriptor(fd: int) -> str:
        """Read the owned descriptor and optionally simulate detector failure."""

        descriptors.append(fd)
        assert os.read(fd, 100) == b"descriptor content"
        if detector_fails:
            raise RuntimeError("detector failed")
        return " TEXT/PLAIN \n"

    monkeypatch.setattr(mime._MAGIC, "from_descriptor", inspect_descriptor)

    if detector_fails:
        with pytest.raises(RuntimeError, match="detector failed"):
            detect_mime_for_file(path)
    else:
        assert detect_mime_for_file(path) == "text/plain"

    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


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
