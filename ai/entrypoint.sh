#!/usr/bin/env bash
# ai/entrypoint.sh
# Initializes the AI sandbox container on startup, then execs the command.
# Installed to /usr/local/bin (image layer, root-owned) so it cannot be
# shadowed or modified via the persistent /home/dev volume.
set -euo pipefail

# ---------------------------------------------------------------------------
# Hardening
# ---------------------------------------------------------------------------

# The home directory is a persistent volume, so files written in one session
# are still there in the next. The steps below remove the most direct ways to
# make such a file run automatically. They do not cover everything (agent
# settings and hooks in ~/.claude, ~/.gitconfig, the venv in ~/.cache):
# reset the volume (docker volume rm akkudoktor-ai-home) if a session may have
# been misled.

# Strip exec bits from $HOME, except from the uv cache and the virtual
# environment in ~/.cache, which must stay executable. A program planted in
# e.g. ~/.local/bin (first on PATH) then does not run in the next session.
# Runs as the non-root `dev` user, so it can only touch its own files.
strip_exec_bits() {
    find /home/dev \
        -path /home/dev/.cache -prune -o \
        -path /home/dev/.local/share/uv -prune -o \
        -type f -perm /111 -exec chmod a-x -- {} + 2>/dev/null || true
}

strip_exec_bits

# Reset the shell startup files on every start, so a startup file written in
# a previous session cannot inject commands into the next shell.
# Customizations of these files do not persist.
reset_shell_startup_files() {
    local f
    for f in .bashrc .profile .bash_logout; do
        # Remove first: cp onto a planted symlink would write to its target.
        rm -f -- "$HOME/$f"
        if [[ -f "/etc/skel/$f" ]]; then
            cp -- "/etc/skel/$f" "$HOME/$f"
        fi
    done
    rm -f -- "$HOME/.bash_profile" "$HOME/.bash_login" \
             "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.zshenv" "$HOME/.zlogin"
}

reset_shell_startup_files

# NOTE: /workspace is the user's own repository. The entrypoint must never
# modify it — in particular .git/hooks is left untouched (pre-commit etc.).
# .git is writable in the sandbox, so a previous session may have added hooks.
# Warn about the active hooks, so they get noticed. This is no protection: the
# review checklist in docs/develop/ai.md checks .git on the host.
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
