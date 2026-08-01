"""
Header security helpers.

Incoming helpers operate on raw ASGI headers and enforce structural integrity
rules. Outgoing helpers build reviewed HTTP header values without interpolating
untrusted text directly into public responses.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

RawHeader = tuple[bytes, bytes]

_CONTENT_DISPOSITION_FALLBACK = "download"
_RFC5987_SAFE_CHARS = "!#$&+-.^_`|~"
_AMBIGUOUS_FILENAME_CHARS = {'"', "\\", ";", "/"}


def _has_illegal_ctrl_bytes(data: bytes, *, allow_tab: bool) -> bool:
    """Return True when a control byte appears in `data`.

    Args:
        data: Raw header name or value bytes.
        allow_tab: Whether TAB (0x09) is permitted.

    Returns:
        True when `data` contains a control byte (< 0x20) that is not allowed.
    """
    for b in data:
        if b >= 0x20:
            continue
        if allow_tab and b == 0x09:
            continue
        return True
    return False


def find_header_integrity_violation(raw_headers: list[RawHeader]) -> str | None:
    """Return the first header-integrity violation reason, if present.

    Args:
        raw_headers: Raw ASGI headers to inspect.

    Returns:
        A machine-readable reason when a violation is detected, otherwise None.
    """
    authorization_count = 0

    for raw_name, raw_value in raw_headers:
        name_lower = raw_name.lower()

        if name_lower == b"authorization":
            authorization_count += 1
            if authorization_count > 1:
                return "duplicate_authorization"

        # Reject whitespace in header names (space or tab)
        if b" " in raw_name or b"\t" in raw_name:
            return "header_name_whitespace"

        # Reject control characters in header names (TAB not allowed here anyway)
        if _has_illegal_ctrl_bytes(raw_name, allow_tab=False):
            return "header_name_control_char"

        # Reject control characters in header values (TAB allowed)
        if _has_illegal_ctrl_bytes(raw_value, allow_tab=True):
            return "header_value_control_char"

    return None


def _is_control_char(ch: str) -> bool:
    """Return whether a string character is an HTTP-unsafe control char.

    Args:
        ch: Single character to inspect.

    Returns:
        True when `ch` is a C0 control or DEL character.
    """

    codepoint = ord(ch)
    return codepoint < 0x20 or codepoint == 0x7F


def _ascii_content_disposition_filename(filename: str) -> str:
    """Return a safe ASCII fallback for the `filename` parameter.

    Args:
        filename: Candidate display filename.

    Returns:
        ASCII filename safe for use inside a quoted `filename` parameter.
    """

    normalized = unicodedata.normalize("NFKD", filename or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    chars: list[str] = []
    for ch in ascii_text:
        if _is_control_char(ch) or ch in _AMBIGUOUS_FILENAME_CHARS:
            chars.append("_")
        else:
            chars.append(ch)

    fallback = "".join(chars).strip().strip(".")
    if fallback and any(ch.isalnum() for ch in fallback):
        return fallback
    return _CONTENT_DISPOSITION_FALLBACK


def _extended_content_disposition_filename(filename: str, *, fallback: str) -> str:
    """Return a filename source safe for RFC 5987 encoding.

    Args:
        filename: Candidate display filename.
        fallback: Fallback returned when no usable filename remains.

    Returns:
        Filename string to percent-encode for `filename*`.
    """

    chars: list[str] = []
    for ch in filename or "":
        if _is_control_char(ch) or ch in {"/", "\\"}:
            chars.append("_")
        else:
            chars.append(ch)

    extended = "".join(chars).strip().strip(".")
    if extended and any(ch.isalnum() for ch in extended):
        return extended
    return fallback


def content_disposition_attachment(filename: str) -> str:
    """Build a safe `Content-Disposition: attachment` header value.

    Args:
        filename: Download display filename.

    Returns:
        Header value with a safe ASCII `filename` fallback and, when needed,
        an RFC 5987 `filename*` parameter preserving the UTF-8 filename.
    """

    fallback = _ascii_content_disposition_filename(filename)
    extended_source = _extended_content_disposition_filename(
        filename,
        fallback=fallback,
    )
    encoded = quote(
        extended_source,
        safe=_RFC5987_SAFE_CHARS,
        encoding="utf-8",
        errors="strict",
    )

    header = f'attachment; filename="{fallback}"'
    if encoded != fallback:
        header = f"{header}; filename*=UTF-8''{encoded}"
    return header


__all__ = [
    "RawHeader",
    "content_disposition_attachment",
    "find_header_integrity_violation",
]
