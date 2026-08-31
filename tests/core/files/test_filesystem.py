"""Tests for descriptor-relative managed storage filesystem operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from star.core.files import filesystem
from star.core.files.filesystem import (
    ManagedStoragePathError,
    ManagedStoragePlatformError,
    open_managed_blob_for_read,
    unlink_managed_blob,
    unlink_managed_metadata,
)
from star.core.files.layout import ensure_storage_dirs, get_blob_path, get_meta_path


def test_open_managed_blob_reads_from_verified_descriptor(settings):
    """
    GIVEN a regular blob in STAR-managed storage
    WHEN it is opened through the descriptor-relative adapter
    THEN the returned stream reads the exact bytes and reports its observed size
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    get_blob_path(file_id, settings).write_bytes(b"verified bytes")

    opened = open_managed_blob_for_read(file_id, settings)

    try:
        assert opened.stream.read() == b"verified bytes"
        assert opened.size_bytes == len(b"verified bytes")
    finally:
        opened.stream.close()


def test_open_managed_blob_rejects_final_symlink_without_reading_target(
    settings,
    tmp_path,
):
    """
    GIVEN a derived blob entry replaced with a symlink to an external target
    WHEN the adapter opens the blob
    THEN it rejects the symlink without reading the target
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    target = tmp_path / "outside.txt"
    target.write_bytes(b"outside bytes")
    get_blob_path(file_id, settings).symlink_to(target)

    with pytest.raises(ManagedStoragePathError):
        open_managed_blob_for_read(file_id, settings)

    assert target.read_bytes() == b"outside bytes"


def test_open_managed_blob_rejects_intermediate_symlink(settings, tmp_path):
    """
    GIVEN an intermediate managed-storage directory replaced with a symlink
    WHEN the adapter opens a derived blob
    THEN descriptor traversal rejects the altered directory component
    """

    ensure_storage_dirs(settings)
    data_dir = Path(settings.star_root_dir) / "data"
    real_data_dir = tmp_path / "real-data"
    data_dir.rename(real_data_dir)
    data_dir.symlink_to(real_data_dir, target_is_directory=True)

    with pytest.raises(ManagedStoragePathError):
        open_managed_blob_for_read(uuid4(), settings)


def test_open_managed_blob_rejects_non_regular_entry(settings):
    """
    GIVEN a derived blob entry that is a directory
    WHEN the adapter opens the blob
    THEN it rejects the non-regular filesystem object
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    get_blob_path(file_id, settings).mkdir()

    with pytest.raises(ManagedStoragePathError):
        open_managed_blob_for_read(file_id, settings)


def test_unlink_managed_blob_rejects_symlink_and_preserves_target(settings, tmp_path):
    """
    GIVEN a derived blob entry that is a symlink to an external file
    WHEN descriptor-relative deletion is requested
    THEN the symlink is rejected and its target remains unchanged
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    target = tmp_path / "outside.txt"
    target.write_bytes(b"outside bytes")
    get_blob_path(file_id, settings).symlink_to(target)

    with pytest.raises(ManagedStoragePathError):
        unlink_managed_blob(file_id, settings)

    assert get_blob_path(file_id, settings).is_symlink()
    assert target.read_bytes() == b"outside bytes"


def test_unlink_managed_metadata_rejects_symlink_and_preserves_target(
    settings,
    tmp_path,
):
    """
    GIVEN a derived metadata entry that is a symlink to an external file
    WHEN descriptor-relative deletion is requested
    THEN the symlink is rejected and its target remains unchanged
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    target = tmp_path / "outside.json"
    target.write_text('{"outside": true}', encoding="utf-8")
    get_meta_path(file_id, settings).symlink_to(target)

    with pytest.raises(ManagedStoragePathError):
        unlink_managed_metadata(file_id, settings)

    assert get_meta_path(file_id, settings).is_symlink()
    assert target.read_text(encoding="utf-8") == '{"outside": true}'


def test_unlink_managed_entries_removes_regular_blob_and_metadata(settings):
    """
    GIVEN regular blob and metadata entries in managed storage
    WHEN each descriptor-relative delete operation runs
    THEN only the server-derived regular entries are removed
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    get_blob_path(file_id, settings).write_bytes(b"blob")
    get_meta_path(file_id, settings).write_text("{}", encoding="utf-8")

    unlink_managed_blob(file_id, settings)
    unlink_managed_metadata(file_id, settings)

    assert not get_blob_path(file_id, settings).exists()
    assert not get_meta_path(file_id, settings).exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO support is required")
def test_open_managed_blob_rejects_fifo(settings):
    """
    GIVEN a derived blob entry that is a FIFO
    WHEN the adapter opens the blob
    THEN it rejects the non-regular object without blocking on the FIFO
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    os.mkfifo(get_blob_path(file_id, settings))

    with pytest.raises(ManagedStoragePathError):
        open_managed_blob_for_read(file_id, settings)


def test_managed_storage_operations_reject_non_uuid_internal_values(settings):
    """
    GIVEN an invalid identifier injected below the typed API boundary
    WHEN a managed storage operation derives its server-controlled basename
    THEN the adapter rejects it instead of accepting path-like text
    """

    invalid_id = cast(UUID, "../outside")

    with pytest.raises(ManagedStoragePathError):
        open_managed_blob_for_read(invalid_id, settings)
    with pytest.raises(ManagedStoragePathError):
        unlink_managed_metadata(invalid_id, settings)


def test_open_managed_blob_fails_closed_without_required_posix_flag(
    settings,
    monkeypatch,
):
    """
    GIVEN a runtime without the required O_NOFOLLOW primitive
    WHEN the adapter attempts to open a managed blob
    THEN it fails closed instead of falling back to pathname-based access
    """

    monkeypatch.delattr(filesystem.os, "O_NOFOLLOW")

    with pytest.raises(ManagedStoragePlatformError):
        open_managed_blob_for_read(uuid4(), settings)
