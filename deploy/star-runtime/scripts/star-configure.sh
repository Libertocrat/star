#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
source "${SCRIPT_DIR}/helpers/common.sh"

# Resolve runtime-local paths explicitly from the current script location.
RUNTIME_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${RUNTIME_DIR}/.env"
SECRET_DIR="${RUNTIME_DIR}/secrets"
SECRET_FILE="${SECRET_DIR}/star_api_token.txt"
USER_SPECS_DIR="${RUNTIME_DIR}/user-specs"

# Default values for generated runtime configuration.
DEFAULT_STAR_VERSION="latest"
DEFAULT_COMPOSE_PROJECT_NAME="star"
DEFAULT_STAR_SHARED_NETWORK="star-network"
DEFAULT_STAR_HOST_PORT="8080"
DEFAULT_STAR_HOST_BIND_ADDRESS="127.0.0.1"
DEFAULT_STAR_PORT="8080"
DEFAULT_STAR_CONTAINER_UID="1001"
DEFAULT_STAR_CONTAINER_GID="1001"
DEFAULT_STAR_PULL_POLICY="missing"
DEFAULT_STAR_ROOT_DIR="/var/lib/star"
DEFAULT_STAR_MAX_FILE_BYTES="104857600"
DEFAULT_STAR_MAX_YML_BYTES="102400"
DEFAULT_STAR_TIMEOUT_MS="5000"
DEFAULT_STAR_MAX_STDOUT_BYTES="65536"
DEFAULT_STAR_MAX_STDERR_BYTES="65536"
DEFAULT_STAR_RATE_LIMIT_RPS="10"
DEFAULT_STAR_ENABLE_SECURITY_HEADERS="true"
DEFAULT_STAR_BLOCKED_BINARIES_EXTRA=""
DEFAULT_STAR_ENABLE_DOCS="true"

# CLI mode flags.
AUTO_MODE=false
FORCE_MODE=false
PRODUCTION_MODE=false
SHOW_TOKEN_FLAG=false

# Selected values are kept in internal *_VALUE variables to avoid collisions.
STAR_VERSION_VALUE="${DEFAULT_STAR_VERSION}"
COMPOSE_PROJECT_NAME_VALUE="${DEFAULT_COMPOSE_PROJECT_NAME}"
STAR_SHARED_NETWORK_VALUE="${DEFAULT_STAR_SHARED_NETWORK}"
STAR_HOST_BIND_ADDRESS_VALUE="${DEFAULT_STAR_HOST_BIND_ADDRESS}"
STAR_HOST_PORT_VALUE="${DEFAULT_STAR_HOST_PORT}"
STAR_PORT_VALUE="${DEFAULT_STAR_PORT}"
STAR_DATA_VOLUME_VALUE=""
STAR_ENABLE_DOCS_VALUE="${DEFAULT_STAR_ENABLE_DOCS}"
STAR_IMAGE_VALUE=""

# .env reuse mode keeps existing runtime values and skips .env rewrite.
REUSE_EXISTING_ENV=false

# Token state and output state.
TOKEN_VALUE=""
TOKEN_WAS_GENERATED=false
TOKEN_WAS_REGENERATED=false
SHOW_TOKEN_OUTPUT=false

# Print CLI usage and examples.
usage() {
    cat << 'EOF'
Usage:
  ./star configure [options]

Description:
  Generates STAR runtime configuration files and local secrets.
  This does not start Docker containers.

Options:
  --auto         Run without prompts using defaults and port autodetection
  --force        Overwrite existing .env
  --production   Use production-oriented defaults, disabling Swagger docs by default
  --show-token   Show API token after configuration
  -h, --help     Show this help

Examples:
  ./star configure
  ./star configure --auto
  ./star configure --auto --production
  ./star configure --auto --force
  ./star configure --force --show-token
EOF
}

