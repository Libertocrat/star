#!/usr/bin/env bash
set -Eeuo pipefail

STAR_API_HELPER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Source common helpers once to avoid readonly redefinition when already loaded.
if [[ -z "${STAR_COMMON_DIR:-}" ]]; then
    # shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
    source "${STAR_API_HELPER_DIR}/common.sh"
fi

# API client state shared across helper calls.
STAR_API_BASE_URL=""
STAR_API_TOKEN=""
STAR_API_VERBOSE=false
STAR_API_LAST_STATUS=""
STAR_API_LAST_BODY=""

# Initialize API helper state once runtime URL and token are available.
star_api_init() {
    local base_url="${1:?base URL is required}"
    local token="${2:?API token is required}"
    local verbose_mode="${3:-false}"

    STAR_API_BASE_URL="${base_url%/}"
    STAR_API_TOKEN="${token}"
    STAR_API_VERBOSE="${verbose_mode}"
}

# Print a copy-paste friendly curl preview for traceable demo output.
star_api_print_request() {
    local method="${1:?HTTP method is required}"
    local path="${2:?path is required}"
    local body_type="${3:-none}"
    local body_value="${4:-}"
    local compact_json

    printf 'Request:\n'
    printf '  curl -sS -X %s "%s%s" \\\n' "${method}" "${STAR_API_BASE_URL}" "${path}"
    # shellcheck disable=SC2016
    # Intentionally print a literal shell variable for copy-paste examples.
    printf "    -H \"Authorization: Bearer %s\"" '$STAR_API_TOKEN'

    case "${body_type}" in
        json)
            if compact_json="$(jq -c . <<< "${body_value}" 2> /dev/null)"; then
                printf ' \\\n'
                printf '    -H "Content-Type: application/json" \\\n'
                printf "    --data-raw '%s'\n" "${compact_json}"
            else
                printf ' \\\n'
                printf '    -H "Content-Type: application/json" \\\n'
                printf "    --data-raw '%s'\n" "${body_value}"
            fi
            ;;
        form-file)
            printf ' \\\n'
            printf '    -F "file=@%s"\n' "$(path_relative_to_pwd "${body_value}")"
            ;;
        *)
            printf '\n'
            ;;
    esac
}

# Capture HTTP status and response body from curl while cleaning temp files.
_star_api_capture_response() {
    local response_file
    local status
    local curl_exit=0

    response_file="$(mktemp)" || {
        error "Failed to allocate temporary response file."
        return 1
    }

    # Ensure temporary response files are always deleted on early returns.
    trap 'rm -f -- "${response_file}"' RETURN

    if status="$(curl -sS -o "${response_file}" -w '%{http_code}' "$@")"; then
        :
    else
        curl_exit=$?
        STAR_API_LAST_STATUS="000"
        STAR_API_LAST_BODY=""
        if [[ -f "${response_file}" ]]; then
            STAR_API_LAST_BODY="$(< "${response_file}")"
        fi
        error "HTTP request failed before a valid response was received."
        trap - RETURN
        rm -f -- "${response_file}"
        return "${curl_exit}"
    fi

    STAR_API_LAST_STATUS="${status}"
    STAR_API_LAST_BODY=""
    if [[ -f "${response_file}" ]]; then
        STAR_API_LAST_BODY="$(< "${response_file}")"
    fi

    trap - RETURN
    rm -f -- "${response_file}"
    return 0
}

# Execute an authenticated GET request against a STAR API path.
star_api_get() {
    local path="${1:?path is required}"

    star_api_print_request "GET" "${path}"
    _star_api_capture_response \
        -X GET \
        -H "Authorization: Bearer ${STAR_API_TOKEN}" \
        "${STAR_API_BASE_URL}${path}"
}

# Execute an authenticated JSON POST request against a STAR API path.
star_api_post_json() {
    local path="${1:?path is required}"
    local json_payload="${2:?JSON payload is required}"

    star_api_print_request "POST" "${path}" "json" "${json_payload}"
    _star_api_capture_response \
        -X POST \
        -H "Authorization: Bearer ${STAR_API_TOKEN}" \
        -H 'Content-Type: application/json' \
        --data-raw "${json_payload}" \
        "${STAR_API_BASE_URL}${path}"
}

