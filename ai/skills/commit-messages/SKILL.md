---
name: commit-messages
description: Use when writing, amending, or reviewing a git commit message in this repository. Enforces Conventional Commits (required by the project) with an optional body section for secondary changes/fixes.
---

# Commit Messages

## When to use

- Writing a new commit message.
- Amending an existing commit message.
- Reviewing or proposing commit messages in a PR description.
- Any agent-generated commit.

## Format

The project uses **Conventional Commits** and enforces them via `commitizen`
(`cz check`). Every commit message must follow this structure:

```text
<type>[(scope)][!]: <description>

[optional body]

[optional footer(s)]
```

The subject line is mandatory. The body is optional and is the place
for secondary changes, fixes, or improvements that are not the primary point
of the commit.

1. Subject (mandatory)

Format: <type>[(scope)][!]: <description>

- type — one of the project-accepted types (see table below).
- scope — optional, in parentheses, e.g. (config), (forecast).
- ! — append after type/scope only for a breaking change.
- description — short, imperative, present tense ("add …", "fix …").
  No trailing period. Aim for ≤ 72 characters; hard limit 100.

| Type | Use for | SemVer bump |
|---|---|---|
| `feat` | User-visible feature | minor |
| `fix` | Bug fix in shipped behavior | patch |
| `perf` | Performance improvement | patch |
| `refactor` | Internal restructure, no behavior change | patch |
| `docs` | Documentation only | none |
| `test` | Tests only, no production code change | none |
| `build` | Build system / dependency pins | none |
| `ci` | CI configuration | none |
| `chore` | Maintenance not covered above | none |
| `style` | Pure formatting/whitespace | none |
| `revert` | Revert a previous commit | none |

Pick the type that matches the primary intent of the commit. Split commits
that mix unrelated concerns.

2. Body (optional — for secondary changes)

Use the body when the commit also contains secondary bug fixes, required
extensions, or small improvements that are not part of the primary change
described in the subject.

Rules:

- Separate the subject and body with exactly one blank line.
- Start the body with a short heading line:

  ```text
  Additional changes
  ```

  or, when more accurate:

  ```text
  Additional fixes — secondary bug fixes.
  Additional improvements — secondary refactors/cleanups.
  ```

- Pick the heading that best reflects the majority of secondary changes. Do
  not stack multiple headings.
- Follow the heading with a short description of each secondary change.
- One short line (or bullet) per distinct secondary change.
- Do not restate the primary change in the body.
- Keep the body free of other headings, emoji, or issue-closing keywords
  (Closes #…) unless the footer is intentionally used for that purpose.

3. Footer (optional)

Use only for breaking changes (BREAKING CHANGE: …), issue references
(Refs: #123) and the agent trailer below. Conventional Commits allows this; it
is not a replacement for the body section.

4. Agent trailer (mandatory for agents)

Every commit that a coding agent wrote or co-wrote ends with

```text
Assisted-by: <tool> (<model id>)
```

e.g. `Assisted-by: Claude Code (claude-opus-5-5)` or
`Assisted-by: OpenCode (qwen3-coder)`. Keep trailers the tool adds itself
(e.g. `Co-Authored-By:`) and put `Assisted-by:` with them in the last
paragraph. The CI check for agent-assisted pull requests reads it, see
`docs/develop/ai.md`.

## Layout

```text
<type>[(scope)][!]: <description>

[Additional changes]
<short description of secondary change>
[<another short description>]

[optional footer]
[Assisted-by: <tool> (<model id>)]
```

- Exactly one blank line between the subject and the body heading.
- No blank line between the body heading and its items.
- Exactly one blank line between the body and any footer.

## Examples

### Subject only

```text
feat(config): add filename linter check to pre-commit hook
```

### Subject + additional fixes in body

```text
feat(config): add filename linter check to pre-commit hook

Additional fixes

Corrected import path in scripts/lint_filenames.py
Removed duplicate entry from .pre-commit-config.yaml
```

### Subject + additional improvements in body

```text
refactor(forecast): share HTTP session across adapter instances

Additional improvements

Extracted shared retry helper
Tightened docstrings on public forecast methods
```

### Subject + breaking change footer

```text
feat(config)!: rename surface_azimuth to azimuth for all surfaces

BREAKING CHANGE: north=0, east=90, south=180, west=270.
```

### Incorrect

```text
Add filename linter
```
(Missing required <type>: prefix — cz check will reject it.)

```text
feat: add filename linter and fix imports and bump deps
```

(Multiple unrelated changes in the subject; move secondary changes to the body
and use a subject that describes the primary intent.)

```text
feat: add filename linter

* Additional fixes
Corrected import path
```

(Asterisk-prefixed heading is not Conventional Commits style and will look
inconsistent with the rest of the log. Use a plain heading line in the body,
as shown above.)

## Decision rules

- If everything in the commit serves the subject → omit the body.
- If the commit contains an unrelated bug fix or a necessary-but-secondary
  extension → add the body section.
- If the body would need two or more different headings → the commit is
  probably too broad; recommend splitting it into focused commits.
- If unsure whether a change is "primary" or "secondary": ask "would a reviewer
  describe this commit primarily in terms of change X?" If yes, X is the
  subject and everything else goes into the body.

## Verify before committing

□ Subject matches <type>[(scope)][!]: <description> and uses an accepted type.
□ Subject is imperative, no trailing period, ≤ 72 chars (hard limit 100).
□ Exactly one blank line between subject and body.
□ At most one body heading (Additional changes / Additional fixes / Additional improvements).
□ Each secondary item is a short single-line description.
□ No emoji, no asterisk-prefixed headings.
□ If more than one body heading would be needed, split the commit instead.
□ Agent-written commits end with `Assisted-by: <tool> (<model id>)`.
□ Run uv run --frozen cz check --rev-range main..HEAD (or rely on the pre-commit hook) to confirm the
  message passes the project's check.
