#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# Path resolution
# -----------------------------------------------------------------------------

SEG_COMMON_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SEG_SCRIPTS_DIR="$(cd -- "${SEG_COMMON_DIR}/.." && pwd)"
SEG_RUNTIME_DIR="$(cd -- "${SEG_SCRIPTS_DIR}/.." && pwd)"

SEG_ENV_FILE="${SEG_RUNTIME_DIR}/.env"
SEG_ENV_EXAMPLE_FILE="${SEG_RUNTIME_DIR}/.env.example"
SEG_COMPOSE_FILE="${SEG_RUNTIME_DIR}/docker-compose.yml"

SEG_SECRET_DIR="${SEG_RUNTIME_DIR}/secrets"
SEG_SECRET_FILE="${SEG_SECRET_DIR}/seg_api_token.txt"

SEG_USER_SPECS_DIR="${SEG_RUNTIME_DIR}/user-specs"
SEG_USER_SPEC_EXAMPLES_DIR="${SEG_RUNTIME_DIR}/user-spec-examples"
SEG_DEMO_ASSETS_DIR="${SEG_RUNTIME_DIR}/demo-assets"

readonly SEG_COMMON_DIR
readonly SEG_SCRIPTS_DIR
readonly SEG_RUNTIME_DIR
readonly SEG_ENV_FILE
# shellcheck disable=SC2034
# Read by scripts that source this library.
readonly SEG_ENV_EXAMPLE_FILE
readonly SEG_COMPOSE_FILE
readonly SEG_SECRET_DIR
readonly SEG_SECRET_FILE
# shellcheck disable=SC2034
# Read by scripts that source this library.
readonly SEG_USER_SPECS_DIR
# shellcheck disable=SC2034
# Read by scripts that source this library.
readonly SEG_USER_SPEC_EXAMPLES_DIR
# shellcheck disable=SC2034
# Read by scripts that source this library.
readonly SEG_DEMO_ASSETS_DIR

# -----------------------------------------------------------------------------
# Colors and output
# -----------------------------------------------------------------------------

SEG_COLOR_ENABLED=false
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    SEG_COLOR_ENABLED=true
fi

if [[ "${SEG_COLOR_ENABLED}" == "true" ]]; then
    SEG_COLOR_RESET='\033[0m'
    SEG_COLOR_INFO='\033[36m'
    SEG_COLOR_OK='\033[32m'
    SEG_COLOR_WARN='\033[33m'
    SEG_COLOR_ERROR='\033[31m'
    SEG_COLOR_PROMPT='\033[35m'
    SEG_COLOR_SECTION='\033[1;34m'
else
    SEG_COLOR_RESET=''
    SEG_COLOR_INFO=''
    SEG_COLOR_OK=''
    SEG_COLOR_WARN=''
    SEG_COLOR_ERROR=''
    SEG_COLOR_PROMPT=''
    SEG_COLOR_SECTION=''
fi

readonly SEG_COLOR_ENABLED
readonly SEG_COLOR_RESET
readonly SEG_COLOR_INFO
readonly SEG_COLOR_OK
readonly SEG_COLOR_WARN
readonly SEG_COLOR_ERROR
readonly SEG_COLOR_SECTION

SEG_STEP_COUNTER=0

# Print an informational message to stdout.
info() {
    printf '%b[INFO]%b %s\n' "${SEG_COLOR_INFO}" "${SEG_COLOR_RESET}" "$*"
}

# Print a success message to stdout.
success() {
    printf '%b[OK]%b %s\n' "${SEG_COLOR_OK}" "${SEG_COLOR_RESET}" "$*"
}

# Print a warning message to stderr.
warn() {
    printf '%b[WARN]%b %s\n' "${SEG_COLOR_WARN}" "${SEG_COLOR_RESET}" "$*" >&2
}

# Print a non-fatal error message to stderr.
error() {
    printf '%b[ERROR]%b %s\n' "${SEG_COLOR_ERROR}" "${SEG_COLOR_RESET}" "$*" >&2
}

# Print an error and terminate with a non-zero exit code.
die() {
    error "$*"
    exit 1
}

# Print a visual section header for script output.
section() {
    local title="${1:-}"
    printf '\n%b==== %s ====%b\n' "${SEG_COLOR_SECTION}" "${title}" "${SEG_COLOR_RESET}"
}

# Print a numbered step marker.
step() {
    SEG_STEP_COUNTER=$((SEG_STEP_COUNTER + 1))
    printf '%b[INFO]%b Step %d: %s\n' "${SEG_COLOR_INFO}" "${SEG_COLOR_RESET}" "${SEG_STEP_COUNTER}" "$*"
}

