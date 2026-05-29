#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
source "${SCRIPT_DIR}/helpers/common.sh"
# shellcheck source=deploy/star-runtime/scripts/helpers/star-api.sh
source "${SCRIPT_DIR}/helpers/star-api.sh"
# shellcheck source=deploy/star-runtime/scripts/demos/demo-deps.sh
source "${SCRIPT_DIR}/demos/demo-deps.sh"
# shellcheck source=deploy/star-runtime/scripts/demos/demo-common.sh
source "${SCRIPT_DIR}/demos/demo-common.sh"
# shellcheck source=deploy/star-runtime/scripts/demos/demo-flows.sh
source "${SCRIPT_DIR}/demos/demo-flows.sh"

# CLI mode flags and selected demo values.
AUTO_MODE=false
KEEP_FILES=false
VERBOSE_MODE=false
DEMO_SELECTION=""
SELECTED_DEMO=""

# Shared runtime constants used by demo helpers.
DEMO_ASSET_FILE="${SCRIPT_DIR}/demos/assets/demo-text.txt"
DEMO_ENCRYPTION_PASSWORD="demo-password-used-only-for-star-encryption-demo"

# Print CLI usage and examples for the demo runner.
usage() {
    cat <<'EOF'
Usage:
  ./star demo [options]

Description:
  Runs an interactive STAR API walkthrough with focused demos.

Options:
  --demo <id|slug>   Run a specific demo directly
  --keep-files       Keep STAR-managed files created by the selected demo
  --auto             Accept demo prompts and install missing demo dependencies automatically when possible
  -v, --verbose      Show full JSON responses and extra API details
  -h, --help         Show help

Supported demos:
  1 | files
  2 | actions
  3 | random
  4 | inspect
  5 | search
  6 | encrypt

Examples:
  ./star demo
  ./star demo --demo encrypt
  ./star demo --demo 6
  ./star demo --auto --demo encrypt
  ./star demo --demo inspect --keep-files
  ./star demo -v --demo actions
EOF
}

# Parse CLI flags and store selection state.
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --demo)
                if [[ $# -lt 2 ]]; then
                    die "--demo requires a value: 1|2|3|4|5|6 or files|actions|random|inspect|search|encrypt"
                fi
                DEMO_SELECTION="$2"
                shift 2
                ;;
            --keep-files)
                KEEP_FILES=true
                shift
                ;;
            --auto)
                AUTO_MODE=true
                shift
                ;;
            -v|--verbose)
                VERBOSE_MODE=true
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

# Print concise intro once before one-time startup checks.
show_intro() {
    section "STAR Runtime Demo"
    info "This guided walkthrough shows STAR-managed file and action workflows quickly."
    info "Swagger/OpenAPI remains the source of full schema and parameter contracts."
}

# Normalize demo selectors from numeric IDs or slugs.
normalize_demo_selector() {
    local raw_value="${1-}"
    local normalized

    normalized="$(trim "${raw_value}")"
    normalized="${normalized,,}"

    case "${normalized}" in
        1|files)
            printf 'files\n'
            return 0
            ;;
        2|actions)
            printf 'actions\n'
            return 0
            ;;
        3|random)
            printf 'random\n'
            return 0
            ;;
        4|inspect)
            printf 'inspect\n'
            return 0
            ;;
        5|search)
            printf 'search\n'
            return 0
            ;;
        6|encrypt)
            printf 'encrypt\n'
            return 0
            ;;
    esac

    return 1
}

# Ensure runtime .env exists and print configure guidance when missing.
ensure_env_file_present() {
    if ensure_file_exists "${STAR_ENV_FILE}" ".env file"; then
        return 0
    fi

    error "Run: './star configure'"
    return 1
}

# Return success when STAR responds at the configured health endpoint.
runtime_is_healthy() {
    curl -fsS "$(health_url)" >/dev/null 2>&1
}

# Ensure STAR runtime is reachable, starting it when allowed.
ensure_runtime_available() {
    local health_endpoint

    health_endpoint="$(health_url)"
    if runtime_is_healthy; then
        success "STAR is reachable at ${health_endpoint}."
        return 0
    fi

    warn "STAR is not reachable at ${health_endpoint}."

    if [[ "${AUTO_MODE}" != "true" ]]; then
        if ! confirm "Start it now with './star up'?" "Y"; then
            printf 'STAR is not running. Start it with:\n' >&2
            printf "  './star up'\n" >&2
            return 10
        fi
    else
        info "Auto mode enabled. Starting STAR with './star up'"
    fi

    if ! run_with_spinner "Starting STAR runtime" "${STAR_SCRIPTS_DIR}/star-up.sh" --silent; then
        warn "STAR startup with --silent failed."

        if [[ "${AUTO_MODE}" != "true" ]]; then
            if confirm "Retry startup with full './star up' output?" "Y"; then
                if ! "${STAR_SCRIPTS_DIR}/star-up.sh"; then
                    error "STAR startup failed."
                    error "Run: './star up'"
                    return 1
                fi
            else
                error "STAR startup failed."
                error "Run: './star up'"
                return 1
            fi
        else
            error "STAR startup failed."
            error "Run: './star up'"
            return 1
        fi
    fi

    if runtime_is_healthy; then
        success "STAR is reachable at ${health_endpoint}."
        return 0
    fi

    error "STAR startup failed."
    error "Run: './star up'"
    return 1
}

