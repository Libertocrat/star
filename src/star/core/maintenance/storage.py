"""Offline inspection and conservative repair for local managed storage.

This module deliberately lives outside ``core.files`` and does not extend
``ManagedFileStore``: normal file lifecycle operations and administrative
reconciliation have different availability, locking, and failure contracts.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from star.core.config import Settings
from star.core.files.filesystem import (
    ManagedStoragePathError,
    maintenance_lock,
    managed_storage_directory,
)
from star.core.files.layout import ensure_storage_dirs, get_blob_filename
from star.core.schemas.files import FileMetadata

_BLOB_DIRECTORY = "blobs"
_METADATA_DIRECTORY = "meta"
_TMP_DIRECTORY = "tmp"
_DIRECTORIES = (_BLOB_DIRECTORY, _METADATA_DIRECTORY, _TMP_DIRECTORY)
_BLOB_PATTERN = re.compile(r"^file_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.bin$")
_META_PATTERN = re.compile(
    r"^file_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.json$"
)
_LOCK_PATTERN = re.compile(
    r"^file_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.lock$"
)
_UPLOAD_TMP_PATTERN = re.compile(
    r"^upload_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.tmp$"
)
_META_TMP_PATTERN = re.compile(
    r"^\.file_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.json\.[A-Za-z0-9_-]+\.tmp$"
)


class StorageMaintenanceError(RuntimeError):
    """Raised when safe storage maintenance cannot complete its inventory."""


class StorageFindingCode(StrEnum):
    """Classifications emitted by local managed-storage inspection."""

    ORPHAN_BLOB = "orphan_blob"
    MISSING_READY_BLOB = "missing_ready_blob"
    STALE_PENDING_METADATA = "stale_pending_metadata"
    STALE_UNVERIFIED_METADATA = "stale_unverified_metadata"
    STALE_UPLOAD_TEMP = "stale_upload_temp"
    STALE_METADATA_TEMP = "stale_metadata_temp"
    STALE_METADATA_LOCK = "stale_metadata_lock"
    CORRUPT_METADATA = "corrupt_metadata"
    METADATA_ID_MISMATCH = "metadata_id_mismatch"
    BLOB_REFERENCE_MISMATCH = "blob_reference_mismatch"
    BLOB_SIZE_MISMATCH = "blob_size_mismatch"
    NON_REGULAR_ARTIFACT = "non_regular_artifact"
    UNKNOWN_ARTIFACT = "unknown_artifact"


class StorageRepairAction(StrEnum):
    """Safe local mutations which may be scheduled by the repair planner."""

    NONE = "none"
    DELETE_BLOB = "delete_blob"
    DELETE_METADATA = "delete_metadata"
    DELETE_PAIR = "delete_pair"
    DELETE_TEMP = "delete_temp"
    DELETE_LOCK = "delete_lock"


class StorageFindingEligibility(StrEnum):
    """Whether a finding may be changed by this maintenance iteration."""

    ACTIONABLE = "actionable"
    TOO_RECENT = "too_recent"
    MANUAL_REVIEW = "manual_review"


class StorageRepairOutcome(StrEnum):
    """Result recorded for a planned maintenance finding."""

    NOT_APPLIED = "not_applied"
    DELETED = "deleted"
    SKIPPED_CHANGED = "skipped_changed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class StorageFinding:
    """One path-safe storage maintenance finding.

    Attributes:
        code: Stable classification without host-path details.
        artifact: Logical storage area containing the finding.
        name: Internal canonical basename used only by the executor.
        file_id: Parsed UUID when the observed basename is canonical.
        eligibility: Whether this iteration can repair the finding.
        planned_action: Mutation selected by the conservative planner.
        identity: Observed leaf identity used for TOCTOU revalidation.
    """

    code: StorageFindingCode
    artifact: Literal["blob", "metadata", "temp", "lock"]
    name: str
    file_id: UUID | None
    eligibility: StorageFindingEligibility
    planned_action: StorageRepairAction
    identity: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class StorageMaintenanceReport:
    """Deterministic report returned by inspection, planning, or repair.

    Attributes:
        mode: Requested maintenance mode.
        complete: Whether the entire bounded inventory was inspected.
        findings: Ordered findings and their final outcomes.
        outcomes: Outcome associated with each finding in order.
        entries_scanned: Number of observed entries across managed directories.
    """

    mode: Literal["inspect", "repair_plan", "repair_apply"]
    complete: bool
    findings: tuple[StorageFinding, ...]
    outcomes: tuple[StorageRepairOutcome, ...]
    entries_scanned: int

    def exit_code(self) -> int:
        """Return the stable CLI exit code for this report."""

        if not self.complete or StorageRepairOutcome.FAILED in self.outcomes:
            return 1
        if self.mode == "repair_apply" and all(
            outcome is StorageRepairOutcome.DELETED for outcome in self.outcomes
        ):
            return 0
        if self.findings:
            return 2
        return 0

    def to_dict(self) -> dict[str, object]:
        """Serialize a redacted, versioned report for the administrative CLI."""

        outcome_counts = {
            outcome.value: self.outcomes.count(outcome)
            for outcome in StorageRepairOutcome
        }
        return {
            "schema_version": 1,
            "mode": self.mode,
            "complete": self.complete,
            "summary": {
                "entries_scanned": self.entries_scanned,
                "findings": len(self.findings),
                "actionable": sum(
                    finding.eligibility is StorageFindingEligibility.ACTIONABLE
                    for finding in self.findings
                ),
                "manual_review": sum(
                    finding.eligibility is StorageFindingEligibility.MANUAL_REVIEW
                    for finding in self.findings
                ),
                **outcome_counts,
            },
            "findings": [
                {
                    "code": finding.code.value,
                    "artifact": finding.artifact,
                    "file_id": str(finding.file_id) if finding.file_id else None,
                    "eligibility": finding.eligibility.value,
                    "planned_action": finding.planned_action.value,
                    "outcome": outcome.value,
                }
                for finding, outcome in zip(self.findings, self.outcomes, strict=True)
            ],
        }


@dataclass(frozen=True, slots=True)
class _Entry:
    """Private no-follow directory entry observation."""

    directory: str
    name: str
    identity: tuple[int, int, int, int]
    mode: int
    mtime: datetime


def inspect_storage(
    settings: Settings,
    *,
    max_entries: int = 10_000,
    now: datetime | None = None,
) -> StorageMaintenanceReport:
    """Inspect local managed storage without mutating it.

    Args:
        settings: Validated runtime storage settings.
        max_entries: Maximum physical entries to inspect.
        now: Optional clock for deterministic testing.

    Returns:
        Complete deterministic inspection report.

    Raises:
        StorageMaintenanceError: If inventory cannot be performed safely.
    """

    entries = _inventory(settings, max_entries=max_entries)
    findings = _classify(entries, settings=settings, now=now or datetime.now(UTC))
    return StorageMaintenanceReport(
        mode="inspect",
        complete=True,
        findings=tuple(findings),
        outcomes=tuple(
            (
                StorageRepairOutcome.MANUAL_REVIEW
                if finding.eligibility is StorageFindingEligibility.MANUAL_REVIEW
                else StorageRepairOutcome.NOT_APPLIED
            )
            for finding in findings
        ),
        entries_scanned=len(entries),
    )


def repair_storage(
    settings: Settings,
    *,
    apply: bool,
    max_entries: int = 10_000,
    max_actions: int = 100,
    now: datetime | None = None,
) -> StorageMaintenanceReport:
    """Plan or apply conservative local-storage repair.

    Applying repair requires the global exclusive maintenance lease. The lease
    is acquired before inventory and retained through every mutation.
    """

    if max_actions < 1:
        raise ValueError("max_actions must be greater than zero")
    if not apply:
        report = inspect_storage(settings, max_entries=max_entries, now=now)
        return StorageMaintenanceReport(
            mode="repair_plan",
            complete=report.complete,
            findings=report.findings,
            outcomes=report.outcomes,
            entries_scanned=report.entries_scanned,
        )

    ensure_storage_dirs(settings)
    with maintenance_lock(settings, exclusive=True, nonblocking=True):
        report = inspect_storage(settings, max_entries=max_entries, now=now)
        outcomes: list[StorageRepairOutcome] = list(report.outcomes)
        actions = 0
        for index, finding in enumerate(report.findings):
            if finding.eligibility is not StorageFindingEligibility.ACTIONABLE:
                continue
            if actions >= max_actions:
                continue
            actions += 1
            outcomes[index] = _apply_finding(finding, settings=settings)
        return StorageMaintenanceReport(
            mode="repair_apply",
            complete=report.complete,
            findings=report.findings,
            outcomes=tuple(outcomes),
            entries_scanned=report.entries_scanned,
        )


def _inventory(settings: Settings, *, max_entries: int) -> list[_Entry]:
    """Observe managed storage leaves through anchored no-follow descriptors."""

    if not 1 <= max_entries <= 1_000_000:
        raise ValueError("max_entries must be between 1 and 1000000")
    ensure_storage_dirs(settings)
    entries: list[_Entry] = []
    try:
        for directory in _DIRECTORIES:
            with managed_storage_directory(settings, directory) as directory_fd:
                for name in sorted(os.listdir(directory_fd)):
                    if len(entries) >= max_entries:
                        raise StorageMaintenanceError(
                            "Storage inventory exceeds max_entries."
                        )
                    try:
                        observed = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise StorageMaintenanceError(
                            "Unable to inspect storage entry."
                        ) from exc
                    entries.append(
                        _Entry(
                            directory=directory,
                            name=name,
                            identity=(
                                observed.st_dev,
                                observed.st_ino,
                                observed.st_mtime_ns,
                                observed.st_size,
                            ),
                            mode=observed.st_mode,
                            mtime=datetime.fromtimestamp(observed.st_mtime, tz=UTC),
                        )
                    )
    except (ManagedStoragePathError, OSError) as exc:
        raise StorageMaintenanceError(
            "Unable to inspect managed storage safely."
        ) from exc
    return entries


def _classify(
    entries: list[_Entry], *, settings: Settings, now: datetime
) -> list[StorageFinding]:
    """Classify one complete local snapshot without changing storage."""

    cutoff = now - timedelta(seconds=settings.star_storage_repair_min_age_seconds)
    blobs: dict[UUID, _Entry] = {}
    metadata_entries: dict[UUID, _Entry] = {}
    locks: list[tuple[UUID, _Entry]] = []
    findings: list[StorageFinding] = []
    protected_ids: set[UUID] = set()

    for entry in entries:
        match = _match(entry)
        if not stat.S_ISREG(entry.mode):
            findings.append(
                _manual(StorageFindingCode.NON_REGULAR_ARTIFACT, entry, match)
            )
            if match is not None:
                protected_ids.add(match)
            continue
        if entry.directory == _BLOB_DIRECTORY and match is not None:
            blobs[match] = entry
        elif (
            entry.directory == _METADATA_DIRECTORY
            and _META_PATTERN.fullmatch(entry.name)
            and match is not None
        ):
            metadata_entries[match] = entry
        elif (
            entry.directory == _METADATA_DIRECTORY
            and _LOCK_PATTERN.fullmatch(entry.name)
            and match is not None
        ):
            locks.append((match, entry))
        elif entry.directory == _TMP_DIRECTORY and _UPLOAD_TMP_PATTERN.fullmatch(
            entry.name
        ):
            findings.append(
                _aged(
                    StorageFindingCode.STALE_UPLOAD_TEMP,
                    "temp",
                    entry,
                    match,
                    StorageRepairAction.DELETE_TEMP,
                    cutoff,
                )
            )
        elif entry.directory == _METADATA_DIRECTORY and _META_TMP_PATTERN.fullmatch(
            entry.name
        ):
            findings.append(
                _aged(
                    StorageFindingCode.STALE_METADATA_TEMP,
                    "temp",
                    entry,
                    match,
                    StorageRepairAction.DELETE_TEMP,
                    cutoff,
                )
            )
        else:
            findings.append(_manual(StorageFindingCode.UNKNOWN_ARTIFACT, entry, match))

    for file_id, entry in metadata_entries.items():
        try:
            metadata = _load_metadata_entry(entry, settings)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            findings.append(
                _manual(StorageFindingCode.CORRUPT_METADATA, entry, file_id)
            )
            protected_ids.add(file_id)
            continue
        if metadata.id != file_id:
            findings.append(
                _manual(StorageFindingCode.METADATA_ID_MISMATCH, entry, file_id)
            )
            protected_ids.add(file_id)
            continue
        if metadata.stored_filename != get_blob_filename(file_id):
            findings.append(
                _manual(StorageFindingCode.BLOB_REFERENCE_MISMATCH, entry, file_id)
            )
            protected_ids.add(file_id)
            continue
        blob = blobs.get(file_id)
        if metadata.status == "ready":
            if blob is None:
                findings.append(
                    _aged(
                        StorageFindingCode.MISSING_READY_BLOB,
                        "metadata",
                        entry,
                        file_id,
                        StorageRepairAction.DELETE_METADATA,
                        cutoff,
                        metadata=metadata,
                    )
                )
            elif blob.identity[3] != metadata.size_bytes:
                findings.append(
                    _manual(StorageFindingCode.BLOB_SIZE_MISMATCH, entry, file_id)
                )
                protected_ids.add(file_id)
        elif metadata.status == "pending":
            findings.append(
                _aged(
                    StorageFindingCode.STALE_PENDING_METADATA,
                    "metadata",
                    entry,
                    file_id,
                    StorageRepairAction.DELETE_PAIR,
                    cutoff,
                    metadata=metadata,
                    companion=blob,
                )
            )
        elif metadata.status == "unverified":
            findings.append(
                _aged(
                    StorageFindingCode.STALE_UNVERIFIED_METADATA,
                    "metadata",
                    entry,
                    file_id,
                    StorageRepairAction.DELETE_PAIR,
                    cutoff,
                    metadata=metadata,
                    companion=blob,
                )
            )

    for file_id, blob in blobs.items():
        if file_id not in metadata_entries and file_id not in protected_ids:
            findings.append(
                _aged(
                    StorageFindingCode.ORPHAN_BLOB,
                    "blob",
                    blob,
                    file_id,
                    StorageRepairAction.DELETE_BLOB,
                    cutoff,
                )
            )

    for file_id, lock in locks:
        if (
            file_id not in metadata_entries
            and file_id not in blobs
            and file_id not in protected_ids
        ):
            findings.append(
                _aged(
                    StorageFindingCode.STALE_METADATA_LOCK,
                    "lock",
                    lock,
                    file_id,
                    StorageRepairAction.DELETE_LOCK,
                    cutoff,
                )
            )

    return sorted(
        findings, key=lambda item: (item.artifact, item.name, item.code.value)
    )


def _match(entry: _Entry) -> UUID | None:
    """Return a canonical UUID encoded by a recognized basename."""

    patterns = {
        _BLOB_DIRECTORY: (_BLOB_PATTERN,),
        _METADATA_DIRECTORY: (_META_PATTERN, _LOCK_PATTERN, _META_TMP_PATTERN),
        _TMP_DIRECTORY: (_UPLOAD_TMP_PATTERN,),
    }
    for pattern in patterns[entry.directory]:
        match = pattern.fullmatch(entry.name)
        if match:
            return UUID(match.group(1))
    return None


def _load_metadata_entry(entry: _Entry, settings: Settings) -> FileMetadata:
    """Read one canonical metadata entry through a verified directory descriptor."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("Managed storage maintenance requires O_NOFOLLOW.")
    flags = os.O_RDONLY | os.O_NONBLOCK | no_follow | getattr(os, "O_CLOEXEC", 0)
    with managed_storage_directory(settings, _METADATA_DIRECTORY) as directory_fd:
        fd = os.open(entry.name, flags, dir_fd=directory_fd)
    try:
        observed = os.fstat(fd)
        if not stat.S_ISREG(observed.st_mode):
            raise OSError("Metadata entry is not regular.")
        payload = os.read(fd, max(observed.st_size + 1, 1))
    finally:
        os.close(fd)
    return FileMetadata.model_validate(json.loads(payload.decode("utf-8")))


