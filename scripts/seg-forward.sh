#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# seg-forward.sh
#
# Development helper that creates a temporary local port-forward to a running
# SEG container. This allows accessing the containerized SEG service from the
# host without exposing ports in docker-compose.
#
# Example use cases:
# - Open Swagger UI (/docs)
# - Run curl or Postman requests
# - Execute integration tests from the host
# - Debug runtime behavior against the containerized service
#
# The forward remains active until the proxy container exits (CTRL+C).
#
# Required configuration (via --env-file or exported):
#   - SEG_SHARED_NETWORK
#   - SEG_PORT
#   - COMPOSE_PROJECT_NAME (only when --container is not provided)
#
# Optional overrides:
#   --container <name>   SEG container name (default: auto-detect)
#   --local-port <port>  Local port to expose
#
# Supported flags:
#   --env-file <path>    Load variables from a .env file
#   --dry-run            Print actions without executing
# -----------------------------------------------------------------------------

SCRIPT_NAME="$(basename "$0")"

ENV_FILE=""
DRY_RUN=false

SEG_CONTAINER=""
LOCAL_PORT=""

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------

log()   { echo "[INFO] $*"; }
warn()  { echo "[WARN] $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

run() {
  if $DRY_RUN; then
    printf '[DRY-RUN] '
    printf '%q ' "$@"
    echo
    return 0
  fi

  "$@"
}

is_port_free() {
  local port="$1"

  if ss -ltn "( sport = :$port )" | grep -q "$port"; then
    return 1
  fi

  if docker ps --format '{{.Ports}}' | grep -q ":$port->"; then
    return 1
  fi

  return 0
}

resolve_seg_container() {

  # Case 1: user explicitly passed --container
  if [[ -n "${SEG_CONTAINER:-}" ]]; then
    if ! docker ps --format '{{.Names}}' | grep -qx "$SEG_CONTAINER"; then
      error "Container '${SEG_CONTAINER}' is not running"
    fi
    echo "$SEG_CONTAINER"
    return
  fi

  # Case 2: autodetect via compose prefix
  local prefix="${COMPOSE_PROJECT_NAME}-seg"

  mapfile -t matches < <(
    docker ps --format '{{.Names}}' | grep "^${prefix}"
  )

  if [[ "${#matches[@]}" -eq 0 ]]; then
    error "No running containers found with prefix '${prefix}'"
  fi

  if [[ "${#matches[@]}" -gt 1 ]]; then
    warn "Multiple SEG containers detected:"
    for c in "${matches[@]}"; do
      echo "  $c"
    done
    error "Please specify one with --container"
  fi

  echo "${matches[0]}"
}

usage() {
cat <<EOF
Usage:
  $SCRIPT_NAME [options]

Description:
  Creates a temporary localhost port-forward to a running SEG container.
  This allows accessing the containerized SEG service without exposing
  ports in docker-compose.

Options:
  --env-file <path>     Load configuration from .env
  --container <name>    SEG container name (default: auto-detect)
  --local-port <port>   Local port to expose (auto-detected if omitted)
  --dry-run             Print actions without executing
  -h, --help            Show this help

Examples:

  # Basic usage using .env configuration
  $SCRIPT_NAME --env-file .env

  # Force a specific local port
  $SCRIPT_NAME --env-file .env --local-port 8081

  # Forward to a specific SEG container
  $SCRIPT_NAME --env-file .env --container seg-1

  # Preview actions without running the proxy
  $SCRIPT_NAME --env-file .env --dry-run

  # Use exported environment variables instead of .env
  export SEG_SHARED_NETWORK=docker-network
  export SEG_PORT=8080
  export COMPOSE_PROJECT_NAME=myproject
  $SCRIPT_NAME

Access the API locally at:

  http://localhost:<PORT>/docs
  http://localhost:<PORT>/health
  http://localhost:<PORT>/openapi.json

EOF
}

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------

for _a in "$@"; do
  if [[ "$_a" == "-h" || "$_a" == "--help" ]]; then
    if [[ $# -gt 1 ]]; then
      error "--help/-h must be used alone"
    fi
    usage
    exit 0
  fi
done

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      shift
      [[ $# -eq 0 ]] && error "--env-file requires a path"
      ENV_FILE="$1"
      shift
      ;;
    --container)
      shift
      [[ $# -eq 0 ]] && error "--container requires a container name"
      SEG_CONTAINER="$1"
      shift
      ;;
    --local-port)
      shift
      [[ $# -eq 0 ]] && error "--local-port requires a port number"
      LOCAL_PORT="$1"
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      error "Unknown argument: $1"
      ;;
  esac
done

# -----------------------------------------------------------------------------
# Preconditions
# -----------------------------------------------------------------------------

command -v docker >/dev/null 2>&1 || error "Docker CLI not found"

REQUIRED_VARS=(
  SEG_SHARED_NETWORK
  SEG_PORT
)

if [[ -z "${SEG_CONTAINER:-}" ]]; then
  REQUIRED_VARS+=(COMPOSE_PROJECT_NAME)
fi

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || error "Env file not found: $ENV_FILE"
  log "Loading environment from $ENV_FILE"

  for var in "${REQUIRED_VARS[@]}"; do
    unset "$var"
  done

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  log "No --env-file provided; expecting required variables exported"
fi

missing_vars=()

for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing_vars+=("$var")
  fi
done

if (( ${#missing_vars[@]} > 0 )); then
  error "Missing required variables: ${missing_vars[*]}"
fi

# -----------------------------------------------------------------------------
# Resolve SEG container
# -----------------------------------------------------------------------------

SEG_CONTAINER="$(resolve_seg_container)"

log "Resolved SEG container: $SEG_CONTAINER"

# -----------------------------------------------------------------------------
# Detect free local port
# -----------------------------------------------------------------------------

if [[ -n "$LOCAL_PORT" ]]; then
  if ! [[ "$LOCAL_PORT" =~ ^[0-9]+$ ]] || (( LOCAL_PORT < 1 || LOCAL_PORT > 65535 )); then
    error "--local-port must be an integer between 1 and 65535"
  fi
fi

if [[ -z "$LOCAL_PORT" ]]; then
  for p in {8081..8099}; do
    if is_port_free "$p"; then
      LOCAL_PORT="$p"
      break
    fi
  done
fi

[[ -z "$LOCAL_PORT" ]] && error "Could not find a free local port"

# -----------------------------------------------------------------------------
# Start forward proxy
# -----------------------------------------------------------------------------

log "SEG container : $SEG_CONTAINER"
log "SEG port      : $SEG_PORT"
log "Local port    : $LOCAL_PORT"
log "Docker network: $SEG_SHARED_NETWORK"

echo
log "Forwarding http://localhost:${LOCAL_PORT} -> ${SEG_CONTAINER}:${SEG_PORT}"
log "Press CTRL+C to stop"
echo
echo "Swagger UI:"
echo "  http://localhost:${LOCAL_PORT}/docs"
echo
echo "OpenAPI schema:"
echo "  http://localhost:${LOCAL_PORT}/openapi.json"
echo
echo "Health endpoint:"
echo "  http://localhost:${LOCAL_PORT}/health"
echo

run docker run --rm \
  --network "$SEG_SHARED_NETWORK" \
  -p "127.0.0.1:${LOCAL_PORT}:${SEG_PORT}" \
  alpine/socat \
  "TCP-LISTEN:${SEG_PORT},fork,reuseaddr" \
  "TCP:${SEG_CONTAINER}:${SEG_PORT}"
