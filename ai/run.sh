#!/usr/bin/env bash
# ai/run.sh
# Entry point for the AkkudoktorEOS AI development sandbox (Linux/macOS).
#
# Hardened launcher:
#   - Fails closed if the compose file is missing any sandbox control.
#   - Rejects command-line flags that would weaken isolation
#     (--privileged, --cap-add, --device, --pid=host, -v /var/run/docker.sock, ...).
#   - Only forwards arguments after `--` as the in-container command.
#   - Requires every published port to bind to 127.0.0.1 (loopback on the host).
#   - Optional rebuild (AI_SANDBOX_REBUILD=1); cached image otherwise.
#   - Regenerates ai/.env atomically with HOST_UID/HOST_GID and mode 0600.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="ai/compose.yaml"
ENV_FILE="ai/.env"
ENV_EXAMPLE="ai/.env.example"

trap 'echo "ERROR: ai/run.sh failed at line $LINENO (exit $?)." >&2' ERR

# ---------------------------------------------------------------------------
# 0. Reject CLI flags that weaken isolation.
#    Forwarded *container* commands must come after `--`.
# ---------------------------------------------------------------------------
FORBIDDEN_ARGS=(
    --privileged
    --cap-add
    --device
    --security-opt
    --pid
    --ipc
    --network
    --net
    --userns
    --uts
    --volume
    -v
    --mount
    --volumes-from
    --group-add
    --runtime
    --publish
    -p
)

reject_forbidden() {
    local arg forbidden
    for arg in "$@"; do
        for forbidden in "${FORBIDDEN_ARGS[@]}"; do
            if [[ "$arg" == "$forbidden" || "$arg" == "$forbidden="* ]]; then
                echo "ERROR: refusing to pass '$arg' to docker compose run." >&2
                echo "       This flag can weaken the sandbox and is not allowed." >&2
                exit 2
            fi
        done
    done
}

reject_forbidden "$@"

