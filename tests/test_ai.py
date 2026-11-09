"""Tests for the AI agent development support in ``ai/``."""

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
