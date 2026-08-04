"""Tests for managed file metadata persistence helpers."""

from __future__ import annotations

from star.core.files import (
    EMPTY_SHA256,
    create_placeholder_file_metadata,
    delete_blob_file,
    delete_metadata_file,
    ensure_storage_dirs,
    get_blob_path,
    get_meta_path,
    load_file_metadata,
    save_file_metadata,
)


def test_save_and_load_metadata_round_trips_valid_record(settings, make_file_metadata):
    """
    GIVEN a valid managed file metadata record
    WHEN metadata is saved and loaded from the STAR metadata sidecar
    THEN the validated record round-trips without leaving a temporary sidecar
    """

    metadata = make_file_metadata(original_filename="informe.txt")
    ensure_storage_dirs(settings)

    save_file_metadata(metadata, settings)

    assert load_file_metadata(metadata.id, settings) == metadata
    assert get_meta_path(metadata.id, settings).exists()
    assert not get_meta_path(metadata.id, settings).with_suffix(".json.tmp").exists()


def test_load_metadata_returns_none_when_sidecar_is_missing(
    settings,
    make_file_metadata,
):
    """
    GIVEN no metadata sidecar exists for a managed file id
    WHEN metadata is loaded
    THEN None is returned instead of creating storage state
    """

    metadata = make_file_metadata()

    assert load_file_metadata(metadata.id, settings) is None
    assert not get_meta_path(metadata.id, settings).exists()


def test_create_placeholder_metadata_persists_pending_generated_output(settings):
    """
    GIVEN managed storage for generated action outputs
    WHEN placeholder metadata is created
    THEN a pending metadata sidecar is persisted with empty-content metadata
    """

    metadata = create_placeholder_file_metadata(
        original_filename="action.output.bin",
        settings=settings,
    )

    assert metadata.status == "pending"
    assert metadata.size_bytes == 0
    assert metadata.sha256 == EMPTY_SHA256
    assert load_file_metadata(metadata.id, settings) == metadata


def test_delete_helpers_remove_blob_and_metadata_sidecars(settings, make_file_metadata):
    """
    GIVEN a persisted managed file blob and metadata sidecar
    WHEN strict delete helpers remove both artifacts
    THEN the removed paths are returned and no artifacts remain
    """

    metadata = make_file_metadata()
    ensure_storage_dirs(settings)
    blob_path = get_blob_path(metadata.id, settings)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"managed content")
    save_file_metadata(metadata, settings)

    deleted_blob = delete_blob_file(metadata.id, settings)
    deleted_metadata = delete_metadata_file(metadata.id, settings)

    assert deleted_blob == blob_path
    assert deleted_metadata == get_meta_path(metadata.id, settings)
    assert not blob_path.exists()
    assert not get_meta_path(metadata.id, settings).exists()
