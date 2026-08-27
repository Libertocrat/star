#!/usr/bin/env bash
set -Eeuo pipefail

# Shared fixture state for Bats deploy lifecycle tests.
DEPLOY_TEST_REPO_ROOT=""
DEPLOY_TEST_TMP_DIR=""
DEPLOY_TEST_PACKAGE_DIR=""
DEPLOY_TEST_BUNDLE_PATH=""
DEPLOY_TEST_COMPOSE_PROJECT=""
DEPLOY_TEST_NETWORK=""
DEPLOY_TEST_VOLUME=""
DEPLOY_TEST_IMAGE=""
DEPLOY_TEST_PORT=""
DEPLOY_TEST_IMAGE_BUILT=false

# Fail if this Bats helper is executed instead of sourced.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'This file is a Bats test helper and must be sourced.' >&2
    exit 1
fi

# Initialize one isolated deploy test fixture.
deploy_test_initialize() {
    local raw_token

    # shellcheck disable=SC2154
    # BATS_TEST_DIRNAME is provided by Bats for each test file.
    DEPLOY_TEST_REPO_ROOT="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    DEPLOY_TEST_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/star-deploy-test.XXXXXX")"

    raw_token="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$(date +%s)-${BASHPID}"
    raw_token="$(printf '%s' "${raw_token}" | tr -cd '[:alnum:].-')"

    DEPLOY_TEST_COMPOSE_PROJECT="star-deploy-test-${raw_token}"
    DEPLOY_TEST_NETWORK="${DEPLOY_TEST_COMPOSE_PROJECT}-network"
    DEPLOY_TEST_VOLUME="${DEPLOY_TEST_COMPOSE_PROJECT}-data"
    DEPLOY_TEST_IMAGE="star:deploy-test-${raw_token}"
    DEPLOY_TEST_PORT="$(deploy_test_select_port)"
}

# Select a currently unused IPv4 loopback port for a serial test run.
deploy_test_select_port() {
    python3 - << 'PYTHON'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PYTHON
}

# Build the current source tree under a test-owned image tag.
deploy_test_build_image() {
    docker image inspect "${DEPLOY_TEST_IMAGE}" > /dev/null 2>&1 && {
        printf 'Refusing to reuse existing test image: %s\n' "${DEPLOY_TEST_IMAGE}" >&2
        return 1
    }

    docker build -t "${DEPLOY_TEST_IMAGE}" "${DEPLOY_TEST_REPO_ROOT}"
    DEPLOY_TEST_IMAGE_BUILT=true
}

# Assemble and extract a release-shaped bundle from tracked deploy files only.
deploy_test_prepare_package() {
    local source_path
    local target_path
    local stage_dir="${DEPLOY_TEST_TMP_DIR}/stage"
    local extract_dir="${DEPLOY_TEST_TMP_DIR}/extract"

    mkdir -p "${stage_dir}/star-deploy" "${extract_dir}"

    while IFS= read -r -d '' source_path; do
        target_path="${stage_dir}/star-deploy/${source_path#deploy/}"
        mkdir -p "$(dirname -- "${target_path}")"
        cp -a -- "${DEPLOY_TEST_REPO_ROOT}/${source_path}" "${target_path}"
    done < <(git -C "${DEPLOY_TEST_REPO_ROOT}" ls-files -z -- deploy)

    printf '%s\n' 'v0.0.0' > "${stage_dir}/star-deploy/star-runtime/.star-release-version"

    DEPLOY_TEST_BUNDLE_PATH="${DEPLOY_TEST_TMP_DIR}/star-deploy-v0.0.0.tar.gz"
    tar -C "${stage_dir}" -czf "${DEPLOY_TEST_BUNDLE_PATH}" star-deploy
    tar -xzf "${DEPLOY_TEST_BUNDLE_PATH}" -C "${extract_dir}"
    DEPLOY_TEST_PACKAGE_DIR="${extract_dir}/star-deploy"
}

# Return one generated runtime value from the extracted package environment.
deploy_test_env_value() {
    local name="${1:?environment variable name is required}"
    local env_file="${DEPLOY_TEST_PACKAGE_DIR}/star-runtime/.env"

    awk -F= -v name="${name}" '$1 == name { print substr($0, length(name) + 2); exit }' "${env_file}"
}

