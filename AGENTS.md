# AGENTS.md — Akkudoktor-EOS

Akkudoktor-EOS is a Python-based energy management and optimization system. It
provides energy forecasts, optimization, energy management plans, and
integrations with local energy devices and controllers.

The project prioritizes reliable local operation, clear interfaces,
maintainable code, testability, and type safety.

## Rules for every change

- Follow existing architecture and coding conventions.
- Prefer small, focused changes that preserve established interfaces and
  behavior.
- Use type annotations for functions, methods, and relevant variables.
- Write Google-style docstrings for modules, classes, and public
  functions/methods, following surrounding code.
- Keep code compatible with the supported Python version (`pyproject.toml`).
- Avoid introducing unnecessary dependencies.
- Use descriptive, domain-meaningful names.
- Never use production `assert` for input validation or runtime error
  handling; use explicit exceptions.
- Use existing logging facilities and conventions.
- Follow existing patterns for config, validation, error reporting, resource
  management.
- Preserve compatibility with existing tests and supported integrations unless
  a change explicitly requires otherwise.
- **Create, parse, convert and compare dates, times, durations and time zones
  with `akkudoktoreos.utils.datetimeutil`** (`to_datetime`, `to_duration`,
  `to_timezone`, `to_time`, `compare_datetimes`, `DateTime`, `Duration`). Do not call
  `datetime`, `pendulum` or `dateutil` directly. Exceptions: `datetimeutil.py`
  itself, and boundaries where a third-party API needs its own type (pandas,
  NumPy, the standard library), converted right at the boundary. Applies to new
  and changed code; migrate existing direct calls only where the change touches
  them anyway.
- **Optimization parameters are always valid**: their preparation never fails
  and never cancels an optimization run. See the `energy-domain` skill.
- Commits written or co-written by an agent end with the trailer
  `Assisted-by: <tool> (<model id>)`, see the `commit-messages` skill.
- Never use the workspace `.venv`: run `uv`, `make` and all Python tools in a
  virtual environment outside the workspace (`UV_PROJECT_ENVIRONMENT`), see
  `ai/skills/python-environment/SKILL.md`.

## Skills

Detailed rules live in `ai/skills/`. Load the relevant skill for the task:

| Skill | Load when |
|---|---|
| `ai/skills/application-configuration/SKILL.md` | Config, Pydantic models, defaults |
| `ai/skills/commit-messages/SKILL.md` | Creating/checking commit messages |
| `ai/skills/energy-domain/SKILL.md` | Optimization, adapters, units/signs |
| `ai/skills/filename-conventions/SKILL.md` | Creating/renaming files |
| `ai/skills/python-environment/SKILL.md` | Before running uv, make, pytest, mypy, ruff or any Python |
| `ai/skills/python-quality/SKILL.md` | Writing/modifying Python |
| `ai/skills/rest-api/SKILL.md` | Adding/changing REST endpoints, API error responses |
| `ai/skills/testing/SKILL.md` | Adding/updating tests, bug fixes |

### Engineering skills

General engineering workflows from
[mattpocock/skills](https://github.com/mattpocock/skills) (MIT, see
`ai/skills/engineering/LICENSE`). The project rules above take precedence
where they overlap.

| Skill | Load when |
|---|---|
| `ai/skills/engineering/code-review/SKILL.md` | Reviewing a branch, PR or changes since a fixed point |
| `ai/skills/engineering/codebase-design/SKILL.md` | Designing module interfaces, seams, testability |
| `ai/skills/engineering/diagnosing-bugs/SKILL.md` | Diagnosing hard bugs and performance regressions |
| `ai/skills/engineering/domain-modeling/SKILL.md` | Domain terminology, glossary, ADRs |
| `ai/skills/engineering/pr/SKILL.md` | Writing a PR body |
| `ai/skills/engineering/prototype/SKILL.md` | Throwaway prototype to answer a design question |
| `ai/skills/engineering/research/SKILL.md` | Researching a question against primary sources |
| `ai/skills/engineering/tdd/SKILL.md` | Test-first features or bug fixes |
| `ai/skills/engineering/wizard/SKILL.md` | Interactive bash wizard for steps only a human can do |

Load these only when the user asks for them by name:

| Skill | Purpose |
|---|---|
| `ai/skills/engineering/ask-matt/SKILL.md` | Pick the skill or flow that fits a situation |
| `ai/skills/engineering/grill-with-docs/SKILL.md` | Interview to sharpen a plan, writing ADRs and glossary |
| `ai/skills/engineering/implement/SKILL.md` | Implement work from a spec or tickets |
| `ai/skills/engineering/implement-spec/SKILL.md` | Implement the result of `to-spec` and `to-tickets` |
| `ai/skills/engineering/improve-codebase-architecture/SKILL.md` | Report deepening opportunities, then grill one |
| `ai/skills/engineering/retro/SKILL.md` | Retrospective on a coding session |
| `ai/skills/engineering/setup-matt-pocock-skills/SKILL.md` | One-time setup of issue tracker, labels, domain docs |
| `ai/skills/engineering/to-spec/SKILL.md` | Turn the conversation into a spec on the tracker |
| `ai/skills/engineering/to-tickets/SKILL.md` | Break a plan into tracer-bullet tickets |
| `ai/skills/engineering/triage/SKILL.md` | Triage issues and external PRs |
| `ai/skills/engineering/wayfinder/SKILL.md` | Plan large work as decision tickets |

## Task plan and self-review

- Before writing code for a task beyond a small fix, agree on a plan with the
  user and write it to `.scratch/<branch>/plan.md` (the **task plan**, see
  `GLOSSARY.md`). `.scratch/` is ignored by git; never commit it. Keep the plan
  up to date when the user changes the task.
- When the task is done, run the **self-review**: the `code-review` skill
  against the base branch (usually `main`). The spec is the GitHub issue the
  commits reference, if any, otherwise the task plan.
- The coding standards for the review are `CONTRIBUTING.md`, this file and the
  project skills in `ai/skills/` (not `ai/skills/engineering/`).
- Fix every **hard violation** (breach of a documented project rule) yourself,
  rerun the checks and the self-review. Report **judgement calls** (code
  smells, possible scope creep) to the user without acting on them.
- Write the pull request body to `.scratch/<branch>/pr.md` with the `pr` skill,
  in the structure of `.github/pull_request_template.md`: the skill's
  `## Summary`, `## Evidence` and `## Merge Danger` (with optional
  **Breaking:** and **Security:** lines), then `## Checklist` with the
  "AI-assisted" box ticked. It ends with a `## Self-review` section in
  exactly this format. CI requires the `Reviewed:` line and notes problems
  with the others, see `docs/develop/ai.md`:

  ```markdown
  ## Self-review

  Reviewed: <full 40-character SHA of the reviewed HEAD commit>
  Base: <base branch or commit>
  Standards: <n> hard, <m> judgement
  Spec: <k> open (source: <#issue | .scratch/<branch>/plan.md | none>)

  - <one line per open finding or judgement call, and why it was left>

  Assisted-by: <tool> (<model id>)
  ```

  Keep footers the tool adds itself below it, e.g. Claude Code's
  `Generated with [Claude Code](https://claude.com/claude-code)`.

- The self-review runs once at the end of the task, not before every commit.
  Before every push (e.g. of fixes after review feedback), rerun the
  self-review and rewrite the section in `.scratch/<branch>/pr.md`; never
  just edit the SHA. The `Reviewed:` SHA must be the head of the pull
  request. The pull request body is replaced right after the push.

## Agent skills

### Issue tracker

GitHub issues of the upstream repository `Akkudoktor-EOS/EOS`. See
`docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repository root. See
`docs/agents/domain.md`.

## Completion checklist

Before considering a change complete:

- [ ] New and renamed files comply with the filename rules in `pyproject.toml`.
- [ ] The filename linter passes.
- [ ] Tests have been added or updated as appropriate.
- [ ] Relevant pytest tests pass.
- [ ] Ruff checks pass.
- [ ] Mypy checks pass where applicable.
- [ ] Configuration and documentation updated when necessary.
- [ ] No unrelated changes or unnecessary exceptions introduced.
- [ ] Self-review done, hard violations fixed, judgement calls reported.
- [ ] `.scratch/<branch>/pr.md` holds an up-to-date `## Self-review` section.
