"""Tests for managed file storage layout helpers."""

from __future__ import annotations

import stat
from pathlib import Path
from uuid import UUID

from star.core.files import (
    ensure_storage_dirs,
    get_blob_dir,
    get_blob_path,
    get_data_root,
    get_files_root,
    get_meta_dir,
    get_meta_path,
    get_runtime_root,
    get_secret_tmp_dir,
    get_tmp_dir,
)


def test_layout_paths_are_derived_from_star_root(settings):
    """
    GIVEN settings with an isolated STAR root directory
    WHEN managed file layout helpers resolve storage paths
    THEN every path is derived from the fixed data/files and data/runtime layout
    """

    root = Path(settings.star_root_dir).resolve()
    file_id = UUID("00000000-0000-0000-0000-000000000123")

    assert get_data_root(settings) == root / "data"
    assert get_files_root(settings) == root / "data" / "files"
    assert get_blob_dir(settings) == root / "data" / "files" / "blobs"
    assert get_meta_dir(settings) == root / "data" / "files" / "meta"
    assert get_tmp_dir(settings) == root / "data" / "files" / "tmp"
    assert get_runtime_root(settings) == root / "data" / "runtime"
    assert get_secret_tmp_dir(settings) == root / "data" / "runtime" / "secrets"
    assert get_blob_path(file_id, settings).name == f"file_{file_id}.bin"
    assert get_meta_path(file_id, settings).name == f"file_{file_id}.json"


def test_ensure_storage_dirs_creates_managed_and_runtime_directories(settings):
    """
    GIVEN an isolated STAR root without storage directories
    WHEN storage directories are ensured
    THEN managed file directories and runtime secret directories exist
    AND runtime secret permissions are restrictive
    """

    ensure_storage_dirs(settings)

    for path in (
        get_data_root(settings),
        get_files_root(settings),
        get_blob_dir(settings),
        get_meta_dir(settings),
        get_tmp_dir(settings),
        get_runtime_root(settings),
        get_secret_tmp_dir(settings),
    ):
        assert path.is_dir()

    secret_mode = stat.S_IMODE(get_secret_tmp_dir(settings).stat().st_mode)
    assert secret_mode == 0o700
