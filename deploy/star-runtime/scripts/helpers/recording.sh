#!/usr/bin/env bash

RECORDING_HELPER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${STAR_COMMON_DIR:-}" ]]; then
    # shellcheck source=deploy/star-runtime/scripts/helpers/common.sh
    source "${RECORDING_HELPER_DIR}/common.sh"
fi

# Return success when a value should be treated as truthy for recording helpers.
recording_value_is_truthy() {
    local raw_value="${1-}"
    local normalized="${raw_value}"

    if declare -F trim >/dev/null 2>&1; then
        normalized="$(trim "${normalized}")"
    fi
    normalized="${normalized,,}"

    case "${normalized}" in
        1|true|yes|y|on)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Return success when recording mode is explicitly enabled.
recording_mode_enabled() {
    recording_value_is_truthy "${STAR_REC_MODE:-}"
}

# Resolve recording pause duration in milliseconds.
recording_pause_ms() {
    local raw_pause="${STAR_REC_PAUSE_MS:-}"
    local normalized="${raw_pause}"

    if declare -F trim >/dev/null 2>&1; then
        normalized="$(trim "${normalized}")"
    fi

    if [[ "${normalized}" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "${normalized}"
        return 0
    fi

    printf '1000\n'
}

# Sleep for the provided milliseconds, or the resolved default when omitted.
recording_sleep_ms() {
    local raw_pause="${1-}"
    local pause_ms="${raw_pause}"
    local seconds

    if declare -F trim >/dev/null 2>&1; then
        pause_ms="$(trim "${pause_ms}")"
    fi

    if [[ -z "${pause_ms}" || ! "${pause_ms}" =~ ^[0-9]+$ ]]; then
        pause_ms="$(recording_pause_ms)"
    fi

    if (( pause_ms == 0 )); then
        return 0
    fi

    seconds="$(printf '%s.%03d' "$((pause_ms / 1000))" "$((pause_ms % 1000))")"
    sleep "${seconds}"
}

# Clear visible terminal content and request scrollback clear when supported.
recording_clear_tty() {
    [[ -t 1 ]] || return 0
    printf '\033[H\033[2J\033[3J'
}

# Pause and optionally clear TTY for cleaner recording transitions.
recording_transition() {
    local pause_ms="${1-}"
    local clear_tty="${2:-false}"

    if ! recording_mode_enabled; then
        return 0
    fi

    recording_sleep_ms "${pause_ms}"

    if recording_value_is_truthy "${clear_tty}"; then
        recording_clear_tty
        recording_sleep_ms 200
    fi
}
