"""Tests for mutable managed-file metadata validation helpers."""

from __future__ import annotations

import pytest

from star.core.files import MAX_FILE_TAGS, canonicalize_tags, validate_file_name


@pytest.mark.parametrize(
    ("file_name", "extension"),
    [
        ("quarterly report.txt", ".txt"),
        ("Q3-Report_2026.PDF", ".pdf"),
        ("archive.tar.gz", ".gz"),
    ],
)
def test_validate_file_name_accepts_safe_ascii_display_names(
    file_name,
    extension,
):
    """
    GIVEN a portable ASCII display filename with the existing extension
    WHEN mutable file metadata validates the filename
    THEN it returns the exact accepted name without path interpretation
    """

    assert validate_file_name(file_name, extension=extension) == file_name


@pytest.mark.parametrize(
    "file_name",
    [
        "../report.txt",
        "report/2026.txt",
        r"report\\2026.txt",
        ".hidden.txt",
        "report..txt",
        "report|2026.txt",
        "report\x00.txt",
        "résumé.txt",
    ],
)
def test_validate_file_name_rejects_unsafe_or_nonportable_values(file_name):
    """
    GIVEN an unsafe, ambiguous, or non-ASCII display filename
    WHEN mutable file metadata validates the filename
    THEN it rejects the value before it can become public metadata
    """

    with pytest.raises(ValueError):
        validate_file_name(file_name, extension=".txt")


def test_validate_file_name_rejects_extension_change():
    """
    GIVEN a safe display filename with a different extension
    WHEN metadata validation compares it to the persisted extension
    THEN it rejects the attempted type-label change
    """

    with pytest.raises(ValueError, match="extension"):
        validate_file_name("report.pdf", extension=".txt")


def test_canonicalize_tags_replaces_order_with_sorted_lowercase_values():
    """
    GIVEN a complete set of unique mixed-case safe tags
    WHEN mutable metadata canonicalizes them
    THEN the resulting replacement set is sorted and lowercase
    """

    assert canonicalize_tags(["Q3", "finance", "tier_1"]) == (
        "finance",
        "q3",
        "tier_1",
    )


@pytest.mark.parametrize(
    "tags",
    [
        ["duplicate", "DUPLICATE"],
        ["contains space"],
        ["contains|pipe"],
        ["under_score", "hyphen-tag", ""],
        ["tag"] * (MAX_FILE_TAGS + 1),
    ],
)
def test_canonicalize_tags_rejects_invalid_or_duplicate_replacement_sets(tags):
    """
    GIVEN malformed, duplicate, or oversized tag replacement values
    WHEN mutable metadata canonicalizes the tags
    THEN it rejects the entire request rather than silently altering intent
    """

    with pytest.raises(ValueError):
        canonicalize_tags(tags)
