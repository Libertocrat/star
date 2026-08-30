"""Filesystem layout helpers for STAR-managed files and runtime files."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from star.core.config import Settings, get_settings

logger = logging.getLogger("star.core.files")


def get_data_root(settings: Settings | None = None) -> Path:
    """Return the configured STAR data root as an absolute expanded path.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Absolute expanded path to the configured data root.
    """

    cfg = settings if settings is not None else get_settings()
    root = Path(cfg.star_root_dir).resolve()
    return root.joinpath("data")


def get_files_root(settings: Settings | None = None) -> Path:
    """Return the root directory for persisted managed file storage.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path for the `data/files/` storage root.
    """

    return get_data_root(settings) / "files"


def get_blob_dir(settings: Settings | None = None) -> Path:
    """Return the directory where validated blobs are persisted.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the `data/files/blobs/` directory.
    """

    return get_files_root(settings) / "blobs"


def get_meta_dir(settings: Settings | None = None) -> Path:
    """Return the directory where metadata JSON files are persisted.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the `data/files/meta/` directory.
    """

    return get_files_root(settings) / "meta"


def get_tmp_dir(settings: Settings | None = None) -> Path:
    """Return the directory where temporary uploads are staged.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the `data/files/tmp/` directory.
    """

    return get_files_root(settings) / "tmp"


def get_runtime_root(settings: Settings | None = None) -> Path:
    """Return the root directory for internal runtime-owned files.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the `data/runtime/` directory under STAR data root.
    """

    return get_data_root(settings) / "runtime"


def get_secret_tmp_dir(settings: Settings | None = None) -> Path:
    """Return the directory for ephemeral action secret files.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the `data/runtime/secrets/` directory.
    """

    return get_runtime_root(settings) / "secrets"


def get_blob_path(file_id: UUID, settings: Settings | None = None) -> Path:
    """Return the persisted blob path for a managed file id.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to `data/files/blobs/file_<uuid>.bin`.
    """

    return get_blob_dir(settings) / f"file_{file_id}.bin"


def get_meta_path(file_id: UUID, settings: Settings | None = None) -> Path:
    """Return the persisted metadata JSON path for a managed file id.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to `data/files/meta/file_<uuid>.json`.
    """

    return get_meta_dir(settings) / f"file_{file_id}.json"


def get_meta_lock_path(file_id: UUID, settings: Settings | None = None) -> Path:
    """Return the server-derived advisory lock path for one metadata record.

    Args:
        file_id: UUID of the persisted file.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Path to the metadata lock sidecar under STAR-managed storage.
    """

    return get_meta_dir(settings) / f"file_{file_id}.lock"


def ensure_storage_dirs(settings: Settings | None = None) -> None:
    """Create STAR managed storage and runtime file directories.

    Args:
        settings: Optional pre-loaded runtime settings.
    """

    cfg = settings if settings is not None else get_settings()
    root = Path(cfg.star_root_dir)
    root.mkdir(parents=True, exist_ok=True)

    data_root = get_data_root(cfg)
    data_root.mkdir(parents=True, exist_ok=True)
    get_blob_dir(cfg).mkdir(parents=True, exist_ok=True)
    get_meta_dir(cfg).mkdir(parents=True, exist_ok=True)
    get_tmp_dir(cfg).mkdir(parents=True, exist_ok=True)
    secret_tmp_dir = get_secret_tmp_dir(cfg)
    secret_tmp_dir.mkdir(parents=True, exist_ok=True)
    # Secret delivery files are invocation-owned runtime artifacts; the
    # directory stays owner-only even though individual files are short-lived.
    try:
        secret_tmp_dir.chmod(0o700)
    except OSError:
        logger.exception("Failed to set restrictive permissions on secret tmp dir")
