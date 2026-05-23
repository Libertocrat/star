"""Path sanitization and sandbox validation primitives for STAR."""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from star.core.config import get_settings


class PathSecurityError(ValueError):
    """Base class for path validation and sandbox policy errors.

    Handlers should treat this as a generic security-related validation
    failure (for example path traversal, symlink rejection or sandbox
    boundary violations). More specific subclasses are provided for
    call sites that need to distinguish conflict-like conditions.
    """


class DestinationExistsError(PathSecurityError):
    """Raised when a destination path already exists and is a regular file.

    Handlers can catch this exception to map the condition to a
    `CONFLICT` semantic (for example when overwrite is not allowed).
    """


class DestinationNotRegularError(PathSecurityError):
    """Raised when a destination path exists but is not a regular file.

    This indicates the path exists but refers to a directory or other
    non-regular file type; handlers may choose to map this to a
    `CONFLICT` or `PATH_NOT_ALLOWED` response depending on context.
    """


logger = logging.getLogger("star.core.security.paths")


@dataclass(frozen=True)
class ValidatedPath:
    """Result of secure path validation.

    Attributes:
        path: Canonical path under the configured sandbox.
        fd: Optional open file descriptor obtained via `safe_open_no_follow`.
            The caller owns this descriptor and must close it.
    """

    path: Path
    fd: int | None = None


def sanitize_rel_path(user_path: str) -> str:
    """Syntactically validate and normalize a user-supplied relative path.

    Args:
        user_path: Path supplied by the client.

    Returns:
        A normalized relative path string with redundant separators and
        '.' components removed.

    Raises:
        PathSecurityError: If the path contains NULs, backslashes,
            control characters, is absolute, contains traversal
            components, is empty, or exceeds the maximum allowed length.

    Note:
        This function performs only syntactic validation and does not
        perform filesystem checks (existence or symlink validation). Use
        `resolve_in_sandbox` or `safe_open_no_follow` for filesystem-safe
        operations.
    """

    if "\x00" in user_path:
        raise PathSecurityError("NUL byte not allowed in path")
    # reject Windows-style separators
    if "\\" in user_path:
        raise PathSecurityError("Backslashes not allowed in path")
    p = user_path.strip()

    # reject control characters
    if any(ord(c) < 32 for c in p):
        raise PathSecurityError("Control characters are not allowed in path")

    # enforce reasonable maximum length to avoid DoS via huge paths
    MAX_PATH_LEN = 4096
    if len(p) > MAX_PATH_LEN:
        raise PathSecurityError("Path length exceeds maximum")

    if p == "":
        raise PathSecurityError("Empty path")

    # reject absolute paths
    if p.startswith("/"):
        raise PathSecurityError("Absolute paths are not allowed")

    # reject traversal
    parts = [star for star in p.split("/") if star not in ("", ".")]
    if any(star == ".." for star in parts):
        raise PathSecurityError("Path traversal '..' is not allowed")

    return "/".join(parts)


def resolve_in_sandbox(sandbox_dir: Path, rel_path: str) -> Path:
    """Resolve a relative path under a configured sandbox.

    Args:
        sandbox_dir: The configured sandbox root directory.
        rel_path: Relative path to resolve under the sandbox directory.

    Returns:
        A canonical `Path` representing the candidate path within the
        sandbox (string-based normalization is used to avoid following
        symlinks).

    Raises:
        PathSecurityError: If the path is outside the sandbox, if the
            configured sandbox does not exist, or if any existing path
            component is a symlink.

    Note:
        Resolving a path and opening it later can introduce TOCTOU
        windows. For sensitive operations prefer `safe_open_no_follow`.
    """
    rel = sanitize_rel_path(rel_path)

    # Ensure sandbox exists and is canonical
    try:
        sandbox_resolved = sandbox_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathSecurityError("Configured sandbox dir does not exist") from exc

    # Reject symlinks in any existing path component under sandbox
    cur = sandbox_resolved
    for part in rel.split("/"):
        candidate_component = cur / part
        if candidate_component.exists() and candidate_component.is_symlink():
            raise PathSecurityError("Symlinks are not allowed in path components")
        cur = candidate_component

    # Construct candidate path without resolving symlinks (normpath on joined strings)
    candidate_str = os.path.normpath(os.path.join(str(sandbox_resolved), rel))
    # Ensure candidate is still within sandbox dir.
    # String-based check avoids following symlinks.
    try:
        common = os.path.commonpath([str(sandbox_resolved), candidate_str])
    except Exception as exc:
        raise PathSecurityError("Path is outside allowed sandbox") from exc

    if common != str(sandbox_resolved):
        raise PathSecurityError("Path is outside allowed sandbox")

    return Path(candidate_str)


