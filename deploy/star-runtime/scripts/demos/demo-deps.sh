#!/usr/bin/env bash
set -Eeuo pipefail

# Resolve demo-local paths for helper sourcing.
DEMO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${STAR_SCRIPTS_DIR:-}" ]]; then
    STAR_SCRIPTS_DIR="$(cd -- "${DEMO_DIR}/.." && pwd)"
fi

# Source shared runtime helpers only once.
if [[ -z "${STAR_COMMON_DIR:-}" ]]; then
    # shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
    source "${STAR_SCRIPTS_DIR}/helpers/common.sh"
fi

# Cache missing demo dependencies after PATH checks.
DEMO_MISSING_DEPS=()

# Collect missing runtime commands required by star-demo.sh.
collect_missing_demo_dependencies() {
    local -a missing=()

    if ! command_exists curl; then
        missing+=(curl)
    fi

    if ! command_exists jq; then
        missing+=(jq)
    fi

    DEMO_MISSING_DEPS=("${missing[@]}")
    (( ${#DEMO_MISSING_DEPS[@]} == 0 ))
}

# Return success only when apt and sudo are available.
can_install_with_apt() {
    command_exists apt && command_exists sudo
}

# Install only requested dependencies with apt on Debian-based hosts.
install_demo_dependencies_with_apt() {
    local -a packages=("$@")

    if (( ${#packages[@]} == 0 )); then
        return 0
    fi

    info "Installing missing demo dependencies with apt: ${packages[*]}"

    if ! sudo apt update; then
        error "Failed to run 'sudo apt update'."
        return 1
    fi

    if ! sudo apt install -y "${packages[@]}"; then
        error "Failed to install required dependencies with apt."
        return 1
    fi

    success "Demo dependencies installed successfully."
    return 0
}

# Print concise manual installation guidance when auto-install is unavailable.
print_manual_dependency_instructions() {
    local -a packages=("$@")

    if (( ${#packages[@]} == 0 )); then
        packages=(curl jq)
    fi

    error "Missing required demo dependencies: ${packages[*]}"
    printf 'Install them manually on Debian/Ubuntu:\n' >&2
    printf '  sudo apt update\n' >&2
    printf '  sudo apt install -y %s\n' "${packages[*]}" >&2
}

# Ensure curl and jq exist, with optional apt install in auto mode.
ensure_demo_dependencies() {
    local auto_mode="${1:-false}"

    if collect_missing_demo_dependencies; then
        success "Demo dependencies are ready (curl, jq)."
        return 0
    fi

    warn "Missing demo dependencies: ${DEMO_MISSING_DEPS[*]}"

    if [[ "${auto_mode}" == "true" ]]; then
        if ! can_install_with_apt; then
            print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
            return 1
        fi

        if ! install_demo_dependencies_with_apt "${DEMO_MISSING_DEPS[@]}"; then
            print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
            return 1
        fi
    else
        if ! can_install_with_apt; then
            print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
            return 1
        fi

        if ! confirm "Install missing demo dependencies now with sudo apt install?" "Y"; then
            print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
            return 1
        fi

        if ! install_demo_dependencies_with_apt "${DEMO_MISSING_DEPS[@]}"; then
            print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
            return 1
        fi
    fi

    if ! collect_missing_demo_dependencies; then
        print_manual_dependency_instructions "${DEMO_MISSING_DEPS[@]}"
        return 1
    fi

    success "Demo dependencies verified after installation."
    return 0
}
