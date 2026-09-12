"""Tests for conservative offline managed-storage maintenance."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from star.core.files.filesystem import maintenance_lock
from star.core.files.layout import (
    ensure_storage_dirs,
    get_blob_dir,
    get_blob_path,
    get_meta_path,
)
from star.core.files.metadata import save_file_metadata
from star.core.maintenance.storage import (
    StorageFindingCode,
    StorageFindingEligibility,
    StorageRepairOutcome,
    inspect_storage,
    repair_storage,
)


def _age(path: Path, *, seconds: int = 7200) -> None:
    """Set a deterministic old mtime for an isolated test artifact."""

    timestamp = (datetime.now(UTC) - timedelta(seconds=seconds)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_inspect_classifies_old_orphan_blob_without_leaking_path(settings):
    """
    GIVEN an old canonical regular blob with no metadata sidecar
    WHEN offline storage inspection runs
    THEN it produces one actionable orphan finding without a host path
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    blob_path = get_blob_path(file_id, settings)
    blob_path.write_bytes(b"orphan")
    _age(blob_path)

    report = inspect_storage(settings)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code is StorageFindingCode.ORPHAN_BLOB
    assert finding.file_id == file_id
    assert finding.eligibility is StorageFindingEligibility.ACTIONABLE
    assert str(Path(settings.star_root_dir)) not in str(report.to_dict())


def test_repair_removes_old_orphan_blob_under_exclusive_lease(settings):
    """
    GIVEN an old canonical orphan blob
    WHEN offline repair is explicitly applied
    THEN the blob is removed and the report exits cleanly
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    blob_path = get_blob_path(file_id, settings)
    blob_path.write_bytes(b"orphan")
    _age(blob_path)

    report = repair_storage(settings, apply=True)

    assert not blob_path.exists()
    assert report.outcomes == (StorageRepairOutcome.DELETED,)
    assert report.exit_code() == 0


def test_repair_retains_recent_pending_metadata(settings, make_file_metadata):
    """
    GIVEN a pending output metadata record inside the repair grace period
    WHEN offline repair is applied
    THEN the record remains untouched and is reported as too recent
    """

    ensure_storage_dirs(settings)
    metadata = make_file_metadata(status="pending")
    save_file_metadata(metadata, settings)

    report = repair_storage(settings, apply=True)

    assert get_meta_path(metadata.id, settings).exists()
    assert report.findings[0].code is StorageFindingCode.STALE_PENDING_METADATA
    assert report.findings[0].eligibility is StorageFindingEligibility.TOO_RECENT
    assert report.exit_code() == 2


def test_inspect_preserves_corrupt_metadata_for_manual_review(settings):
    """
    GIVEN a canonical metadata sidecar containing invalid JSON
    WHEN offline storage inspection runs
    THEN it is retained and reported only for manual review
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    metadata_path = get_meta_path(file_id, settings)
    metadata_path.write_text("{invalid", encoding="utf-8")
    _age(metadata_path)

    report = repair_storage(settings, apply=True)

    assert metadata_path.exists()
    assert report.findings[0].code is StorageFindingCode.CORRUPT_METADATA
    assert report.outcomes == (StorageRepairOutcome.MANUAL_REVIEW,)
    assert report.exit_code() == 2


def test_inspect_does_not_treat_wrong_blob_suffix_as_an_orphan(settings):
    """
    GIVEN a UUID-looking entry with a metadata suffix in the blob directory
    WHEN offline storage inspection runs
    THEN it remains an unknown artifact rather than becoming deletable content
    """

    ensure_storage_dirs(settings)
    file_id = uuid4()
    wrong_entry = get_blob_dir(settings) / f"file_{file_id}.json"
    wrong_entry.write_text("{}", encoding="utf-8")
    _age(wrong_entry)

    report = inspect_storage(settings)

    assert report.findings[0].code is StorageFindingCode.UNKNOWN_ARTIFACT
    assert report.findings[0].eligibility is StorageFindingEligibility.MANUAL_REVIEW


def test_repair_refuses_a_concurrent_exclusive_maintenance_lease(settings):
    """
    GIVEN another process holds STAR's exclusive storage-maintenance lease
    WHEN repair attempts to apply mutations
    THEN it fails immediately without inspecting or changing storage
    """

    ensure_storage_dirs(settings)

    with maintenance_lock(settings, exclusive=True):
        try:
            repair_storage(settings, apply=True)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("Concurrent repair unexpectedly acquired the lease.")


def test_repair_refuses_the_runtime_shared_maintenance_lease(settings):
    """
    GIVEN a STAR runtime holds the shared maintenance lease
    WHEN offline repair attempts to apply mutations
    THEN it refuses the operation instead of racing the active runtime
    """

    ensure_storage_dirs(settings)

    with maintenance_lock(settings, exclusive=False):
        try:
            repair_storage(settings, apply=True)
        except BlockingIOError:
            pass
        else:
            raise AssertionError(
                "Repair unexpectedly ran while runtime lease was held."
            )
