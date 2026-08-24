"""Validate STAR release and smoke publication supply-chain contracts."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml


@dataclass(frozen=True)
class Finding:
    """Describe one static release supply-chain contract violation.

    Attributes:
        source: Workflow or action file that violated the contract.
        message: Human-readable description of the missing or unsafe control.
    """

    source: Path
    message: str


def _read_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping.

    Args:
        path: YAML file to load.

    Returns:
        Parsed top-level mapping.

    Raises:
        ValueError: If the file cannot be loaded as a mapping.
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


def _steps(payload: Mapping[str, Any], *, action: bool) -> list[dict[str, Any]] | None:
    """Return ordered action or workflow job steps when their shape is valid."""
    if action:
        runs = payload.get("runs")
        raw_steps = runs.get("steps") if isinstance(runs, Mapping) else None
    else:
        jobs = payload.get("jobs")
        release = jobs.get("release") if isinstance(jobs, Mapping) else None
        raw_steps = release.get("steps") if isinstance(release, Mapping) else None
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, str):
        return None
    if not all(isinstance(step, dict) for step in raw_steps):
        return None
    return list(raw_steps)


def _named_step(
    steps: Sequence[Mapping[str, Any]], name: str
) -> tuple[int, Mapping[str, Any]] | None:
    """Return the index and mapping for an exact named step."""
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index, step
    return None


def _with_value(step: Mapping[str, Any], name: str) -> str | None:
    """Return one string input from an action step."""
    values = step.get("with")
    if not isinstance(values, Mapping):
        return None
    value = values.get(name)
    return value if isinstance(value, str) else None


def _require_composite_token_input(
    findings: list[Finding], path: Path, payload: Mapping[str, Any]
) -> None:
    """Require a composite action to accept but not directly resolve a token."""
    inputs = payload.get("inputs")
    token = inputs.get("github-token") if isinstance(inputs, Mapping) else None
    if not isinstance(token, Mapping) or token.get("required") is not True:
        findings.append(Finding(path, "composite action must require github-token"))
    if "secrets." in yaml.safe_dump(payload):
        findings.append(
            Finding(path, "composite action must not reference the secrets context")
        )


def _require_token_forwarding(
    findings: list[Finding], path: Path, step: Mapping[str, Any], label: str
) -> None:
    """Require a workflow wrapper to forward its GitHub token to one core."""
    if _with_value(step, "github-token") != "${{ secrets.GITHUB_TOKEN }}":
        findings.append(Finding(path, f"{label} must forward github-token"))


def _workflow_trigger(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the workflow trigger mapping, accommodating PyYAML's YAML 1.1 key."""
    raw_payload = cast(Mapping[Any, Any], payload)
    trigger = raw_payload.get("on", raw_payload.get(True))
    return trigger if isinstance(trigger, Mapping) else None


def _has_attestation(steps: Sequence[Mapping[str, Any]], *, sbom: bool) -> bool:
    """Return whether the core retains one expected image attestation."""
    for step in steps:
        if step.get("uses") != "actions/attest@v4":
            continue
        values = step.get("with")
        if not isinstance(values, Mapping):
            continue
        if ("sbom-path" in values) != sbom:
            continue
        if (
            values.get("subject-name")
            == "ghcr.io/${{ env.IMAGE_OWNER }}/${{ env.IMAGE_NAME }}"
            and values.get("subject-digest") == "${{ env.IMAGE_DIGEST }}"
            and values.get("push-to-registry") is True
        ):
            return True
    return False


def _require_permission(
    findings: list[Finding], path: Path, payload: Mapping[str, Any], name: str
) -> None:
    """Append a finding unless one top-level permission has its required value."""
    permissions = payload.get("permissions")
    if not isinstance(permissions, Mapping) or permissions.get(name) != "write":
        findings.append(Finding(path, f"permissions.{name} must be write"))


def _require_local_core(
    findings: list[Finding],
    path: Path,
    payload: Mapping[str, Any],
    action: str,
    job_name: str = "release",
) -> Mapping[str, Any] | None:
    """Return one wrapper job's sole local core invocation or append a finding."""
    jobs = payload.get("jobs")
    job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
    raw_steps = job.get("steps") if isinstance(job, Mapping) else None
    steps = (
        list(raw_steps)
        if isinstance(raw_steps, Sequence) and not isinstance(raw_steps, str)
        else None
    )
    if (
        steps is None
        or len(steps) != 2
        or not isinstance(steps[0], Mapping)
        or not isinstance(steps[1], Mapping)
        or steps[0].get("uses") != "actions/checkout@v4"
        or steps[0].get("with", {}).get("fetch-depth") != 0
        or steps[1].get("uses") != action
    ):
        findings.append(
            Finding(path, f"release wrapper must checkout then call {action}")
        )
        return None
    return steps[1]


