# CI enforces a self-declared, SHA-bound self-review for agent-assisted pull requests

Pull requests that a coding agent wrote or co-wrote must carry a `## Self-review` section in the
pull request body that names the full head commit SHA it reviewed. CI does not verify the review
itself: the project has no AI in CI, and a signing key in the sandbox would be reachable by a misled
agent. Instead CI detects agent assistance (an `Assisted-by:` or Anthropic `Co-Authored-By:` commit
trailer, an `Assisted-by:` line or a known agent footer in the pull request body, or the
"AI-assisted" box in the pull request template). It fails only when the declaration or its
`Reviewed:` SHA is missing or malformed, or names a SHA other than the current head; everything
else (an unticked box next to a trailer or marker, malformed optional lines) is only noted. Like a
DCO sign-off, this makes forgetting impossible and a false claim deliberate; the author review and
the maintainers' review remain the actual controls.

## Considered Options

- **AI review in CI**: rejected, the project runs no AI in CI.
- **Signed review attestation from the sandbox**: rejected, the agent can reach the key and sign
  anything.
- **Honour system (template checkbox only)**: rejected, a declaration that is not bound to a commit
  silently covers code pushed after the review.

## Consequences

- Every push to an agent-assisted pull request needs a fresh self-review before the push and an
  updated pull request body right after it; rebases and squashes before merge do too.
- Open findings do not fail the check, so agents have no incentive to hide them.
- The check only blocks merging once maintainers make it a required status check.
- The workflow runs on `pull_request_target` with the base branch's script and never runs pull
  request code, so a pull request cannot weaken its own check; changes to the check take effect
  only after they are merged. Superseded by [0002](0002-self-review-check-on-pull-request.md).
- The workflow needs `pull-requests: write` to comment how to fix a failing check. That is safe
  only as long as it runs no pull request code. Superseded by
  [0002](0002-self-review-check-on-pull-request.md).