# Replace one exact environment assignment without interpreting its value.
deploy_test_set_env_value() {
    local name="${1:?environment variable name is required}"
    local value="${2:?environment variable value is required}"
    local env_file="${DEPLOY_TEST_PACKAGE_DIR}/star-runtime/.env"
    local temporary_file
    local line
    local replaced=false

    temporary_file="$(mktemp "${env_file}.XXXXXX")"
    while IFS= read -r line || [[ -n "${line}" ]]; do
        if [[ "${line}" == "${name}="* ]]; then
            printf '%s=%s\n' "${name}" "${value}" >> "${temporary_file}"
            replaced=true
        else
            printf '%s\n' "${line}" >> "${temporary_file}"
        fi
    done < "${env_file}"

    if [[ "${replaced}" != "true" ]]; then
        rm -f -- "${temporary_file}"
        printf 'Missing environment variable in test package: %s\n' "${name}" >&2
        return 1
    fi

    mv -- "${temporary_file}" "${env_file}"
}

# Configure the extracted package to use only fixture-owned Docker resources.
deploy_test_apply_isolated_runtime() {
    deploy_test_set_env_value COMPOSE_PROJECT_NAME "${DEPLOY_TEST_COMPOSE_PROJECT}"
    deploy_test_set_env_value STAR_DATA_VOLUME "${DEPLOY_TEST_VOLUME}"
    deploy_test_set_env_value STAR_SHARED_NETWORK "${DEPLOY_TEST_NETWORK}"
    deploy_test_set_env_value STAR_IMAGE "${DEPLOY_TEST_IMAGE}"
    deploy_test_set_env_value STAR_PULL_POLICY never
    deploy_test_set_env_value STAR_HOST_BIND_ADDRESS 127.0.0.1
    deploy_test_set_env_value STAR_HOST_PORT "${DEPLOY_TEST_PORT}"
}

# Fail unless the test-owned Docker resources have all been removed.
deploy_test_assert_runtime_removed() {
    if docker ps -aq --filter "label=com.docker.compose.project=${DEPLOY_TEST_COMPOSE_PROJECT}" | grep -q .; then
        printf 'Test-owned Compose containers still exist for %s\n' "${DEPLOY_TEST_COMPOSE_PROJECT}" >&2
        return 1
    fi

    if docker volume inspect "${DEPLOY_TEST_VOLUME}" > /dev/null 2>&1; then
        printf 'Test-owned Docker volume still exists: %s\n' "${DEPLOY_TEST_VOLUME}" >&2
        return 1
    fi

    if docker network inspect "${DEPLOY_TEST_NETWORK}" > /dev/null 2>&1; then
        printf 'Test-owned Docker network still exists: %s\n' "${DEPLOY_TEST_NETWORK}" >&2
        return 1
    fi
}

# Remove only resources allocated by this fixture, even after a test failure.
deploy_test_cleanup() {
    local env_file="${DEPLOY_TEST_PACKAGE_DIR:-}/star-runtime/.env"
    local compose_file="${DEPLOY_TEST_PACKAGE_DIR:-}/star-runtime/docker-compose.yml"

    if [[ "${DEPLOY_TEST_COMPOSE_PROJECT:-}" == star-deploy-test-* ]] && [[ -f "${env_file}" ]]; then
        "${DEPLOY_TEST_PACKAGE_DIR}/star" down --docker-cleanup --force --silent > /dev/null 2>&1 || true
        docker compose --env-file "${env_file}" -f "${compose_file}" down --volumes --remove-orphans > /dev/null 2>&1 || true
        docker network rm "${DEPLOY_TEST_NETWORK}" > /dev/null 2>&1 || true
    fi

    if [[ "${DEPLOY_TEST_IMAGE_BUILT}" == "true" ]]; then
        docker image rm "${DEPLOY_TEST_IMAGE}" > /dev/null 2>&1 || true
    fi

    if [[ "${DEPLOY_TEST_TMP_DIR:-}" == "${TMPDIR:-/tmp}/star-deploy-test."* ]]; then
        rm -rf -- "${DEPLOY_TEST_TMP_DIR}"
    fi
}