def check_release_contracts(root: Path) -> list[Finding]:
    """Validate production and smoke release workflow wiring.

    Args:
        root: Repository root containing the workflows and local actions.

    Returns:
        Ordered contract violations; an empty list means the contract holds.
    """
    paths = {
        "release": root / ".github/workflows/release.yml",
        "release_docs": root / ".github/workflows/release-docs.yml",
        "smoke": root / ".github/workflows/release-smoke-test.yml",
        "core": root / ".github/actions/release-core/action.yml",
        "docs_core": root / ".github/actions/release-docs-core/action.yml",
    }
    findings: list[Finding] = []
    payloads: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        try:
            payloads[name] = _read_yaml(path)
        except ValueError as exc:
            findings.append(Finding(path, str(exc)))
    if findings:
        return findings

    release = payloads["release"]
    for permission in ("contents", "packages", "id-token", "attestations"):
        _require_permission(findings, paths["release"], release, permission)
    release_step = _require_local_core(
        findings, paths["release"], release, "./.github/actions/release-core"
    )
    if release_step is not None:
        _require_token_forwarding(
            findings, paths["release"], release_step, "production release"
        )
        expected = {
            "image-name": "star",
            "release-draft": "false",
            "require-eligible-tag": "true",
            "verify-release-assets": "true",
        }
        for name, value in expected.items():
            if _with_value(release_step, name) != value:
                findings.append(
                    Finding(paths["release"], f"production {name} must be {value}")
                )

    _require_composite_token_input(findings, paths["core"], payloads["core"])
    _require_composite_token_input(findings, paths["docs_core"], payloads["docs_core"])

    core_steps = _steps(payloads["core"], action=True)
    if core_steps is None:
        findings.append(Finding(paths["core"], "missing composite action steps"))
        return findings
    required_order = (
        "Verify release tag eligibility",
        "Build image locally (no push yet)",
        "Scan image before publish (block on HIGH/CRITICAL)",
        "Push semver + sha + latest tags",
    )
    ordered = [_named_step(core_steps, name) for name in required_order]
    if any(step is None for step in ordered):
        findings.append(
            Finding(paths["core"], "missing release guard, build, scan, or push step")
        )
    else:
        indexes = [step[0] for step in ordered if step is not None]
        if indexes != sorted(indexes):
            findings.append(
                Finding(paths["core"], "release guard, scan, and push order is unsafe")
            )
        if indexes[0] != 0:
            findings.append(
                Finding(
                    paths["core"],
                    "release eligibility must run immediately after checkout",
                )
            )

    build = _named_step(core_steps, "Build image locally (no push yet)")
    if build is None or build[1].get("uses") != "docker/build-push-action@v5":
        findings.append(
            Finding(paths["core"], "release image must build locally before publish")
        )
    else:
        values = build[1].get("with")
        if (
            not isinstance(values, Mapping)
            or values.get("push") is not False
            or values.get("load") is not True
        ):
            findings.append(
                Finding(
                    paths["core"], "release image must build locally before publish"
                )
            )
        elif values.get("pull") is not True:
            findings.append(
                Finding(
                    paths["core"],
                    "release image must pull the current base before scanning",
                )
            )

    push = _named_step(core_steps, "Push semver + sha + latest tags")
    if push is None or not all(
        fragment in str(push[1].get("run", ""))
        for fragment in (
            "docker push",
            "docker buildx imagetools inspect",
            "--format '{{.Manifest.Digest}}'",
            "IMAGE_DIGEST=${canonical_digest}",
        )
    ):
        findings.append(
            Finding(
                paths["core"],
                "release image must resolve and persist one published digest",
            )
        )

    if not _has_attestation(core_steps, sbom=False):
        findings.append(
            Finding(
                paths["core"], "missing image provenance attestation for IMAGE_DIGEST"
            )
        )
    if not _has_attestation(core_steps, sbom=True):
        findings.append(
            Finding(paths["core"], "missing image SBOM attestation for IMAGE_DIGEST")
        )

    required_steps = {
        "Build deploy bundle assets": "star-runtime/.star-release-version",
        "Generate image SBOM": "--format spdx-json",
        "Generate release checksums": "star-image-${RELEASE_VERSION}.spdx.json",
        "Install Cosign": "sigstore/cosign-installer@v3",
        "Sign published image digest with GitHub OIDC": "cosign sign --yes",
        "Sign release checksums with GitHub OIDC": "cosign sign-blob --yes",
        "Validate release assets": "cosign verify-blob",
        "Validate smoke release asset structure": "star-deploy/star",
        "Create GitHub Release and upload assets": "dist/SHA256SUMS.sigstore.json",
    }
    for name, fragment in required_steps.items():
        step = _named_step(core_steps, name)
        text = ""
        if step is not None:
            step_data = step[1]
            text = "\n".join(
                str(step_data.get(key, "")) for key in ("uses", "run", "with")
            )
        if step is None or fragment not in text:
            findings.append(
                Finding(paths["core"], f"missing release evidence control: {name}")
            )

    smoke = payloads["smoke"]
    trigger = _workflow_trigger(smoke)
    push_trigger = trigger.get("push") if trigger is not None else None
    branches = (
        push_trigger.get("branches") if isinstance(push_trigger, Mapping) else None
    )
    if branches != ["test/smoke-release-**"]:
        findings.append(
            Finding(
                paths["smoke"],
                "smoke workflow must trigger only on test/smoke-release-**",
            )
        )
    if trigger is None or len(trigger) != 1:
        findings.append(
            Finding(
                paths["smoke"],
                "smoke workflow must have no manual, tag, or pull-request trigger",
            )
        )

    smoke_jobs = smoke.get("jobs")
    smoke_release = (
        smoke_jobs.get("release") if isinstance(smoke_jobs, Mapping) else None
    )
    smoke_step = _require_local_core(
        findings, paths["smoke"], smoke, "./.github/actions/release-core"
    )
    if smoke_step is not None:
        _require_token_forwarding(findings, paths["smoke"], smoke_step, "smoke release")
        expected = {
            "image-name": "star-release-test",
            "release-draft": "true",
            "require-eligible-tag": "false",
            "verify-release-assets": "false",
        }
        for name, value in expected.items():
            if _with_value(smoke_step, name) != value:
                findings.append(
                    Finding(paths["smoke"], f"smoke {name} must be {value}")
                )
        expected_version = "v${{ github.run_id }}.${{ github.run_attempt }}.0"
        if _with_value(smoke_step, "release-version") != expected_version:
            findings.append(
                Finding(
                    paths["smoke"], "smoke must use a unique synthetic SemVer version"
                )
            )
        if _with_value(smoke_step, "release-tag-name") != f"smoke-{expected_version}":
            findings.append(
                Finding(
                    paths["smoke"],
                    "smoke release tag must not trigger production workflows",
                )
            )

    release_mapping = (
        cast(Mapping[str, Any], smoke_release)
        if isinstance(smoke_release, Mapping)
        else {}
    )
    environment = release_mapping.get("environment")
    if (
        not isinstance(environment, Mapping)
        or environment.get("name") != "release-smoke"
        or environment.get("deployment") is not False
    ):
        findings.append(
            Finding(
                paths["smoke"],
                "smoke publication must use release-smoke without deployments",
            )
        )
    permissions = release_mapping.get("permissions")
    if not isinstance(permissions, Mapping) or any(
        permissions.get(name) != "write"
        for name in ("contents", "packages", "id-token", "attestations")
    ):
        findings.append(
            Finding(
                paths["smoke"],
                "smoke publication must retain least required write permissions",
            )
        )

    docs = payloads["release_docs"]
    docs_step = _require_local_core(
        findings,
        paths["release_docs"],
        docs,
        "./.github/actions/release-docs-core",
        "release-docs",
    )
    if docs_step is not None and (
        _with_value(docs_step, "require-eligible-tag") != "true"
        or _with_value(docs_step, "publish-docs") != "true"
    ):
        findings.append(
            Finding(
                paths["release_docs"],
                "production docs must require an eligible tag and publish docs",
            )
        )

    if docs_step is not None:
        _require_token_forwarding(
            findings, paths["release_docs"], docs_step, "production docs"
        )

    docs_core_steps = _steps(payloads["docs_core"], action=True)
    if docs_core_steps is None:
        findings.append(Finding(paths["docs_core"], "missing composite action steps"))
    else:
        required = (
            "Verify release tag eligibility",
            "Export OpenAPI schema",
            "Validate OpenAPI schema",
            "Build versioned docs site",
            "Publish documentation",
        )
        if any(_named_step(docs_core_steps, name) is None for name in required):
            findings.append(
                Finding(
                    paths["docs_core"],
                    "docs core is missing a release documentation stage",
                )
            )

    smoke_docs_step = _require_local_core(
        findings,
        paths["smoke"],
        smoke,
        "./.github/actions/release-docs-core",
        "docs",
    )
    if (
        smoke_docs_step is not None
        and _with_value(smoke_docs_step, "publish-docs") != "false"
    ):
        findings.append(
            Finding(paths["smoke"], "smoke docs must build without publishing gh-pages")
        )

    if smoke_docs_step is not None:
        _require_token_forwarding(
            findings, paths["smoke"], smoke_docs_step, "smoke docs"
        )

    return findings


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Validate STAR release and smoke publication contracts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root containing release workflows and local actions.",
    )
    return parser


def main() -> int:
    """Run release contract validation and report findings."""
    findings = check_release_contracts(build_parser().parse_args().root)
    if not findings:
        print("Release supply-chain contract passed")
        return 0
    for finding in findings:
        print(f"{finding.source}: {finding.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