def safe_open_no_follow(path: Path, flags: int = os.O_RDONLY):
    """Open `path` without following a final symlink (POSIX `O_NOFOLLOW`).

    Args:
        path: The candidate filesystem path to open.
        flags: Flags to pass to `os.open` (`os.O_RDONLY` by default).

    Returns:
        An integer file descriptor opened with `O_NOFOLLOW`. The caller
        owns and must close the descriptor (for example via `os.close`
        or wrapping with `os.fdopen`).

    Raises:
        FileNotFoundError: If the target does not exist.
        PathSecurityError: If the open fails for security-related reasons
            or if the opened target is not a regular file.
        OSError: For other low-level OS errors raised by `os.open`/`fstat`.

    Note:
        This helper reduces symlink-following at open time but does not
        eliminate TOCTOU if callers resolved paths earlier; prefer to
        validate and open in close succession.
    """
    # Add O_NOFOLLOW if available on this platform
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    open_flags = flags | nofollow
    try:
        fd = os.open(str(path), open_flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        # Translate certain errno to a security error
        raise PathSecurityError("Failed to open path safely") from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            try:
                os.close(fd)
            except Exception as close_exc:
                logger.warning(
                    "Failed to close fd %s during cleanup: %s", fd, close_exc
                )
            raise PathSecurityError("Target is not a regular file")
    except Exception:
        # Ensure fd closed on any failure
        try:
            os.close(fd)
        except Exception as close_exc:
            logger.warning("Failed to close fd %s during cleanup: %s", fd, close_exc)
        raise

    return fd


def validate_path(
    *,
    user_path: str,
    sandbox_dir: Path | None = None,
    require_exists: bool = True,
    require_regular_file: bool = True,
    open_no_follow: bool = False,
    open_flags: int = os.O_RDONLY,
) -> ValidatedPath:
    """Validate and optionally open a user path under the STAR sandbox.

    Args:
        user_path: User-provided path relative to the sandbox.
        sandbox_dir: Optional sandbox root. Defaults to the configured
            sandbox directory when `None`.
        require_exists: If True, raise `FileNotFoundError` when the
            target is absent. When False, the function may return a
            `ValidatedPath` with `fd=None` for non-existent targets.
        require_regular_file: If True, reject non-regular files (applies
            when `open_no_follow` is False).
        open_no_follow: If True, open the target via `safe_open_no_follow`
            and return an owned file descriptor in the result.
        open_flags: Flags passed to the secure open when
            `open_no_follow` is True.

    Returns:
        ValidatedPath: Canonical sandboxed `Path` and optional owned
        file descriptor (`fd`) when `open_no_follow` is True and the
        target exists.

    Raises:
        PathSecurityError: On sandbox boundary, symlink, or policy violations.
        FileNotFoundError: If `require_exists` is True and the target is
            missing.
        OSError: For low-level open/stat errors not mapped as security errors.
    """

    # Resolve sandbox root
    sandbox = (
        sandbox_dir if sandbox_dir is not None else Path(get_settings().star_root_dir)
    )

    # Resolve and validate the path under sandbox policies
    resolved_path = resolve_in_sandbox(sandbox_dir=sandbox, rel_path=user_path)

    # ------------------------------------------------------------------
    # Atomic open mode (mitigates TOCTOU for final component)
    # ------------------------------------------------------------------
    if open_no_follow:
        try:
            fd = safe_open_no_follow(resolved_path, flags=open_flags)
        except FileNotFoundError:
            if require_exists:
                raise
            return ValidatedPath(path=resolved_path, fd=None)

        return ValidatedPath(path=resolved_path, fd=fd)

    # ------------------------------------------------------------------
    # Validation-only mode (no atomic open)
    # ------------------------------------------------------------------
    if not require_exists:
        return ValidatedPath(path=resolved_path, fd=None)

    if not resolved_path.exists():
        raise FileNotFoundError(str(resolved_path))

    # Explicitly reject final-component symlinks before file checks
    if resolved_path.is_symlink():
        raise PathSecurityError("Symlinks are not allowed in path components")

    if require_regular_file and not resolved_path.is_file():
        raise PathSecurityError("Target is not a regular file")

    return ValidatedPath(path=resolved_path, fd=None)
