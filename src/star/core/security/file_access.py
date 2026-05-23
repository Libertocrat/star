"""Secure file-access helpers built on top of sandbox path validation."""

import os

from star.core.security.paths import (
    DestinationExistsError,
    DestinationNotRegularError,
    PathSecurityError,
    ValidatedPath,
    validate_path,
)


def secure_file_open_readonly(
    user_path: str, require_exists: bool = True
) -> ValidatedPath:
    """Open a sandboxed file for secure read-only access.

    This helper opens the final path component using an atomic, no-follow
    open to mitigate symlink attacks and TOCTOU windows.

    Args:
        user_path: Path supplied by the client, relative to the configured
            sandbox directory.
        require_exists: If True (default), raise `FileNotFoundError` when
            the target is absent. If False, return a `ValidatedPath` with
            `fd=None` when the target does not exist (useful for idempotent
            operations).

    Returns:
        ValidatedPath: The canonical sandboxed `Path` and an owned file
        descriptor in `fd` that the caller must close, or `fd=None` when
        `require_exists` is False and the target does not exist.

    Raises:
        PathSecurityError: If the path violates sandbox or symlink policies.
        FileNotFoundError: If the target does not exist and `require_exists`
            is True.
        OSError: For low-level open/stat errors not mapped as security errors.
    """
    validated = validate_path(
        user_path=user_path,
        require_exists=require_exists,
        require_regular_file=True,
        open_no_follow=True,
        open_flags=os.O_RDONLY,
    )
    return validated


def secure_file_validate_only(user_path: str) -> ValidatedPath:
    """Validate a sandboxed file path without opening it.

    This performs the same validation as `secure_file_open_readonly` but
    does not open the file. The returned `ValidatedPath` has `fd=None`.

    Args:
        user_path: Path supplied by the client, relative to the configured
            sandbox directory.

    Returns:
        ValidatedPath: Canonical sandboxed `Path` with `fd=None`.

    Raises:
        PathSecurityError: If the path violates sandbox or symlink policies.
        FileNotFoundError: If the target does not exist.
    """
    validated = validate_path(
        user_path=user_path,
        require_exists=True,
        require_regular_file=True,
        open_no_follow=False,
    )
    return validated


def secure_file_destination_validate(user_path: str) -> ValidatedPath:
    """Validate a destination path for move operations under the sandbox.

    The helper normalizes and validates the destination path without
    opening it. It ensures the path is syntactically valid and stays
    within the configured sandbox. If the destination exists it is
    required to be a regular file.

    Args:
        user_path: Destination path supplied by the client, relative to the
            configured sandbox directory.

    Returns:
        ValidatedPath: Canonical sandboxed `Path` with `fd=None`.

    Raises:
        PathSecurityError: If the path violates sandbox policies or if the
            existing destination is not a regular file.
    """
    validated = validate_path(
        user_path=user_path,
        require_exists=False,
        require_regular_file=False,
        open_no_follow=False,
    )

    dest = validated.path

    # If destination exists, distinguish three conditions so callers can
    # map them appropriately:
    #  - symlink or other sandbox violation -> PathSecurityError
    #  - exists and regular file -> DestinationExistsError (conflict)
    #  - exists and non-regular file -> DestinationNotRegularError
    if dest.exists():
        if dest.is_symlink():
            raise PathSecurityError("Symlinks are not allowed in path components")
        if dest.is_file():
            raise DestinationExistsError("Target already exists and is a regular file")
        raise DestinationNotRegularError("Target exists and is not a regular file")

    return ValidatedPath(path=dest, fd=None)
