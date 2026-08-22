"""Test static release and smoke supply-chain workflow assertions."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import assert_release_supply_chain

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _copy_release_contract_tree(destination: Path) -> Path:
    """Copy the release workflow and action contract tree into a test root.

    Args:
        destination: Empty temporary directory supplied by pytest.

    Returns:
        Repository-shaped root containing the static contract inputs.
    """
    github = destination / ".github"
    shutil.copytree(REPOSITORY_ROOT / ".github", github)
    return destination


def _read_yaml(path: Path) -> dict[str, Any]:
    """Load a mutable YAML fixture mapping.

    Args:
        path: Workflow or action fixture to load.

    Returns:
        Parsed mutable mapping.
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Write one deterministic YAML fixture.

    Args:
        path: Fixture destination.
        payload: Mapping to serialize.
    """
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _action_step(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a mutable named composite-action step.

    Args:
        payload: Composite action fixture.
        name: Exact step name to find.

    Returns:
        Mutable step mapping.
    """
    runs = payload["runs"]
    assert isinstance(runs, dict)
    steps = runs["steps"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step, dict)
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing fixture step: {name}")


def _mutate_core_push(root: Path) -> None:
    """Remove digest capture from a copied release core fixture."""
    path = root / ".github/actions/release-core/action.yml"
    payload = _read_yaml(path)
    _action_step(payload, "Push semver + sha + latest tags").update(
        {"run": "docker push image"}
    )
    _write_yaml(path, payload)


def _remove_smoke_release_token(root: Path) -> None:
    """Remove the token forwarded to the smoke release composite action."""
    path = root / ".github/workflows/release-smoke-test.yml"
    payload = _read_yaml(path)
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    release = jobs["release"]
    assert isinstance(release, dict)
    steps = release["steps"]
    assert isinstance(steps, list)
    core = steps[1]
    assert isinstance(core, dict)
    inputs = core["with"]
    assert isinstance(inputs, dict)
    inputs.pop("github-token")
    _write_yaml(path, payload)


def test_committed_release_contracts_satisfy_supply_chain_invariants() -> None:
    """
    GIVEN the committed release, docs, smoke, and core workflow configuration
    WHEN static supply-chain validation runs
    THEN production controls and isolated smoke routing both satisfy the contract
    """
    findings = assert_release_supply_chain.check_release_contracts(REPOSITORY_ROOT)

    assert findings == []


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        pytest.param(
            lambda root: _write_yaml(
                root / ".github/workflows/release.yml",
                {
                    **_read_yaml(root / ".github/workflows/release.yml"),
                    "permissions": {"contents": "write"},
                },
            ),
            "permissions.id-token must be write",
            id="missing_production_oidc_permission",
        ),
        pytest.param(
            lambda root: _mutate_core_push(root),
            "release image must resolve and persist one published digest",
            id="missing_digest_capture",
        ),
        pytest.param(
            _remove_smoke_release_token,
            "smoke release must forward github-token",
            id="missing_smoke_release_token",
        ),
        pytest.param(
            lambda root: _write_yaml(
                root / ".github/workflows/release-smoke-test.yml",
                {
                    **_read_yaml(root / ".github/workflows/release-smoke-test.yml"),
                    "jobs": {
                        **_read_yaml(root / ".github/workflows/release-smoke-test.yml")[
                            "jobs"
                        ],
                        "release": {
                            **_read_yaml(
                                root / ".github/workflows/release-smoke-test.yml"
                            )["jobs"]["release"],
                            "environment": "release-smoke",
                        },
                    },
                },
            ),
            "smoke publication must use release-smoke without deployments",
            id="smoke_creates_deployment_history",
        ),
    ],
)
def test_release_validator_rejects_weakened_contract(
    tmp_path: Path, mutator: Any, expected_message: str
) -> None:
    """
    GIVEN a release contract fixture with one required control removed
    WHEN static validation runs
    THEN it reports the specific weakened invariant
    """
    root = _copy_release_contract_tree(tmp_path)

    mutator(root)
    findings = assert_release_supply_chain.check_release_contracts(root)

    assert expected_message in [finding.message for finding in findings]
