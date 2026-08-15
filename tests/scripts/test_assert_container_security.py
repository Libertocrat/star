"""Test the static Docker and Compose security assertions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import assert_container_security

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


# ============================================================================
# Compose Hardening
# ============================================================================


def _valid_compose_payload() -> dict[str, Any]:
    """Return the smallest Compose payload that satisfies STAR hardening."""

    return {
        "services": {
            "star": {
                "image": "example.invalid/star:latest",
                "init": True,
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "pids_limit": 256,
                "mem_limit": "1g",
                "cpus": "1.0",
                "stop_grace_period": "30s",
            }
        },
        "secrets": {"star_api_token": {"file": "./token.txt"}},
        "volumes": {"star_data": {}},
    }


def _write_compose(path: Path, payload: dict[str, Any]) -> None:
    """Write a deterministic Compose fixture to a temporary path."""

    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize(
    "compose_path",
    [
        pytest.param(REPOSITORY_ROOT / "docker-compose.yml", id="source_tree"),
        pytest.param(
            REPOSITORY_ROOT / "deploy/star-runtime/docker-compose.yml",
            id="packaged_runtime",
        ),
    ],
)
def test_committed_compose_manifests_satisfy_application_hardening(
    compose_path: Path,
):
    """
    GIVEN a committed STAR Compose runtime
    WHEN static hardening validation is required
    THEN its application service satisfies every fixed first-pass control
    """
    findings = assert_container_security.check_compose(
        compose_path,
        require_hardening=True,
        require_healthcheck=False,
    )

    assert findings == []


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        pytest.param(
            "init",
            False,
            "star must enable init for child-process reaping",
            id="init",
        ),
        pytest.param(
            "security_opt",
            [],
            "star must enable no-new-privileges",
            id="no_new_privileges",
        ),
        pytest.param(
            "cap_drop",
            [],
            "star must drop all Linux capabilities",
            id="cap_drop",
        ),
        pytest.param(
            "pids_limit",
            255,
            "star must set pids_limit to 256",
            id="pids_limit",
        ),
        pytest.param(
            "mem_limit",
            "512m",
            "star must set mem_limit to 1g",
            id="mem_limit",
        ),
        pytest.param(
            "cpus",
            "2.0",
            "star must set cpus to 1.0",
            id="cpus",
        ),
        pytest.param(
            "stop_grace_period",
            "10s",
            "star must set stop_grace_period to 30s",
            id="stop_grace_period",
        ),
    ],
)
def test_hardening_validator_rejects_missing_or_incorrect_app_service_control(
    tmp_path: Path,
    field: str,
    value: object,
    expected_message: str,
):
    """
    GIVEN a Compose service with one missing or incorrect hardening control
    WHEN static hardening validation runs
    THEN it reports the control required on the application service
    """
    payload = _valid_compose_payload()
    service = payload["services"]["star"]
    assert isinstance(service, dict)
    service[field] = value
    compose_path = tmp_path / "docker-compose.yml"
    _write_compose(compose_path, payload)

    findings = assert_container_security.check_compose(
        compose_path,
        require_hardening=True,
        require_healthcheck=False,
    )

    assert [finding.message for finding in findings] == [expected_message]


def test_hardening_validator_does_not_accept_controls_on_init_service(
    tmp_path: Path,
):
    """
    GIVEN hardening controls exist only on the root init helper
    WHEN static hardening validation runs
    THEN it rejects the unprotected STAR application service
    """
    payload = _valid_compose_payload()
    hardened_service = payload["services"].pop("star")
    payload["services"] = {
        "star-init": hardened_service,
        "star": {"image": "example.invalid/star:latest"},
    }
    compose_path = tmp_path / "docker-compose.yml"
    _write_compose(compose_path, payload)

    findings = assert_container_security.check_compose(
        compose_path,
        require_hardening=True,
        require_healthcheck=False,
    )

    messages = [finding.message for finding in findings]
    assert "star must enable init for child-process reaping" in messages
    assert "star must drop all Linux capabilities" in messages
    assert "star must set mem_limit to 1g" in messages
