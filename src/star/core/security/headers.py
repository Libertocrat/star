"""
Header security helpers.

These helpers operate on raw ASGI headers (bytes) and enforce structural
integrity rules:
- Reject duplicate Authorization headers
- Reject whitespace in header names (space/tab)
- Reject control characters (< 0x20) in header names/values, with TAB (0x09)
  allowed only in values (names are rejected for whitespace anyway)
"""

from __future__ import annotations

RawHeader = tuple[bytes, bytes]


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


__all__ = [
    "RawHeader",
    "find_header_integrity_violation",
]