# Print info unless the caller enables silent mode.
say_info() {
    local silent_mode="false"

    if [[ "${1:-}" == "true" || "${1:-}" == "false" ]]; then
        silent_mode="$1"
        shift || true
    fi

    [[ "${silent_mode}" == "true" ]] && return 0
    info "$@"
}

# Print success unless the caller enables silent mode.
say_success() {
    local silent_mode="false"

    if [[ "${1:-}" == "true" || "${1:-}" == "false" ]]; then
        silent_mode="$1"
        shift || true
    fi

    [[ "${silent_mode}" == "true" ]] && return 0
    success "$@"
}

# Print section headers unless the caller enables silent mode.
say_section() {
    local silent_mode="false"

    if [[ "${1:-}" == "true" || "${1:-}" == "false" ]]; then
        silent_mode="$1"
        shift || true
    fi

    [[ "${silent_mode}" == "true" ]] && return 0
    section "$@"
}

# Print numbered steps unless the caller enables silent mode.
say_step() {
    local silent_mode="false"

    if [[ "${1:-}" == "true" || "${1:-}" == "false" ]]; then
        silent_mode="$1"
        shift || true
    fi

    [[ "${silent_mode}" == "true" ]] && return 0
    step "$@"
}

# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------

# Trim leading and trailing whitespace from a string.
trim() {
    local value="${1-}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "${value}"
}

# Inspect a file state and print owner UID, group GID, and octal mode.
read_file_state() {
    local file_path="${1:?file path is required}"
    stat -c '%u %g %a' "${file_path}"
}