# Initialize API helper globals after runtime and token checks.
initialize_api_helpers() {
    local token

    token="$(read_token)" || return 1
    star_api_init "$(base_url)" "${token}" "${VERBOSE_MODE}"
}

# Ensure the shared demo asset exists before any upload-based flow.
ensure_demo_asset_present() {
    ensure_file_exists "${DEMO_ASSET_FILE}" "demo asset file"
}

# Print the menu shown whenever --demo is not provided.
print_demo_menu() {
    section "STAR Runtime Demo"
    cat <<'EOF'
Available demos:
  [1] Files API walkthrough
  [2] Actions API walkthrough
  [3] Generate random tokens
  [4] Measure and inspect a text file
  [5] Search patterns in a text file
  [6] Encrypt and decrypt a file
  [q] Quit
EOF
}

# Resolve demo from --demo flag or fail.
select_demo_from_flag_or_fail() {
    local selected_slug

    if ! is_non_empty "${DEMO_SELECTION}"; then
        error "Internal error: --demo value is required."
        return 1
    fi

    if selected_slug="$(normalize_demo_selector "${DEMO_SELECTION}")"; then
        SELECTED_DEMO="${selected_slug}"
        return 0
    fi

    error "Invalid --demo value: ${DEMO_SELECTION}"
    return 1
}

# Resolve demo from interactive menu. Returns 11 when user selects quit.
select_demo_from_menu() {
    local selected_input=""
    local selected_slug=""

    while true; do
        print_demo_menu
        printf '\n' >&2
        selected_input="$(prompt_default "Select a demo" "6")"
        selected_input="$(trim "${selected_input}")"

        if [[ "${selected_input,,}" == "q" ]]; then
            return 11
        fi

        if selected_slug="$(normalize_demo_selector "${selected_input}")"; then
            SELECTED_DEMO="${selected_slug}"
            return 0
        fi

        warn "Unknown demo selection. Choose 1-6, a valid slug, or q to quit."
    done
}

# Run one selected demo, cleanup its files, and print per-demo completion summary.
run_one_demo_session() {
    local demo_status=0

    reset_demo_state

    if run_selected_demo; then
        demo_status=0
    else
        demo_status=$?
    fi

    cleanup_created_files

    section "Demo Complete"
    if (( demo_status == 0 )); then
        success "Selected demo finished successfully."
        return 0
    fi

    error "Selected demo finished with errors."
    return "${demo_status}"
}

# Optionally stop STAR runtime at session end, defaulting to keep running.
prompt_stop_runtime() {
    if [[ "${AUTO_MODE}" == "true" ]]; then
        return 0
    fi

    if ! confirm "Stop STAR runtime now?" "N"; then
        return 0
    fi

    if run_with_spinner "Stopping STAR runtime" "${STAR_SCRIPTS_DIR}/star-down.sh" --silent; then
        success "STAR runtime stopped."
        return 0
    fi

    warn "Failed to stop STAR runtime with --silent."
    warn "Run: './star down'"
    return 0
}

# Print a short end-of-session summary and exploration links.
print_goodbye_summary() {
    success "Thanks for discovering STAR! Goodbye for now."
    printf '\nExplore STAR:\n'
    printf '  %-27s %s\n' "Swagger / OpenAPI docs" "$(docs_url)"
    printf '  %-27s %s\n' "STAR official repo" "https://github.com/Libertocrat/star"
    printf '\nIf STAR helped you, consider starring the repo to support the project.\n'
}

# Run all one-time checks and initialization before any demo session loop.
run_initial_checks_once() {
    local startup_status=0

    show_intro
    ensure_demo_dependencies "${AUTO_MODE}" || return 1
    ensure_env_file_present || return 1
    load_env required

    if ensure_runtime_available; then
        startup_status=0
    else
        startup_status=$?
        if [[ "${startup_status}" == "10" ]]; then
            return 10
        fi
        return "${startup_status}"
    fi

    initialize_api_helpers || return 1
    ensure_demo_asset_present || return 1
    return 0
}

# Main orchestration loop for direct and menu-driven demo sessions.
main() {
    local overall_status=0
    local init_status=0
    local run_status=0
    local menu_status=0

    parse_args "$@"

    if run_initial_checks_once; then
        init_status=0
    else
        init_status=$?
        if [[ "${init_status}" == "10" ]]; then
            exit 0
        fi
        exit "${init_status}"
    fi

    if is_non_empty "${DEMO_SELECTION}"; then
        select_demo_from_flag_or_fail || exit 1

        if run_one_demo_session; then
            overall_status=0
        else
            overall_status=$?
        fi

        prompt_stop_runtime
        print_goodbye_summary
        exit "${overall_status}"
    fi

    while true; do
        if select_demo_from_menu; then
            menu_status=0
        else
            menu_status=$?
            if [[ "${menu_status}" == "11" ]]; then
                break
            fi
            warn "Demo selection failed."
            overall_status=1
            break
        fi

        if run_one_demo_session; then
            run_status=0
        else
            run_status=$?
            overall_status="${run_status}"
        fi

        if ! confirm "Run another demo?" "Y"; then
            break
        fi
    done

    prompt_stop_runtime
    print_goodbye_summary
    exit "${overall_status}"
}

main "$@"