def _aged(
    code: StorageFindingCode,
    artifact: Literal["blob", "metadata", "temp", "lock"],
    entry: _Entry,
    file_id: UUID | None,
    action: StorageRepairAction,
    cutoff: datetime,
    *,
    metadata: FileMetadata | None = None,
    companion: _Entry | None = None,
) -> StorageFinding:
    """Create an actionable finding only when all relevant state is old."""

    old_enough = entry.mtime < cutoff
    if metadata is not None:
        old_enough = old_enough and metadata.updated_at < cutoff
    if companion is not None:
        old_enough = old_enough and companion.mtime < cutoff
    return StorageFinding(
        code=code,
        artifact=artifact,
        name=entry.name,
        file_id=file_id,
        eligibility=(
            StorageFindingEligibility.ACTIONABLE
            if old_enough
            else StorageFindingEligibility.TOO_RECENT
        ),
        planned_action=(action if old_enough else StorageRepairAction.NONE),
        identity=entry.identity,
    )


def _manual(
    code: StorageFindingCode, entry: _Entry, file_id: UUID | None
) -> StorageFinding:
    """Create a finding that this iteration must not mutate."""

    return StorageFinding(
        code,
        _artifact(entry),
        entry.name,
        file_id,
        StorageFindingEligibility.MANUAL_REVIEW,
        StorageRepairAction.NONE,
        entry.identity,
    )


