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

# Source demo-common helpers only once.
if ! command -v reset_demo_state >/dev/null 2>&1; then
    # shellcheck source=deploy/star-runtime/scripts/demos/demo-common.sh
    source "${STAR_SCRIPTS_DIR}/demos/demo-common.sh"
fi

# Demo 1: Upload/list/metadata walkthrough for Files API.
run_demo_files() {
    local file_id
    local listed_count
    local -a listed_items=()
    local item

    section "Files API Walkthrough"
    printf 'What this demo shows:\n'
    printf '  Upload file metadata, retrieve details, list recent files, and cleanup by file ID.\n\n'

    demo_step "Upload shared demo text file"
    upload_demo_asset "files demo upload" || return 1
    file_id="${LAST_UPLOADED_FILE_ID}"
    demo_pause

    demo_step "Fetch metadata from GET /v1/files/{id}"
    star_api_get "/v1/files/${file_id}" || return 1
    star_api_require_success "Get file metadata" || return 1

    printf '\nResult:\n'
    printf '  %-27s %s\n' "file_id" "$(star_api_extract_metadata_field "id")"
    printf '  %-27s %s\n' "original_filename" "$(star_api_extract_metadata_field "original_filename")"
    printf '  %-27s %s\n' "mime_type" "$(star_api_extract_metadata_field "mime_type")"
    printf '  %-27s %s\n' "extension" "$(star_api_extract_metadata_field "extension")"
    printf '  %-27s %s\n' "size_bytes" "$(star_api_extract_metadata_field "size_bytes")"
    printf '  %-27s %s\n' "sha256" "$(star_api_extract_metadata_field "sha256")"
    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        star_api_print_json_if_verbose
    fi
    demo_pause

    demo_step "List recent files with GET /v1/files?limit=5&order=desc"
    star_api_get "/v1/files?limit=5&order=desc" || return 1
    star_api_require_success "List files" || return 1

    listed_count="$(jq -r '.data.pagination.count // 0' <<< "${STAR_API_LAST_BODY}")"
    mapfile -t listed_items < <(jq -r '.data.files[]? | "\(.id)  \(.original_filename)"' <<< "${STAR_API_LAST_BODY}")

    printf '\nResult:\n'
    printf '  %-27s %s\n' "returned_count" "${listed_count}"
    if (( ${#listed_items[@]} == 0 )); then
        printf '  %-27s %s\n' "items" "(none)"
    else
        printf '  %-27s\n' "items"
        for item in "${listed_items[@]}"; do
            printf '  %s\n' "${item}"
        done
    fi
    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        star_api_print_json_if_verbose
    fi
    demo_pause
}

# Demo 2: Action discovery, filters, and public spec inspection.
run_demo_actions() {
    local module_count
    local action_count
    local tags_joined
    local action_id
    local action_summary
    #local action_tags
    local -a selected_modules=()
    local -a crypto_actions=()
    local -a arg_rows=()
    local -a output_rows=()
    local row

    section "Actions API Walkthrough"
    printf 'What this demo shows:\n'
    printf '  Discover actions, filter by tags, and inspect public action specs.\n\n'

    demo_step "List available actions"
    star_api_get "/v1/actions" || return 1
    star_api_require_success "List actions" || return 1

    module_count="$(jq -r '.data.modules | length' <<< "${STAR_API_LAST_BODY}")"
    action_count="$(jq -r '[.data.modules[]?.actions[]?] | length' <<< "${STAR_API_LAST_BODY}")"
    mapfile -t selected_modules < <(jq -r '[.data.modules[]?.module_id] | unique | .[:8] | .[]?' <<< "${STAR_API_LAST_BODY}")

    printf '\nResult:\n'
    printf '  %-27s %s\n' "module_count" "${module_count}"
    printf '  %-27s %s\n' "action_count" "${action_count}"
    if (( ${#selected_modules[@]} > 0 )); then
        printf '  %-27s\n' "registered_modules"
        for row in "${selected_modules[@]}"; do
            printf '    %s\n' "${row}"
        done
    fi
    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        star_api_print_json_if_verbose
    fi
    demo_pause

    demo_step "Filter actions by crypto tag"
    star_api_get "/v1/actions?tags=crypto" || return 1
    star_api_require_success "List crypto actions" || return 1
    mapfile -t crypto_actions < <(jq -r '[.data.modules[]?.actions[]? | {id:.action_id,summary:(.summary // ""),tags:(.tags // [])}] | unique_by(.id) | .[]? | @base64' <<< "${STAR_API_LAST_BODY}")

    printf '\nActions found:\n'
    if (( ${#crypto_actions[@]} == 0 )); then
        printf '  None\n'
    else
        #printf '  %-27s\n' "crypto_actions"
        for row in "${crypto_actions[@]}"; do
            action_id="$(printf '%s' "${row}" | base64 -d | jq -r '.id')"
            action_summary="$(printf '%s' "${row}" | base64 -d | jq -r '.summary')"
            #action_tags="$(printf '%s' "${row}" | base64 -d | jq -r '.tags | join(",")')"
            printf '  %s\n' "${action_id}"
            if is_non_empty "${action_summary}"; then
                printf '    %-12s %s\n' "summary" "${action_summary}"
            fi
            #if is_non_empty "${action_tags}"; then
                #printf '      %-12s %s\n' "tags" "${action_tags}"
            #fi
        done
    fi
    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        star_api_print_json_if_verbose
    fi
    demo_pause

    demo_step "Read public specification for base.crypto.encrypt_file_aes256"
    star_api_get "/v1/actions/base.crypto.encrypt_file_aes256" || return 1
    if ! star_api_require_success "Get action spec base.crypto.encrypt_file_aes256"; then
        if [[ "${STAR_API_LAST_STATUS}" == "404" ]]; then
            error "Required action is not available: base.crypto.encrypt_file_aes256"
            printf 'Check the official STAR image or open Swagger docs:\n' >&2
            printf '  %s\n' "$(docs_url)" >&2
        fi
        return 1
    fi

    tags_joined="$(jq -r '.data.tags | join(",") // ""' <<< "${STAR_API_LAST_BODY}")"
    mapfile -t arg_rows < <(jq -r '.data.args[]? | "name=\(.name // "-") type=\(.type // "-") required=\(.required // false)"' <<< "${STAR_API_LAST_BODY}")
    mapfile -t output_rows < <(jq -r '.data.outputs[]? | "name=\(.name // "-") type=\(.type // "-")"' <<< "${STAR_API_LAST_BODY}")

    printf '\nResult:\n'
    printf '  %-27s %s\n' "action_id" "$(jq -r '.data.action_id // ""' <<< "${STAR_API_LAST_BODY}")"
    printf '  %-27s %s\n' "summary" "$(jq -r '.data.summary // ""' <<< "${STAR_API_LAST_BODY}")"
    printf '  %-27s %s\n' "tags" "${tags_joined}"

    if (( ${#arg_rows[@]} == 0 )); then
        printf '  %-27s %s\n' "args" "(none)"
    else
        printf '  %-27s\n' "args"
        for row in "${arg_rows[@]}"; do
            printf '    %s\n' "${row}"
        done
    fi

    if (( ${#output_rows[@]} == 0 )); then
        printf '  %-27s %s\n' "outputs" "(none)"
    else
        printf '  %-27s\n' "outputs"
        for row in "${output_rows[@]}"; do
            printf '    %s\n' "${row}"
        done
    fi
    if [[ "${VERBOSE_MODE:-false}" == "true" ]]; then
        star_api_print_json_if_verbose
    fi
    demo_pause
}

# Demo 3: Execute allow-listed random generators without files.
run_demo_random() {
    local params

    section "Generate Random Tokens"
    printf 'What this demo shows:\n'
    printf '  Safe execution of allow-listed random actions with structured outputs.\n\n'

    demo_step "Execute base.random.gen_uuid"
    params='{}'
    execute_action "base.random.gen_uuid" "${params}" || return 1
    print_action_result "base.random.gen_uuid"
    demo_pause

    demo_step "Execute base.random.gen_token_hex with bytes=32"
    params='{"bytes":32}'
    execute_action "base.random.gen_token_hex" "${params}" || return 1
    print_action_result "base.random.gen_token_hex"
    demo_pause

    demo_step "Execute base.random.gen_token_base64 with bytes=32"
    params='{"bytes":32}'
    execute_action "base.random.gen_token_base64" "${params}" || return 1
    print_action_result "base.random.gen_token_base64"
    demo_pause
}

# Demo 4: Inspect line/word/char metrics and previews for one managed file.
run_demo_inspect() {
    local file_id
    local params

    section "Measure and Inspect a Text File"
    printf 'What this demo shows:\n'
    printf '  Read-only analysis actions over STAR file IDs instead of local paths.\n\n'

    demo_step "Upload shared demo text file"
    upload_demo_asset "inspect demo upload" || return 1
    file_id="${LAST_UPLOADED_FILE_ID}"
    demo_pause

    demo_step "Run base.analyze.inspect_file_type"
    params="$(jq -cn --arg file_id "${file_id}" '{input_file: $file_id}')"
    execute_action "base.analyze.inspect_file_type" "${params}" || return 1
    print_action_result "base.analyze.inspect_file_type"
    demo_pause

    demo_step "Run base.analyze.count_file_lines"
    execute_action "base.analyze.count_file_lines" "${params}" || return 1
    print_action_result "base.analyze.count_file_lines"
    demo_pause

    demo_step "Run base.analyze.count_file_words"
    execute_action "base.analyze.count_file_words" "${params}" || return 1
    print_action_result "base.analyze.count_file_words"
    demo_pause

    demo_step "Run base.analyze.count_file_chars"
    execute_action "base.analyze.count_file_chars" "${params}" || return 1
    print_action_result "base.analyze.count_file_chars"
    demo_pause

    demo_step "Run base.analyze.preview_file_start with lines=5"
    params="$(jq -cn --arg file_id "${file_id}" '{input_file: $file_id, lines: 5}')"
    execute_action "base.analyze.preview_file_start" "${params}" || return 1
    print_action_result "base.analyze.preview_file_start"
    demo_pause

    demo_step "Run base.analyze.preview_file_end with lines=5"
    execute_action "base.analyze.preview_file_end" "${params}" || return 1
    print_action_result "base.analyze.preview_file_end"
    demo_pause
}

# Demo 5: Demonstrate regex and literal pattern searches on a managed file.
run_demo_search() {
    local file_id
    local -a search_cases=()
    local search_case
    local case_label
    local pattern
    local params

    section "Search Patterns in a Text File"
    printf 'What this demo shows:\n'
    printf '  Controlled regex and exact-phrase search via allow-listed actions.\n\n'

    search_cases=(
        "Email address regex|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}|true"
        "URL regex|https?://[^[:space:]]+|true"
        "Phone number regex|\\+?[0-9][0-9 -]{7,}[0-9]|true"
        "Exact text phrase|arbitrary shell|false"
    )

    demo_step "Upload shared demo text file"
    upload_demo_asset "search demo upload" || return 1
    file_id="${LAST_UPLOADED_FILE_ID}"
    demo_pause

    for search_case in "${search_cases[@]}"; do
        IFS='|' read -r case_label pattern regex_enabled <<< "${search_case}"

        demo_step "Run base.analyze.search_file_lines"
        printf '\nPattern type:\n'
        printf '  %s\n\n' "${case_label}"
        printf 'Pattern:\n'
        printf '  %s\n\n' "${pattern}"

        if [[ "${regex_enabled}" == "true" ]]; then
            params="$(jq -cn --arg file_id "${file_id}" --arg pattern "${pattern}" '{input_file: $file_id, pattern: $pattern, regex: true}')"
        else
            params="$(jq -cn --arg file_id "${file_id}" --arg pattern "${pattern}" '{input_file: $file_id, pattern: $pattern}')"
        fi

        execute_action "base.analyze.search_file_lines" "${params}" || return 1
        print_action_result "base.analyze.search_file_lines"
        demo_pause
    done
}

# Demo 6: Full upload -> encrypt -> decrypt -> verify checksum workflow.
run_demo_encrypt() {
    local original_id
    local original_sha
    local original_mime
    local original_size
    local encrypt_params
    local decrypt_params
    local preview_params
    local inspect_params
    local encrypted_id
    local encrypted_sha
    local encrypted_mime
    local encrypted_size
    local decrypted_id
    local decrypted_sha
    local decrypted_mime
    local decrypted_size
    local encrypted_detected_stdout
    local encrypted_detected_type
    local decrypted_preview
    local sha_match="no"

    section "Encrypt and Decrypt a File"
    printf 'What this demo shows:\n'
    printf '  upload -> preview -> encrypt -> inspect -> decrypt -> verify checksum equality.\n\n'

    demo_step "Upload shared demo text file"
    upload_demo_asset "original file" || return 1
    original_id="${LAST_UPLOADED_FILE_ID}"
    original_sha="$(star_api_extract_metadata_field "sha256")"
    original_mime="$(star_api_extract_metadata_field "mime_type")"
    original_size="$(star_api_extract_metadata_field "size_bytes")"
    demo_pause

    demo_step "Preview first lines of original file"
    preview_params="$(jq -cn --arg file_id "${original_id}" '{input_file: $file_id, lines: 5}')"
    execute_action "base.analyze.preview_file_start" "${preview_params}" || return 1
    print_action_result "base.analyze.preview_file_start"
    demo_pause

    demo_step "Encrypt file with base.crypto.encrypt_file_aes256"
    encrypt_params="$(jq -cn --arg file_id "${original_id}" --arg password "${DEMO_ENCRYPTION_PASSWORD}" '{input_file: $file_id, password: $password}')"
    execute_action "base.crypto.encrypt_file_aes256" "${encrypt_params}" || return 1
    print_action_result "base.crypto.encrypt_file_aes256"

    encrypted_id="$(star_api_extract_output_file_field "encrypted_file" "id")"
    encrypted_sha="$(star_api_extract_output_file_field "encrypted_file" "sha256")"
    encrypted_mime="$(star_api_extract_output_file_field "encrypted_file" "mime_type")"
    encrypted_size="$(star_api_extract_output_file_field "encrypted_file" "size_bytes")"

    if ! is_non_empty "${encrypted_id}"; then
        error "Encrypt action succeeded but encrypted output metadata is missing."
        return 1
    fi

    track_created_file "${encrypted_id}" "encrypted file"
    demo_pause

    demo_step "Inspect encrypted file type"
    inspect_params="$(jq -cn --arg file_id "${encrypted_id}" '{input_file: $file_id}')"
    execute_action "base.analyze.inspect_file_type" "${inspect_params}" || return 1
    print_action_result "base.analyze.inspect_file_type"
    encrypted_detected_stdout="${DEMO_LAST_ACTION_STDOUT}"
    encrypted_detected_type="$(printf '%s\n' "${encrypted_detected_stdout}" | sed -n '1p')"
    demo_pause

    demo_step "Decrypt file with base.crypto.decrypt_file_aes256"
    decrypt_params="$(jq -cn --arg file_id "${encrypted_id}" --arg password "${DEMO_ENCRYPTION_PASSWORD}" '{input_file: $file_id, password: $password}')"
    execute_action "base.crypto.decrypt_file_aes256" "${decrypt_params}" || return 1
    print_action_result "base.crypto.decrypt_file_aes256"

    decrypted_id="$(star_api_extract_output_file_field "decrypted_file" "id")"
    decrypted_sha="$(star_api_extract_output_file_field "decrypted_file" "sha256")"
    decrypted_mime="$(star_api_extract_output_file_field "decrypted_file" "mime_type")"
    decrypted_size="$(star_api_extract_output_file_field "decrypted_file" "size_bytes")"

    if ! is_non_empty "${decrypted_id}"; then
        error "Decrypt action succeeded but decrypted output metadata is missing."
        return 1
    fi

    track_created_file "${decrypted_id}" "decrypted file"
    demo_pause

    demo_step "Preview first lines of decrypted file"
    preview_params="$(jq -cn --arg file_id "${decrypted_id}" '{input_file: $file_id, lines: 5}')"
    execute_action "base.analyze.preview_file_start" "${preview_params}" || return 1
    print_action_result "base.analyze.preview_file_start"
    decrypted_preview="${DEMO_LAST_ACTION_STDOUT}"
    demo_pause

    if [[ "${original_sha}" == "${decrypted_sha}" ]]; then
        sha_match="yes"
    fi

    section "Encryption Verification Summary"

    printf '\nOriginal file:\n'
    printf '  %-27s %s\n' "MIME type" "${original_mime}"
    printf '  %-27s %s\n' "Size bytes" "${original_size}"
    printf '  %-27s %s\n' "SHA-256" "${original_sha}"

    printf '\nEncrypted file:\n'
    printf '  %-27s %s\n' "MIME type" "${encrypted_mime}"
    printf '  %-27s %s\n' "Detected type" "${encrypted_detected_type:-unknown}"
    printf '  %-27s %s\n' "Size bytes" "${encrypted_size}"
    printf '  %-27s %s\n' "SHA-256" "${encrypted_sha}"

    printf '\nDecrypted file:\n'
    printf '  %-27s %s\n' "MIME type" "${decrypted_mime}"
    printf '  %-27s %s\n' "Size bytes" "${decrypted_size}"
    printf '  %-27s %s\n' "SHA-256" "${decrypted_sha}"

    printf '\nIntegrity:\n'
    printf '  %-27s %s\n' "Original == decrypted" "${sha_match}"

    # In recording mode: skip decrypted preview to shorten output
    if [[ "${STAR_REC_MODE:-}" != "1" ]]; then
        printf '\nDecrypted preview:\n'
        print_stdout_excerpt "${decrypted_preview}" 8
    fi

    if [[ "${sha_match}" != "yes" ]]; then
        error "Checksum verification failed for decrypted file."
        return 1
    fi

    demo_pause
}

# Dispatch to one selected demo implementation.
run_selected_demo() {
    case "${SELECTED_DEMO}" in
        files)
            run_demo_files
            ;;
        actions)
            run_demo_actions
            ;;
        random)
            run_demo_random
            ;;
        inspect)
            run_demo_inspect
            ;;
        search)
            run_demo_search
            ;;
        encrypt)
            run_demo_encrypt
            ;;
        *)
            die "Internal error: unsupported demo '${SELECTED_DEMO}'"
            ;;
    esac
}
