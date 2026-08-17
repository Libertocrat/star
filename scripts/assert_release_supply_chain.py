"""Validate the release workflow supply-chain evidence contract."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Finding:
    """Describe one static release supply-chain contract violation.

    Attributes:
        source: Workflow file that violated the contract.
        message: Human-readable description of the missing or unsafe control.
    """

    source: Path
    message: str


def _read_workflow(path: Path) -> dict[str, Any]:
    """Load a GitHub Actions workflow mapping.

    Args:
        path: Workflow YAML file to parse.

    Returns:
        Parsed top-level YAML mapping.

    Raises:
        ValueError: If the file is missing, invalid YAML, or not a mapping.
    """
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _workflow_steps(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Return release job steps when their shape is valid.

    Args:
        payload: Parsed workflow mapping.

    Returns:
        Release job steps, or ``None`` when the workflow shape is invalid.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, Mapping):
        return None
    release = jobs.get("release")
    if not isinstance(release, Mapping):
        return None
    steps = release.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, str):
        return None
    if not all(isinstance(step, dict) for step in steps):
        return None
    return list(steps)


def _named_step(
    steps: Sequence[Mapping[str, Any]], name: str
) -> tuple[int, Mapping[str, Any]] | None:
    """Find a named workflow step and retain its order index.

    Args:
        steps: Workflow steps to inspect.
        name: Exact step name required by the contract.

    Returns:
        Step index and mapping, or ``None`` when absent.
    """
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index, step
    return None


def _has_attestation(steps: Sequence[Mapping[str, Any]], *, sbom: bool) -> bool:
    """Return whether an expected image attestation step exists.

    Args:
        steps: Workflow steps to inspect.
        sbom: Whether the required attestation must include an SPDX SBOM path.

    Returns:
        ``True`` only when the corresponding image attestation is configured.
    """
    for step in steps:
        if step.get("uses") != "actions/attest@v4":
            continue
        inputs = step.get("with")
        if not isinstance(inputs, Mapping):
            continue
        is_sbom = "sbom-path" in inputs
        if is_sbom != sbom:
            continue
        if (
            inputs.get("subject-name")
            == "ghcr.io/${{ env.IMAGE_OWNER }}/${{ env.IMAGE_NAME }}"
            and inputs.get("subject-digest") == "${{ env.IMAGE_DIGEST }}"
            and inputs.get("push-to-registry") is True
        ):
            return True
    return False


def _release_assets(step: Mapping[str, Any]) -> set[str]:
    """Return trimmed asset paths declared by the GitHub release step.

    Args:
        step: GitHub release workflow step.

    Returns:
        Declared release asset paths.
    """
    inputs = step.get("with")
    if not isinstance(inputs, Mapping):
        return set()
    files = inputs.get("files")
    if not isinstance(files, str):
        return set()
    return {line.strip() for line in files.splitlines() if line.strip()}


def check_release_workflow(path: Path) -> list[Finding]:
    """Validate STAR's release supply-chain workflow invariants.

    Args:
        path: Release workflow YAML file to validate.

    Returns:
        Ordered findings for every missing or weakened contract.
    """
    findings: list[Finding] = []
    try:
        payload = _read_workflow(path)
    except ValueError as exc:
        return [Finding(path, str(exc))]

    permissions = payload.get("permissions")
    if not isinstance(permissions, Mapping):
        return [Finding(path, "missing top-level permissions mapping")]
    required_permissions = {
        "contents": "write",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    for permission, expected in required_permissions.items():
        if permissions.get(permission) != expected:
            findings.append(
                Finding(path, f"permissions.{permission} must be {expected}")
            )

    steps = _workflow_steps(payload)
    if steps is None:
        findings.append(Finding(path, "missing release job steps"))
        return findings

    required_order = (
        "Build image locally (no push yet)",
        "Scan image before publish (block on HIGH/CRITICAL)",
        "Push semver + sha + latest tags",
    )
    ordered_steps = [_named_step(steps, name) for name in required_order]
    if any(step is None for step in ordered_steps):
        findings.append(Finding(path, "missing build, scan, or push release step"))
    else:
        build_result, scan_result, push_result = ordered_steps
        if build_result is None or scan_result is None or push_result is None:
            raise AssertionError("required release steps must be present")

        indices = [build_result[0], scan_result[0], push_result[0]]
        if indices != sorted(indices):
            findings.append(
                Finding(path, "image scan must run before image publication")
            )

        build_step = build_result[1]
        build_inputs = build_step.get("with")
        if (
            build_step.get("uses") != "docker/build-push-action@v5"
            or not isinstance(build_inputs, Mapping)
            or build_inputs.get("push") is not False
            or build_inputs.get("load") is not True
        ):
            findings.append(
                Finding(path, "release image must build locally before publish")
            )

        scan_step = scan_result[1]
        if "trivy image" not in str(scan_step.get("run", "")):
            findings.append(Finding(path, "release image must be scanned with Trivy"))

        push_step = push_result[1]
        push_script = str(push_step.get("run", ""))
        required_push_fragments = (
            "docker push",
            "docker buildx imagetools inspect",
            "--format '{{.Manifest.Digest}}'",
            "IMAGE_DIGEST=${canonical_digest}",
        )
        if not all(fragment in push_script for fragment in required_push_fragments):
            findings.append(
                Finding(
                    path, "release image must resolve and persist one published digest"
                )
            )

    if not _has_attestation(steps, sbom=False):
        findings.append(
            Finding(path, "missing image provenance attestation for IMAGE_DIGEST")
        )
    if not _has_attestation(steps, sbom=True):
        findings.append(
            Finding(path, "missing image SBOM attestation for IMAGE_DIGEST")
        )

    sbom_step = _named_step(steps, "Generate image SBOM")
    checksum_step = _named_step(steps, "Generate release checksums")
    cosign_install = _named_step(steps, "Install Cosign")
    image_sign = _named_step(steps, "Sign published image digest with GitHub OIDC")
    checksum_sign = _named_step(steps, "Sign release checksums with GitHub OIDC")
    validate_assets = _named_step(steps, "Validate release assets")
    release_upload = _named_step(steps, "Create GitHub Release and upload assets")

    if (
        sbom_step is None
        or "trivy image" not in str(sbom_step[1].get("run", ""))
        or "--format spdx-json" not in str(sbom_step[1].get("run", ""))
        or "star-image-${GITHUB_REF_NAME}.spdx.json"
        not in str(sbom_step[1].get("run", ""))
    ):
        findings.append(
            Finding(path, "missing SPDX SBOM generation from release image")
        )
    if checksum_step is None or "star-image-${GITHUB_REF_NAME}.spdx.json" not in str(
        checksum_step[1].get("run", "")
    ):
        findings.append(Finding(path, "release checksums must include the image SBOM"))

    if (
        cosign_install is None
        or cosign_install[1].get("uses") != "sigstore/cosign-installer@v3"
    ):
        findings.append(Finding(path, "missing Cosign installer"))
    if (
        image_sign is None
        or "cosign sign --yes" not in str(image_sign[1].get("run", ""))
        or "@${IMAGE_DIGEST}" not in str(image_sign[1].get("run", ""))
    ):
        findings.append(
            Finding(path, "missing keyless Cosign signature for published image digest")
        )
    if (
        checksum_sign is None
        or "cosign sign-blob --yes" not in str(checksum_sign[1].get("run", ""))
        or "SHA256SUMS.sigstore.json" not in str(checksum_sign[1].get("run", ""))
    ):
        findings.append(Finding(path, "missing signed SHA256SUMS bundle"))
    if (
        validate_assets is None
        or "cosign verify-blob" not in str(validate_assets[1].get("run", ""))
        or "SHA256SUMS must list every release content asset exactly once"
        not in str(validate_assets[1].get("run", ""))
    ):
        findings.append(
            Finding(path, "release assets must verify signed complete checksums")
        )

    required_assets = {
        "dist/star-image-${{ github.ref_name }}.spdx.json",
        "dist/SHA256SUMS.sigstore.json",
    }
    if release_upload is None or not required_assets.issubset(
        _release_assets(release_upload[1])
    ):
        findings.append(
            Finding(
                path, "release upload must include SBOM and checksum signature bundle"
            )
        )

    return findings


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Validate STAR release supply-chain workflow contracts."
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/release.yml"),
        help="GitHub Actions release workflow to validate.",
    )
    return parser


def main() -> int:
    """Run release workflow validation and report findings.

    Returns:
        Process exit status.
    """
    args = build_parser().parse_args()
    findings = check_release_workflow(args.workflow)
    if not findings:
        print(f"Release supply-chain contract passed: {args.workflow}")
        return 0

    for finding in findings:
        print(f"{finding.source}: {finding.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
