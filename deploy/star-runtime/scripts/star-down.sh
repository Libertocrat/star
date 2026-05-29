#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
source "${SCRIPT_DIR}/helpers/common.sh"

# CLI mode flags with conservative defaults.
REMOVE_VOLUMES=false
REMOVE_NETWORK=false
REMOVE_ORPHANS=false
FORCE_MODE=false
SILENT_MODE=false

# Runtime status for final output.
NETWORK_STATUS="kept"
TOKEN_RESTORE_STATUS="not checked"

# Print CLI usage and examples for shutdown behavior.
usage() {
    cat <<'EOF'
Usage:
  ./star down [options]

Description:
  Stops the STAR runtime stack. By default, it keeps Docker volumes, Docker network, and images.

Options:
  --volumes         Remove STAR Docker volumes via docker compose down --volumes.
  --network         Remove the configured external STAR Docker network if unused.
  --docker-cleanup  Equivalent to --volumes --network.
  --remove-orphans  Remove containers not defined in the Compose file.
  --force           Do not ask for confirmation before destructive Docker cleanup.
  --dry-run         Print commands without executing them.
  --silent          Suppress normal stdout output. Warnings and errors still go to stderr.
  -h, --help        Show this help.

Examples:
  ./star down
  ./star down --remove-orphans
  ./star down --volumes
  ./star down --network
  ./star down --docker-cleanup
  ./star down --docker-cleanup --force
  ./star down --docker-cleanup --force --silent
  ./star down --dry-run
EOF
}

# Parse CLI flags for shutdown and optional Docker cleanup modes.
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --volumes)
                REMOVE_VOLUMES=true
                shift
                ;;
            --network)
                REMOVE_NETWORK=true
                shift
                ;;
            --docker-cleanup)
                # Docker cleanup mode only affects Docker volumes/network resources.
                REMOVE_VOLUMES=true
                REMOVE_NETWORK=true
                shift
                ;;
            --remove-orphans)
                REMOVE_ORPHANS=true
                shift
                ;;
            --force)
                FORCE_MODE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                export DRY_RUN
                shift
                ;;
            --silent)
                SILENT_MODE=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Unknown argument: $1"
                usage
                exit 1
                ;;
        esac
    done
}

# Return success when destructive Docker cleanup flags are enabled.
has_destructive_cleanup() {
    [[ "${REMOVE_VOLUMES}" == "true" || "${REMOVE_NETWORK}" == "true" ]]
}

# Validate unsupported flag combinations before executing any shutdown logic.
validate_cli_flags() {
    if [[ "${DRY_RUN:-false}" == "true" && "${SILENT_MODE}" == "true" ]]; then
        die "--dry-run and --silent cannot be used together."
    fi

    # Silent destructive cleanup is only allowed in non-interactive safe modes.
    if [[ "${SILENT_MODE}" == "true" && "${FORCE_MODE}" != "true" ]]; then
        if has_destructive_cleanup; then
            die "--silent cannot be used with destructive Docker cleanup unless --force is also provided."
        fi
    fi
}

# Require a runtime env variable to exist and be non-empty.
require_runtime_env_value() {
    local name="${1:?name is required}"
    local value="${!name-}"

    if ! is_non_empty "${value}"; then
        die "Missing required runtime variable: ${name}"
    fi
}

