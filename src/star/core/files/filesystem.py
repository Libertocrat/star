"""Descriptor-relative filesystem operations for STAR-managed storage."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from star.core.config import Settings
from star.core.files.layout import get_blob_filename, get_meta_filename

_BLOB_DIRECTORY = "blobs"
_METADATA_DIRECTORY = "meta"
_DIRECTORY_COMPONENTS = ("data", "files")


class ManagedStoragePathError(ValueError):
    """Raised when a managed storage entry violates filesystem policy."""


class ManagedStoragePlatformError(OSError):
    """Raised when the runtime lacks a required POSIX filesystem primitive."""


@dataclass(slots=True)
class OpenedManagedFile:
    """An owned regular file opened beneath STAR-managed storage.

    Attributes:
        stream: Binary stream backed by the already verified file descriptor.
        size_bytes: Size observed from that descriptor before streaming.
    """

    stream: BinaryIO
    size_bytes: int


def open_managed_blob_for_read(
    file_id: UUID,
    settings: Settings,
) -> OpenedManagedFile:
    """Open one managed blob as a verified regular file.

    Args:
        file_id: UUID that identifies the server-derived blob name.
        settings: Validated runtime settings that own the trusted root.

    Returns:
        Owned binary stream and size observed from the opened descriptor.

    Raises:
        FileNotFoundError: If the derived blob does not exist.
        ManagedStoragePathError: If a directory component, symlink, or leaf type
            violates managed-storage policy.
        OSError: If the underlying filesystem operation fails.
    """

    return _open_regular_file(
        settings=settings,
        directory_name=_BLOB_DIRECTORY,
        filename=get_blob_filename(_canonical_uuid(file_id)),
    )


def validate_managed_blob_regular(file_id: UUID, settings: Settings) -> None:
    """Require that one managed blob is currently a regular no-follow file.

    This validation is used by the deletion workflow before metadata becomes
    unavailable. It deliberately does not return a path or retain a descriptor;
    the later delete operation independently rechecks the leaf.

    Args:
        file_id: UUID that identifies the server-derived blob name.
        settings: Validated runtime settings that own the trusted root.

    Raises:
        FileNotFoundError: If the derived blob does not exist.
        ManagedStoragePathError: If a directory component, symlink, or leaf type
            violates managed-storage policy.
        OSError: If the underlying filesystem operation fails.
    """

    opened = open_managed_blob_for_read(file_id, settings)
    opened.stream.close()


def unlink_managed_blob(file_id: UUID, settings: Settings) -> None:
    """Delete one verified regular managed blob by descriptor-relative name.

    Args:
        file_id: UUID that identifies the server-derived blob name.
        settings: Validated runtime settings that own the trusted root.

    Raises:
        FileNotFoundError: If the derived blob does not exist.
        ManagedStoragePathError: If a directory component, symlink, or leaf type
            violates managed-storage policy.
        OSError: If the underlying filesystem operation fails.
    """

    _unlink_regular_file(
        settings=settings,
        directory_name=_BLOB_DIRECTORY,
        filename=get_blob_filename(_canonical_uuid(file_id)),
    )


def unlink_managed_metadata(file_id: UUID, settings: Settings) -> None:
    """Delete one verified regular managed metadata sidecar by descriptor name.

    Args:
        file_id: UUID that identifies the server-derived metadata name.
        settings: Validated runtime settings that own the trusted root.

    Raises:
        FileNotFoundError: If the derived metadata sidecar does not exist.
        ManagedStoragePathError: If a directory component, symlink, or leaf type
            violates managed-storage policy.
        OSError: If the underlying filesystem operation fails.
    """

    _unlink_regular_file(
        settings=settings,
        directory_name=_METADATA_DIRECTORY,
        filename=get_meta_filename(_canonical_uuid(file_id)),
    )


def _canonical_uuid(file_id: UUID) -> UUID:
    """Parse one storage identifier into the canonical UUID representation."""

    if not isinstance(file_id, UUID):
        raise ManagedStoragePathError("Managed storage id must be a UUID.")

    try:
        return UUID(str(file_id))
    except (TypeError, ValueError) as exc:
        raise ManagedStoragePathError("Managed storage id must be a UUID.") from exc


def _open_regular_file(
    *,
    settings: Settings,
    directory_name: str,
    filename: str,
) -> OpenedManagedFile:
    """Open a regular leaf from one verified managed storage directory."""

    flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | _required_open_flag("O_NOFOLLOW")
        | _close_on_exec_flag()
    )
    with _managed_storage_directory(settings, directory_name) as directory_fd:
        try:
            file_fd = os.open(filename, flags, dir_fd=directory_fd)
        except OSError as exc:
            _raise_policy_error_for_open(exc)
            raise

    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ManagedStoragePathError(
                "Managed storage entry is not a regular file."
            )
        return OpenedManagedFile(
            stream=os.fdopen(file_fd, "rb", closefd=True),
            size_bytes=file_stat.st_size,
        )
    except Exception:
        os.close(file_fd)
        raise


def _unlink_regular_file(
    *,
    settings: Settings,
    directory_name: str,
    filename: str,
) -> None:
    """Inspect and unlink a regular leaf relative to a verified directory."""

    with _managed_storage_directory(settings, directory_name) as directory_fd:
        try:
            entry_stat = os.stat(
                filename,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            _raise_policy_error_for_open(exc)
            raise

        if not stat.S_ISREG(entry_stat.st_mode):
            raise ManagedStoragePathError(
                "Managed storage entry is not a regular file."
            )

        try:
            os.unlink(filename, dir_fd=directory_fd)
        except OSError as exc:
            _raise_policy_error_for_open(exc)
            raise


@contextmanager
def _managed_storage_directory(
    settings: Settings,
    leaf_directory: str,
) -> Iterator[int]:
    """Yield an owned descriptor for one STAR-managed storage directory."""

    root_path = Path(settings.star_root_dir)
    root_fd = _open_directory_path(root_path)
    current_fd = root_fd
    try:
        for component in (*_DIRECTORY_COMPONENTS, leaf_directory):
            next_fd = _open_directory_at(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def _open_directory_path(path: Path) -> int:
    """Open the configured trusted root as a no-follow directory."""

    try:
        directory_fd = os.open(str(path), _directory_open_flags())
    except OSError as exc:
        _raise_policy_error_for_open(exc)
        raise
    _require_directory(directory_fd)
    return directory_fd


def _open_directory_at(parent_fd: int, component: str) -> int:
    """Open one server-controlled directory component without following links."""

    try:
        directory_fd = os.open(component, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        _raise_policy_error_for_open(exc)
        raise
    _require_directory(directory_fd)
    return directory_fd


def _directory_open_flags() -> int:
    """Return the required POSIX flags for managed directory traversal."""

    return (
        os.O_RDONLY
        | _required_open_flag("O_DIRECTORY")
        | _required_open_flag("O_NOFOLLOW")
        | _close_on_exec_flag()
    )


def _required_open_flag(flag_name: str) -> int:
    """Return a required platform open flag or fail closed when unavailable."""

    value = getattr(os, flag_name, None)
    if value is None:
        raise ManagedStoragePlatformError(
            f"Managed storage requires the POSIX {flag_name} primitive."
        )
    return value


def _close_on_exec_flag() -> int:
    """Return close-on-exec when the platform makes it available."""

    return getattr(os, "O_CLOEXEC", 0)


def _require_directory(directory_fd: int) -> None:
    """Verify that an opened descriptor still denotes a directory."""

    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise ManagedStoragePathError(
                "Managed storage component is not a directory."
            )
    except Exception:
        os.close(directory_fd)
        raise


def _raise_policy_error_for_open(exc: OSError) -> None:
    """Translate symlink and wrong-type open failures into policy failures."""

    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        raise ManagedStoragePathError(
            "Managed storage path violates filesystem policy."
        ) from exc
