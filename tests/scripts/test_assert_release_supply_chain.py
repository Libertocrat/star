"""Test static release supply-chain workflow assertions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import assert_release_supply_chain

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/release.yml"


def _release_workflow_payload() -> dict[str, Any]:
    """Return the committed release workflow as a mutable fixture."""
    payload = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_workflow(path: Path, payload: dict[str, Any]) -> None:
    """Write a deterministic workflow fixture.

    Args:
        path: Destination workflow fixture path.
        payload: Workflow mapping to serialize.
    """
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _step(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Return an exact named release step from a workflow fixture.

    Args:
        payload: Mutable workflow mapping.
        name: Exact required step name.

    Returns:
        Mutable workflow step mapping.
    """
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    release = jobs["release"]
    assert isinstance(release, dict)
    steps = release["steps"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step, dict)
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing fixture step: {name}")


def test_committed_release_workflow_satisfies_supply_chain_contract() -> None:
    """
    GIVEN the committed STAR release workflow
    WHEN static supply-chain validation runs
    THEN image publication and release assets retain their evidence controls
    """
    findings = assert_release_supply_chain.check_release_workflow(RELEASE_WORKFLOW)

    assert findings == []


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        pytest.param(
            lambda payload: payload["permissions"].pop("id-token"),
            "permissions.id-token must be write",
            id="missing_oidc_permission",
        ),
        pytest.param(
            lambda payload: _step(payload, "Build image locally (no push yet)")[
                "with"
            ].update({"push": True}),
            "release image must build locally before publish",
            id="push_before_scan",
        ),
        pytest.param(
            lambda payload: _step(payload, "Push semver + sha + latest tags").update(
                {"run": "docker push image"}
            ),
            "release image must resolve and persist one published digest",
            id="missing_digest_capture",
        ),
        pytest.param(
            lambda payload: _step(payload, "Generate image SBOM").update(
                {"run": "echo no-sbom"}
            ),
            "missing SPDX SBOM generation from release image",
            id="missing_sbom_generation",
        ),
        pytest.param(
            lambda payload: _step(payload, "Generate image SBOM attestation")[
                "with"
            ].pop("sbom-path"),
            "missing image SBOM attestation for IMAGE_DIGEST",
            id="missing_sbom_attestation",
        ),
        pytest.param(
            lambda payload: _step(
                payload, "Sign published image digest with GitHub OIDC"
            ).update({"run": "echo unsigned"}),
            "missing keyless Cosign signature for published image digest",
            id="missing_image_signature",
        ),
        pytest.param(
            lambda payload: _step(payload, "Validate release assets").update(
                {"run": "sha256sum -c SHA256SUMS"}
            ),
            "release assets must verify signed complete checksums",
            id="missing_signed_manifest_verification",
        ),
        pytest.param(
            lambda payload: _step(payload, "Create GitHub Release and upload assets")[
                "with"
            ].update({"files": "dist/SHA256SUMS"}),
            "release upload must include SBOM and checksum signature bundle",
            id="missing_release_evidence_assets",
        ),
    ],
)
def test_release_validator_rejects_weakened_supply_chain_control(
    tmp_path: Path,
    mutator: Any,
    expected_message: str,
) -> None:
    """
    GIVEN a release workflow with one supply-chain control removed
    WHEN static validation runs
    THEN it reports the weakened contract
    """
    payload = _release_workflow_payload()
    mutator(payload)
    workflow_path = tmp_path / "release.yml"
    _write_workflow(workflow_path, payload)

    findings = assert_release_supply_chain.check_release_workflow(workflow_path)

    assert expected_message in [finding.message for finding in findings]
