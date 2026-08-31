"""Test explicit least-privilege permissions for read-only workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
READ_ONLY_PERMISSIONS = {"contents": "read"}


def _read_workflow(path: Path) -> dict[str, Any]:
    """Load one GitHub Actions workflow mapping.

    Args:
        path: Workflow file to parse.

    Returns:
        Parsed workflow mapping.
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    "workflow_name",
    [
        pytest.param("ci.yml", id="ci"),
        pytest.param("security.yml", id="security"),
    ],
)
def test_read_only_workflow_permissions_are_explicit(workflow_name: str) -> None:
    """
    GIVEN a CI or deep-security workflow that does not publish repository state
    WHEN its GitHub Actions permissions contract is parsed
    THEN it grants only read access to repository contents without job overrides
    """
    workflow = _read_workflow(REPOSITORY_ROOT / ".github" / "workflows" / workflow_name)

    assert workflow.get("permissions") == READ_ONLY_PERMISSIONS

    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert all(
        isinstance(job, dict) and "permissions" not in job for job in jobs.values()
    )