EXTRA_CMD=()
if [[ $# -gt 0 ]]; then
    if [[ "$1" != "--" ]]; then
        echo "ERROR: extra arguments must be supplied after '--' as the in-container command." >&2
        echo "       Example: ./ai/run.sh -- bash -lc 'pytest -q'" >&2
        exit 2
    fi
    shift
    EXTRA_CMD=("$@")
fi

# ---------------------------------------------------------------------------
# 1. Prerequisites.
# ---------------------------------------------------------------------------
echo "==> Checking prerequisites..."

command -v docker >/dev/null || { echo "ERROR: docker not installed." >&2; exit 1; }
docker compose version >/dev/null 2>&1 \
    || { echo "ERROR: docker compose v2 not available." >&2; exit 1; }

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon not reachable." >&2
    echo "       - Is the daemon running?         (systemctl status docker)" >&2
    echo "       - Is your user in 'docker' group? (groups)" >&2
    exit 1
fi

[[ -f pyproject.toml ]] \
    || { echo "ERROR: pyproject.toml not found; run from inside the repo." >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] \
    || { echo "ERROR: $COMPOSE_FILE missing." >&2; exit 1; }

# ---------------------------------------------------------------------------
# 2. Verify sandbox controls in compose.yaml. Fails closed.
# ---------------------------------------------------------------------------
echo "==> Verifying sandbox hardening in $COMPOSE_FILE ..."

compose_text="$(cat "$COMPOSE_FILE")"

require_in_compose() {
    local needle="$1" why="$2"
    if ! grep -Eq -- "$needle" <<<"$compose_text"; then
        echo "ERROR: compose.yaml does not contain: $needle" >&2
        echo "       $why" >&2
        exit 3
    fi
}

require_in_compose 'cap_drop:[[:space:]]*$|cap_drop:[[:space:]]*\[' 'Missing cap_drop (must drop ALL).'
require_in_compose '-\s*"?ALL"?|\[[[:space:]]*"?ALL"?[[:space:]]*\]' 'cap_drop must include ALL.'
require_in_compose 'no-new-privileges:true|no-new-privileges:[[:space:]]*true' 'Missing no-new-privileges:true.'
require_in_compose 'read_only:[[:space:]]*true' 'Missing read_only:true.'

# The docker socket must never be mounted.
if grep -Eq '/var/run/docker\.sock' <<<"$compose_text"; then
    echo "ERROR: compose.yaml mounts the Docker socket. This is an escape vector." >&2
    exit 3
fi

# privileged must be false or absent.
if grep -Eq 'privileged:[[:space:]]*true' <<<"$compose_text"; then
    echo "ERROR: compose.yaml sets privileged:true; refusing to start." >&2
    exit 3
fi

# network_mode: host must never be used.
if grep -Eq 'network_mode:[[:space:]]*"?host"?' <<<"$compose_text"; then
    echo "ERROR: compose.yaml sets network_mode: host; refusing to start." >&2
    exit 3
fi

# Every published port MUST bind to 127.0.0.1. Ports are allowed here because
# the sandbox is used for local testing; the constraint is that nothing inside
# becomes reachable from the LAN/VPN.
if grep -Eq '^[[:space:]]*ports:' <<<"$compose_text"; then
    if ! awk '
        /^[[:space:]]*ports:[[:space:]]*$/ { inports=1; next }
        inports && /^[[:space:]]*-/ {
            line=$0
            sub(/^[[:space:]]*-[[:space:]]*/, "", line)
            gsub(/["'"'"']/, "", line)
            if (line !~ /^127\.0\.0\.1:/) {
                print "OFFENDING: " line > "/dev/stderr"
                bad=1
            }
            next
        }
        inports && !/^[[:space:]]*-/ && !/^[[:space:]]*$/ { inports=0 }
        END { exit bad ? 1 : 0 }
    ' <<<"$compose_text"; then
        echo "ERROR: compose.yaml publishes a port on a non-loopback interface." >&2
        echo "       Every entry under 'ports:' must start with '127.0.0.1:'." >&2
        echo "       Change e.g. \"8503:8503\" to \"127.0.0.1:8503:8503\"." >&2
        exit 3
    fi
fi

# ---------------------------------------------------------------------------
# 3. Regenerate ai/.env atomically (mode 0600). Do not leak .bak files.
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$ENV_EXAMPLE" ]]; then
        echo "==> Creating $ENV_FILE from $ENV_EXAMPLE"
        install -m 0600 "$ENV_EXAMPLE" "$ENV_FILE"
    else
        echo "==> Creating empty $ENV_FILE"
        : >"$ENV_FILE"
        chmod 0600 "$ENV_FILE"
    fi
fi
chmod 0600 "$ENV_FILE"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "$tmp_env"' EXIT
{
    grep -Ev '^HOST_(UID|GID)=' "$ENV_FILE" || true
    printf 'HOST_UID=%s\n' "$HOST_UID"
    printf 'HOST_GID=%s\n' "$HOST_GID"
} >"$tmp_env"
mv -f "$tmp_env" "$ENV_FILE"
chmod 0600 "$ENV_FILE"
trap 'echo "ERROR: ai/run.sh failed at line $LINENO (exit $?)." >&2' ERR

# ---------------------------------------------------------------------------
# 4. Rebuild is opt-in. A tampered Dockerfile/entrypoint cannot be silently
#    rebuilt by a wrapper invocation.
# ---------------------------------------------------------------------------
BUILD_FLAG=()
if [[ "${AI_SANDBOX_REBUILD:-0}" == "1" ]]; then
    echo "==> Rebuilding image (AI_SANDBOX_REBUILD=1)"
    BUILD_FLAG=(--build)
else
    echo "==> Using cached image (set AI_SANDBOX_REBUILD=1 to rebuild)"
fi

# ---------------------------------------------------------------------------
# 5. Run. Use exec so signals reach docker compose directly.
# ---------------------------------------------------------------------------
echo "==> Starting AI dev sandbox (UID=$HOST_UID, GID=$HOST_GID)..."

exec docker compose -f "$COMPOSE_FILE" run --rm "${BUILD_FLAG[@]}" ai-dev "${EXTRA_CMD[@]}"
