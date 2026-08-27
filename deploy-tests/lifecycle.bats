#!/usr/bin/env bats

load 'helpers/test_fixture.sh'

assert_success() {
    if [[ "${status}" -ne 0 ]]; then
        printf 'Expected success, got %s:\n%s\n' "${status}" "${output}" >&2
        return 1
    fi
}

assert_output_contains() {
    local expected="${1:?expected output is required}"

    if [[ "${output}" != *"${expected}"* ]]; then
        printf 'Expected output to contain %q:\n%s\n' "${expected}" "${output}" >&2
        return 1
    fi
}

setup() {
    deploy_test_initialize
}

teardown() {
    deploy_test_cleanup
}

@test "an extracted deploy bundle completes an isolated lifecycle" {
    deploy_test_build_image
    deploy_test_prepare_package

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" configure --auto
    assert_success

    [[ "$(deploy_test_env_value STAR_IMAGE)" == 'ghcr.io/libertocrat/star:v0.0.0' ]]

    deploy_test_apply_isolated_runtime

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" up --silent
    assert_success

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" status
    assert_success
    assert_output_contains 'Service'
    assert_output_contains 'running'
    assert_output_contains 'Health'

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" logs --tail 5
    assert_success

    run "${DEPLOY_TEST_PACKAGE_DIR}/star" down --docker-cleanup --force --silent
    assert_success

    deploy_test_assert_runtime_removed

    run stat -c '%a' "${DEPLOY_TEST_PACKAGE_DIR}/star-runtime/secrets/star_api_token.txt"
    assert_success
    [[ "${output}" == '600' ]]
}