# Return a path relative to the active shell directory when possible.
path_relative_to_pwd() {
    local input_path="${1:?path is required}"
    local relative_path

    if command_exists realpath; then
        relative_path="$(realpath --relative-to="${PWD}" "${input_path}" 2>/dev/null || true)"
        if is_non_empty "${relative_path}"; then
            printf '%s\n' "${relative_path}"
            return 0
        fi
    fi

    case "${input_path}" in
        "${PWD}")
            printf '.\n'
            return 0
            ;;
        "${PWD}"/*)
            printf '%s\n' "${input_path#"${PWD}/"}"
            return 0
            ;;
    esac

    printf '%s\n' "${input_path}"
}

# -----------------------------------------------------------------------------
# Prompts
# -----------------------------------------------------------------------------

# Prompt for a value with default fallback and return the chosen value.
prompt_default() {
    local prompt_text="${1:?prompt text is required}"
    local default_value="${2-}"
    local input=''

    printf '%b[?]%b %s [%s]: ' \
    "${SEG_COLOR_PROMPT}" "${SEG_COLOR_RESET}" \
    "${prompt_text}" "${default_value}" >&2

    read -r input || input=""

    if [[ -z "${input}" ]]; then
        printf '%s\n' "${default_value}"
        return 0
    fi

    printf '%s\n' "${input}"
}

# Prompt for yes/no confirmation with configurable default.
confirm() {
    local prompt_text="${1:?prompt text is required}"
    local default_choice="${2:-Y}"
    local default_upper
    local input
    local normalized

    default_upper="${default_choice^^}"
    if [[ "${default_upper}" != "Y" && "${default_upper}" != "N" ]]; then
        error "confirm default must be Y or N"
        return 1
    fi

    while true; do
        if [[ "${default_upper}" == "Y" ]]; then
            printf '%b[?]%b %s [Y/n]: ' \
            "${SEG_COLOR_PROMPT}" "${SEG_COLOR_RESET}" \
            "${prompt_text}" >&2
        else
            printf '%b[?]%b %s [y/N]: ' \
            "${SEG_COLOR_PROMPT}" "${SEG_COLOR_RESET}" \
            "${prompt_text}" >&2
        fi

        read -r input || input=""
        normalized="$(trim "${input}")"
        normalized="${normalized,,}"

        if [[ -z "${normalized}" ]]; then
            [[ "${default_upper}" == "Y" ]]
            return $?
        fi

        case "${normalized}" in
            y|yes)
                return 0
                ;;
            n|no)
                return 1
                ;;
            *)
                warn "Please answer yes or no."
                ;;
        esac
    done
}

# -----------------------------------------------------------------------------
# Dependencies
# -----------------------------------------------------------------------------

# Return success if a command exists in PATH.
command_exists() {
    local cmd="${1:?command is required}"
    command -v "${cmd}" >/dev/null 2>&1
}

# Require a command to exist, otherwise terminate with an error.
require_command() {
    local cmd="${1:?command is required}"
    local friendly_name="${2:-${cmd}}"

    if ! command_exists "${cmd}"; then
        die "${friendly_name} is required but was not found in PATH."
    fi
}

# Require Docker CLI and a reachable Docker daemon.
require_docker() {
    require_command docker "Docker CLI"

    if ! docker info >/dev/null 2>&1; then
        die "Docker daemon is not reachable. Start Docker and try again."
    fi
}

# Require Docker Compose v2 via `docker compose`.
require_docker_compose() {
    require_command docker "Docker CLI"

    if ! docker compose version >/dev/null 2>&1; then
        die "Docker Compose v2 is required. Ensure 'docker compose' is available."
    fi
}

# Require curl to be installed.
require_curl() {
    require_command curl "curl"
}

# -----------------------------------------------------------------------------
# Validators
# -----------------------------------------------------------------------------

# Validate that a value is non-empty after trimming whitespace.
is_non_empty() {
    [[ -n "$(trim "${1-}")" ]]
}

# Validate that a value is an integer.
is_int() {
    local value
    value="$(trim "${1-}")"
    [[ "${value}" =~ ^-?[0-9]+$ ]]
}

# Validate common boolean-like values.
is_bool() {
    local value
    value="$(trim "${1-}")"
    value="${value,,}"

    case "${value}" in
        true|false|yes|no|y|n|1|0)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Validate a TCP/UDP port number in range 1..65535.
is_port() {
    local value
    value="$(trim "${1-}")"

    if ! is_int "${value}"; then
        return 1
    fi

    (( value >= 1 && value <= 65535 ))
}

# Validate supported bind addresses (localhost, common defaults, basic IPv4).
is_bind_address() {
    local value
    local octet
    local -a ipv4_parts
    value="$(trim "${1-}")"

    case "${value}" in
        127.0.0.1|localhost|0.0.0.0)
            return 0
            ;;
    esac

    if [[ ! "${value}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        return 1
    fi

    IFS='.' read -r -a ipv4_parts <<< "${value}"
    for octet in "${ipv4_parts[@]}"; do
        if (( octet < 0 || octet > 255 )); then
            return 1
        fi
    done

    return 0
}

# Validate conservative Docker resource naming rules.
is_safe_docker_name() {
    local value
    value="$(trim "${1-}")"
    [[ "${value}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]
}

# -----------------------------------------------------------------------------
# Environment loading
# -----------------------------------------------------------------------------

# Load SEG runtime .env variables and export them for child processes.
load_env() {
    local mode="${1:-required}"
    local had_allexport=0

    if [[ "${mode}" != "required" && "${mode}" != "optional" ]]; then
        die "load_env mode must be 'required' or 'optional'."
    fi

    if [[ ! -f "${SEG_ENV_FILE}" ]]; then
        if [[ "${mode}" == "optional" ]]; then
            return 0
        fi
        die "Missing env file: $(path_relative_to_pwd "${SEG_ENV_FILE}")"
    fi

    [[ "${-}" == *a* ]] && had_allexport=1
    set -a

    # shellcheck disable=SC1090
    if ! source "${SEG_ENV_FILE}"; then
        (( had_allexport == 0 )) && set +a
        die "Failed to load env file: $(path_relative_to_pwd "${SEG_ENV_FILE}")"
    fi

    (( had_allexport == 0 )) && set +a
}

# -----------------------------------------------------------------------------
# Docker Compose helpers
# -----------------------------------------------------------------------------

# Run docker compose against SEG runtime files, honoring DRY_RUN when enabled.
compose() {
    run docker compose --env-file "$(path_relative_to_pwd "${SEG_ENV_FILE}")" -f "$(path_relative_to_pwd "${SEG_COMPOSE_FILE}")" "$@"
}

# -----------------------------------------------------------------------------
# URL helpers
# -----------------------------------------------------------------------------

# Return a display-friendly host based on bind address configuration.
public_host() {
    local bind_address="${SEG_HOST_BIND_ADDRESS:-}"

    case "${bind_address}" in
        ''|127.0.0.1|0.0.0.0)
            printf '%s\n' 'localhost'
            ;;
        *)
            printf '%s\n' "${bind_address}"
            ;;
    esac
}

# Build the base HTTP URL from host and port settings.
base_url() {
    printf 'http://%s:%s\n' "$(public_host)" "${SEG_HOST_PORT:-8080}"
}

# Return the SEG health endpoint URL.
health_url() {
    printf '%s/health\n' "$(base_url)"
}

# Return the SEG Swagger docs URL.
docs_url() {
    printf '%s/docs\n' "$(base_url)"
}

# Return the SEG OpenAPI JSON URL.
openapi_url() {
    printf '%s/openapi.json\n' "$(base_url)"
}

# -----------------------------------------------------------------------------
# Token helpers
# -----------------------------------------------------------------------------

# Validate token strength by length and character-class diversity.
validate_token_strength() {
    local token
    local classes=0

    token="$(trim "${1-}")"
    if (( ${#token} < 32 )); then
        return 1
    fi

    [[ "${token}" =~ [a-z] ]] && (( classes += 1 ))
    [[ "${token}" =~ [A-Z] ]] && (( classes += 1 ))
    [[ "${token}" =~ [0-9] ]] && (( classes += 1 ))
    [[ "${token}" =~ [^[:alnum:]] ]] && (( classes += 1 ))

    (( classes >= 2 ))
}

# Generate a fallback token as 32 random bytes encoded in lowercase hex.
_generate_token_fallback() {
    [[ -r /dev/urandom ]] || return 1

    if command_exists od; then
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
        return 0
    fi

    if command_exists hexdump; then
        hexdump -n 32 -e '32/1 "%02x"' /dev/urandom
        return 0
    fi

    return 1
}

# Generate a strong token using OpenSSL first, then fallback encoders.
generate_token() {
    local token=''

    if command_exists openssl; then
        token="$(openssl rand -hex 32 2>/dev/null || true)"
    fi

    if ! validate_token_strength "${token}"; then
        token="$(_generate_token_fallback)"
    fi

    if ! validate_token_strength "${token}"; then
        return 1
    fi

    printf '%s\n' "${token}"
}

# Read the token from disk and ensure it is present.
read_token() {
    local token

    if [[ ! -f "${SEG_SECRET_FILE}" ]]; then
        error "Token file not found: $(path_relative_to_pwd "${SEG_SECRET_FILE}")"
        return 1
    fi

    token="$(<"${SEG_SECRET_FILE}")"
    token="$(trim "${token}")"

    if [[ -z "${token}" ]]; then
        error "Token file is empty: $(path_relative_to_pwd "${SEG_SECRET_FILE}")"
        return 1
    fi

    printf '%s\n' "${token}"
}

# Ensure token file exists with secure permissions and strong content.
ensure_token_file() {
    local token

    if ! ensure_dir "${SEG_SECRET_DIR}"; then
        return 1
    fi

    if [[ ! -f "${SEG_SECRET_FILE}" ]]; then
        token="$(generate_token)" || {
            error "Failed to generate API token."
            return 1
        }

        if ! ( umask 177 && printf '%s\n' "${token}" > "${SEG_SECRET_FILE}" ); then
            error "Failed to write token file: $(path_relative_to_pwd "${SEG_SECRET_FILE}")"
            return 1
        fi
    else
        token="$(read_token)" || return 1

        if ! validate_token_strength "${token}"; then
            error "Existing token in $(path_relative_to_pwd "${SEG_SECRET_FILE}") is too weak (minimum 32 chars, 2 classes)."
            return 1
        fi
    fi

    chmod 600 "${SEG_SECRET_FILE}" 2>/dev/null || true
    return 0
}

# -----------------------------------------------------------------------------
# Port helpers
# -----------------------------------------------------------------------------

# Check whether a local port appears unused by host listeners and Docker mappings.
is_port_free() {
    local port="${1-}"

    if ! is_port "${port}"; then
        return 1
    fi

    if command_exists ss; then
        if ss -ltn "( sport = :${port} )" 2>/dev/null | grep -q "${port}"; then
            return 1
        fi
    fi

    if command_exists docker && docker info >/dev/null 2>&1; then
        if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->"; then
            return 1
        fi
    fi

    return 0
}

# Resolve the first free port from target or a given scan range.
find_free_port() {
    local target="${1-}"
    local start="${2-}"
    local end="${3-}"
    local candidate

    if ! is_port "${target}" || ! is_port "${start}" || ! is_port "${end}"; then
        return 1
    fi

    if (( start > end )); then
        return 1
    fi

    if is_port_free "${target}"; then
        printf '%s\n' "${target}"
        return 0
    fi

    for (( candidate = start; candidate <= end; candidate++ )); do
        if is_port_free "${candidate}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

# -----------------------------------------------------------------------------
# File helpers
# -----------------------------------------------------------------------------

# Ensure a directory exists (create it if needed).
ensure_dir() {
    local path="${1-}"

    if [[ -z "${path}" ]]; then
        error "Directory path is required."
        return 1
    fi

    if ! mkdir -p -- "${path}"; then
        error "Failed to create directory: ${path}"
        return 1
    fi

    return 0
}

# Ensure a file exists, returning a readable error when missing.
ensure_file_exists() {
    local path="${1-}"
    local friendly_name="${2:-file}"

    if [[ -f "${path}" ]]; then
        return 0
    fi

    error "Missing ${friendly_name}: $(path_relative_to_pwd "${path}")"
    return 1
}

# -----------------------------------------------------------------------------
# Optional dry-run helper
# -----------------------------------------------------------------------------

# Execute a command, or print it when DRY_RUN=true.
run() {
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        printf '[DRY-RUN] '
        printf '%q ' "$@"
        printf '\n'
        return 0
    fi

    "$@"
}
