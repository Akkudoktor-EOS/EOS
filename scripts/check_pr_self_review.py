#!/usr/bin/env python3
"""Check the self-review declaration of agent-assisted pull requests.

A pull request is agent-assisted when any of its commits carries an
``Assisted-by:`` trailer (or Claude Code's ``Co-Authored-By: ... <noreply@anthropic.com>``),
when its body carries an agent marker (an ``Assisted-by:`` line or Claude Code's
"Generated with [Claude Code]" footer), or when the "AI-assisted" box of the pull request
template is ticked. A trailer or body marker without the ticked box is only noted.

An agent-assisted pull request must carry a ``## Self-review`` section in its body with a
``Reviewed:`` line naming the full 40-character lowercase SHA of the current head commit. A
missing section, a missing or malformed SHA, or a SHA other than the head are the only errors.

The other lines of ``SELF_REVIEW_FORMAT`` are expected from agents but optional for the check;
when present but malformed they are only noted:

- ``Base:`` a non-empty ref,
- ``Standards: <n> hard, <m> judgement`` with numbers,
- ``Spec: <k> open (source: ...)`` with a number and a non-empty source.

Open findings do not fail the check. Pull requests by dependabot and titles starting with
``Release`` are skipped.

The check does not verify the review itself; see
``docs/adr/0001-self-declared-sha-bound-review-gate.md`` and ``docs/develop/ai.md``.

Only the Python standard library is used, so CI needs no project environment.

Usage::

    python scripts/check_pr_self_review.py --body-file body.md --commits-file commits.jsonl \\
        --head-sha <sha> --title "<title>" --author <login>

``commits.jsonl`` holds one JSON-encoded commit message per line, e.g. from
``gh api --paginate repos/<owner>/<repo>/pulls/<n>/commits --jq '.[].commit.message | @json'``.

With ``--report-file`` the check also writes a Markdown report for the pull request comment and
the job summary: how to fix a failing check, or a short note that the check passes. The report
starts with ``COMMENT_MARKER``, by which the workflow finds and updates its one comment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SELF_REVIEW_FORMAT = """\
## Self-review

