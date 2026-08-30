"""Validation helpers for mutable STAR-managed file metadata."""

from __future__ import annotations

import re
from collections.abc import Sequence

MAX_FILE_NAME_BYTES = 255
MAX_FILE_TAGS = 50
MAX_FILE_TAG_LENGTH = 48
_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")


def validate_file_name(file_name: str, *, extension: str | None = None) -> str:
    """Validate one display filename without using it as a storage path.

    Args:
        file_name: Untrusted client-supplied display filename.
        extension: Optional existing normalized extension that must be preserved.

    Returns:
        The accepted filename unchanged.

    Raises:
        ValueError: If the filename is unsafe, malformed, or changes extension.
    """

    try:
        encoded = file_name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("File name must contain printable ASCII only.") from exc

    if not file_name or not file_name.strip() or len(encoded) > MAX_FILE_NAME_BYTES:
        raise ValueError("File name is empty or exceeds the allowed length.")
    if file_name != file_name.strip():
        raise ValueError("File name cannot start or end with whitespace.")
    if file_name in {".", ".."} or file_name.startswith(".") or ".." in file_name:
        raise ValueError("File name contains a prohibited path component.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in file_name):
        raise ValueError("File name contains a control character.")
    if "/" in file_name or "\\" in file_name:
        raise ValueError("File name cannot contain path separators.")
    if not _FILE_NAME_PATTERN.fullmatch(file_name):
        raise ValueError("File name contains characters outside the allowed grammar.")

    suffix = file_name[file_name.rfind(".") :].lower() if "." in file_name else ""
    if extension is not None and suffix != extension.lower():
        raise ValueError("File name extension must match the stored file extension.")
    return file_name


def canonicalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Return a deterministic canonical tag set from untrusted labels.

    Args:
        tags: Client-supplied complete tag set.

    Returns:
        Sorted unique lowercase tags.

    Raises:
        ValueError: If tags exceed limits or violate the tag grammar.
    """

    if len(tags) > MAX_FILE_TAGS:
        raise ValueError("Too many file tags.")

    canonical: list[str] = []
    for tag in tags:
        normalized = tag.lower()
        if len(normalized) > MAX_FILE_TAG_LENGTH or not _TAG_PATTERN.fullmatch(
            normalized
        ):
            raise ValueError("File tag does not match the allowed grammar.")
        canonical.append(normalized)

    if len(set(canonical)) != len(canonical):
        raise ValueError("File tags must be unique after normalization.")
    return tuple(sorted(canonical))


__all__ = [
    "MAX_FILE_NAME_BYTES",
    "MAX_FILE_TAG_LENGTH",
    "MAX_FILE_TAGS",
    "canonicalize_tags",
    "validate_file_name",
]
