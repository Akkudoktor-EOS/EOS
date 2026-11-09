"""Update the vendored mattpocock engineering skills from GitHub.

The engineering skills in ``ai/skills/engineering`` are a copy of
``skills/engineering`` from https://github.com/mattpocock/skills, plus the
repository's ``LICENSE`` file.

The local copy is compared with upstream by git blob hashes. A shallow,
blobless clone fetches only the upstream commit and its trees, which hold
the hash of every file; the local hashes come from ``git hash-object``. File
contents are fetched only when the hashes differ and an update is requested.

Usage:
    Update the local copy from the upstream ``main`` branch::

        uv run python scripts/update_mattpocock_skills.py

    Only report differences, without fetching or changing files::

        uv run python scripts/update_mattpocock_skills.py --check

    Update from another branch or a tag::

        uv run python scripts/update_mattpocock_skills.py --ref v1.3.1

Exit status:
    0: The local copy is up to date, or it was updated.
    1: ``--check`` was given and the local copy differs from upstream.
    2: Upstream could not be cloned or does not contain the skills.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import git

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET_DIR = PROJECT_ROOT / "ai" / "skills" / "engineering"
UPSTREAM_REPOSITORY = "mattpocock/skills"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPOSITORY}.git"
UPSTREAM_DEFAULT_REF = "main"
UPSTREAM_SKILLS_DIR = "skills/engineering"
LICENSE_FILENAME = "LICENSE"

UPDATE_COMMAND = "uv run python scripts/update_mattpocock_skills.py"

# Path relative to the local skills directory -> git blob hash (hex).
SkillHashes = dict[str, str]


class UpstreamSkillsError(RuntimeError):
    """Raised when the upstream skills cannot be cloned or read."""


@dataclass
class SkillsDiff:
    """Differences between the local and the upstream skill files.

    Attributes:
        added: Files that exist upstream but not locally.
        removed: Files that exist locally but not upstream.
        changed: Files whose content differs.
    """

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return whether the local and upstream files are identical."""
        return not (self.added or self.removed or self.changed)

    def summary(self) -> str:
        """Return a human-readable, one line per file summary of the differences."""
        lines = [f"  added:   {path}" for path in self.added]
        lines += [f"  removed: {path}" for path in self.removed]
        lines += [f"  changed: {path}" for path in self.changed]
        return "\n".join(lines)


def upstream_repo_path(relative_path: str) -> str:
    """Return the upstream repository path of a file in the local skills directory.

    Args:
        relative_path: Path relative to the local skills directory.

    Returns:
        Path relative to the upstream repository root.
    """
    if relative_path == LICENSE_FILENAME:
        return LICENSE_FILENAME
    return f"{UPSTREAM_SKILLS_DIR}/{relative_path}"


@contextmanager
def upstream_clone(url: str = UPSTREAM_URL, ref: str = UPSTREAM_DEFAULT_REF) -> Iterator[git.Repo]:
    """Clone the upstream repository without file contents into a temporary directory.

    The clone is shallow and blobless: it holds the commit and its trees, but
    no file contents. Git fetches contents on demand, e.g. on checkout.

    Args:
        url: URL of the upstream repository.
        ref: Branch or tag to clone.

    Yields:
        The cloned repository. It is deleted when the context exits.

    Raises:
        UpstreamSkillsError: If the repository cannot be cloned.
    """
    with tempfile.TemporaryDirectory(prefix="mattpocock-skills-") as clone_dir:
        try:
            repo = git.Repo.clone_from(
                url,
                clone_dir,
                depth=1,
                branch=ref,
                single_branch=True,
                filter="blob:none",
                no_checkout=True,
            )
        except git.GitError as exc:
            raise UpstreamSkillsError(f"Cannot clone {url}@{ref}: {exc}") from exc
        try:
            yield repo
        finally:
            repo.close()