# Parse CLI arguments and set mode flags.
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --auto)
                AUTO_MODE=true
                shift
                ;;
            --force)
                FORCE_MODE=true
                shift
                ;;
            --production)
                PRODUCTION_MODE=true
                shift
                ;;
            --show-token)
                SHOW_TOKEN_FLAG=true
                shift
                ;;
            -h | --help)
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

# Print wizard intro.
show_intro() {
    section "STAR Runtime Configuration"
    info "This wizard creates the local .env file, STAR API token, and runtime directories."
    info "After configuration, you can start Docker resources with './star up'."
}

# Initialize selected values with recommended defaults.
set_selected_defaults() {
    STAR_VERSION_VALUE="${DEFAULT_STAR_VERSION}"
    COMPOSE_PROJECT_NAME_VALUE="${DEFAULT_COMPOSE_PROJECT_NAME}"
    STAR_SHARED_NETWORK_VALUE="${DEFAULT_STAR_SHARED_NETWORK}"
    STAR_HOST_BIND_ADDRESS_VALUE="${DEFAULT_STAR_HOST_BIND_ADDRESS}"
    STAR_HOST_PORT_VALUE="${DEFAULT_STAR_HOST_PORT}"
    STAR_PORT_VALUE="${DEFAULT_STAR_PORT}"

    # Production mode changes the default docs behavior.
    if [[ "${PRODUCTION_MODE}" == "true" ]]; then
        STAR_ENABLE_DOCS_VALUE="false"
    else
        STAR_ENABLE_DOCS_VALUE="${DEFAULT_STAR_ENABLE_DOCS}"
    fi

    STAR_IMAGE_VALUE="ghcr.io/libertocrat/star:${STAR_VERSION_VALUE}"
    STAR_DATA_VOLUME_VALUE="${COMPOSE_PROJECT_NAME_VALUE}_star-data"
}