def _artifact(entry: _Entry) -> Literal["blob", "metadata", "temp", "lock"]:
    """Map one storage directory and basename to its report artifact kind."""

    if entry.directory == _BLOB_DIRECTORY:
        return "blob"
    if entry.directory == _TMP_DIRECTORY:
        return "temp"
    if _LOCK_PATTERN.fullmatch(entry.name):
        return "lock"
    return "metadata"


def _apply_finding(
    finding: StorageFinding, *, settings: Settings
) -> StorageRepairOutcome:
    """Revalidate and apply one safe maintenance action."""

    try:
        if finding.planned_action is StorageRepairAction.DELETE_PAIR:
            _unlink_if_unchanged(
                _METADATA_DIRECTORY, finding.name, finding.identity, settings
            )
            if finding.file_id is not None:
                _unlink_if_regular(
                    _BLOB_DIRECTORY, get_blob_filename(finding.file_id), settings
                )
        elif finding.planned_action is StorageRepairAction.DELETE_BLOB:
            _unlink_if_unchanged(
                _BLOB_DIRECTORY, finding.name, finding.identity, settings
            )
        elif finding.planned_action is StorageRepairAction.DELETE_METADATA:
            _unlink_if_unchanged(
                _METADATA_DIRECTORY, finding.name, finding.identity, settings
            )
        elif finding.planned_action is StorageRepairAction.DELETE_TEMP:
            directory = (
                _TMP_DIRECTORY
                if finding.artifact == "temp" and finding.name.startswith("upload_")
                else _METADATA_DIRECTORY
            )
            _unlink_if_unchanged(directory, finding.name, finding.identity, settings)
        elif finding.planned_action is StorageRepairAction.DELETE_LOCK:
            _unlink_if_unchanged(
                _METADATA_DIRECTORY, finding.name, finding.identity, settings
            )
        return StorageRepairOutcome.DELETED
    except (FileNotFoundError, _ChangedEntry):
        return StorageRepairOutcome.SKIPPED_CHANGED
    except (ManagedStoragePathError, OSError):
        return StorageRepairOutcome.FAILED


class _ChangedEntry(Exception):
    """Private signal that an entry changed since inventory."""


def _unlink_if_unchanged(
    directory: str, name: str, identity: tuple[int, int, int, int], settings: Settings
) -> None:
    """Delete a regular entry only when its no-follow identity is unchanged."""

    with managed_storage_directory(settings, directory) as directory_fd:
        observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        current = (
            observed.st_dev,
            observed.st_ino,
            observed.st_mtime_ns,
            observed.st_size,
        )
        if current != identity:
            raise _ChangedEntry()
        if not stat.S_ISREG(observed.st_mode):
            raise ManagedStoragePathError("Managed storage entry is not regular.")
        os.unlink(name, dir_fd=directory_fd)


def _unlink_if_regular(directory: str, name: str, settings: Settings) -> None:
    """Delete an optional regular companion while refusing special entries."""

    with managed_storage_directory(settings, directory) as directory_fd:
        try:
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(observed.st_mode):
            raise ManagedStoragePathError("Managed storage entry is not regular.")
        os.unlink(name, dir_fd=directory_fd)
