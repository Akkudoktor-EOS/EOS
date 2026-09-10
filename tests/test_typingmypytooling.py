"""Exercise the typing entry points with real runtime dependency types."""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mypy_entry_points(tmp_path: Path, is_finalize: bool) -> None:
    """Check valid and invalid dependency types in a fresh locked environment.

    This integration test installs the locked development dependencies with uv.
    Only the mypy hook is copied, so unrelated formatting hooks cannot modify the probe.
    The project checkout, virtual environment and pre-commit/mypy caches are temporary.
    """
    if not is_finalize:
        pytest.skip("Typing toolchain integration requires --finalize (installs dependencies).")
    if shutil.which("uv") is None or shutil.which("make") is None:
        pytest.skip("Typing entry point integration requires uv and make on PATH.")

    for name in ("Makefile", "pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE"):
        shutil.copy2(PROJECT_ROOT / name, tmp_path / name)
    (tmp_path / "version.txt").write_text("0.0.0", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    source_probe = tmp_path / "src" / "typing_probe.py"
    test_probe = tmp_path / "tests" / "test_typing_probe.py"

    config = yaml.safe_load((PROJECT_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    mypy_repos = []
    for repo in config["repos"]:
        hooks = [hook for hook in repo["hooks"] if hook["id"] == "mypy"]
        if hooks:
            mypy_repos.append({**repo, "hooks": hooks})
    assert mypy_repos, "The pre-commit mypy hook must exist."
    (tmp_path / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump({**config, "repos": mypy_repos}), encoding="utf-8"
    )
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/pre-commit.yml").read_text(encoding="utf-8")
    )
    ci_command = next(
        step["run"]
        for step in workflow["jobs"]["pre-commit"]["steps"]
        if "pre-commit run" in step.get("run", "")
    )

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / ".venv")
    env["PRE_COMMIT_HOME"] = str(tmp_path / "pre-commit-cache")
    env["MYPY_CACHE_DIR"] = str(tmp_path / "mypy-cache")
    env["NO_COLOR"] = "1"

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=300
        )

    initialized = run(["git", "init", "--quiet"])
    assert initialized.returncode == 0, initialized.stderr

    # A configuration-only hook run must still check the complete src/tests scope.
    commands = [
        ["make", "mypy"],
        [
            "uv",
            "run",
            "--locked",
            "--extra",
            "dev",
            "pre-commit",
            "run",
            "mypy",
            "--files",
            "pyproject.toml",
        ],
        shlex.split(ci_command),
    ]
    source_probe.write_text(
        "from pydantic import BaseModel\nmodel: BaseModel = BaseModel()\n", encoding="utf-8"
    )
    test_probe.write_text(
        "from pendulum import DateTime\ninstant: DateTime = DateTime(2026, 1, 1)\n",
        encoding="utf-8",
    )
    for command in commands:
        result = run(command)
        assert result.returncode == 0, result.stdout + result.stderr

    source_probe.write_text(
        "from pydantic import BaseModel\nmodel: BaseModel = 1\n", encoding="utf-8"
    )
    test_probe.write_text(
        'from pendulum import DateTime\ninstant: DateTime = "invalid"\n', encoding="utf-8"
    )
    outputs = []
    for command in commands:
        result = run(command)
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        diagnostics = sorted(line for line in output.splitlines() if re.search(r":\d+: error:", line))
        assert len(diagnostics) == 2, output
        assert all("[assignment]" in line for line in diagnostics), output
        assert any('variable has type "BaseModel"' in line for line in diagnostics), output
        assert any('variable has type "DateTime"' in line for line in diagnostics), output
        outputs.append(diagnostics)
    assert outputs[0] == outputs[1] == outputs[2]
