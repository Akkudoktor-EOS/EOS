---
name: python-environment
description: Use before running any Python, uv, pytest, mypy, ruff, pre-commit or make command. Keeps the agent's virtual environment out of the workspace .venv.
---

# Python Environment

## Why

The workspace may be shared with the developer's machine (e.g. a mounted
directory or a container). The workspace `.venv` then belongs to the developer
and is bound to an interpreter on their machine, which does not exist for the
agent. `uv` treats such a `.venv` as broken: it deletes and recreates it for
the agent's interpreter, which breaks the developer's environment, and the
developer's next `uv` call breaks the agent's in turn (e.g. dev tools such as
pytest or mypy go missing).

## Rules

- **Never use, create, modify or delete the workspace `.venv`.**
- Use a virtual environment outside the workspace, selected by
  `UV_PROJECT_ENVIRONMENT`, e.g.:

  ```bash
  export UV_PROJECT_ENVIRONMENT="$HOME/.cache/eos-venv"
  ```

  Put it on a disk with enough space (the environment needs several hundred
  MB), not on a small tmpfs such as `/tmp`.
- Set the variable in **every** shell command that runs `uv` or `make`, e.g.
  `export UV_PROJECT_ENVIRONMENT=... && <command>`. Exported variables do not
  carry over between separate tool calls.
- It applies to all commands that run Python through `uv`:
  `uv sync`, `uv run ...` (pytest, mypy, python scripts, `pre-commit`), and the
  `make` targets that use `uv run` (`make mypy`, `make gen-docs`, `make test`,
  ...).
- Create or update the environment with the dev tools:

  ```bash
  uv sync --extra dev
  ```

  A plain `uv sync` (without `--extra dev`) removes pytest, mypy and the other
  dev tools from the environment.
- Do not let a newer local `uv` rewrite `uv.lock`: use `uv sync --frozen` /
  `uv run --frozen`. When the lock must change (dependencies changed), lock with
  the `uv` version CI uses (`.github/workflows/pre-commit.yml`), e.g.
  `uv tool run --from uv==<version> uv lock`, and check that `revision` in
  `uv.lock` did not change.
- Do not run commands that remove the workspace `.venv`, e.g. `make clean`
  (`rm -rf build .venv`) or `rm -rf .venv`.

## Verify

- `uv run python -c "import sys; print(sys.prefix)"` prints the environment
  outside the workspace, not `<workspace>/.venv`.
- If `uv` reports `Removed virtual environment at: .venv` or
  `Ignoring existing virtual environment linked to non-existent Python
  interpreter`, the variable was not set: the workspace `.venv` was rebuilt.
  Do not try to repair it; tell the developer to run `uv sync --extra dev` on
  their machine.
