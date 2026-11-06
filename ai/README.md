# AkkudoktorEOS AI Development Sandbox

Isolated Docker environment for AI-assisted development (OpenCode + Claude Code + Gwen)
against the local AkkudoktorEOS repository.

## Layout

- `compose.yaml` – service definition (bind-mounts the repo root to `/workspace`)
- `Dockerfile` – Debian slim + Python 3.13 + uv + Node.js + OpenCode + Claude Code + gh
- `entrypoint.sh` – container init (exec-bit stripping in `$HOME`, read-only warning about active git hooks, uv cache, git config, gh credential helper); installed to `/usr/local/bin` in the image, outside the `ai-home` volume
- `.env.example` – template, copied to `.env` on first run
- `run.sh` / `run.ps1` / `run.bat` – entry points (prerequisite checks + launch)

## Usage

From the repository root:

- Linux/macOS: `chmod +x ai/run.sh && ./ai/run.sh`
- Windows PowerShell: `.\ai\run.ps1`
- Windows CMD: `ai\run.bat`

Inside the container (working dir `/workspace`):

opencode auth login # or /connect in the TUI
claude # then /login on first run
qwen

gh auth login # then gh auth setup-git

All auth state persists in the `akkudoktor-ai-home` Docker volume. The local
repository is bind-mounted at `/workspace`, so AI-made changes are visible on
the host immediately.

## Security notes

- No `privileged` mode, no Docker socket, no host home mount.
- Container user matches the host UID/GID (Linux/macOS) so file ownership is preserved.
- Credentials are never written to git or to `compose.yaml`; they live only
  in the persistent home volume via each tool's own login flow.