def upstream_hashes(repo: git.Repo) -> SkillHashes:
    """Return the blob hashes of the vendored upstream files.

    Only the trees of the clone are read; no file contents are fetched.

    Args:
        repo: Clone of the upstream repository.

    Returns:
        Blob hashes keyed by the path relative to the local skills directory.
        The upstream license is stored under ``LICENSE``.

    Raises:
        UpstreamSkillsError: If the license or the engineering skills are missing.
    """
    tree = repo.head.commit.tree
    try:
        license_blob = tree / LICENSE_FILENAME
        skills_tree = tree / UPSTREAM_SKILLS_DIR
    except KeyError as exc:
        raise UpstreamSkillsError(f"Upstream repository contains no {exc}") from exc

    hashes: SkillHashes = {}
    for item in skills_tree.traverse():
        if isinstance(item, git.Blob):
            relative_path = PurePosixPath(item.path).relative_to(UPSTREAM_SKILLS_DIR).as_posix()
            hashes[relative_path] = item.hexsha
    if not hashes:
        raise UpstreamSkillsError(f"Upstream repository contains no files in {UPSTREAM_SKILLS_DIR}")
    if LICENSE_FILENAME in hashes:
        raise UpstreamSkillsError(
            f"Upstream {UPSTREAM_SKILLS_DIR}/{LICENSE_FILENAME} clashes with the repository license"
        )
    hashes[LICENSE_FILENAME] = license_blob.hexsha
    return hashes


def local_hashes(target_dir: Path) -> SkillHashes:
    """Return the git blob hashes of the local skill files.

    Args:
        target_dir: Local engineering skills directory.

    Returns:
        Blob hashes keyed by the path relative to ``target_dir``. Empty if the
        directory does not exist.
    """
    if not target_dir.is_dir():
        return {}
    files = sorted(
        path
        for path in target_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    if not files:
        return {}
    # --no-filters: hash the raw file content, as stored upstream.
    hexshas = git.Git().hash_object("--no-filters", *map(str, files)).splitlines()
    return {
        path.relative_to(target_dir).as_posix(): hexsha
        for path, hexsha in zip(files, hexshas, strict=True)
    }


def diff_skills(local: Mapping[str, str], upstream: Mapping[str, str]) -> SkillsDiff:
    """Compare the local skill file hashes with the upstream ones.

    Args:
        local: Local blob hashes, keyed by relative path.
        upstream: Upstream blob hashes, keyed by relative path.

    Returns:
        The added, removed and changed files, each sorted by path.
    """
    return SkillsDiff(
        added=sorted(upstream.keys() - local.keys()),
        removed=sorted(local.keys() - upstream.keys()),
        changed=sorted(
            path for path in local.keys() & upstream.keys() if local[path] != upstream[path]
        ),
    )


def update_skills(repo: git.Repo, target_dir: Path, diff: SkillsDiff) -> None:
    """Make the local skills directory identical to upstream.

    Checks out the vendored upstream files, which fetches their contents,
    copies added and changed files with their file mode, deletes removed
    files and removes directories left empty.

    Args:
        repo: Blobless clone of the upstream repository.
        target_dir: Local engineering skills directory.
        diff: Differences between the local and the upstream files.

    Raises:
        UpstreamSkillsError: If the upstream files cannot be checked out.
    """
    try:
        repo.git.checkout("HEAD", "--", LICENSE_FILENAME, UPSTREAM_SKILLS_DIR)
    except git.GitError as exc:
        raise UpstreamSkillsError(f"Cannot check out upstream files: {exc}") from exc
    if repo.working_tree_dir is None:
        raise UpstreamSkillsError("Upstream clone has no working tree")
    clone_dir = Path(repo.working_tree_dir)

    for relative_path in diff.removed:
        (target_dir / relative_path).unlink()
    for relative_path in diff.added + diff.changed:
        destination = target_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clone_dir / upstream_repo_path(relative_path), destination)
    # Deepest directories first, so parents emptied by their children go too.
    for directory in sorted(target_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Command line arguments without the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit status, see the module docstring.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report differences; exit with status 1 if there are any",
    )
    parser.add_argument(
        "--ref",
        default=UPSTREAM_DEFAULT_REF,
        help=f"upstream branch or tag (default: {UPSTREAM_DEFAULT_REF})",
    )
    parser.add_argument(
        "--url",
        default=UPSTREAM_URL,
        help="upstream repository URL (default: %(default)s)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help="local engineering skills directory (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    upstream_name = f"{args.url}@{args.ref}"

    try:
        with upstream_clone(args.url, args.ref) as repo:
            diff = diff_skills(local_hashes(args.target), upstream_hashes(repo))
            if diff.is_empty():
                print(f"{args.target} is up to date with {upstream_name}.")
                return 0

            print(f"{args.target} differs from {upstream_name}:\n{diff.summary()}")
            if args.check:
                print(f"Run '{UPDATE_COMMAND}' to update.")
                return 1

            update_skills(repo, args.target, diff)
    except UpstreamSkillsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Updated {args.target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
