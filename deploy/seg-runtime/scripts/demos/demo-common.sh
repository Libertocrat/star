#!/usr/bin/env bash
set -Eeuo pipefail

# Resolve demo-local paths for helper sourcing.
DEMO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${SEG_SCRIPTS_DIR:-}" ]]; then
    SEG_SCRIPTS_DIR="$(cd -- "${DEMO_DIR}/.." && pwd)"
fi

# Source shared runtime helpers only once.
if [[ -z "${SEG_COMMON_DIR:-}" ]]; then
    # shellcheck source=deploy/seg-runtime/scripts/helpers/common.sh
    source "${SEG_SCRIPTS_DIR}/helpers/common.sh"
fi

# Source API helpers only once.
if ! command -v seg_api_init >/dev/null 2>&1; then
    # shellcheck source=deploy/seg-runtime/scripts/helpers/seg-api.sh
    source "${SEG_SCRIPTS_DIR}/helpers/seg-api.sh"
fi

# Demo-local state reused across all flow scripts.
if ! declare -p CREATED_FILE_IDS >/dev/null 2>&1; then
    declare -ga CREATED_FILE_IDS=()
fi
if ! declare -p CREATED_FILE_ROLES >/dev/null 2>&1; then
    declare -gA CREATED_FILE_ROLES=()
fi
LAST_UPLOADED_FILE_ID="${LAST_UPLOADED_FILE_ID:-}"
DEMO_LAST_ACTION_ID="${DEMO_LAST_ACTION_ID:-}"
DEMO_LAST_ACTION_STDOUT="${DEMO_LAST_ACTION_STDOUT:-}"
DEMO_LAST_EXIT_CODE="${DEMO_LAST_EXIT_CODE:-}"
DEMO_LAST_EXEC_TIME="${DEMO_LAST_EXEC_TIME:-}"
DEMO_LAST_ACTION_BODY="${DEMO_LAST_ACTION_BODY:-}"

# Reset all per-demo mutable state before running a selected demo.
reset_demo_state() {
    SEG_STEP_COUNTER=0
    CREATED_FILE_IDS=()
    unset CREATED_FILE_ROLES
    declare -gA CREATED_FILE_ROLES=()
    LAST_UPLOADED_FILE_ID=""
    DEMO_LAST_ACTION_ID=""
    DEMO_LAST_ACTION_STDOUT=""
    DEMO_LAST_EXIT_CODE=""
    DEMO_LAST_EXEC_TIME=""
    DEMO_LAST_ACTION_BODY=""
}

# Print one blank line before each step except the first one in a demo run.
demo_step() {
    if (( SEG_STEP_COUNTER > 0 )); then
        printf '\n'
    fi
    step "$@"
}

# Pause between demo steps in interactive mode only.
demo_pause() {
    [[ "${AUTO_MODE:-false}" == "true" ]] && return 0
    # Show prompt on a visually separated line during pause.
    # This blank line is temporary and removed together with the prompt.
    printf '\n' >&2
    printf '%b[NEXT]%b Press any key to continue...' \
        "${SEG_COLOR_PROMPT:-}" "${SEG_COLOR_RESET:-}" >&2
    read -rsn1 || true
    # Clear prompt line, then move up and clear the temporary separator line.
    printf '\r\033[2K\033[1A\r\033[2K' >&2
}

