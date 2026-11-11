# The self-review check runs on pull_request, guarded by code owners

GitHub blocks `pull_request_target` by default in public repositories from December 2, 2026.
The `ai-self-review` workflow of [0001](0001-self-declared-sha-bound-review-gate.md) therefore
runs on `pull_request`. It still checks out and runs the check script of the base branch and no
other code of the pull request, but the workflow file itself now comes from the pull request, so
a pull request can weaken its own check by changing the workflow. `.github/CODEOWNERS` makes a
maintainer the code owner of `.github/workflows/`, `.github/CODEOWNERS` and
`scripts/check_pr_self_review.py`, so such a change needs a maintainer's review. This supersedes
the consequence of 0001 that a pull request cannot weaken its own check.

## Considered Options

- **Keep `pull_request_target` and allow it in an Actions event policy**: rejected, it depends on
  a repository setting outside the code.
- **`pull_request` without code owners**: rejected, nothing would force a review of a change that
  weakens the check.

## Consequences

- The code owner review is enforced only once the branch protection of `main` requires a review
  from code owners.
- A pull request that deletes the workflow gets no check run; if the check is required, the pull
  request waits for it and cannot be merged.
- The workflow keeps `pull-requests: write` to comment. The authors of pull requests from the
  repository itself can write to it anyway; pull requests from forks get a read-only token, so
  the workflow posts no comment for them, and the report is in the job summary only.
- Depending on the approval setting of the repository, runs for pull requests from forks of
  first-time contributors wait for a maintainer's approval.
- `pull_request` runs on the merge commit of the pull request, so a pull request with merge
  conflicts gets no run; a required check stays pending until the conflicts are resolved.
