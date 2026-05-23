"""Centralized extension-to-MIME mapping used by :mod:`file_verify`.

Keys are lowercase extensions including the leading dot (for example
`.pdf`). Values are sets of canonical MIME type strings. Values are
intentionally stored as `frozenset` to indicate immutability and enable
safe sharing without accidental mutation.

Design goals:
- Deterministic
- Docker-independent
- One-to-many mapping (an extension may map to multiple valid MIME types)
- Hardcoded for v0.1.0 (future override may be added)
"""

from __future__ import annotations

EXTENSION_MIME_MAP: dict[str, frozenset[str]] = {
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/plain"}),
    ".csv": frozenset({"text/csv", "application/csv", "text/plain"}),
    ".json": frozenset({"application/json", "text/json"}),
    ".pdf": frozenset({"application/pdf"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".zip": frozenset({"application/zip", "application/x-zip-compressed"}),
    ".tar": frozenset({"application/x-tar"}),
    ".gz": frozenset({"application/gzip", "application/x-gzip"}),
    ".exe": frozenset(
        {
            "application/vnd.microsoft.portable-executable",
            "application/x-dosexec",
            "application/x-msdownload",
        }
    ),
    ".sh": frozenset({"text/x-shellscript", "application/x-shellscript", "text/plain"}),
    ".py": frozenset({"text/x-python", "text/plain"}),
    ".js": frozenset({"application/javascript", "text/javascript", "text/plain"}),
}