# Track a SEG-managed file id once, preserving a role label for summaries.
track_created_file() {
    local file_id="${1-}"
    local role_label="${2:-demo output}"
    local existing_id

    if ! is_non_empty "${file_id}"; then
        return 1
    fi

    if (( ${#CREATED_FILE_IDS[@]} > 0 )); then
        for existing_id in "${CREATED_FILE_IDS[@]}"; do
            if [[ "${existing_id}" == "${file_id}" ]]; then
                return 0
            fi
        done
    fi

    CREATED_FILE_IDS+=("${file_id}")
    CREATED_FILE_ROLES["${file_id}"]="${role_label}"
    return 0
}

# Print stdout excerpts with two-space indentation for readable demos.
print_stdout_excerpt() {
    local stdout_text="${1-}"
    local max_lines="${2:-8}"
    local total_lines=0
    local printed_lines=0
    local line

    if [[ -z "${stdout_text}" ]]; then
        printf '  (empty)\n'
        return 0
    fi

    while IFS= read -r line; do
        total_lines=$((total_lines + 1))
        if (( printed_lines < max_lines )); then
            printf '  %s\n' "${line}"
            printed_lines=$((printed_lines + 1))
        fi
    done <<< "${stdout_text}"

    if (( total_lines > max_lines )); then
        printf '  ... (%d more lines)\n' "$((total_lines - max_lines))"
    fi
}

# Execute a SEG action using the concise request body with only params.
execute_action() {
    local action_id="${1:?action ID is required}"
    local params_json="${2:?params JSON is required}"
    local request_json

    request_json="$(jq -cn --argjson params "${params_json}" '{params: $params}')"

    if ! seg_api_post_json "/v1/actions/${action_id}" "${request_json}"; then
        return 1
    fi

    if ! seg_api_require_success "Execute action ${action_id}"; then
        if [[ "${SEG_API_LAST_STATUS}" == "404" ]]; then
            error "Required action is not available: ${action_id}"
            printf 'Check the official SEG image or open Swagger docs:\n' >&2
            printf '  %s\n' "$(docs_url)" >&2
        fi
        return 1
    fi

    DEMO_LAST_ACTION_ID="${action_id}"
    DEMO_LAST_ACTION_STDOUT="$(jq -r '.data.stdout // ""' <<< "${SEG_API_LAST_BODY}")"
    DEMO_LAST_EXIT_CODE="$(jq -r '.data.exit_code // "n/a"' <<< "${SEG_API_LAST_BODY}")"
    DEMO_LAST_EXEC_TIME="$(jq -r '.data.exec_time // "n/a"' <<< "${SEG_API_LAST_BODY}")"
    DEMO_LAST_ACTION_BODY="${SEG_API_LAST_BODY}"
    return 0
}

# Print file outputs from action responses when present.
print_action_file_outputs() {
    local -a output_names=()
    local output_name

    mapfile -t output_names < <(jq -r '.data.outputs // {} | to_entries[]? | select(.value != null) | .key' <<< "${DEMO_LAST_ACTION_BODY}")
    if (( ${#output_names[@]} == 0 )); then
        return 0
    fi

    printf 'Result (file outputs):\n'
    for output_name in "${output_names[@]}"; do
        printf '  %s\n' "${output_name}"
        printf '    %-24s %s\n' "id" "$(jq -r --arg n "${output_name}" '.data.outputs[$n].id // ""' <<< "${DEMO_LAST_ACTION_BODY}")"
        printf '    %-24s %s\n' "mime_type" "$(jq -r --arg n "${output_name}" '.data.outputs[$n].mime_type // ""' <<< "${DEMO_LAST_ACTION_BODY}")"
        printf '    %-24s %s\n' "size_bytes" "$(jq -r --arg n "${output_name}" '.data.outputs[$n].size_bytes // ""' <<< "${DEMO_LAST_ACTION_BODY}")"
        printf '    %-24s %s\n' "sha256" "$(jq -r --arg n "${output_name}" '.data.outputs[$n].sha256 // ""' <<< "${DEMO_LAST_ACTION_BODY}")"
    done
}

# Print concise action output and optional verbose execution metadata.
print_action_result() {
    local action_id="${1:?action ID is required}"
    local has_stdout=false
    local has_file_outputs=false

    printf 'Action:\n'
    printf '  %s\n\n' "${action_id}"

    if is_non_empty "${DEMO_LAST_ACTION_STDOUT}"; then
        has_stdout=true
    fi

    if jq -e '.data.outputs // {} | to_entries[]? | select(.value != null)' >/dev/null 2>&1 <<< "${DEMO_LAST_ACTION_BODY}"; then
        has_file_outputs=true
    fi

    if [[ "${has_stdout}" == "true" ]]; then
        printf 'Result (stdout):\n'
        print_stdout_excerpt "${DEMO_LAST_ACTION_STDOUT}"
    fi

    if [[ "${has_file_outputs}" == "true" ]]; then
        if [[ "${has_stdout}" == "true" ]]; then
            printf '\n'
        fi
        print_action_file_outputs
    fi

    if [[ "${has_stdout}" != "true" && "${has_file_outputs}" != "true" ]]; then
        printf 'Result (stdout):\n'
        print_stdout_excerpt "${DEMO_LAST_ACTION_STDOUT}"
    fi

    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        printf '\nExecution details:\n'
        printf '  %-27s %s\n' "exit_code" "${DEMO_LAST_EXIT_CODE}"
        printf '  %-27s %s\n' "exec_time" "${DEMO_LAST_EXEC_TIME}"
        seg_api_print_json_if_verbose
    fi
}

# Upload the shared demo asset and print selected metadata fields.
upload_demo_asset() {
    local role_label="${1:-demo input}"
    local file_id
    local filename
    local mime_type
    local extension
    local size_bytes
    local sha256

    if ! seg_api_upload_file "${DEMO_ASSET_FILE}"; then
        return 1
    fi

    if ! seg_api_require_success "Upload demo file"; then
        return 1
    fi

    file_id="$(seg_api_extract_file_id_from_upload)"
    filename="$(seg_api_extract_metadata_field "original_filename")"
    mime_type="$(seg_api_extract_metadata_field "mime_type")"
    extension="$(seg_api_extract_metadata_field "extension")"
    size_bytes="$(seg_api_extract_metadata_field "size_bytes")"
    sha256="$(seg_api_extract_metadata_field "sha256")"

    if ! is_non_empty "${file_id}"; then
        error "Upload succeeded but response did not include data.file.id"
        return 1
    fi

    LAST_UPLOADED_FILE_ID="${file_id}"
    track_created_file "${file_id}" "${role_label}"

    printf '\nResult:\n'
    printf '  %-27s %s\n' "file_id" "${file_id}"
    printf '  %-27s %s\n' "original_filename" "${filename}"
    printf '  %-27s %s\n' "mime_type" "${mime_type}"
    printf '  %-27s %s\n' "extension" "${extension}"
    printf '  %-27s %s\n' "size_bytes" "${size_bytes}"
    printf '  %-27s %s\n' "sha256" "${sha256}"

    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        seg_api_print_json_if_verbose
    fi

    return 0
}

# Cleanup only files created during the current demo run.
cleanup_created_files() {
    local deleted_count=0
    local failed_count=0
    local file_id
    local role_label

    if (( ${#CREATED_FILE_IDS[@]} == 0 )); then
        return 0
    fi

    section "Demo Cleanup"

    if [[ "${KEEP_FILES:-false}" == "true" ]]; then
        info "Keeping SEG-managed files created by this demo."
        for file_id in "${CREATED_FILE_IDS[@]}"; do
            role_label="${CREATED_FILE_ROLES["${file_id}"]-demo output}"
            printf '  %-27s %s (%s)\n' "kept" "${file_id}" "${role_label}"
        done
        return 0
    fi

    for file_id in "${CREATED_FILE_IDS[@]}"; do
        role_label="${CREATED_FILE_ROLES["${file_id}"]-demo output}"
        if seg_api_delete_file "${file_id}" "true" && [[ "${SEG_API_LAST_STATUS}" =~ ^2[0-9]{2}$ ]] && jq -e '.success == true' >/dev/null 2>&1 <<< "${SEG_API_LAST_BODY}"; then
            deleted_count=$((deleted_count + 1))
            printf '  %-27s %s (%s)\n' "deleted" "${file_id}" "${role_label}"
        else
            failed_count=$((failed_count + 1))
            warn "Failed to delete SEG-managed file ${file_id} (role: ${role_label}, HTTP ${SEG_API_LAST_STATUS})."
        fi
    done

    printf '\nCleanup summary:\n'
    printf '  %-27s %s\n' "deleted" "${deleted_count}"
    printf '  %-27s %s\n' "delete failures" "${failed_count}"
    return 0
}
