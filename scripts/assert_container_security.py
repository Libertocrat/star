#!/usr/bin/env python3
"""Run portable static checks for Dockerfile and Compose service hardening."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


class Finding:
    """A validation finding produced by the static container checks.

    Attributes:
        source: File or logical source that produced the finding.
        message: Human-readable validation message.
    """

    def __init__(self, source: str, message: str) -> None:
        """Initialize the finding.

        Args:
            source: File or logical source that produced the finding.
            message: Human-readable validation message.
        """

        self.source = source
        self.message = message

    def render(self) -> str:
        """Return the finding as a stable command-line string.

        Returns:
            Rendered finding.
        """

        return f"{self.source}: {self.message}"


def _read_text(path: Path) -> str:
    """Read a UTF-8 text file.

    Args:
        path: Path to read.

    Returns:
        File contents.

    Raises:
        SystemExit: If the file cannot be read.
    """

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"{path}: unable to read file: {exc}") from exc


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    """Read a Compose YAML document as a mapping.

    Args:
        path: Compose YAML file to read.

    Returns:
        Parsed Compose document.

    Raises:
        SystemExit: If the document cannot be parsed as a mapping.
    """

    try:
        payload = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise SystemExit(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: Compose document must be a mapping")
    return payload


def check_dockerfile(path: Path) -> list[Finding]:
    """Validate basic Dockerfile security properties.

    Args:
        path: Dockerfile path.

    Returns:
        Validation findings.
    """

    text = _read_text(path)
    findings: list[Finding] = []
    source = str(path)

    user_lines = re.findall(r"(?im)^\s*USER\s+(.+?)\s*$", text)
    if not user_lines:
        findings.append(Finding(source, "missing final non-root USER instruction"))
    else:
        final_user = user_lines[-1].strip().strip('"').strip("'")
        if final_user in {"0", "root", "0:0", "root:root"}:
            findings.append(Finding(source, "final USER must not be root"))

    if re.search(r"(?im)^\s*ADD\s+https?://", text):
        findings.append(Finding(source, "remote ADD URLs are not allowed"))

    copy_lines = re.findall(r"(?im)^\s*COPY\s+(.+)$", text)
    owned_copies = [line for line in copy_lines if "--chown=" in line]
    if copy_lines and not owned_copies:
        findings.append(
            Finding(source, "COPY instructions should set explicit ownership")
        )

    if "HEALTHCHECK" not in text:
        findings.append(Finding(source, "missing HEALTHCHECK instruction"))

    if "chmod -R go-w" not in text:
        findings.append(
            Finding(
                source, "application tree should remove group/world write permissions"
            )
        )

    if re.search(r"(?im)^\s*ENV\s+.*(TOKEN|PASSWORD|SECRET|KEY)\s*=", text):
        findings.append(
            Finding(source, "Dockerfile must not define secret-like ENV values")
        )

    return findings


def check_compose(
    path: Path,
    *,
    require_hardening: bool,
    require_healthcheck: bool,
) -> list[Finding]:
    """Validate basic Compose service security properties.

    Args:
        path: Compose file path.
        require_hardening: Whether to require explicit hardening settings.
        require_healthcheck: Whether to require an explicit Compose healthcheck.

    Returns:
        Validation findings.
    """

    text = _read_text(path)
    findings: list[Finding] = []
    source = str(path)

    forbidden_patterns = {
        r"(?im)^\s*privileged:\s*true\s*$": "privileged containers are not allowed",
        r"(?im)^\s*network_mode:\s*host\s*$": "host networking is not allowed",
        r"(?im)^\s*pid:\s*host\s*$": "host PID namespace is not allowed",
        r"(?im)^\s*ipc:\s*host\s*$": "host IPC namespace is not allowed",
    }
    for pattern, message in forbidden_patterns.items():
        if re.search(pattern, text):
            findings.append(Finding(source, message))

    if re.search(r"(?m)^\s*-\s*[\"']?\$\{[^}:]+:-0\.0\.0\.0\}:", text):
        findings.append(Finding(source, "published ports must not default to 0.0.0.0"))

    if "secrets:" not in text:
        findings.append(Finding(source, "missing Compose secrets declaration"))

    if require_healthcheck and "healthcheck:" not in text:
        findings.append(Finding(source, "missing Compose healthcheck"))

    if "volumes:" not in text:
        findings.append(Finding(source, "missing Compose volumes declaration"))

    if require_hardening:
        compose = _read_yaml_mapping(path)
        services = compose.get("services")
        if not isinstance(services, dict):
            findings.append(Finding(source, "missing Compose services mapping"))
            return findings

        service_name = next(
            (name for name in ("star", "star-core") if name in services), None
        )
        if service_name is None:
            findings.append(Finding(source, "missing STAR application service"))
            return findings

        service = services[service_name]
        if not isinstance(service, dict):
            findings.append(Finding(source, f"{service_name} must be a mapping"))
            return findings

        security_opt = service.get("security_opt")
        cap_drop = service.get("cap_drop")
        requirements = (
            (
                service.get("init") is True,
                f"{service_name} must enable init for child-process reaping",
            ),
            (
                isinstance(security_opt, list)
                and "no-new-privileges:true" in security_opt,
                f"{service_name} must enable no-new-privileges",
            ),
            (
                isinstance(cap_drop, list) and "ALL" in cap_drop,
                f"{service_name} must drop all Linux capabilities",
            ),
            (
                service.get("pids_limit") == 256,
                f"{service_name} must set pids_limit to 256",
            ),
            (
                service.get("mem_limit") == "${STAR_CONTAINER_MEMORY_LIMIT:-1g}",
                f"{service_name} must interpolate "
                "STAR_CONTAINER_MEMORY_LIMIT with fallback 1g",
            ),
            (
                service.get("cpus") == "${STAR_CONTAINER_CPUS_LIMIT:-1.0}",
                f"{service_name} must interpolate "
                "STAR_CONTAINER_CPUS_LIMIT with fallback 1.0",
            ),
            (
                service.get("stop_grace_period") == "30s",
                f"{service_name} must set stop_grace_period to 30s",
            ),
        )
        for satisfied, message in requirements:
            if not satisfied:
                findings.append(Finding(source, message))

    return findings


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        Configured argument parser.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dockerfile",
        type=Path,
        action="append",
        default=[],
        help="Dockerfile to inspect.",
    )
    parser.add_argument(
        "--compose",
        type=Path,
        action="append",
        default=[],
        help="Compose YAML file to inspect.",
    )
    parser.add_argument(
        "--require-compose-hardening",
        action="store_true",
        help="Require STAR application service hardening settings.",
    )
    parser.add_argument(
        "--require-compose-healthcheck",
        action="store_true",
        help="Require an explicit Compose healthcheck.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the validator.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Process exit status.
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.dockerfile and not args.compose:
        parser.error("provide at least one --dockerfile or --compose path")

    findings: list[Finding] = []
    for dockerfile in args.dockerfile:
        findings.extend(check_dockerfile(dockerfile))
    for compose in args.compose:
        findings.extend(
            check_compose(
                compose,
                require_hardening=args.require_compose_hardening,
                require_healthcheck=args.require_compose_healthcheck,
            )
        )

    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print("container security checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
