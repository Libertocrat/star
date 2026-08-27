#!/usr/bin/env bats

load 'helpers/test_fixture.sh'
load 'helpers/docker_stub.sh'

assert_success() {
    if [[ "${status}" -ne 0 ]]; then
        printf 'Expected success, got %s:\n%s\n' "${status}" "${output}" >&2
        return 1
    fi
}

assert_failure() {
    if [[ "${status}" -eq 0 ]]; then
        printf 'Expected failure, got success:\n%s\n' "${output}" >&2
        return 1
    fi
}

setup() {
    deploy_test_initialize
    deploy_test_prepare_package
}

teardown() {
    deploy_test_cleanup
}

@test "the extracted CLI documents and rejects invalid modes before Docker effects" {
    run "${DEPLOY_TEST_PACKAGE_DIR}/star" --help
    assert_success

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" up --unknown
    assert_failure

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" down --dry-run --silent
    assert_failure
}

@test "dry-run and pull modes preserve Docker effect boundaries" {
    run "${DEPLOY_TEST_PACKAGE_DIR}/star" configure --auto
    assert_success

    deploy_test_apply_isolated_runtime

    deploy_test_create_docker_stub

    run env "PATH=${DEPLOY_TEST_DOCKER_STUB_BIN}:${PATH}" "DEPLOY_TEST_DOCKER_LOG=${DEPLOY_TEST_DOCKER_LOG}" "${DEPLOY_TEST_PACKAGE_DIR}/star" up --dry-run --no-wait
    assert_success

    run grep -F 'network create' "${DEPLOY_TEST_DOCKER_LOG}"
    assert_failure

    run grep -F 'compose up' "${DEPLOY_TEST_DOCKER_LOG}"
    assert_failure

    : > "${DEPLOY_TEST_DOCKER_LOG}"
    run env "PATH=${DEPLOY_TEST_DOCKER_STUB_BIN}:${PATH}" "DEPLOY_TEST_DOCKER_LOG=${DEPLOY_TEST_DOCKER_LOG}" "${DEPLOY_TEST_PACKAGE_DIR}/star" up --pull --no-wait --silent
    assert_success

    run grep -F 'pull star-core' "${DEPLOY_TEST_DOCKER_LOG}"
    assert_success
}