# Validate STAR version/tag syntax using the safe docker-like pattern.
is_valid_star_version_tag() {
    local value="$1"
    [[ -n "${value}" && "${value}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]
}

# Show only beginner-facing recommended defaults.
show_recommended_defaults() {
    local docs_state="enabled"

    if [[ "${STAR_ENABLE_DOCS_VALUE}" != "true" ]]; then
        docs_state="disabled"
    fi

    info "Recommended defaults:"
    printf '  %-27s %s\n' "STAR version:" "${STAR_VERSION_VALUE}"
    printf '  %-27s %s\n' "Compose project:" "${COMPOSE_PROJECT_NAME_VALUE}"
    printf '  %-27s %s\n' "Shared Docker network:" "${STAR_SHARED_NETWORK_VALUE}"
    printf '  %-27s %s\n' "Localhost port:" "${DEFAULT_STAR_HOST_PORT} (auto-detected if busy)"
    printf '  %-27s %s\n' "Data volume:" "${STAR_DATA_VOLUME_VALUE}"
    printf '  %-27s %s\n' "Swagger / OpenAPI docs:" "${docs_state}"
}

# Prompt for a STAR version tag and keep asking until it is valid.
prompt_star_version() {
    local value

    while true; do
        value="$(prompt_default "STAR version tag" "${DEFAULT_STAR_VERSION}")"
        if ! is_non_empty "${value}"; then
            warn "STAR version tag must not be empty."
            continue
        fi
        if ! is_valid_star_version_tag "${value}"; then
            warn "Invalid STAR version tag. Use letters, numbers, dots, underscores or dashes."
            continue
        fi
        printf '%s\n' "${value}"
        return 0
    done
}

# Prompt for a docker-safe resource name.
prompt_safe_name() {
    local prompt_text="$1"
    local default_value="$2"
    local value

    while true; do
        value="$(prompt_default "${prompt_text}" "${default_value}")"
        if is_safe_docker_name "${value}"; then
            printf '%s\n' "${value}"
            return 0
        fi
        warn "Invalid value. Use letters, numbers, dots, underscores or dashes."
    done
}

# Prompt for a valid TCP port number.
prompt_port_value() {
    local default_value="$1"
    local value

    while true; do
        value="$(prompt_default "Localhost port" "${default_value}")"
        if is_port "${value}"; then
            printf '%s\n' "${value}"
            return 0
        fi
        warn "Invalid port. Please enter an integer between 1 and 65535."
    done
}

# Resolve a free localhost port depending on interaction mode.
# Modes:
#   - auto-accept: use suggested free port automatically
#   - prompt: ask user before switching to suggested port
resolve_host_port() {
    local preferred_port="$1"
    local mode="$2"
    local suggested_port

    while true; do
        if ! is_port "${preferred_port}"; then
            if [[ "${mode}" == "auto-accept" ]]; then
                die "Invalid preferred localhost port: ${preferred_port}"
            fi
            preferred_port="$(prompt_port_value "${DEFAULT_STAR_HOST_PORT}")"
            continue
        fi

        # If the user-selected port is already free, keep it.
        if is_port_free "${preferred_port}"; then
            printf '%s\n' "${preferred_port}"
            return 0
        fi

        suggested_port=""
        if ((preferred_port <= 8099)); then
            suggested_port="$(find_free_port "${preferred_port}" "${preferred_port}" "8099" || true)"
        fi

        if [[ -n "${suggested_port}" && "${suggested_port}" != "${preferred_port}" ]]; then
            if [[ "${mode}" == "auto-accept" ]]; then
                warn "Port ${preferred_port} is already in use. Next available port: ${suggested_port}."
                printf '%s\n' "${suggested_port}"
                return 0
            fi

            warn "Port ${preferred_port} is already in use."
            if confirm "Use ${suggested_port} instead?" "Y"; then
                printf '%s\n' "${suggested_port}"
                return 0
            fi
        else
            if [[ "${mode}" == "auto-accept" ]]; then
                die "Could not find a free localhost port between ${preferred_port} and 8099 in auto mode."
            fi
            warn "Could not find a free port between ${preferred_port} and 8099."
        fi

        # In interactive mode, ask for another port until a free one is found.
        preferred_port="$(prompt_port_value "${preferred_port}")"
    done
}

# Prompt for Swagger docs setting in custom flow.
# In production mode, default answer changes to No.
prompt_custom_docs_setting() {
    local default_choice="Y"

    if [[ "${PRODUCTION_MODE}" == "true" ]]; then
        default_choice="N"
    fi

    info "Swagger / OpenAPI docs are useful for local testing and demos, but should be disabled in production."
    if confirm "Enable Swagger / OpenAPI docs for local testing and demos?" "${default_choice}"; then
        STAR_ENABLE_DOCS_VALUE="true"
    else
        STAR_ENABLE_DOCS_VALUE="false"
    fi
}

# Run custom prompts for user-selected values.
run_custom_prompts() {
    local default_volume

    section "Custom Configuration"
    info "Press Enter to accept each default value."
    STAR_VERSION_VALUE="$(prompt_star_version)"
    STAR_IMAGE_VALUE="ghcr.io/libertocrat/star:${STAR_VERSION_VALUE}"

    COMPOSE_PROJECT_NAME_VALUE="$(prompt_safe_name "Compose project name" "${DEFAULT_COMPOSE_PROJECT_NAME}")"
    STAR_SHARED_NETWORK_VALUE="$(prompt_safe_name "Shared Docker network" "${DEFAULT_STAR_SHARED_NETWORK}")"
    STAR_HOST_PORT_VALUE="$(prompt_port_value "${DEFAULT_STAR_HOST_PORT}")"

    default_volume="${COMPOSE_PROJECT_NAME_VALUE}_star-data"
    STAR_DATA_VOLUME_VALUE="$(prompt_safe_name "Data volume" "${default_volume}")"

    prompt_custom_docs_setting
}

# Print custom configuration summary before write confirmation.
show_custom_summary() {
    local docs_state="disabled"

    if [[ "${STAR_ENABLE_DOCS_VALUE}" == "true" ]]; then
        docs_state="enabled"
    fi

    section "Configuration Summary"
    printf '  %-27s %s\n' "STAR version" "${STAR_VERSION_VALUE}"
    printf '  %-27s %s\n' "Image" "${STAR_IMAGE_VALUE}"
    printf '  %-27s %s\n' "Compose project" "${COMPOSE_PROJECT_NAME_VALUE}"
    printf '  %-27s %s\n' "Shared Docker network" "${STAR_SHARED_NETWORK_VALUE}"
    printf '  %-27s %s\n' "Data volume" "${STAR_DATA_VOLUME_VALUE}"
    #printf '  %-22s %s\n' "Host bind" "${DEFAULT_STAR_HOST_BIND_ADDRESS}"
    printf '  %-27s %s\n' "Host port" "${STAR_HOST_PORT_VALUE}"
    #printf '  %-22s %s\n' "Internal port" "${DEFAULT_STAR_PORT}"
    printf '  %-27s %s\n' "Swagger / OpenAPI docs" "${docs_state}"
    #printf '  %-22s %s\n' "User specs dir" "./user-specs"
    #printf '  %-22s %s\n' "STAR API token file" "./secrets/star_api_token.txt"
}

# Handle interactive mode flow with defaults/custom branches.
run_interactive_flow() {
    while true; do
        set_selected_defaults
        show_recommended_defaults

        if confirm "Use recommended default settings?" "Y"; then
            # Defaults flow: no custom prompts, no summary, docs default by mode.
            STAR_HOST_PORT_VALUE="$(resolve_host_port "${STAR_HOST_PORT_VALUE}" "auto-accept")"
            return 0
        fi

        # Custom flow: collect user values and ask for final confirmation.
        run_custom_prompts
        STAR_HOST_PORT_VALUE="$(resolve_host_port "${STAR_HOST_PORT_VALUE}" "prompt")"

        show_custom_summary
        if confirm "Write this configuration?" "Y"; then
            return 0
        fi

        info "Restarting configuration prompts..."
    done
}

# Handle non-interactive auto mode with defaults and strict safety checks.
run_auto_flow() {
    set_selected_defaults
    STAR_HOST_PORT_VALUE="$(resolve_host_port "${STAR_HOST_PORT_VALUE}" "auto-accept")"
}

# Load runtime values from an existing .env when overwrite is declined.
load_existing_env_configuration() {
    load_env required

    STAR_IMAGE_VALUE="${STAR_IMAGE:-ghcr.io/libertocrat/star:${DEFAULT_STAR_VERSION}}"
    COMPOSE_PROJECT_NAME_VALUE="${COMPOSE_PROJECT_NAME:-${DEFAULT_COMPOSE_PROJECT_NAME}}"
    STAR_SHARED_NETWORK_VALUE="${STAR_SHARED_NETWORK:-${DEFAULT_STAR_SHARED_NETWORK}}"
    STAR_DATA_VOLUME_VALUE="${STAR_DATA_VOLUME:-${COMPOSE_PROJECT_NAME_VALUE}_star-data}"
    STAR_HOST_BIND_ADDRESS_VALUE="${STAR_HOST_BIND_ADDRESS:-${DEFAULT_STAR_HOST_BIND_ADDRESS}}"
    STAR_HOST_PORT_VALUE="${STAR_HOST_PORT:-${DEFAULT_STAR_HOST_PORT}}"
    STAR_PORT_VALUE="${STAR_PORT:-${DEFAULT_STAR_PORT}}"
    STAR_ENABLE_DOCS_VALUE="${STAR_ENABLE_DOCS:-${DEFAULT_STAR_ENABLE_DOCS}}"
}

# Verify the host port from existing .env is still valid and available.
validate_existing_env_host_port() {
    local env_path_display
    local suggested_port

    env_path_display="$(path_relative_to_pwd "${ENV_FILE}")"

    if ! is_port "${STAR_HOST_PORT_VALUE}"; then
        die "Existing .env has invalid STAR_HOST_PORT: ${STAR_HOST_PORT_VALUE}. Update STAR_HOST_PORT in ${env_path_display} and re-run this script."
    fi

    if ! is_port_free "${STAR_HOST_PORT_VALUE}"; then
        # Reuse the existing auto-accept helper to find the next free port suggestion.
        # If no port is available in range, resolve_host_port itself exits via die.
        suggested_port="$(resolve_host_port "${STAR_HOST_PORT_VALUE}" "auto-accept")"
        die "Configured STAR_HOST_PORT ${STAR_HOST_PORT_VALUE} in ${env_path_display} is already in use. Update STAR_HOST_PORT to ${suggested_port} and re-run this script."
    fi
}

# Persist token value with restrictive host-side permissions.
# star-up.sh adjusts token readability before Docker Compose startup.
write_token_file() {
    local token="$1"

    if ! (umask 177 && printf '%s\n' "${token}" > "${SECRET_FILE}"); then
        return 1
    fi

    chmod 600 "${SECRET_FILE}" 2> /dev/null || true
    return 0
}

# Read existing token from disk and validate non-empty content.
read_local_token() {
    local token

    if [[ ! -f "${SECRET_FILE}" ]]; then
        error "Token file not found: $(path_relative_to_pwd "${SECRET_FILE}")"
        return 1
    fi

    token="$(< "${SECRET_FILE}")"
    token="$(trim "${token}")"
    if [[ -z "${token}" ]]; then
        error "Token file is empty: $(path_relative_to_pwd "${SECRET_FILE}")"
        return 1
    fi

    printf '%s\n' "${token}"
}

# Ensure token exists and is strong, following interactive/auto policy.
handle_token() {
    local existing_token
    local new_token

    if [[ ! -f "${SECRET_FILE}" ]]; then
        new_token="$(generate_token)" || die "Failed to generate a strong API token."
        write_token_file "${new_token}" || die "Failed to write token file: $(path_relative_to_pwd "${SECRET_FILE}")"

        TOKEN_VALUE="${new_token}"
        TOKEN_WAS_GENERATED=true
        return 0
    fi

    existing_token="$(read_local_token)" || return 1
    if validate_token_strength "${existing_token}"; then
        success "Existing API token is valid."
        TOKEN_VALUE="${existing_token}"
        return 0
    fi

    if [[ "${AUTO_MODE}" == "true" ]]; then
        die "Existing STAR API token is too weak. Replace it with a token of at least 32 characters or remove the token file and re-run this script."
    fi

    warn "Existing STAR API token is too weak."
    if ! confirm "Regenerate a stronger STAR API token now?" "Y"; then
        error "STAR will reject weak API tokens. Re-run this script and regenerate the token before starting STAR."
        return 1
    fi

    new_token="$(generate_token)" || {
        error "Failed to generate a strong STAR API token."
        return 1
    }

    if ! write_token_file "${new_token}"; then
        error "Failed to write token file: $(path_relative_to_pwd "${SECRET_FILE}")"
        return 1
    fi

    TOKEN_VALUE="${new_token}"
    TOKEN_WAS_REGENERATED=true
    success "API token was regenerated."
    return 0
}

# Decide whether token should be shown in the final output.
resolve_token_output_policy() {
    SHOW_TOKEN_OUTPUT=false

    if [[ "${SHOW_TOKEN_FLAG}" == "true" ]]; then
        SHOW_TOKEN_OUTPUT=true
        return 0
    fi

    # Auto mode must never prompt for token visibility.
    if [[ "${AUTO_MODE}" == "true" ]]; then
        return 0
    fi

    # Ask only when a new token was created in interactive mode.
    if [[ "${TOKEN_WAS_GENERATED}" == "true" || "${TOKEN_WAS_REGENERATED}" == "true" ]]; then
        if confirm "Show generated STAR API token?" "N"; then
            SHOW_TOKEN_OUTPUT=true
        fi
    fi
}

# Generate the runtime .env file from scratch.
write_env_file() {
    cat > "${ENV_FILE}" << EOF
# =============================================================================
# Secure Templated Actions Runtime (STAR) - Generated Runtime Configuration
#
# Generated by scripts/star-configure.sh
# For detailed documentation, see .env.example.
# NEVER commit this file to version control.
# =============================================================================

# Container identity / runtime volume permissions
STAR_CONTAINER_UID=${DEFAULT_STAR_CONTAINER_UID}
STAR_CONTAINER_GID=${DEFAULT_STAR_CONTAINER_GID}

# Docker / Compose infrastructure
COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME_VALUE}
STAR_DATA_VOLUME=${STAR_DATA_VOLUME_VALUE}
STAR_SHARED_NETWORK=${STAR_SHARED_NETWORK_VALUE}
STAR_IMAGE=${STAR_IMAGE_VALUE}
STAR_PULL_POLICY=${DEFAULT_STAR_PULL_POLICY}
STAR_HOST_BIND_ADDRESS=${DEFAULT_STAR_HOST_BIND_ADDRESS}
STAR_HOST_PORT=${STAR_HOST_PORT_VALUE}
STAR_PORT=${DEFAULT_STAR_PORT}

# STAR persistent storage
STAR_ROOT_DIR=${DEFAULT_STAR_ROOT_DIR}

# Runtime binary execution policy
STAR_BLOCKED_BINARIES_EXTRA=${DEFAULT_STAR_BLOCKED_BINARIES_EXTRA}

# Runtime limits and safeguards
STAR_MAX_FILE_BYTES=${DEFAULT_STAR_MAX_FILE_BYTES}
STAR_MAX_YML_BYTES=${DEFAULT_STAR_MAX_YML_BYTES}
STAR_TIMEOUT_MS=${DEFAULT_STAR_TIMEOUT_MS}
STAR_MAX_STDOUT_BYTES=${DEFAULT_STAR_MAX_STDOUT_BYTES}
STAR_MAX_STDERR_BYTES=${DEFAULT_STAR_MAX_STDERR_BYTES}
STAR_RATE_LIMIT_RPS=${DEFAULT_STAR_RATE_LIMIT_RPS}

# API docs / runtime UI
STAR_ENABLE_DOCS=${STAR_ENABLE_DOCS_VALUE}

# Response security headers
STAR_ENABLE_SECURITY_HEADERS=${DEFAULT_STAR_ENABLE_SECURITY_HEADERS}
EOF
}

# Print final completion output in aligned table/spec style.
print_final_output() {
    local docs_state="disabled"

    if [[ "${STAR_ENABLE_DOCS_VALUE}" == "true" ]]; then
        docs_state="enabled"
    fi

    section "STAR Runtime Configuration Complete"
    if [[ "${REUSE_EXISTING_ENV}" == "true" ]]; then
        success "STAR runtime configuration loaded from $(path_relative_to_pwd "${ENV_FILE}")."
    else
        success "STAR runtime configuration generated."
    fi

    if [[ "${TOKEN_WAS_GENERATED}" == "true" ]]; then
        success "STAR API token was generated and saved to $(path_relative_to_pwd "${SECRET_FILE}")."
    elif [[ "${TOKEN_WAS_REGENERATED}" == "true" ]]; then
        success "STAR API token was regenerated and saved to $(path_relative_to_pwd "${SECRET_FILE}")."
    else
        success "STAR API token is valid and ready."
    fi

    printf '\nConfiguration:\n'
    printf '  %-27s %s\n' "STAR Image" "${STAR_IMAGE_VALUE}"
    printf '  %-27s %s\n' "Compose project" "${COMPOSE_PROJECT_NAME_VALUE}"
    printf '  %-27s %s\n' "Shared Docker network" "${STAR_SHARED_NETWORK_VALUE}"
    printf '  %-27s %s\n' "Data volume" "${STAR_DATA_VOLUME_VALUE}"
    printf '  %-27s %s\n' "Host bind" "${STAR_HOST_BIND_ADDRESS_VALUE}"
    printf '  %-27s %s\n' "Host port" "${STAR_HOST_PORT_VALUE}"
    printf '  %-27s %s\n' "Internal port" "${STAR_PORT_VALUE}"
    printf '  %-27s %s\n' "Swagger / OpenAPI docs" "${docs_state}"

    printf '\nFiles and directories:\n'
    printf '  %-27s %s\n' ".env file" "$(path_relative_to_pwd "${ENV_FILE}")"
    printf '  %-27s %s\n' "secrets/" "ready"
    printf '  %-27s %s\n' "user-specs/" "ready"
    printf '  %-27s %s\n' "STAR API token file" "$(path_relative_to_pwd "${SECRET_FILE}")"

    printf '\nSecurity note:\n'
    if [[ "${STAR_ENABLE_DOCS_VALUE}" == "true" ]]; then
        printf '  Swagger / OpenAPI docs are enabled for local testing and demos.\n'
        printf '  Disable them for production by setting STAR_ENABLE_DOCS=false.\n'
    else
        printf '  Swagger / OpenAPI docs are disabled. This is recommended for production.\n'
        printf '  Enable STAR_ENABLE_DOCS=true in .env for local demos.\n'
    fi

    if [[ "${SHOW_TOKEN_OUTPUT}" == "true" ]]; then
        printf '\nSTAR API token:\n'
        warn 'Sensitive value. Store it securely and do not commit it.'
        printf '  %s\n' "${TOKEN_VALUE}"
    fi

    printf '\n'
    if [[ "${REUSE_EXISTING_ENV}" == "true" ]]; then
        info "Existing .env was kept and not modified. Values above were loaded from $(path_relative_to_pwd "${ENV_FILE}")."
    fi
    info "You can edit $(path_relative_to_pwd "${ENV_FILE}") later to adjust runtime settings. See .env.example for details."
}

# Enforce overwrite policy for existing .env according to mode.
check_env_overwrite_policy() {
    if [[ ! -f "${ENV_FILE}" || "${FORCE_MODE}" == "true" ]]; then
        return 0
    fi

    if [[ "${AUTO_MODE}" == "true" ]]; then
        die "Existing .env found. Re-run with --force to overwrite it, or remove the existing .env file."
    fi

    warn "Existing .env file found: $(path_relative_to_pwd "${ENV_FILE}")"
    if ! confirm "Overwrite it with a new runtime configuration?" "N"; then
        info "Keeping existing .env. Runtime values will be loaded from the current file."
        REUSE_EXISTING_ENV=true
    fi
}

# Main entry point.
main() {
    parse_args "$@"
    show_intro

    check_env_overwrite_policy

    if [[ "${REUSE_EXISTING_ENV}" == "true" ]]; then
        load_existing_env_configuration
        validate_existing_env_host_port
    else
        if [[ "${AUTO_MODE}" == "true" ]]; then
            run_recording_transition --clear-tty
            run_auto_flow
        else
            run_recording_transition --clear-tty
            run_interactive_flow
        fi
    fi

    # Ensure runtime directories exist before token/.env writes.
    ensure_dir "${SECRET_DIR}" || die "Failed to create secrets directory."
    ensure_dir "${USER_SPECS_DIR}" || die "Failed to create user-specs directory."

    handle_token || exit 1
    resolve_token_output_policy
    if [[ "${REUSE_EXISTING_ENV}" != "true" ]]; then
        write_env_file
    fi
    print_final_output
}

main "$@"