Reviewed: <full 40-character SHA of the reviewed HEAD commit>
Base: <base branch or commit>
Standards: <n> hard, <m> judgement
Spec: <k> open (source: <#issue | .scratch/<branch>/plan.md | none>)
"""

COMMENT_MARKER = "<!-- ai-self-review -->"
DOCS_URL = (
    "https://github.com/Akkudoktor-EOS/EOS/blob/main/docs/develop/ai.md"
    "#self-review-and-the-pull-request-check"
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ASSISTED_BY = re.compile(r"^Assisted-by:[ \t]*\S", re.IGNORECASE | re.MULTILINE)
_ANTHROPIC_CO_AUTHOR = re.compile(
    r"^Co-Authored-By:.*<noreply@anthropic\.com>", re.IGNORECASE | re.MULTILINE
)
# Footers that agents write into pull request bodies on their own. Add a tool's footer here
# once it is known; do not guess.
_AGENT_BODY_FOOTERS = (re.compile(r"Generated with \[Claude Code\]", re.IGNORECASE),)
_AI_CHECKBOX = re.compile(r"^[ \t]*[-*][ \t]*\[([ xX])\][ \t]*AI-assisted\b", re.MULTILINE)
_SELF_REVIEW_HEADING = re.compile(r"^##[ \t]+Self-review[ \t]*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,2}[ \t]+\S", re.MULTILINE)
_REVIEWED = re.compile(r"^Reviewed:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_BASE = re.compile(r"^Base:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
_STANDARDS = re.compile(r"^Standards:[ \t]*\d+[ \t]+hard,[ \t]*\d+[ \t]+judgement[ \t]*$", re.MULTILINE)
_SPEC = re.compile(r"^Spec:[ \t]*\d+[ \t]+open[ \t]+\(source:[ \t]*[^)\s][^)]*\)[ \t]*$", re.MULTILINE)
_OPTIONAL_LINES = (
    ("Base", _BASE, "Base: <ref>"),
    ("Standards", _STANDARDS, "Standards: <n> hard, <m> judgement"),
    ("Spec", _SPEC, "Spec: <k> open (source: ...)"),
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SKIPPED_AUTHORS = re.compile(r"^dependabot(\[bot\])?$", re.IGNORECASE)
_SKIPPED_TITLES = re.compile(r"^Release")


@dataclass
class CheckResult:
    """Outcome of the self-review check of one pull request.

    Attributes:
        agent_assisted: Whether the pull request counts as agent-assisted.
        skipped_reason: Why the check was skipped, or an empty string.
        errors: Problems that fail the check; empty when the check passes.
        notes: Problems reported without failing the check.
    """

    agent_assisted: bool = False
    skipped_reason: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return whether the check passed."""
        return not self.errors


def normalize_newlines(text: str) -> str:
    """Convert Windows and old Mac line endings to ``\\n``.

    GitHub's web editor often saves pull request bodies with ``\\r\\n``; the line patterns
    anchor at ``$`` and would not match a line that ends in ``\\r``.

    Args:
        text: Text with any line endings.

    Returns:
        The text with ``\\n`` line endings only.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_html_comments(text: str) -> str:
    """Remove HTML comments, so template hints never count as content.

    Args:
        text: Markdown text, e.g. a pull request body.

    Returns:
        The text without ``<!-- ... -->`` comments.
    """
    return _HTML_COMMENT.sub("", text)


def has_agent_trailer(message: str) -> bool:
    """Return whether a commit message carries an agent trailer.

    Args:
        message: The full commit message.

    Returns:
        True for an ``Assisted-by:`` trailer or an Anthropic ``Co-Authored-By:`` trailer.
    """
    return bool(_ASSISTED_BY.search(message) or _ANTHROPIC_CO_AUTHOR.search(message))


def has_agent_body_marker(body: str) -> bool:
    """Return whether a pull request body carries an agent marker.

    Args:
        body: The pull request body without HTML comments.

    Returns:
        True for an ``Assisted-by:`` line or a known agent footer such as
        "Generated with [Claude Code]".
    """
    return bool(_ASSISTED_BY.search(body)) or any(
        footer.search(body) for footer in _AGENT_BODY_FOOTERS
    )


def is_ai_box_ticked(body: str) -> bool:
    """Return whether the "AI-assisted" box of the pull request template is ticked.

    Args:
        body: The pull request body without HTML comments.

    Returns:
        True when the box is ticked.
    """
    match = _AI_CHECKBOX.search(body)
    return bool(match and match.group(1) in "xX")


def extract_self_review(body: str) -> str | None:
    """Return the ``## Self-review`` section of a pull request body.

    Args:
        body: The pull request body without HTML comments.

    Returns:
        The section text up to the next level-one or level-two heading, or None if the
        body has no such section.
    """
    heading = _SELF_REVIEW_HEADING.search(body)
    if heading is None:
        return None
    rest = body[heading.end() :]
    next_heading = _NEXT_HEADING.search(rest)
    return rest[: next_heading.start()] if next_heading else rest


def check_self_review_section(section: str, head_sha: str) -> tuple[list[str], list[str]]:
    """Check the fields of a self-review section.

    Only the ``Reviewed:`` SHA is required. The other lines are optional; a present but
    malformed optional line is only noted.

    Args:
        section: The section text below the ``## Self-review`` heading.
        head_sha: The full SHA of the pull request head commit.

    Returns:
        The errors (missing, malformed or stale SHA) and the notes (malformed optional lines).
    """
    errors: list[str] = []
    notes: list[str] = []
    reviewed = _REVIEWED.search(section)
    if reviewed is None:
        errors.append("The self-review has no 'Reviewed: <sha>' line.")
    elif not _FULL_SHA.match(reviewed.group(1)):
        errors.append(
            f"'Reviewed: {reviewed.group(1)}' is not a full 40-character lowercase commit SHA."
        )
    elif reviewed.group(1) != head_sha.lower():
        errors.append(
            f"The self-review is for {reviewed.group(1)}, but the head of the pull request is"
            f" {head_sha}. Rerun the self-review and update the pull request body."
        )
    for key, pattern, expected in _OPTIONAL_LINES:
        present = re.search(rf"(?m)^{key}:", section) is not None
        if present and pattern.search(section) is None:
            notes.append(f"The '{key}:' line does not have the form '{expected}'.")
    return errors, notes


def check_pull_request(
    body: str, commit_messages: list[str], head_sha: str, title: str, author: str
) -> CheckResult:
    """Check the self-review declaration of one pull request.

    Args:
        body: The pull request body (Markdown).
        commit_messages: The full messages of all commits of the pull request.
        head_sha: The full SHA of the pull request head commit.
        title: The pull request title.
        author: The login of the pull request author.

    Returns:
        The check result.
    """
    result = CheckResult()
    if _SKIPPED_AUTHORS.match(author):
        result.skipped_reason = f"pull request by {author}"
        return result
    if _SKIPPED_TITLES.match(title):
        result.skipped_reason = "release pull request"
        return result

    body = strip_html_comments(normalize_newlines(body))
    trailer = any(has_agent_trailer(normalize_newlines(message)) for message in commit_messages)
    body_marker = has_agent_body_marker(body)
    box_ticked = is_ai_box_ticked(body)
    result.agent_assisted = trailer or body_marker or box_ticked
    if not result.agent_assisted:
        return result

    if trailer and not box_ticked:
        result.notes.append(
            "Commits carry an agent trailer (Assisted-by: or Co-Authored-By: Claude), but the"
            " 'AI-assisted' box in the pull request body is not ticked."
        )
    if body_marker and not box_ticked:
        result.notes.append(
            "The pull request body carries an agent marker (Assisted-by: or 'Generated with"
            " [Claude Code]'), but the 'AI-assisted' box is not ticked."
        )
    section = extract_self_review(body)
    if section is None:
        result.errors.append("The pull request body has no '## Self-review' section.")
    else:
        errors, notes = check_self_review_section(section, head_sha)
        result.errors.extend(errors)
        result.notes.extend(notes)
    return result


def read_commit_messages(path: Path) -> list[str]:
    """Read commit messages from a JSON Lines file.

    Args:
        path: File with one JSON-encoded commit message per line.

    Returns:
        The commit messages.

    Raises:
        ValueError: If a line is not a JSON string.
    """
    messages: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        message = json.loads(line)
        if not isinstance(message, str):
            raise ValueError(f"{path}:{number}: expected a JSON string, got {type(message).__name__}")
        messages.append(message)
    return messages


def _fenced(text: str, language: str = "") -> str:
    """Return text as a fenced code block that the text cannot close early.

    Pull request authors control parts of the error messages (e.g. the ``Reviewed:`` value). In a
    code block they cannot mention users or inject Markdown, as long as the fence is longer than
    any backtick run in the text.

    Args:
        text: The block content.
        language: Optional info string of the block.

    Returns:
        The fenced block.
    """
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def render_report(result: CheckResult, head_sha: str) -> str:
    """Render the Markdown report for the pull request comment and the job summary.

    Args:
        result: The check result.
        head_sha: The full SHA of the pull request head commit.

    Returns:
        The report, starting with ``COMMENT_MARKER``.
    """
    notes = ""
    if result.notes:
        listed = "\n".join(f"- {note}" for note in result.notes)
        notes = f"\nNotes (they do not fail the check):\n\n{_fenced(listed)}\n"
    if result.passed:
        if result.skipped_reason:
            detail = f"The check does not apply: {result.skipped_reason}."
        elif result.agent_assisted:
            detail = f"The self-review declaration is up to date for `{head_sha}`."
        else:
            detail = "The pull request is not agent-assisted; there is nothing to check."
        return f"{COMMENT_MARKER}\n### ✅ AI self-review check passed\n\n{detail}\n{notes}"
    errors = "\n".join(f"- {error}" for error in result.errors)
    return (
        f"{COMMENT_MARKER}\n"
        "### ❌ AI self-review check failed\n\n"
        "This pull request counts as agent-assisted (an agent trailer in a commit, an agent marker"
        " in the body, or the ticked \"AI-assisted\" box), but its self-review declaration is"
        f" missing, incomplete or out of date for the head commit `{head_sha}`:\n\n"
        f"{_fenced(errors)}\n"
        f"{notes}\n"
        "**How to fix**\n\n"
        "1. Let the agent rerun the self-review on the head you are going to push and rewrite the"
        " section in `.scratch/<branch>/pr.md`. Do not just edit the SHA. If the review leads to"
        " fixes, commit them and review the new head.\n"
        "2. Tick the \"AI-assisted\" box in `pr.md`, if not done yet.\n"
        "3. Push the new commits, if any, and right after the push replace the body, either"
        " with `gh pr edit <number> --repo Akkudoktor-EOS/EOS --body-file .scratch/<branch>/pr.md`,"
        " or by hand: open the"
        " \"...\" menu of the pull request description on GitHub, choose \"Edit\", replace the"
        " whole text with the content of `pr.md` and click \"Update comment\". Editing the body"
        " reruns the check, and this comment is updated.\n\n"
        "Repeat this before every later push.\n\n"
        "Expected section at the end of the body; the check requires the `Reviewed:` line, the"
        " other lines are expected from agents:\n\n"
        f"{_fenced(SELF_REVIEW_FORMAT, 'markdown')}\n\n"
        f"See [Self-review and the pull request check]({DOCS_URL}) in `docs/develop/ai.md`.\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the check from the command line.

    Args:
        argv: Command line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        0 when the check passes or does not apply, 1 when it fails.
    """
    parser = argparse.ArgumentParser(
        description="Check the self-review declaration of agent-assisted pull requests."
    )
    parser.add_argument("--body-file", type=Path, required=True, help="pull request body")
    parser.add_argument(
        "--commits-file", type=Path, required=True, help="JSON Lines file of commit messages"
    )
    parser.add_argument("--head-sha", required=True, help="full SHA of the head commit")
    parser.add_argument("--title", default="", help="pull request title")
    parser.add_argument("--author", default="", help="login of the pull request author")
    parser.add_argument(
        "--report-file", type=Path, help="write the Markdown report for the comment and summary"
    )
    args = parser.parse_args(argv)

    result = check_pull_request(
        body=args.body_file.read_text(encoding="utf-8"),
        commit_messages=read_commit_messages(args.commits_file),
        head_sha=args.head_sha,
        title=args.title,
        author=args.author,
    )
    if args.report_file is not None:
        args.report_file.write_text(render_report(result, args.head_sha), encoding="utf-8")
    if result.skipped_reason:
        print(f"Self-review check skipped: {result.skipped_reason}.")
        return 0
    if not result.agent_assisted:
        print(
            "Not agent-assisted: no agent trailer in the commits, no agent marker in the body,"
            " 'AI-assisted' box not ticked."
        )
        return 0
    for note in result.notes:
        print(f"::warning::{note}")
    if result.passed:
        print("Agent-assisted pull request with an up-to-date self-review declaration.")
        return 0
    print("Agent-assisted pull request: the self-review declaration is incomplete.")
    for error in result.errors:
        print(f"::error::{error}")
    print("\nExpected section in the pull request body (see docs/develop/ai.md):\n")
    print(SELF_REVIEW_FORMAT)
    return 1


if __name__ == "__main__":
    sys.exit(main())
