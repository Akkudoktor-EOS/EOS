# AkkudoktorEOS AI Development Sandbox

Isolated Docker environment for AI-assisted development (OpenCode + Claude Code + Qwen Code)
against the local AkkudoktorEOS repository.

**The sandbox does not protect your repository from harmful changes by the agent.** It limits
what a misled agent can reach on your machine, but the agent can still write any change into the
repository. Check every change before you run it on the host, open a pull request or update one.
See the security model in `docs/develop/ai.md`.

## Layout

- `compose.yaml` – service definition (bind-mounts the repo root to `/workspace`, with `ai/`
  read-only)
- `Dockerfile` – Debian slim + Python 3.13 + uv + Node.js + OpenCode + Claude Code + Qwen Code + gh,
  with pinned versions
- `entrypoint.sh` – container init (exec-bit stripping and reset of the shell startup files in
  `$HOME`, warning about active git hooks, uv cache, git config, gh credential helper); installed
  to `/usr/local/bin` in the image, outside the `ai-home` volume
- `.env.example` – template, copied to `.env` on first run
- `run.sh` / `run.ps1` / `run.bat` – entry points (prerequisite checks, sanity check of
  `compose.yaml`, launch)
- `skills/` – project rules for the agents, indexed in `AGENTS.md`

## Usage

From the repository root:

- Linux/macOS: `chmod +x ai/run.sh && ./ai/run.sh`
- Windows PowerShell: `.\ai\run.ps1`
- Windows CMD: `ai\run.bat`

Options (environment variables):

- `AI_SANDBOX_REBUILD=1` – rebuild the image (e.g. after bumping the agent CLI versions in
  `Dockerfile`)
- `AI_SANDBOX_PORTS=1` – publish the ports of `compose.yaml` (8503 EOS, 8504 EOSdash) on
  `127.0.0.1` of the host. Off by default, as only one sandbox at a time can publish them.
  Start the servers with `--host 0.0.0.0` inside the container to make them reachable.
- `AI_SANDBOX_SKILLS_RW=1` – make `ai/skills` writable in the sandbox, so the agent can update the
  skills. The rest of `ai/` stays read-only.

A command after `--` runs instead of the shell, e.g. `./ai/run.sh -- claude`. In PowerShell the
`--` is optional: `.\ai\run.ps1 claude`. On Windows prefer PowerShell 7; `run.bat` cannot pass
`!` and `%` to the container.

On Windows the repository must be checked out with LF line endings, which `.gitattributes`
enforces (see `docs/develop/ai.md`). In the sandbox, run `pre-commit` on the changed files only;
`--all-files` fails on the read-only `ai/` directory. Changes to `ai/` itself (outside `ai/skills`,
or to `ai/skills` without `AI_SANDBOX_SKILLS_RW=1`) must be checked on the host for the same reason.

See `docs/develop/ai.md` for the AI coding workflow.

Inside the container (working dir `/workspace`):

```bash
opencode auth login   # or /connect in the TUI
claude                # then /login on first run
qwen
gh auth login         # then gh auth setup-git
```

All auth state persists in the `akkudoktor-ai-home` Docker volume. The local
repository is bind-mounted at `/workspace`, so AI-made changes are visible on
the host immediately.

## Security notes

- No `privileged` mode, no Docker socket, no host home mount, no `sudo`.
- `ai/` (sandbox definition and launchers) is read-only in the container.
- `.git` is writable, so the agent can commit. It can also plant git configuration and hooks that
  run on the host (e.g. through `.git/commondir`). Check `.git` before you run any git command on
  the host, see the review checklist in `docs/develop/ai.md`.
- Git worktree checkouts are not supported: their `.git` is a file pointing outside the
  repository, so git does not work in the sandbox.
- Container user matches the host UID/GID (Linux/macOS) so file ownership is preserved.
- Credentials are never written to git or to `compose.yaml`; they live only
  in the persistent home volume via each tool's own login flow.
- The home volume persists between sessions. Remove it (`docker volume rm akkudoktor-ai-home`)
  if a session may have been misled.
- The launchers' checks of `compose.yaml` are a sanity check, not a complete validation.
