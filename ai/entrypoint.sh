#!/usr/bin/env bash
# ai/entrypoint.sh
# Initializes the AI sandbox container on startup, then execs the command.
# Installed to /usr/local/bin (image layer, root-owned) so it cannot be
# shadowed or modified via the persistent /home/dev volume.
set -euo pipefail

# ---------------------------------------------------------------------------
# Hardening
# ---------------------------------------------------------------------------

# Belt-and-braces: strip exec bits from $HOME except from a short allowlist.
# Runs as the non-root `dev` user, so it can only touch its own files.
strip_exec_bits() {
    local keep=(
        "/home/dev/.local/bin/uv"
        "/home/dev/.local/bin/uvx"
        # add any legitimately-executable per-user binaries here
    )
    find /home/dev \
        -path /home/dev/.cache -prune -o \
        -path /home/dev/.local/share/uv -prune -o \
        -type f -perm /111 -print0 2>/dev/null |
    while IFS= read -r -d '' f; do
        for k in "${keep[@]}"; do
            [[ "$f" == "$k" ]] && continue 2
        done
        chmod a-x -- "$f" 2>/dev/null || true
    done
}

strip_exec_bits

# Never allow a persistent shell startup file to inject commands.
for f in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" \
         "$HOME/.zshrc"    "$HOME/.zprofile"  "$HOME/.zshenv"; do
    [[ -f "$f" ]] && chmod u+w,go-w -- "$f" || true
done

# NOTE: /workspace is the user's own repository. The entrypoint must never
# modify it — in particular .git/hooks is left untouched (pre-commit etc.).
# Instead, warn (read-only) about active hooks so unexpected ones get noticed:
# the agent can write to the repo, and a planted hook would run on the host.
warn_git_hooks() {
    local hooks_dir
    # Honors core.hooksPath and worktrees; falls back to the default location.
    hooks_dir="$(git -C /workspace rev-parse --path-format=absolute --git-path hooks 2>/dev/null)" \
        || hooks_dir="/workspace/.git/hooks"
    [[ -d "$hooks_dir" ]] || return 0

    local hooks=()
    while IFS= read -r -d '' f; do
        hooks+=("$f")
    done < <(find "$hooks_dir" -maxdepth 1 -type f ! -name '*.sample' -print0 2>/dev/null)
    [[ ${#hooks[@]} -gt 0 ]] || return 0

    {
        echo "WARNING: active git hooks found in $hooks_dir"
        echo "         They run on the host too. Verify they are expected:"
        local f
        for f in "${hooks[@]}"; do
            printf '           %s  (modified %s)\n' \
                "$(basename -- "$f")" \
                "$(date -r "$f" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '?')"
        done
    } >&2
}

warn_git_hooks || true

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

# Prepare a persistent uv cache inside the home volume.
export UV_CACHE_DIR="/home/dev/.cache/uv"
mkdir -p "$UV_CACHE_DIR"

# Configure git identity if provided via environment.
if [[ -n "${GIT_USER_NAME:-}" ]]; then
    git config --global user.name "$GIT_USER_NAME"
fi
if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
    git config --global user.email "$GIT_USER_EMAIL"
fi

# If gh is already authenticated, wire git to use it as a credential helper.
if gh auth status &>/dev/null; then
    gh auth setup-git || true
fi

exec "$@"
