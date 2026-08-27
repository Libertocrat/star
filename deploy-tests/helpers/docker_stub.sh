#!/usr/bin/env bash
set -Eeuo pipefail

# Fail if this Bats helper is executed instead of sourced.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'This file is a Bats test helper and must be sourced.' >&2
    exit 1
fi

# Create a Docker command stub that records compose command shapes without effects.
deploy_test_create_docker_stub() {
    DEPLOY_TEST_DOCKER_STUB_BIN="${DEPLOY_TEST_TMP_DIR}/docker-stub-bin"
    DEPLOY_TEST_DOCKER_LOG="${DEPLOY_TEST_TMP_DIR}/docker-stub.log"
    mkdir -p "${DEPLOY_TEST_DOCKER_STUB_BIN}"
    : > "${DEPLOY_TEST_DOCKER_LOG}"

    cat > "${DEPLOY_TEST_DOCKER_STUB_BIN}/docker" << 'SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail

printf '%s\n' "$*" >> "${DEPLOY_TEST_DOCKER_LOG}"

case "${1:-}" in
    info)
        exit 0
        ;;
    network)
        case "${2:-}" in
            inspect)
                exit 1
                ;;
            create | rm)
                exit 0
                ;;
        esac
        ;;
    compose)
        shift
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --env-file | -f)
                    shift 2
                    ;;
                *)
                    break
                    ;;
            esac
        done

        case "${1:-}" in
            version)
                printf '%s\n' 'Docker Compose version test-stub'
                ;;
        esac
        exit 0
        ;;
esac

exit 0
SCRIPT
    chmod 755 "${DEPLOY_TEST_DOCKER_STUB_BIN}/docker"
}