# Validate env values used to target STAR Docker resources safely.
validate_runtime_env() {
    require_runtime_env_value COMPOSE_PROJECT_NAME
    require_runtime_env_value STAR_DATA_VOLUME
    require_runtime_env_value STAR_SHARED_NETWORK
    require_runtime_env_value STAR_ROOT_DIR

    if ! is_safe_docker_name "${COMPOSE_PROJECT_NAME}"; then
        die "COMPOSE_PROJECT_NAME must use letters, numbers, dots, underscores or dashes. Current value: ${COMPOSE_PROJECT_NAME}"
    fi

    if ! is_safe_docker_name "${STAR_DATA_VOLUME}"; then
        die "STAR_DATA_VOLUME must use letters, numbers, dots, underscores or dashes. Current value: ${STAR_DATA_VOLUME}"
    fi

    if ! is_safe_docker_name "${STAR_SHARED_NETWORK}"; then
        die "STAR_SHARED_NETWORK must use letters, numbers, dots, underscores or dashes. Current value: ${STAR_SHARED_NETWORK}"
    fi

    if [[ "${STAR_ROOT_DIR}" != /* ]]; then
        die "STAR_ROOT_DIR must be an absolute container path. Current value: ${STAR_ROOT_DIR}"
    fi
}

# Confirm destructive cleanup intent unless force or dry-run mode is active.
confirm_destructive_cleanup() {
    if ! has_destructive_cleanup; then
        return 0
    fi

    # Destructive cleanup requires confirmation unless --force or --dry-run.
    if [[ "${FORCE_MODE}" == "true" || "${DRY_RUN:-false}" == "true" ]]; then
        return 0
    fi

    if [[ "${REMOVE_VOLUMES}" == "true" && "${REMOVE_NETWORK}" == "true" ]]; then
        if confirm "This will remove STAR Docker volume data and attempt to remove the STAR Docker network if unused. Continue?" "N"; then
            return 0
        fi
    elif [[ "${REMOVE_VOLUMES}" == "true" ]]; then
        if confirm "This will remove STAR Docker volume data. Continue?" "N"; then
            return 0
        fi
    elif [[ "${REMOVE_NETWORK}" == "true" ]]; then
        if confirm "This will attempt to remove the STAR Docker network if unused. Continue?" "N"; then
            return 0
        fi
    fi

    say_info "${SILENT_MODE}" "Shutdown cancelled. No Docker resources were changed."
    exit 0
}

# Build and run docker compose down arguments from selected shutdown flags.
run_compose_down() {
    local -a down_args
    down_args=(down)

    if [[ "${REMOVE_VOLUMES}" == "true" ]]; then
        down_args+=(--volumes)
    fi

    if [[ "${REMOVE_ORPHANS}" == "true" ]]; then
        down_args+=(--remove-orphans)
    fi

    say_step "${SILENT_MODE}" "Stop STAR runtime stack"
    compose_quiet_if_silent "${down_args[@]}"
}

# Remove the external STAR Docker network when requested and safe to do so.
remove_network_if_requested() {
    if [[ "${REMOVE_NETWORK}" != "true" ]]; then
        NETWORK_STATUS="kept"
        return 0
    fi

    if ! docker network inspect "${STAR_SHARED_NETWORK}" >/dev/null 2>&1; then
        NETWORK_STATUS="already absent"
        say_info "${SILENT_MODE}" "Docker network already absent: ${STAR_SHARED_NETWORK}"
        return 0
    fi

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        NETWORK_STATUS="would attempt removal"
        say_info "${SILENT_MODE}" "Would remove Docker network if unused: ${STAR_SHARED_NETWORK}"
        run docker network rm "${STAR_SHARED_NETWORK}"
        return 0
    fi

    NETWORK_STATUS="removal attempted"
    say_info "${SILENT_MODE}" "Removing Docker network if unused: ${STAR_SHARED_NETWORK}"

    if docker network rm "${STAR_SHARED_NETWORK}" >/dev/null 2>&1; then
        NETWORK_STATUS="removed"
        say_success "${SILENT_MODE}" "Docker network removed: ${STAR_SHARED_NETWORK}"
    else
        NETWORK_STATUS="kept or still in use"
        warn "Could not remove Docker network '${STAR_SHARED_NETWORK}'. It may still be used by another container."
    fi

    return 0
}

# Restore token file permissions back to secure host defaults after shutdown.
restore_token_permissions() {
    local token_path_display
    token_path_display="$(path_relative_to_pwd "${STAR_SECRET_FILE}")"

    # Missing token is unusual but should not block runtime shutdown.
    if [[ ! -f "${STAR_SECRET_FILE}" ]]; then
        TOKEN_RESTORE_STATUS="skipped; token file missing"
        warn "STAR API token file not found; skipping permission restore."
        return 0
    fi

    # Restore restrictive host-local mode after startup-time relaxations.
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        TOKEN_RESTORE_STATUS="would restore to 600"
        run chmod 600 "${token_path_display}"
        return 0
    fi

    if chmod 600 "${STAR_SECRET_FILE}" 2>/dev/null; then
        TOKEN_RESTORE_STATUS="restored to 600"
        say_success "${SILENT_MODE}" "STAR API token permissions restored to 600."
    else
        TOKEN_RESTORE_STATUS="restore failed; see warning above"
        warn "Failed to restore STAR API token permissions to 600."
        warn "Run: chmod 600 ${token_path_display}"
    fi

    return 0
}

# Print final shutdown summary for runtime, Docker resources, and token state.
print_final_output() {
    local containers_state="stopped/removed"
    local volumes_state="kept"
    local orphan_state="not requested"
    local token_path_display
    token_path_display="$(path_relative_to_pwd "${STAR_SECRET_FILE}")"

    [[ "${SILENT_MODE}" == "true" ]] && return 0

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        containers_state="would stop/remove"
        if [[ "${REMOVE_VOLUMES}" == "true" ]]; then
            volumes_state="would remove"
        else
            volumes_state="would keep"
        fi

        if [[ "${REMOVE_ORPHANS}" == "true" ]]; then
            orphan_state="would request removal"
        fi
    else
        if [[ "${REMOVE_VOLUMES}" == "true" ]]; then
            volumes_state="removed"
        fi

        if [[ "${REMOVE_ORPHANS}" == "true" ]]; then
            orphan_state="removal requested"
        fi
    fi

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        section "STAR Runtime Down Dry Run"
        success "Dry-run completed. No Docker resources or files were changed."
    else
        section "STAR Runtime Stopped"
        success "STAR runtime stack stopped."
    fi

    printf '\nDocker resources:\n'
    printf '  %-27s %s\n' "Compose project" "${COMPOSE_PROJECT_NAME}"
    printf '  %-27s %s\n' "Containers" "${containers_state}"
    printf '  %-27s %s\n' "Volumes" "${volumes_state}"
    printf '  %-27s %s\n' "Network" "${NETWORK_STATUS}"
    printf '  %-27s %s\n' "Orphan containers" "${orphan_state}"

    printf '\nFiles and directories:\n'
    printf '  %-27s %s\n' ".env file" "$(path_relative_to_pwd "${STAR_ENV_FILE}") (kept)"
    printf '  %-27s %s\n' "STAR API token file" "${token_path_display}"
    printf '  %-27s %s\n' "Token permissions" "${TOKEN_RESTORE_STATUS}"
    printf '  %-27s %s\n' "User specs directory" "$(path_relative_to_pwd "${STAR_USER_SPECS_DIR}") (kept)"
}

# Orchestrate shutdown validation, compose down, cleanup, and final summary.
main() {
    parse_args "$@"
    validate_cli_flags

    say_section "${SILENT_MODE}" "STAR Runtime Shutdown"

    require_docker
    require_docker_compose

    ensure_file_exists "${STAR_COMPOSE_FILE}" "docker-compose.yml" || exit 1

    # Missing .env is fatal because safe resource names cannot be inferred reliably.
    ensure_file_exists "${STAR_ENV_FILE}" ".env file" || {
        error "STAR runtime .env is required for safe shutdown."
        exit 1
    }

    load_env required
    validate_runtime_env
    confirm_destructive_cleanup

    # Conservative behavior: stop stack and optionally clean Docker resources only.
    # Never delete local runtime files, secrets, user specs, examples, or demo assets.
    run_compose_down
    remove_network_if_requested
    restore_token_permissions
    print_final_output
}

main "$@"