# Upload a local file as multipart/form-data through the STAR Files API.
star_api_upload_file() {
    local file_path="${1:?file path is required}"

    ensure_file_exists "${file_path}" "demo input file" || return 1

    star_api_print_request "POST" "/v1/files" "form-file" "${file_path}"
    _star_api_capture_response \
        -X POST \
        -H "Authorization: Bearer ${STAR_API_TOKEN}" \
        -F "file=@${file_path}" \
        "${STAR_API_BASE_URL}/v1/files"
}

# Delete a STAR-managed file by UUID.
star_api_delete_file() {
    local file_id="${1:?file ID is required}"
    local quiet_mode="${2:-false}"

    if [[ "${quiet_mode}" != "true" ]]; then
        star_api_print_request "DELETE" "/v1/files/${file_id}"
    fi

    _star_api_capture_response \
        -X DELETE \
        -H "Authorization: Bearer ${STAR_API_TOKEN}" \
        "${STAR_API_BASE_URL}/v1/files/${file_id}"
}

# Require successful API envelope semantics and print actionable diagnostics.
star_api_require_success() {
    local context="${1:-Request}"
    local star_code="UNKNOWN_ERROR"
    local star_message="No STAR error message available."

    if [[ ! "${STAR_API_LAST_STATUS}" =~ ^2[0-9]{2}$ ]]; then
        if jq -e . > /dev/null 2>&1 <<< "${STAR_API_LAST_BODY}"; then
            star_code="$(jq -r '.error.code // "UNKNOWN_ERROR"' <<< "${STAR_API_LAST_BODY}")"
            star_message="$(jq -r '.error.message // "No STAR error message available."' <<< "${STAR_API_LAST_BODY}")"
        fi

        error "${context} failed."
        printf 'Request failed:\n' >&2
        printf '  HTTP status: %s\n' "${STAR_API_LAST_STATUS}" >&2
        printf '  STAR error: %s\n' "${star_code}" >&2
        printf '  Message: %s\n' "${star_message}" >&2
        star_api_print_json_if_verbose
        return 1
    fi

    if ! jq -e '.success == true' > /dev/null 2>&1 <<< "${STAR_API_LAST_BODY}"; then
        star_code="$(jq -r '.error.code // "UNKNOWN_ERROR"' <<< "${STAR_API_LAST_BODY}" 2> /dev/null || printf 'UNKNOWN_ERROR')"
        star_message="$(jq -r '.error.message // "No STAR error message available."' <<< "${STAR_API_LAST_BODY}" 2> /dev/null || printf 'No STAR error message available.')"
        error "${context} failed."
        printf 'Request failed:\n' >&2
        printf '  HTTP status: %s\n' "${STAR_API_LAST_STATUS}" >&2
        printf '  STAR error: %s\n' "${star_code}" >&2
        printf '  Message: %s\n' "${star_message}" >&2
        star_api_print_json_if_verbose
        return 1
    fi

    return 0
}

# Run jq against the most recent API response body.
star_api_jq() {
    local jq_filter="${1:?jq filter is required}"
    jq -r "${jq_filter}" <<< "${STAR_API_LAST_BODY}"
}

# Pretty-print the last API JSON payload only when verbose mode is enabled.
star_api_print_json_if_verbose() {
    if [[ "${STAR_API_VERBOSE}" != "true" ]]; then
        return 0
    fi

    printf 'Full response JSON:\n'
    if jq . > /dev/null 2>&1 <<< "${STAR_API_LAST_BODY}"; then
        jq . <<< "${STAR_API_LAST_BODY}"
    else
        printf '%s\n' "${STAR_API_LAST_BODY}"
    fi
}

# Extract uploaded file ID from a successful POST /v1/files response.
star_api_extract_file_id_from_upload() {
    jq -r '.data.file.id // empty' <<< "${STAR_API_LAST_BODY}"
}

# Extract a metadata field from .data.file in the latest response.
star_api_extract_metadata_field() {
    local field_name="${1:?field name is required}"
    jq -r --arg field "${field_name}" '.data.file[$field] // empty' <<< "${STAR_API_LAST_BODY}"
}

# Extract a file metadata field from .data.outputs.<name> in the latest response.
star_api_extract_output_file_field() {
    local output_name="${1:?output name is required}"
    local field_name="${2:?field name is required}"

    jq -r --arg output "${output_name}" --arg field "${field_name}" \
        '.data.outputs[$output][$field] // empty' <<< "${STAR_API_LAST_BODY}"
}
