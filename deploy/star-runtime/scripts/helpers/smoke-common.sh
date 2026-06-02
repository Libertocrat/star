#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
source "${SCRIPT_DIR}/common.sh"

FAILURES=0

record_success() {
    success "$*"
}

record_failure() {
    error "$*"
    FAILURES=$((FAILURES + 1))
}

record_dependency_ok() {
    success "$*"
}

record_dependency_warn() {
    warn "$*"
}

record_dependency_error() {
    error "$*"
    FAILURES=$((FAILURES + 1))
}

assert_true() {
    local message="$1"
    shift

    if "$@"; then
        record_success "${message}"
    else
        record_failure "${message}"
    fi
}

assert_false() {
    local message="$1"
    shift

    if "$@"; then
        record_failure "${message}"
    else
        record_success "${message}"
    fi
}

assert_eq() {
    local message="$1"
    local expected="$2"
    local actual="$3"

    if [[ "${actual}" == "${expected}" ]]; then
        record_success "${message}"
    else
        record_failure "${message} (expected='${expected}', actual='${actual}')"
    fi
}

section "STAR common.sh smoke runner"
step "Print resolved runtime paths"

info "STAR_COMMON_DIR=${STAR_COMMON_DIR}"
info "STAR_SCRIPTS_DIR=${STAR_SCRIPTS_DIR}"
info "STAR_RUNTIME_DIR=${STAR_RUNTIME_DIR}"
info "STAR_ENV_FILE=${STAR_ENV_FILE}"
info "STAR_ENV_EXAMPLE_FILE=${STAR_ENV_EXAMPLE_FILE}"
info "STAR_COMPOSE_FILE=${STAR_COMPOSE_FILE}"
info "STAR_SECRET_DIR=${STAR_SECRET_DIR}"
info "STAR_SECRET_FILE=${STAR_SECRET_FILE}"
info "STAR_USER_SPECS_DIR=${STAR_USER_SPECS_DIR}"
info "STAR_USER_SPEC_EXAMPLES_DIR=${STAR_USER_SPEC_EXAMPLES_DIR}"
info "STAR_DEMO_ASSETS_DIR=${STAR_DEMO_ASSETS_DIR}"

step "Demonstrate output helpers"
info "Info helper sample"
success "Success helper sample"
warn "Warn helper sample"
error "Error helper sample (non-fatal)"

step "Validate critical path values"
assert_true "STAR_RUNTIME_DIR should be non-empty" is_non_empty "${STAR_RUNTIME_DIR}"
assert_true "STAR_COMPOSE_FILE should be non-empty" is_non_empty "${STAR_COMPOSE_FILE}"

step "Validate selected validators"
assert_true "is_non_empty should accept non-empty text" is_non_empty "hello"
assert_false "is_non_empty should reject only-whitespace" is_non_empty "   "
assert_true "is_int should accept 42" is_int "42"
assert_false "is_int should reject 4.2" is_int "4.2"
assert_true "is_bool should accept yes" is_bool "yes"
assert_false "is_bool should reject maybe" is_bool "maybe"
assert_true "is_port should accept 8080" is_port "8080"
assert_false "is_port should reject 70000" is_port "70000"
assert_true "is_bind_address should accept localhost" is_bind_address "localhost"
assert_true "is_bind_address should accept 192.168.1.10" is_bind_address "192.168.1.10"
assert_false "is_bind_address should reject 256.1.1.1" is_bind_address "256.1.1.1"
assert_true "is_safe_docker_name should accept star-network" is_safe_docker_name "star-network"
assert_false "is_safe_docker_name should reject names with spaces" is_safe_docker_name "bad name"

step "Validate URL helpers with temporary STAR_HOST values"
OLD_STAR_HOST_BIND_ADDRESS="${STAR_HOST_BIND_ADDRESS-}"
OLD_STAR_HOST_PORT="${STAR_HOST_PORT-}"
OLD_BIND_SET="${STAR_HOST_BIND_ADDRESS+x}"
OLD_PORT_SET="${STAR_HOST_PORT+x}"

export STAR_HOST_BIND_ADDRESS="127.0.0.1"
export STAR_HOST_PORT="8080"

