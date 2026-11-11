% SPDX-License-Identifier: Apache-2.0
(ai-page)=

# AI-Assisted Development

EOS can be developed together with an AI coding agent such as
[Claude Code](https://docs.anthropic.com/en/docs/claude-code),
[OpenCode](https://opencode.ai/) or [Qwen Code](https://github.com/QwenLM/qwen-code).
The agent reads and edits the code, runs the tests and checks, and writes commits. You stay in
charge: you choose the task, review every change and decide what is pushed and proposed upstream.

This page describes the workflow and how to run it in the AI development sandbox that ships with
the repository in the `ai/` directory.

(ai-coding-workflow)=

## The AI coding workflow

### Overview

```text
 host                                    sandbox container (ai/run.sh)
 ─────────────────────────────────       ─────────────────────────────────────────
 1. create a feature branch
 2. start the sandbox          ───────▶  3. start an agent (claude, opencode, qwen)
                                         4. describe the task, agree on a plan
 5. watch the changes in your  ◀───────  6. agent edits code, adds tests,
    IDE, answer questions                   runs pytest, ruff, mypy, linters
 7. review the diff            ◀───────  8. agent commits (Conventional Commits)
 9. push, open a pull request
```

The repository on your machine is mounted into the container. Every change the agent makes is
visible on the host immediately, and every change you make on the host is visible to the agent.
There is no copy to synchronize.

### Project rules for agents

Agents get their project knowledge from two places in the repository:

- `AGENTS.md` in the repository root is the entry point. It gives a short project summary, the
  rules for every change, the index of the skills and the completion checklist. Claude Code and
  OpenCode read it automatically when they start in the repository. If an agent does not, ask it
  to read `AGENTS.md` first.
- `ai/skills/<skill>/SKILL.md` holds the detailed rules, one skill per topic. The agent loads a
  skill when the task needs it, as listed in `AGENTS.md`:

| Skill | Content |
|---|---|
| `python-environment` | How the agent runs `uv`, `make`, pytest, mypy and ruff without breaking your `.venv` |
| `python-quality` | Ruff, mypy, typing and docstring rules |
| `testing` | Where and how to add tests |
| `filename-conventions` | File name rules enforced by `scripts/lint_filenames.py` |
| `application-configuration` | Configuration models, defaults and migration |
| `energy-domain` | Optimization, adapters, units and sign conventions |
| `rest-api` | REST endpoints and error responses |
| `commit-messages` | Conventional Commits as checked by `commitizen` |

General engineering workflows such as test-driven development and code review come from an
external skill collection, see [](#ai-engineering-skills).

The skills are not registered as agent-specific skills (e.g. in `.claude/skills/`). The agent reads
them because `AGENTS.md` tells it to, which works the same for every agent.

When you notice that an agent repeatedly gets something wrong, add or sharpen the rule in the
matching skill instead of correcting the agent again in every session. Changes to the skills are
reviewed and committed like code. `ai/skills` is read-only in the sandbox. To let the agent edit
the skills, start the sandbox with `AI_SANDBOX_SKILLS_RW=1`.

### Working with the agent

1. **Start from a clean branch.** Create a branch for the task on the host, e.g.
   `git switch -c feat/my-feature eos/main`. Commit or stash unrelated work first, so the agent's
   changes are easy to tell apart.
2. **Describe the task precisely.** Name the goal, the affected component and the constraints,
   and point the agent to relevant files, issues or error messages. Paste tracebacks in full.
3. **Plan before larger changes.** For anything beyond a small fix, ask the agent for a short
   design note or plan first and agree on it before code is written. Claude Code has a plan mode
   (`Shift+Tab`) for this.
   The agent writes the agreed plan to `.scratch/<branch>/plan.md` (the task plan, see
   [](#ai-self-review)).
4. **Let the agent verify its work.** The skills require the agent to add tests and to run
   pytest, ruff, mypy and the filename linter. Ask for the results if they are not reported.
   At the end of the task the agent runs its self-review and writes the pull request body, see
   [](#ai-self-review).
5. **Watch and review.** Open the repository in your IDE on the host. You can follow the edits
   live, answer the agent's questions with full knowledge of the code, run the result yourself and
   change things by hand. Review the complete diff before you open or update a pull request, as
   described in [](#ai-review-checklist). You are the author of what goes upstream.
6. **Commit in small steps.** Let the agent commit each logical step with a Conventional Commits
   message (see the `commit-messages` skill), or commit yourself. Agent-written commits end
   with an `Assisted-by:` trailer. Check the messages with commitizen, which works on every
   platform: `uv run --frozen cz check --rev-range main..HEAD`.
7. **Push and open the pull request from the host.** Then the sandbox needs no GitHub
   credentials at all. See [](#ai-sandbox-github) if you want the agent to push. Use the body the
   agent wrote. Before every further push, let the agent rerun the self-review and rewrite the
   body, and replace the body right after the push, see [](#ai-self-review).

(ai-engineering-skills)=

## Engineering skills

`ai/skills/engineering` holds a copy of the engineering skills from Matt Pocock's
[Skills For Real Engineers](https://github.com/mattpocock/skills) (MIT license, see
`ai/skills/engineering/LICENSE`). They are small, composable workflows for everyday engineering
work, such as test-driven development, diagnosing bugs, code review and domain modeling, and they
work with any agent. The EOS project rules take precedence where they overlap.

`AGENTS.md` lists them in two groups:

- Skills the agent loads when the task needs them:
  [code-review](https://github.com/mattpocock/skills/blob/main/docs/engineering/code-review.md),
  [codebase-design](https://github.com/mattpocock/skills/blob/main/docs/engineering/codebase-design.md),
  [diagnosing-bugs](https://github.com/mattpocock/skills/blob/main/docs/engineering/diagnosing-bugs.md),
  [domain-modeling](https://github.com/mattpocock/skills/blob/main/docs/engineering/domain-modeling.md),
  [pr](https://github.com/mattpocock/skills/blob/main/docs/engineering/pr.md),
  [prototype](https://github.com/mattpocock/skills/blob/main/docs/engineering/prototype.md),
  [research](https://github.com/mattpocock/skills/blob/main/docs/engineering/research.md),
  [tdd](https://github.com/mattpocock/skills/blob/main/docs/engineering/tdd.md) and
  [wizard](https://github.com/mattpocock/skills/blob/main/docs/engineering/wizard.md).
- Skills the agent loads only when you ask for them by name:
  [ask-matt](https://github.com/mattpocock/skills/blob/main/docs/engineering/ask-matt.md),
  [grill-with-docs](https://github.com/mattpocock/skills/blob/main/docs/engineering/grill-with-docs.md),
  [implement](https://github.com/mattpocock/skills/blob/main/docs/engineering/implement.md),
  [implement-spec](https://github.com/mattpocock/skills/blob/main/docs/engineering/implement-spec.md),
  [improve-codebase-architecture](https://github.com/mattpocock/skills/blob/main/docs/engineering/improve-codebase-architecture.md),
  [retro](https://github.com/mattpocock/skills/blob/main/docs/engineering/retro.md),
  [setup-matt-pocock-skills](https://github.com/mattpocock/skills/blob/main/docs/engineering/setup-matt-pocock-skills.md),
  [to-spec](https://github.com/mattpocock/skills/blob/main/docs/engineering/to-spec.md),
  [to-tickets](https://github.com/mattpocock/skills/blob/main/docs/engineering/to-tickets.md),
  [triage](https://github.com/mattpocock/skills/blob/main/docs/engineering/triage.md) and
  [wayfinder](https://github.com/mattpocock/skills/blob/main/docs/engineering/wayfinder.md).
  If you are unsure which one fits, ask the agent to use `ask-matt`.

The skills that work with issues and tickets (`to-spec`, `to-tickets`, `triage`, `wayfinder`,
`implement-spec`, `code-review`) expect the issue tracker setup that `setup-matt-pocock-skills`
writes. See the [upstream README](https://github.com/mattpocock/skills#readme) for an overview of
all skills and flows.

The copy is not edited in this repository; change requests go upstream. The test
`tests/test_ai.py` warns when the copy differs from the upstream `main` branch. It keeps the
upstream file hashes in the pytest cache and fetches them at most once a day; `pytest --cache-clear`
forces a fresh check. To update the copy, run

```bash
uv run python scripts/update_mattpocock_skills.py
```

and review and commit the changes like code. `--check` only lists the differences. In the sandbox
the update needs `AI_SANDBOX_SKILLS_RW=1`, because `ai/skills` is read-only there.

If you also installed the skills as an agent plugin, e.g. `mattpocock-skills` in Claude Code, the
agent sees every skill twice. Use either the plugin or the copy in the repository.

(ai-self-review)=

## Self-review and the pull request check

### Task plan

For a task beyond a small fix, the agent writes the plan you agreed on to
`.scratch/<branch>/plan.md`, e.g. `.scratch/feat/profile/plan.md`. `.scratch/` is ignored by git
and outlives the session, so a later session can pick up the plan. The task plan is the spec the
self-review checks the change against, unless the commits reference a GitHub issue: then the issue
is the spec.

### Self-review

When the task is done, the agent reviews its own change with the `code-review` engineering skill
against the base branch, usually `main`. The review has two separate parts:

- **Standards**: does the change follow `CONTRIBUTING.md`, `AGENTS.md` and the project skills?
- **Spec**: does it do what the task plan or the issue asked for, and nothing else?

The agent fixes every **hard violation** (a breach of a documented project rule) itself, then
reruns the checks and the self-review. It reports **judgement calls** (code smells, possible scope
creep) to you and leaves the decision to you. The self-review is advisory: it catches cheap
mistakes before you spend time on them. It does not replace your author review, and it is no
security control, because a misled agent reviews its own change (see [](#ai-review-checklist)).

### Pull request body

The agent writes the pull request body to `.scratch/<branch>/pr.md` with the `pr` engineering
skill. It follows `.github/pull_request_template.md`, which uses the skill's sections, so agents
and people write the same structure:

- `## Summary`: the smallest view that makes the change clear (pseudocode, a call or file tree, a
  diff sketch or a diagram) with short text,
- `## Evidence`: before and after, e.g. a failing and a passing test run,
- `## Merge Danger`: whether the change is a one-way or two-way door, its blast radius, and
  optional **Breaking:** and **Security:** lines,
- `## Checklist`: the "AI-assisted" and author-review boxes,
- `## Self-review`: the self-review declaration:

```markdown
## Self-review

Reviewed: 3f2a9c1e5b7d4a6f8e0c2b4d6f8a0c2e4b6d8f0a
Base: main
Standards: 0 hard, 2 judgement
Spec: 1 open (source: .scratch/feat/profile/plan.md)

- judgement: possible Feature Envy in `geneticparams.py`, left as is because ...
- spec: ...

Assisted-by: Claude Code (claude-opus-5-5)
```

`Reviewed:` is the full SHA of the commit the review ran on. `Spec:` names the source of the spec:
an issue (`#123`), the task plan, or `none`.

Open the pull request from the host with that body. Before every further push, let the agent
rerun the self-review and rewrite `pr.md`, not just edit the SHA; replace the body right after the
push:

```bash
gh pr create --repo Akkudoktor-EOS/EOS --body-file .scratch/<branch>/pr.md ...
gh pr edit <number> --repo Akkudoktor-EOS/EOS --body-file .scratch/<branch>/pr.md
```

Without `gh`, do it by hand on GitHub: paste the content of `pr.md` as the description when you
open the pull request. To replace it later, open the "..." menu of the pull request description,
choose "Edit", replace the whole text with the content of `pr.md` and click "Update comment".

### The pull request check

The CI workflow `ai-self-review` (`scripts/check_pr_self_review.py`) treats a pull request as
agent-assisted when

- any commit carries an `Assisted-by:` trailer (or Claude Code's
  `Co-Authored-By: ... <noreply@anthropic.com>`),
- the body carries an agent marker: an `Assisted-by:` line, or a footer an agent writes on its own,
  such as Claude Code's `Generated with [Claude Code](https://claude.com/claude-code)` (the check
  matches the Markdown text `Generated with [Claude Code]`), or
- the "AI-assisted" box in the body is ticked.

So a pull request that an agent opened is detected even when its commits were written by hand.
Template comments (`<!-- ... -->`) never count. The check then fails only when

- the body has no `## Self-review` section, or the section has no `Reviewed:` line,
- `Reviewed:` is not the full 40-character lowercase SHA of the current head of the pull request.

Everything else is only noted, without failing the check: as a warning in the log, in the job
summary, and in the comment if the pull request already has one. A passing check never adds a new
comment because of notes. Notes are given for:

- a commit or the body carries an agent marker but the box is not ticked,
- `Base:`, `Standards:` or `Spec:` is present but malformed: the counts must be numbers and
  `Spec:` needs a source, as in the format above.

Agents still write all four lines; the check requires only `Reviewed:`, because the SHA is what
binds the declaration to the code. A missing optional line is not reported.

Open findings do not fail the check. Dependabot and release pull requests are skipped.

When the check fails, the workflow posts a comment on the pull request: what is wrong, how to fix
it, and the expected format of the section. It keeps one such comment per pull request and updates
it on every run, so pushes and body edits do not add new comments; once the check passes, the
comment says so. The same report appears in the job summary of the run. Values taken from the
pull request body or the commit messages appear in the comment only inside a code block, so they
cannot mention anyone or inject Markdown. If the check script itself fails, the comment says that
the check could not run and points to the log of the workflow run; the check then fails.
Pull requests from forks get a read-only token, so the workflow does not comment on them; their
report is only in the job summary of the run.

Every push changes the head, so the check fails until the body names the new head. Therefore the
self-review is rerun and `pr.md` rewritten before the push, and the body is replaced right after
it. The check run started by the push sees the old body; the run started by the body edit cancels
it if it is still running, and decides the result. This is deliberate: a declaration must not
silently cover code pushed after the review.
Rebases and squashes before the merge need a fresh declaration too.

The check cannot tell a real review from a typed-in SHA, as no AI runs in CI. Like a sign-off, it
makes forgetting impossible and a false claim deliberate. Your author review and the maintainers'
review remain the controls. See the decision records
[0001](../adr/0001-self-declared-sha-bound-review-gate.md) and
[0002](../adr/0002-self-review-check-on-pull-request.md).

The workflow runs on `pull_request`, because GitHub blocks `pull_request_target` by default in
public repositories from December 2026. The workflow file therefore comes from the pull request,
while the check script is taken from the base branch, and no other code of the pull request runs. A
pull request that changes the workflow can weaken its own check, so `.github/CODEOWNERS` makes a
maintainer the code owner of `.github/workflows/`, `.github/CODEOWNERS` and
`scripts/check_pr_self_review.py`. This takes effect once the branch protection of `main` requires a
review from code owners. Depending on the approval setting of the repository, runs for pull requests
from forks of first-time contributors wait for a maintainer's approval. A pull request with merge
conflicts gets no run until the conflicts are resolved. The workflow runs only for pull requests
into `main`, never on pushes to `main` or to other branches, and not for pull requests into other
branches.

## The AI development sandbox

An AI agent runs commands on your behalf, and it can be misled, for example by instructions hidden
in a downloaded file or web page. The sandbox limits what such a command can reach. It is a Docker
container that sees only the repository and its own home volume.

:::{warning}
The sandbox does not protect your repository from harmful changes by the agent. The repository is
mounted read-write, so the agent can write any change into it, and such a change takes effect when
you run tests, `make`, pre-commit or EOS on the host, or when it is pushed. **Check every change
the agent made before you run it on the host, open a pull request or update one.** See
[](#ai-review-checklist).
:::

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2 (`docker compose version`).
  On Linux, your user must be allowed to use the Docker daemon (member of the `docker` group).
- A normal clone of the repository. Git worktree checkouts are not supported: their `.git` is a
  file that points outside the repository, so git does not work in the sandbox.
- On Windows: a checkout with LF line endings. The repository's `.gitattributes` checks out all
  text files with LF (only `*.bat` and `*.cmd` with CRLF), whatever `core.autocrlf` says, so the
  shell scripts run in the container and git in the container sees the same changes as git on
  the host. A clone made before this setting still has CRLF files; commit or stash your work and
  check out all files again: `git rm -r -q --cached .` and then `git reset -q --hard`.
- An account for at least one agent: an Anthropic subscription or API key for Claude Code, an
  account with any provider supported by OpenCode (including local models), or a Qwen account.

### Start the sandbox

Run the launcher from the repository root:

| Host | Command |
|---|---|
| Linux, macOS | `./ai/run.sh` (once before: `chmod +x ai/run.sh`) |
| Windows PowerShell | `.\ai\run.ps1` |
| Windows CMD | `ai\run.bat` |

The launcher

1. checks that Docker and Docker Compose are available,
2. sanity-checks that `ai/compose.yaml` contains the main sandbox controls and refuses to start
   otherwise,
3. writes `ai/.env` (from `ai/.env.example`) with your user and group id, so files the agent
   creates belong to you,
4. starts a new container and opens a `bash` shell in `/workspace`, the repository.

The first start builds the image. Later starts reuse it. Rebuild the image to get new versions of
the agent CLIs or after changing `ai/Dockerfile`:

```bash
AI_SANDBOX_REBUILD=1 ./ai/run.sh
```

To run a single command instead of a shell, put it after `--`:

```bash
./ai/run.sh -- claude
./ai/run.sh -- bash -lc 'git status'
```

PowerShell consumes an unquoted `--` itself, so there it is optional: `.\ai\run.ps1 claude`.
The launchers forward all arguments as the command; they do not take Docker Compose options such
as `--build` (use the variables described here instead).

On Windows, prefer PowerShell 7 (`pwsh`): it passes quotes and the characters `|`, `&`, `!` and
`%` to the container unchanged. Windows PowerShell 5.1 does not pass embedded double quotes on to
native commands, so put double quotes outside and single quotes inside:
`.\ai\run.ps1 bash -lc "pytest -k 'a and b'"`. `ai\run.bat` passes each argument with its own
quotes, so `|`, `&`, `<` and `>` inside a quoted argument reach the container, but `!` and `%`
cannot be passed through `run.bat` at all; use `run.ps1` for commands that contain them.

To let the agent edit the skills in `ai/skills`, which are read-only in the sandbox by default,
start it with `AI_SANDBOX_SKILLS_RW=1`. The rest of `ai/` stays read-only.

Every call starts a fresh container that is removed on exit, so you can run several sandboxes in
parallel, e.g. one agent per terminal. Note that they all work on the same repository and branch.
To reach servers in the sandbox from the host, start it with `AI_SANDBOX_PORTS=1` (see
[](#ai-sandbox-servers)).

To give commits made in the sandbox your identity, export it before starting the sandbox:

```bash
export GIT_USER_NAME="Your Name"
export GIT_USER_EMAIL="you@example.com"
./ai/run.sh
```

### Log in to the agents

The image contains Claude Code (`claude`), OpenCode (`opencode`), Qwen Code (`qwen`) and the GitHub
CLI (`gh`). Log in once, the login is kept in the sandbox home volume (see
[](#ai-sandbox-persistence)).

**Claude Code**: start `claude`. On the first start a setup assistant asks for the color theme and
the login method. With a Claude subscription choose "Claude account with subscription", with an
Anthropic API key choose "Anthropic Console account". Claude Code shows a login link. Press `c` to
copy it, open it in a browser on the host, log in, and paste the code the browser shows back into
the terminal. When the login has expired, run `/login` in a Claude Code session.

**OpenCode**: start `opencode` and run `/connect` in it, or run `opencode auth login` in the shell.
Choose the provider, e.g. OpenAI, Anthropic with an API key, or a local model.

**Qwen Code**: start `qwen` and follow its login prompt.

### Start the agent

Start the agent in `/workspace`, the repository root, so it finds `AGENTS.md`:

```bash
claude      # or: opencode, qwen
```

Then work with it as described in [](#ai-coding-workflow).

### Commands in the sandbox

The sandbox contains Python 3.13, `uv`, Node.js, `git`, `gh` and `make`. Run the project's commands
as you would on the host, with one difference: **use a virtual environment outside the
repository**.

The repository's `.venv` belongs to the host. It is bound to the Python interpreter of your
machine, which does not exist in the container. If `uv` used it, it would delete and recreate
`.venv` for the container's interpreter and break your environment on the host. The sandbox
therefore sets `UV_PROJECT_ENVIRONMENT=/home/dev/.cache/eos-venv` for every shell and every agent
tool call. Create the environment once:

```bash
uv sync --frozen --extra dev
```

The first sync downloads and installs all packages; on Docker Desktop (Windows, macOS) it can take
many minutes. Later syncs take a moment. `/home/dev` is the persistent home volume, so the
environment survives restarts. Do not change the variable to a path on `/tmp`: `/tmp` is small and
does not allow running programs.

The usual commands work:

| Task | Command |
|---|---|
| Run all tests | `uv run --frozen pytest` |
| Run the tests of one module | `uv run --frozen pytest tests/test_config.py` |
| Run the tests as CI does | `make test-ci` |
| Check types | `make mypy` |
| Pre-commit hooks on committed changes | `uv run --frozen pre-commit run --from-ref main --to-ref HEAD` |
| Pre-commit hooks on changed files | `uv run --frozen pre-commit run --files <file> ...` |
| Check file names | `uv run --frozen python scripts/lint_filenames.py` |
| Check commit messages since `main` | `uv run --frozen cz check --rev-range main..HEAD` |
| Generate the documentation | `make gen-docs` |
| Start the EOS server for development | `make run-dev` (see [](#ai-sandbox-servers)) |
| Start EOSdash for development | `make run-dash-dev` (see [](#ai-sandbox-servers)) |

Do not run `pre-commit run --all-files` in the sandbox: some hooks open every file for writing,
which fails on the read-only `ai/` directory. Run it on the host. For the same reason
`--from-ref`/`--files` fail with `Errno 30` when the changes include files in `ai/` outside
`ai/skills`, or in `ai/skills` without `AI_SANDBOX_SKILLS_RW=1`; check those changes on the host
too. Do not run `make clean` in the sandbox either. It deletes the host's `.venv`.

(ai-sandbox-servers)=

### Run EOS and EOSdash in the sandbox

A server started in the sandbox can always be tested from within the same container, e.g. let the
agent start it in the background and query it with `curl http://localhost:8503/v1/health`. A second
`./ai/run.sh` starts a separate container, which cannot reach it.

To open EOS and EOSdash in the browser on the host:

1. Start the sandbox with published ports:

   ```bash
   AI_SANDBOX_PORTS=1 ./ai/run.sh
   ```

   On Windows, set the variable first: `$env:AI_SANDBOX_PORTS = "1"` in PowerShell or
   `set AI_SANDBOX_PORTS=1` in CMD. The ports 8503 (EOS) and 8504 (EOSdash) are published on the
   host's loopback interface only, so they are not reachable from the network. Only one sandbox at a
   time can publish them. That's why publishing is off by default.

2. Start the servers so that they listen on all interfaces of the container. `make run-dev` and
   `make run-dash-dev` listen on `localhost` only, which is not reachable through the published
   ports:

   ```bash
   uv run --frozen python -m akkudoktoreos.server.eos \
       --host 0.0.0.0 --port 8503 --log_level DEBUG \
       --startup_eosdash false --reload true &
   uv run --frozen python -m akkudoktoreos.server.eosdash \
       --host 0.0.0.0 --port 8504 --eos-host localhost --eos-port 8503 \
       --log_level DEBUG --reload true
   ```

3. Open <http://localhost:8504> (EOSdash) or <http://localhost:8503/docs> (EOS REST API) in the
   browser on the host.

(ai-sandbox-github)=

### GitHub access

The recommended way is to push and open pull requests from the host, after your review. The
sandbox then needs no GitHub credentials.

If the agent shall push or create pull requests itself, log in to GitHub in the sandbox:

```bash
gh auth login
gh auth setup-git
```

`gh auth setup-git` lets `git push` use the `gh` login. Later sandbox starts set this up
automatically. Prefer a
[fine-grained personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
restricted to your fork over a full login.

(ai-sandbox-persistence)=

### Persistent data

Everything in the sandbox is temporary, except

- `/workspace`: the repository on your machine,
- `/home/dev`: the Docker volume `akkudoktor-ai-home`. It holds the logins of the agents and of
  `gh`, the agents' settings, memory and session transcripts, the `uv` cache and the virtual
  environment. The shell startup files (`~/.bashrc`, `~/.profile`) are reset on every start, so
  changes to them do not persist.

To log out of everything and start over, remove the volume while no sandbox is running:

```bash
docker volume rm akkudoktor-ai-home
```

### Security model

The sandbox reduces what a misled agent can do on your machine. It does not make the agent's output
trustworthy, and it does not protect the repository: the agent can write any change into it. Your
review of every change is the main control.

The container

- sees only the repository and its own home volume, no other host directories and no Docker
  socket,
- mounts `ai/` (the sandbox definition and the launchers) read-only, so the agent cannot change
  the next sandbox start,
- runs as your user id, not as root, with all Linux capabilities dropped, `no-new-privileges` and
  no `sudo`, so the agent cannot gain privileges,
- has a read-only root file system. `/tmp`, `/run` and `/var/tmp` are in-memory and do not allow
  running programs,
- is limited to 2 CPUs, 4 GB of memory and 512 processes,
- has network access to the internet, which the agents and `uv` need,
- publishes no ports, unless started with `AI_SANDBOX_PORTS=1`, and then only on the host's
  loopback interface.

The launchers check `ai/compose.yaml` for the main controls before they start the sandbox. This is
a sanity check against accidental edits, not a complete validation. Review changes to `ai/` like
any other change.

Keep in mind what the agent can still reach:

- **The repository, read and write.** Everything outside `ai/`. Many files in it are run on the
  host: `Makefile`, `.pre-commit-config.yaml` (local hooks run on your next commit),
  `pyproject.toml`, tests and `conftest.py`, scripts, the CI workflows.
- **`.git`, read and write.** The agent commits through it, and can create commits that change
  `ai/`. It can also plant git configuration that runs commands on the host, e.g. a
  `core.fsmonitor` that runs on the next `git status`, or hooks that run on the next commit. It
  does not need to touch `.git/config` or `.git/hooks` for that: git also reads configuration and
  hooks from the directory named in `.git/commondir`. Read-only mounts of single files in `.git`
  cannot prevent this, so the sandbox does not pretend to. Check `.git` before you run any git
  command on the host (see [](#ai-review-checklist)).
- **The home volume.** It persists between sessions, so a misled session can leave state behind
  that takes effect later: agent settings and hooks in `~/.claude`, `~/.gitconfig`, the virtual
  environment in `~/.cache`. If a session may have been misled, remove the volume
  (`docker volume rm akkudoktor-ai-home`) and log in again.
- **The internet.** The agent can download code and send data. Do not put secrets into the
  repository directory.
- **The logins in the home volume.** A GitHub login in the sandbox lets the agent push to every
  repository the login can write to. That's why pushing from the host is recommended.
- **The skills, if enabled.** With `AI_SANDBOX_SKILLS_RW=1` the agent can change the rules that
  later sessions follow.

(ai-review-checklist)=

### Check the changes before you push

Do this on the host, before you run the agent's changes there and before you open or update a pull
request. Start with `.git`, before you run any git command, because a planted configuration
runs on the first one, even on `git status` or `git diff`:

- Look at `.git` with plain file tools: `ls -la .git`. There must be no `commondir` file and no
  unexpected files or directories (a normal `.git` holds `HEAD`, `config`, `description`, `index`,
  `hooks/`, `info/`, `logs/`, `objects/`, `refs/` and some `*_HEAD`, `COMMIT_EDITMSG`,
  `packed-refs`, `modules/` or `worktrees/`). Read `.git/config` with a text viewer and look for
  `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `filter.*` and `include*` entries you did
  not add. List the active hooks: `ls .git/hooks` (anything not ending in `.sample`). If anything
  looks wrong, do not run git; remove the planted files first.
- Review the full diff against the target branch, including every commit:
  `git diff main...HEAD` and `git log -p main..HEAD`. The agent may have committed changes that
  are no longer visible in the working tree.
- Check `git status` for unexpected modified and untracked files, and `git status --ignored` for
  unexpected ignored ones.
- Look closely at changes to files the host runs: `Makefile`, `.pre-commit-config.yaml`,
  `pyproject.toml`, `conftest.py` and tests, `scripts/`, `.github/`, `ai/` and `ai/skills/`.
- Cross-check the effective local git configuration: `git config --local --list --show-origin`.
  Every entry must come from `.git/config`.
- If you suspect that a session was misled, discard its changes and reset the home volume.

See `ai/README.md` for the files of the sandbox.
