"""Tests for the AI agent development support in ``ai/``."""

import json
import re
import stat
import sys
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import git
import pytest

DIR_PROJECT_ROOT = Path(__file__).parent.parent
if str(DIR_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DIR_PROJECT_ROOT))

from scripts import check_pr_self_review as self_review_check  # noqa: E402
from scripts import update_mattpocock_skills as skills_updater  # noqa: E402


class KeyValueCache(Protocol):
    """The part of ``pytest.Cache`` used here."""

    def get(self, key: str, default: Any) -> Any:
        """Return the cached value for a key, or ``default``."""
        ...

    def set(self, key: str, value: object) -> None:
        """Store a JSON-serializable value for a key."""
        ...


class TestMattpocockSkills:
    """Vendored mattpocock engineering skills in ``ai/skills/engineering``."""

    # Key and time to live of the upstream hashes in the pytest cache.
    UPSTREAM_CACHE_KEY = "ai/mattpocock-skills/upstream-hashes"
    UPSTREAM_CACHE_TTL_SECONDS = 24 * 60 * 60

    # Files of the fake upstream repository, by repository path.
    UPSTREAM_FILES = {
        "LICENSE": b"MIT",
        "README.md": b"readme",
        "skills/engineering/tdd/SKILL.md": b"tdd",
        "skills/engineering/wizard/template.sh": b"#!/bin/sh\n",
        "skills/productivity/grilling/SKILL.md": b"grilling",
    }
    # The vendored subset, by path in the local skills directory.
    VENDORED_FILES = {
        "LICENSE": b"MIT",
        "tdd/SKILL.md": b"tdd",
        "wizard/template.sh": b"#!/bin/sh\n",
    }

    @staticmethod
    def read_files(directory: Path) -> dict[str, bytes]:
        """Return all files below a directory, keyed by relative POSIX path."""
        return {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }

    @staticmethod
    def fetch_upstream_hashes() -> skills_updater.SkillHashes:
        """Fetch the upstream hashes from the default upstream repository."""
        with skills_updater.upstream_clone() as repo:
            return skills_updater.upstream_hashes(repo)

    @classmethod
    def cached_upstream_hashes(
        cls,
        cache: KeyValueCache,
        fetch: Callable[[], skills_updater.SkillHashes],
        now: float,
    ) -> skills_updater.SkillHashes:
        """Return the upstream hashes, fetched at most once per time to live.

        Args:
            cache: Cache that keeps the hashes between test runs, usually the
                pytest cache.
            fetch: Fetches the upstream hashes.
            now: Current time in seconds since the epoch.

        Returns:
            The cached hashes if they are younger than the time to live and
            were fetched from the same upstream, otherwise freshly fetched ones.

        Raises:
            UpstreamSkillsError: If the hashes must be fetched and that fails.
                Nothing is cached then.
        """
        upstream = f"{skills_updater.UPSTREAM_URL}@{skills_updater.UPSTREAM_DEFAULT_REF}"
        entry = cache.get(cls.UPSTREAM_CACHE_KEY, None)
        if (
            isinstance(entry, dict)
            and entry.get("upstream") == upstream
            and isinstance(entry.get("fetched_at"), (int, float))
            and 0 <= now - entry["fetched_at"] < cls.UPSTREAM_CACHE_TTL_SECONDS
            and isinstance(entry.get("hashes"), dict)
        ):
            return entry["hashes"]

        hashes = fetch()
        cache.set(
            cls.UPSTREAM_CACHE_KEY, {"upstream": upstream, "fetched_at": now, "hashes": hashes}
        )
        return hashes

    @pytest.fixture
    def memory_cache(self) -> KeyValueCache:
        """Return an in-memory stand-in for the pytest cache."""

        class MemoryCache(dict[str, Any]):
            def set(self, key: str, value: object) -> None:
                self[key] = value

        return MemoryCache()

    @pytest.fixture
    def skills_dir(self, tmp_path: Path) -> Path:
        """Return a not yet existing local skills directory.

        A subdirectory keeps the files other fixtures put into ``tmp_path``
        (e.g. the EOS configuration) out of the skill files.
        """
        return tmp_path / "engineering"

    @pytest.fixture
    def upstream_url(self, tmp_path: Path) -> str:
        """Create a local upstream repository and return its ``file://`` URL."""
        repo_dir = tmp_path / "upstream"
        repo = git.Repo.init(repo_dir, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("uploadpack", "allowFilter", "true")
        for repo_path, content in self.UPSTREAM_FILES.items():
            path = repo_dir / repo_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            if path.suffix == ".sh":
                path.chmod(0o755)
        repo.index.add(list(self.UPSTREAM_FILES))
        actor = git.Actor("Upstream", "upstream@example.com")
        repo.index.commit("Add skills", author=actor, committer=actor)
        repo.close()
        return repo_dir.as_uri()

    def test_engineering_skills_match_upstream(self, pytestconfig: pytest.Config) -> None:
        """Warn if the vendored skills differ from the upstream repository.

        Compares git blob hashes from a blobless clone; no skill files are
        downloaded. The upstream hashes are kept in the pytest cache and
        fetched at most once a day; ``pytest --cache-clear`` forces a fetch.
        """
        if pytestconfig.cache is None:
            pytest.skip("Needs the pytest cache provider")
        try:
            upstream = self.cached_upstream_hashes(
                pytestconfig.cache, self.fetch_upstream_hashes, time.time()
            )
        except skills_updater.UpstreamSkillsError as exc:
            pytest.skip(f"Upstream skills not reachable: {exc}")

        local = skills_updater.local_hashes(skills_updater.DEFAULT_TARGET_DIR)
        diff = skills_updater.diff_skills(local, upstream)
        if not diff.is_empty():
            warnings.warn(
                f"ai/skills/engineering differs from {skills_updater.UPSTREAM_REPOSITORY}"
                f"@{skills_updater.UPSTREAM_DEFAULT_REF}:\n{diff.summary()}\n"
                f"Run '{skills_updater.UPDATE_COMMAND}' to update.",
                UserWarning,
                stacklevel=1,
            )

    def test_cached_upstream_hashes(self, memory_cache: KeyValueCache) -> None:
        """Upstream hashes are fetched once per time to live and reused in between."""
        fetched: list[skills_updater.SkillHashes] = []

        def fetch() -> skills_updater.SkillHashes:
            fetched.append({"LICENSE": f"hash{len(fetched)}"})
            return fetched[-1]

        ttl = self.UPSTREAM_CACHE_TTL_SECONDS
        assert self.cached_upstream_hashes(memory_cache, fetch, 1000.0) == {"LICENSE": "hash0"}
        assert self.cached_upstream_hashes(memory_cache, fetch, 1000.0 + ttl - 1) == {
            "LICENSE": "hash0"
        }
        assert len(fetched) == 1

        assert self.cached_upstream_hashes(memory_cache, fetch, 1000.0 + ttl) == {
            "LICENSE": "hash1"
        }
        assert len(fetched) == 2

    @pytest.mark.parametrize(
        "entry",
        [
            {"upstream": "https://example.com/other.git@main", "fetched_at": 1000.0},
            {"fetched_at": 2000.0},  # from the future
            {"fetched_at": "yesterday"},
            "garbage",
        ],
    )
    def test_cached_upstream_hashes_refetches_unusable_entry(
        self, memory_cache: KeyValueCache, entry: Any
    ) -> None:
        """Entries of another upstream, with a bad timestamp or malformed are not used."""
        upstream = f"{skills_updater.UPSTREAM_URL}@{skills_updater.UPSTREAM_DEFAULT_REF}"
        if isinstance(entry, dict):
            entry = {"upstream": upstream, "hashes": {"LICENSE": "stale"}, **entry}
        memory_cache.set(self.UPSTREAM_CACHE_KEY, entry)

        hashes = self.cached_upstream_hashes(memory_cache, lambda: {"LICENSE": "fresh"}, 1000.0)

        assert hashes == {"LICENSE": "fresh"}
        assert memory_cache.get(self.UPSTREAM_CACHE_KEY, None)["hashes"] == hashes

    def test_cached_upstream_hashes_does_not_cache_errors(
        self, memory_cache: KeyValueCache
    ) -> None:
        """A failed fetch is raised and leaves the cache empty, so the next run retries."""

        def fail() -> skills_updater.SkillHashes:
            raise skills_updater.UpstreamSkillsError("offline")

        with pytest.raises(skills_updater.UpstreamSkillsError):
            self.cached_upstream_hashes(memory_cache, fail, 1000.0)
        assert memory_cache.get(self.UPSTREAM_CACHE_KEY, None) is None

    def test_upstream_hashes_match_local_hashes(self, upstream_url: str, skills_dir: Path) -> None:
        """Hashes from the upstream trees equal the hashes of the same local files."""
        skills_dir.mkdir()
        for relative_path, content in self.VENDORED_FILES.items():
            (skills_dir / relative_path).parent.mkdir(parents=True, exist_ok=True)
            (skills_dir / relative_path).write_bytes(content)

        with skills_updater.upstream_clone(upstream_url) as repo:
            upstream = skills_updater.upstream_hashes(repo)

        assert upstream == skills_updater.local_hashes(skills_dir)
        assert set(upstream) == set(self.VENDORED_FILES)

    def test_upstream_clone_unknown_ref(self, upstream_url: str) -> None:
        """An unknown branch or tag is reported as upstream error."""
        with pytest.raises(skills_updater.UpstreamSkillsError, match="Cannot clone"):
            with skills_updater.upstream_clone(upstream_url, "no-such-branch"):
                pass

    def test_local_hashes_missing_directory(self, skills_dir: Path) -> None:
        """A missing local directory has no files."""
        assert skills_updater.local_hashes(skills_dir) == {}

    def test_diff_skills(self) -> None:
        """Added, removed and changed files are reported sorted by path."""
        local = {"same.md": "1", "changed.md": "2", "removed.md": "3"}
        upstream = {"same.md": "1", "changed.md": "4", "added.md": "5"}

        diff = skills_updater.diff_skills(local, upstream)

        assert diff == skills_updater.SkillsDiff(
            added=["added.md"], removed=["removed.md"], changed=["changed.md"]
        )
        assert not diff.is_empty()
        assert skills_updater.diff_skills(upstream, upstream).is_empty()

    def test_main_check_reports_difference(
        self,
        upstream_url: str,
        skills_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--check`` exits with 1 and neither fetches nor writes files."""

        def fail_update(*args: object) -> None:
            raise AssertionError("--check must not update")

        monkeypatch.setattr(skills_updater, "update_skills", fail_update)

        status = skills_updater.main(["--check", "--url", upstream_url, "--target", str(skills_dir)])

        assert status == 1
        assert not skills_dir.exists()
        assert skills_updater.UPDATE_COMMAND in capsys.readouterr().out

    def test_main_updates(self, upstream_url: str, skills_dir: Path) -> None:
        """An update makes the local directory identical to upstream."""
        (skills_dir / "stale").mkdir(parents=True)
        (skills_dir / "stale" / "SKILL.md").write_bytes(b"stale")
        (skills_dir / "LICENSE").write_bytes(b"old license")
        args = ["--url", upstream_url, "--target", str(skills_dir)]

        assert skills_updater.main(args) == 0

        assert self.read_files(skills_dir) == self.VENDORED_FILES
        assert not (skills_dir / "stale").exists()
        assert (skills_dir / "wizard" / "template.sh").stat().st_mode & stat.S_IXUSR
        assert skills_updater.main(["--check", *args]) == 0

    def test_main_upstream_error(self, tmp_path: Path, skills_dir: Path) -> None:
        """An unreachable upstream exits with 2."""
        missing_url = (tmp_path / "missing").as_uri()

        assert skills_updater.main(["--url", missing_url, "--target", str(skills_dir)]) == 2


class TestPrSelfReviewCheck:
    """CI check of the self-review declaration in ``scripts/check_pr_self_review.py``."""

    HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
    OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
    AGENT_COMMIT = "feat: add x\n\nAssisted-by: Claude Code (claude-opus-5-5)\n"
    CLAUDE_COMMIT = "fix: y\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    HUMAN_COMMIT = "fix: z\n\nCo-Authored-By: Jane Doe <jane@example.com>\n"

    @classmethod
    def body(cls, ticked: bool = True, review: str | None = None) -> str:
        """Return a pull request body following the template."""
        box = "x" if ticked else " "
        if review is None:
            review = (
                f"Reviewed: {cls.HEAD_SHA}\n"
                "Base: main\n"
                "Standards: 0 hard, 2 judgement\n"
                "Spec: 1 open (source: .scratch/chore/ai_qa/plan.md)\n\n"
                "- judgement: possible Feature Envy, left as is\n"
            )
        return (
            "## Summary\n\nSomething.\n\n"
            f"- [{box}] AI-assisted (an agent wrote or co-wrote part of this PR)\n"
            "- [x] I reviewed the complete diff myself (author review)\n\n"
            "<!-- The agent fills in the section below. -->\n"
            f"## Self-review\n\n{review}"
        )

    def check(
        self, body: str, commits: list[str], title: str = "feat: x", author: str = "someone"
    ) -> self_review_check.CheckResult:
        """Run the check for the head SHA."""
        return self_review_check.check_pull_request(body, commits, self.HEAD_SHA, title, author)

    def test_not_agent_assisted_passes(self) -> None:
        """Human-only commits and an unticked box need no self-review."""
        result = self.check("## Summary\n\n- [ ] AI-assisted\n", [self.HUMAN_COMMIT])

        assert not result.agent_assisted
        assert result.passed

    @pytest.mark.parametrize("commit", [AGENT_COMMIT, CLAUDE_COMMIT])
    def test_complete_self_review_passes(self, commit: str) -> None:
        """A ticked box and a fresh, complete self-review pass, open findings included."""
        result = self.check(self.body(), [self.HUMAN_COMMIT, commit])

        assert result.agent_assisted
        assert result.errors == []

    def test_ticked_box_without_trailer_needs_self_review(self) -> None:
        """A ticked box alone makes the pull request agent-assisted."""
        result = self.check(self.body(review="nothing\n").replace("## Self-review", "## Notes"), [])

        assert result.agent_assisted
        assert result.errors == ["The pull request body has no '## Self-review' section."]

    def test_trailer_without_ticked_box_is_noted(self) -> None:
        """An agent trailer with an unticked box passes with a note."""
        result = self.check(self.body(ticked=False), [self.AGENT_COMMIT])

        assert result.passed
        assert len(result.notes) == 1
        assert "box" in result.notes[0]

    @pytest.mark.parametrize(
        "marker",
        [
            "Assisted-by: OpenCode (qwen3-coder)",
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        ],
    )
    def test_body_marker_makes_agent_assisted(self, marker: str) -> None:
        """An agent marker in the body counts; an unticked box is noted."""
        ticked = self.check(self.body() + f"\n{marker}\n", [self.HUMAN_COMMIT])
        unticked = self.check(self.body(ticked=False) + f"\n{marker}\n", [self.HUMAN_COMMIT])

        assert ticked.agent_assisted
        assert ticked.errors == []
        assert ticked.notes == []
        assert unticked.agent_assisted
        assert unticked.passed
        assert len(unticked.notes) == 1
        assert "agent marker" in unticked.notes[0]

    def test_marker_without_section_fails(self) -> None:
        """An agent marker makes the section with the SHA required, ticked box or not."""
        body = "- [ ] AI-assisted\n\nAssisted-by: OpenCode (qwen3-coder)\n"

        result = self.check(body, [self.HUMAN_COMMIT])

        assert "The pull request body has no '## Self-review' section." in result.errors

    def test_body_marker_in_html_comment_does_not_count(self) -> None:
        """A marker inside a template comment is not a signal."""
        body = "- [ ] AI-assisted\n<!-- e.g. Assisted-by: Claude Code (claude-opus-5-5) -->\n"

        result = self.check(body, [self.HUMAN_COMMIT])

        assert not result.agent_assisted

    def test_stale_self_review_fails(self) -> None:
        """A self-review for another commit than the head fails."""
        body = self.body().replace(self.HEAD_SHA, self.OTHER_SHA)

        result = self.check(body, [self.AGENT_COMMIT])

        assert len(result.errors) == 1
        assert self.OTHER_SHA in result.errors[0]
        assert self.HEAD_SHA in result.errors[0]

    def test_abbreviated_sha_fails(self) -> None:
        """An abbreviated SHA is rejected, even if it is a prefix of the head."""
        body = self.body().replace(self.HEAD_SHA, self.HEAD_SHA[:12])

        result = self.check(body, [self.AGENT_COMMIT])

        assert len(result.errors) == 1
        assert "full 40-character" in result.errors[0]

    def test_missing_sha_fails(self) -> None:
        """A section without the Reviewed line fails; missing optional lines are not reported."""
        result = self.check(self.body(review="- looks fine\n"), [self.AGENT_COMMIT])

        assert result.errors == ["The self-review has no 'Reviewed: <sha>' line."]
        assert result.notes == []

    def test_only_sha_passes(self) -> None:
        """The Reviewed line alone is enough to pass."""
        result = self.check(self.body(review=f"Reviewed: {self.HEAD_SHA}\n"), [self.AGENT_COMMIT])

        assert result.passed
        assert result.notes == []

    def test_uppercase_sha_fails(self) -> None:
        """A full SHA in uppercase is malformed."""
        body = self.body().replace(self.HEAD_SHA, self.HEAD_SHA.upper())

        result = self.check(body, [self.AGENT_COMMIT])

        assert len(result.errors) == 1
        assert "lowercase" in result.errors[0]

    @pytest.mark.parametrize(
        ("good", "bad", "key"),
        [
            ("Standards: 0 hard, 2 judgement", "Standards: two hard, 2 judgement", "Standards:"),
            ("Standards: 0 hard, 2 judgement", "Standards: 0 hard", "Standards:"),
            (
                "Spec: 1 open (source: .scratch/chore/ai_qa/plan.md)",
                "Spec: 1 open (source: )",
                "Spec:",
            ),
            ("Spec: 1 open (source: .scratch/chore/ai_qa/plan.md)", "Spec: 1 open", "Spec:"),
            ("Base: main", "Base:", "Base:"),
        ],
        ids=["standards-word", "standards-short", "spec-empty-source", "spec-no-source", "base"],
    )
    def test_malformed_optional_lines_are_noted(self, good: str, bad: str, key: str) -> None:
        """A present but malformed optional line passes with a note."""
        body = self.body()
        assert good in body

        result = self.check(body.replace(good, bad), [self.AGENT_COMMIT])

        assert result.passed
        assert len(result.notes) == 1
        assert f"'{key}" in result.notes[0]

    def test_section_inside_html_comment_does_not_count(self) -> None:
        """An example section in a template comment is not a declaration."""
        body = self.body().replace("## Self-review", "<!--\n## Self-review") + "-->\n"

        result = self.check(body, [self.AGENT_COMMIT])

        assert result.errors == ["The pull request body has no '## Self-review' section."]

    def test_section_ends_at_next_heading(self) -> None:
        """Fields after the next heading do not belong to the self-review."""
        body = self.body(review=f"Reviewed: {self.HEAD_SHA}\n\n## Other\n\nBase: main\n")

        result = self.check(body, [self.AGENT_COMMIT])

        assert result.passed
        assert result.notes == []

    def test_windows_line_endings_pass(self) -> None:
        """A body and commits saved with CRLF line endings are checked like LF ones."""
        body = self.body().replace("\n", "\r\n")
        commit = self.AGENT_COMMIT.replace("\n", "\r\n")

        result = self.check(body, [commit])

        assert result.agent_assisted
        assert result.errors == []
        assert result.notes == []

    def test_report_lists_notes(self) -> None:
        """Notes appear in the report of a passing and of a failing check."""
        passing = self.check(self.body(ticked=False), [self.AGENT_COMMIT])
        failing = self.check(
            self.body(ticked=False).replace(self.HEAD_SHA, self.OTHER_SHA), [self.AGENT_COMMIT]
        )

        for result in (passing, failing):
            report = self_review_check.render_report(result, self.HEAD_SHA)
            assert "do not fail the check" in report
            assert result.notes[0] in report

    @pytest.mark.parametrize(
        ("title", "author"),
        [("chore: bump x", "dependabot[bot]"), ("Release 1.2.3", "someone")],
    )
    def test_skipped_pull_requests(self, title: str, author: str) -> None:
        """Dependabot and release pull requests are not checked."""
        result = self.check("", [self.AGENT_COMMIT], title=title, author=author)

        assert result.skipped_reason
        assert result.passed

    def test_main_exit_codes(self, tmp_path: Path) -> None:
        """The command line exits 0 for a valid and 1 for a stale declaration."""
        body_file = tmp_path / "body.md"
        commits_file = tmp_path / "commits.jsonl"
        body_file.write_text(self.body(), encoding="utf-8")
        commits_file.write_text(
            "\n".join(json.dumps(m) for m in [self.HUMAN_COMMIT, self.AGENT_COMMIT]) + "\n",
            encoding="utf-8",
        )
        args = ["--body-file", str(body_file), "--commits-file", str(commits_file)]

        assert self_review_check.main([*args, "--head-sha", self.HEAD_SHA]) == 0
        assert self_review_check.main([*args, "--head-sha", self.OTHER_SHA]) == 1

    def test_report_for_failure_explains_the_fix(self) -> None:
        """A failing check renders the marked comment with the errors and how to fix them."""
        body = self.body().replace(self.HEAD_SHA, self.OTHER_SHA)
        result = self.check(body, [self.AGENT_COMMIT])

        report = self_review_check.render_report(result, self.HEAD_SHA)

        assert report.startswith(self_review_check.COMMENT_MARKER + "\n")
        assert "check failed" in report
        assert f"- {result.errors[0]}" in report
        assert "How to fix" in report
        assert "gh pr edit" in report
        assert "Reviewed: <full 40-character SHA" in report

    def test_report_keeps_author_text_inside_the_code_block(self) -> None:
        """Backticks from the body cannot close the code block and inject Markdown."""
        body = self.body().replace(self.HEAD_SHA, "````\n@someone")
        result = self.check(body, [self.AGENT_COMMIT])

        report = self_review_check.render_report(result, self.HEAD_SHA)

        fence_match = re.search(r"(?m)^(`{4,})$", report)
        assert fence_match is not None
        fence = fence_match.group(1)
        block = report[fence_match.end() : report.index("\n" + fence + "\n", fence_match.end())]
        assert "@someone" not in report.replace(block, "")
        assert "````" in block

    @pytest.mark.parametrize(
        ("body", "commits", "title", "expected"),
        [
            ("BODY", ["AGENT"], "feat: x", "up to date for"),
            ("- [ ] AI-assisted\n", ["HUMAN"], "feat: x", "not agent-assisted"),
            ("", ["AGENT"], "Release 1.0.0", "does not apply"),
        ],
    )
    def test_report_for_passing_check(
        self, body: str, commits: list[str], title: str, expected: str
    ) -> None:
        """A passing check renders a short marked note."""
        messages = {"AGENT": self.AGENT_COMMIT, "HUMAN": self.HUMAN_COMMIT}
        result = self.check(
            self.body() if body == "BODY" else body, [messages[c] for c in commits], title=title
        )

        report = self_review_check.render_report(result, self.HEAD_SHA)

        assert report.startswith(self_review_check.COMMENT_MARKER + "\n")
        assert "check passed" in report
        assert expected in report

    def test_main_writes_report_file(self, tmp_path: Path) -> None:
        """``--report-file`` receives the report for both outcomes."""
        body_file = tmp_path / "body.md"
        commits_file = tmp_path / "commits.jsonl"
        report_file = tmp_path / "report.md"
        body_file.write_text(self.body(), encoding="utf-8")
        commits_file.write_text(json.dumps(self.AGENT_COMMIT) + "\n", encoding="utf-8")
        args = ["--body-file", str(body_file), "--commits-file", str(commits_file)]

        assert self_review_check.main(
            [*args, "--head-sha", self.HEAD_SHA, "--report-file", str(report_file)]
        ) == 0
        assert "check passed" in report_file.read_text(encoding="utf-8")
        assert self_review_check.main(
            [*args, "--head-sha", self.OTHER_SHA, "--report-file", str(report_file)]
        ) == 1
        assert "check failed" in report_file.read_text(encoding="utf-8")

    def test_workflow_uses_the_script_marker(self) -> None:
        """The workflow finds its comment by the same marker the script writes."""
        workflow = (DIR_PROJECT_ROOT / ".github" / "workflows" / "ai-self-review.yml").read_text(
            encoding="utf-8"
        )

        assert f'COMMENT_MARKER: "{self_review_check.COMMENT_MARKER}"' in workflow

    def test_report_for_failure_links_the_docs(self) -> None:
        """The failure comment links the docs and covers the push."""
        result = self.check(self.body(review="- nothing\n"), [self.AGENT_COMMIT])

        report = self_review_check.render_report(result, self.HEAD_SHA)

        assert f"]({self_review_check.DOCS_URL})" in report
        assert "right after the push" in report
        assert "before every later push" in report
        assert "gh pr edit" in report
        assert "by hand" in report

    def test_read_commit_messages_rejects_non_strings(self, tmp_path: Path) -> None:
        """A commits file line that is not a JSON string is an error."""
        commits_file = tmp_path / "commits.jsonl"
        commits_file.write_text('{"message": "x"}\n', encoding="utf-8")

        with pytest.raises(ValueError, match="expected a JSON string"):
            self_review_check.read_commit_messages(commits_file)