assert_eq "public_host should map 127.0.0.1 to localhost" "localhost" "$(public_host)"
assert_eq "base_url should match expected URL" "http://localhost:8080" "$(base_url)"
assert_eq "health_url should match expected URL" "http://localhost:8080/health" "$(health_url)"
assert_eq "docs_url should match expected URL" "http://localhost:8080/docs" "$(docs_url)"
assert_eq "openapi_url should match expected URL" "http://localhost:8080/openapi.json" "$(openapi_url)"

if [[ -n "${OLD_BIND_SET}" ]]; then
    export STAR_HOST_BIND_ADDRESS="${OLD_STAR_HOST_BIND_ADDRESS}"
else
    unset STAR_HOST_BIND_ADDRESS
fi

if [[ -n "${OLD_PORT_SET}" ]]; then
    export STAR_HOST_PORT="${OLD_STAR_HOST_PORT}"
else
    unset STAR_HOST_PORT
fi

step "Generate temporary token and validate strength"
if token="$(generate_token)"; then
    token_len="${#token}"
    info "Generated temporary token=${token}"
    info "Generated token length=${token_len}"
    if ((token_len >= 32)); then
        record_success "Generated token length check"
    else
        record_failure "Generated token length check"
    fi

    if validate_token_strength "${token}"; then
        record_success "validate_token_strength should accept generated token"
    else
        record_failure "validate_token_strength should accept generated token"
    fi
else
    record_failure "generate_token should succeed"
fi

step "Run find_free_port in localhost range"
if free_port="$(find_free_port 8080 8080 8099)"; then
    info "find_free_port result=${free_port}"
else
    warn "No free port found in range 8080-8099"
fi

step "Validate dependency helper status"
if command_exists bash; then
    record_dependency_ok "bash is installed"
else
    record_dependency_error "bash is missing"
fi

if command_exists docker; then
    record_dependency_ok "Docker CLI is installed"
else
    record_dependency_error "Docker CLI is missing"
fi

if command_exists curl; then
    record_dependency_ok "curl is installed"
else
    record_dependency_error "curl is missing"
fi

if command_exists docker; then
    if docker info > /dev/null 2>&1; then
        record_dependency_ok "Docker daemon is reachable"
    else
        record_dependency_warn "Docker daemon is not reachable"
    fi

    if docker compose version > /dev/null 2>&1; then
        record_dependency_ok "Docker Compose v2 is available"
    else
        record_dependency_error "Docker Compose v2 is missing"
    fi
else
    record_dependency_warn "Skipping Docker daemon and Compose checks because Docker CLI is missing"
fi

if command_exists openssl; then
    record_dependency_ok "openssl is installed"
else
    record_dependency_warn "openssl is missing; token generation will use fallback if available"
fi

if command_exists od; then
    record_dependency_ok "od is installed"
else
    record_dependency_warn "od is missing"
fi

if command_exists hexdump; then
    record_dependency_ok "hexdump is installed"
else
    record_dependency_warn "hexdump is missing"
fi

if command_exists od || command_exists hexdump; then
    record_dependency_ok "At least one hex encoder is available (od/hexdump)"
else
    record_dependency_error "No hex encoder found; token fallback cannot work without od or hexdump"
fi

if command_exists ss; then
    record_dependency_ok "ss is installed"
else
    record_dependency_warn "ss is missing; port checks may be less accurate"
fi

if (require_command bash "bash") > /dev/null 2>&1; then
    record_success "require_command should accept bash"
else
    record_failure "require_command should accept bash"
fi

if (require_command definitely-not-a-real-command "fake command") > /dev/null 2>&1; then
    record_failure "require_command should reject missing command"
else
    record_success "require_command should reject missing command"
fi

step "Compose helper dry-run demonstration"
# shellcheck disable=SC2034
# DRY_RUN is consumed by run()/compose() helpers in sourced common.sh.
DRY_RUN=true
compose ps
unset DRY_RUN

if ((FAILURES > 0)); then
    error "Smoke checks failed: ${FAILURES} critical check(s)."
    exit 1
fi

success "All critical common.sh smoke checks passed."
