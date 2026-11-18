import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional, cast

import pytest

from akkudoktoreos.core.coreabc import singletons_init

if TYPE_CHECKING:
    from sphinx.domains.python import PythonDomain
    from sphinx.environment import BuildEnvironment

DIR_PROJECT_ROOT = Path(__file__).absolute().parent.parent
DIR_BUILD = DIR_PROJECT_ROOT / "build"
DIR_BUILD_DOCS = DIR_PROJECT_ROOT / "build" / "docs"
DIR_DOCS = DIR_PROJECT_ROOT / "docs"
DIR_SRC = DIR_PROJECT_ROOT / "src"

HASH_FILE = DIR_BUILD / ".sphinx_hash.json"

# Parallel jobs of the Sphinx build. Sphinx reads the sources and writes the pages in several
# processes; resolving the cross-references stays in the main process. With the EOS API
# reference each process needs up to 1.4 GB of memory, so the number is limited. Sphinx builds
# with one process where it cannot fork (Windows).
SPHINX_JOBS = min(4, os.cpu_count() or 1)


import os
import subprocess
from pathlib import Path


def find_sphinx_build() -> list[str]:
    """Return command to invoke sphinx-build via virtualenv, uv, or globally."""
    candidates = []

    # 1️⃣ Currently active virtualenv
    venv = os.getenv("VIRTUAL_ENV")
    if venv:
        candidates.append(Path(venv))

    # 2️⃣ uv‑managed virtualenv
    uv_venv = Path(".uv") / "venv"
    if uv_venv.exists():
        candidates.append(uv_venv)

    # 3️⃣ traditional .venv
    dot_venv = Path(".venv")
    if dot_venv.exists():
        candidates.append(dot_venv)

    # Check each candidate for the sphinx‑build binary
    for base in candidates:
        sphinx_build_path = base / ("Scripts" if os.name == "nt" else "bin") / (
            "sphinx-build.exe" if os.name == "nt" else "sphinx-build"
        )
        if sphinx_build_path.exists():
            return [str(sphinx_build_path)]

    # 4️⃣ fallback to uv run sphinx‑build
    try:
        subprocess.run(
            ["uv", "run", "sphinx-build", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ["uv", "run", "sphinx-build"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 5️⃣ final fallback to system sphinx‑build
    return ["sphinx-build"]


@pytest.fixture(scope="session")
def sphinx_changed(version_and_hash) -> Optional[str]:
    """Returns new hash if any watched files have changed since last run.

    Hash is stored in .sphinx_hash.json.
    """
    new_hash = None

    # Load previous hash
    try:
        previous = json.loads(HASH_FILE.read_text())
        previous_hash = previous.get("hash")
    except Exception:
        previous_hash = None

    changed = (previous_hash != version_and_hash["hash_current"])

    if changed:
        new_hash = version_and_hash["hash_current"]

    return new_hash


@pytest.mark.docs
class TestSphinxDocumentation:
    """Test class to verify Sphinx documentation generation.

    Ensures no major warnings are emitted.
    """

    SPHINX_CMD = find_sphinx_build() + [
        "-M",
        "html",
        str(DIR_DOCS),
        str(DIR_BUILD_DOCS),
        "-j",
        str(SPHINX_JOBS),
    ]

    def _cleanup_autosum_dirs(self):
        """Delete all *_autosum folders inside docs/."""
        for folder in DIR_DOCS.rglob("*_autosum"):
            if folder.is_dir():
                shutil.rmtree(folder)

    def _cleanup_build_dir(self):
        """Delete build/docs directory if present."""
        if DIR_BUILD_DOCS.exists():
            shutil.rmtree(DIR_BUILD_DOCS)

    def test_sphinx_build(self, sphinx_changed: Optional[str], is_finalize: bool):
        """Build Sphinx documentation and ensure no major warnings appear in the build output."""

        # Ensure docs folder exists
        if not DIR_DOCS.exists():
            pytest.skip(f"Skipping Sphinx build test - docs folder not present: {DIR_DOCS}")

        if not sphinx_changed:
            pytest.skip(f"Skipping Sphinx build — no relevant file changes detected: {HASH_FILE}")

        if not is_finalize:
            pytest.skip("Skipping Sphinx test — not finalize")

        # Clean directories
        self._cleanup_autosum_dirs()
        self._cleanup_build_dir()

        # Set environment for sphinx run (sphinx will make eos create a config file)
        eos_tmp_dir = tempfile.TemporaryDirectory()
        eos_dir = str(eos_tmp_dir.name)
        env = os.environ.copy()
        env["EOS_DIR"] = eos_dir
        env["EOS_CONFIG_DIR"] = eos_dir
        # An empty config file, so EOS does not fall back to (and migrate) the
        # user's own config file in the user config directory.
        Path(eos_dir, "EOS.config.json").write_text("{}", encoding="utf-8")

        try:
            process = subprocess.run(
                self.SPHINX_CMD,
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Sphinx writes UTF-8; the platform default (e.g. cp1252 on
                # Windows) cannot decode it.
                encoding="utf-8",
                errors="replace",
                cwd=DIR_PROJECT_ROOT,          # use the existing constant
            )
            output = process.stdout + "\n" + process.stderr
            returncode = process.returncode
        except subprocess.CalledProcessError as e:
            output = e.stdout + "\n" + e.stderr if e.stdout else ""
            returncode = e.returncode
        except Exception as e:
            output = f"Failed to execute command: {e}"
            returncode = -1

        # Remove temporary EOS_DIR
        eos_tmp_dir.cleanup()

        if returncode != 0:
            pytest.fail(
                f"Sphinx build failed with exit code {returncode}.\n"
                f"{output}\n"
            )

        # Possible markers: ERROR: WARNING: TRACEBACK:
        major_markers = ("ERROR:", "TRACEBACK:")

        bad_lines = [
            line for line in output.splitlines()
            if any(marker in line for marker in major_markers)
        ]

        assert not bad_lines, f"Sphinx build contained errors:\n" + "\n".join(bad_lines)

        # Update stored hash
        HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HASH_FILE.write_text(json.dumps({"hash": sphinx_changed}, indent=2))


# --- Local Sphinx extension docs/_ext/pyxrefindex.py --------------------------------

PYXREFINDEX_FILE = DIR_DOCS / "_ext" / "pyxrefindex.py"

# (name, objtype, docname): ambiguous names in several modules and classes, all Python
# object types, a top-level name without dots and an aliased duplicate.
PYXREFINDEX_OBJECTS = [
    ("eos", "module", "doc1"),
    ("eos.core", "module", "doc1"),
    ("eos.core.Config", "class", "doc1"),
    ("eos.core.Config.load", "method", "doc1"),
    ("eos.core.Config.from_file", "classmethod", "doc1"),
    ("eos.core.Config.value", "attribute", "doc1"),
    ("eos.core.Config.size", "property", "doc1"),
    ("eos.core.Settings", "class", "doc1"),
    ("eos.core.Settings.type", "attribute", "doc1"),
    ("eos.core.helper", "function", "doc1"),
    ("eos.core.make", "staticmethod", "doc1"),
    ("eos.core.LIMIT", "data", "doc1"),
    ("eos.core.Alias", "type", "doc1"),
    ("eos.core.Error", "exception", "doc1"),
    ("eos.devices.Battery", "class", "doc2"),
    ("eos.devices.Battery.load", "method", "doc2"),
    ("eos.devices.Battery.value", "property", "doc2"),
    ("eos.devices.Settings", "class", "doc2"),
    ("eos.devices.Settings.type", "attribute", "doc2"),
    ("eos.devices.helper", "data", "doc2"),
    ("Top", "class", "doc2"),
    ("Top.load", "method", "doc2"),
]


class PyXrefIndexEnvironment:
    """The part of a Sphinx build environment that the Python domain uses in these tests.

    A real Sphinx application would register its directives and roles in docutils for the
    rest of the test process.
    """

    def __init__(self) -> None:
        self.domaindata: dict[str, dict[str, Any]] = {}
        self.current_document = SimpleNamespace(docname="")


def _load_pyxrefindex(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import docs/_ext/pyxrefindex.py as module ``pyxrefindex``."""
    spec = importlib.util.spec_from_file_location("pyxrefindex", PYXREFINDEX_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "pyxrefindex", module)
    spec.loader.exec_module(module)
    return module


class TestPyXrefIndex:
    """The indexed Python domain finds the same objects as Sphinx's own search.

    Fast tests without a documentation build. They fail when a Sphinx update
    changes ``PythonDomain.find_obj`` so that the index no longer matches it.
    """

    @pytest.fixture
    def pyxrefindex(self, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
        return _load_pyxrefindex(monkeypatch)

    @pytest.fixture
    def domains(self, pyxrefindex: ModuleType) -> tuple["PythonDomain", "PythonDomain"]:
        """Return (Sphinx's PythonDomain, indexed domain) that share their objects."""
        from sphinx.domains.python import PythonDomain

        env = PyXrefIndexEnvironment()
        original = PythonDomain(cast("BuildEnvironment", env))
        indexed = pyxrefindex.IndexedPythonDomain(env)
        assert original.data is indexed.data
        for name, objtype, docname in PYXREFINDEX_OBJECTS:
            env.current_document.docname = docname
            indexed.note_object(name, objtype, f"id-{name}")
        env.current_document.docname = "doc1"
        indexed.note_object("eos.core.Config.load", "method", "alias", aliased=True)
        return original, indexed

    @staticmethod
    def _queries() -> list[str]:
        names = {""}
        for name, _, _ in PYXREFINDEX_OBJECTS:
            parts = name.split(".")
            for i in range(len(parts)):
                names.add(".".join(parts[i:]))
        names |= {f"{n}()" for n in list(names)}
        names |= {"missing", "Config.missing", ".load", "load.", "..load", "core.Settings.type"}
        return sorted(names)

    def test_find_obj_matches_sphinx(self, domains: tuple["PythonDomain", "PythonDomain"]) -> None:
        original, indexed = domains
        roles = [None, "unknown", *sorted({r for t in original.object_types.values() for r in t.roles})]
        ambiguous = 0
        for searchmode in (0, 1):
            for modname in (None, "eos.core", "eos.devices"):
                for classname in (None, "Config", "Settings"):
                    for role in roles:
                        for name in self._queries():
                            args = (original.env, modname, classname, name, role, searchmode)
                            expected = original.find_obj(*args)  # type: ignore[arg-type]
                            assert indexed.find_obj(*args) == expected, args[1:]  # type: ignore[arg-type]
                            ambiguous += len(expected) > 1
        # The objects really exercise the fuzzy search with several matches.
        assert ambiguous > 0

    def test_index_follows_object_changes(self, domains: tuple["PythonDomain", "PythonDomain"]) -> None:
        from sphinx.domains.python import ObjectEntry

        original, indexed = domains
        env = indexed.env

        def methods_named(name: str) -> list[str]:
            """Names the indexed search finds; they must be those of Sphinx's search."""
            found = [n for n, _ in indexed.find_obj(env, None, None, name, "meth", 1)]  # type: ignore[arg-type]
            assert found == [n for n, _ in original.find_obj(env, None, None, name, "meth", 1)]  # type: ignore[arg-type]
            return found

        assert methods_named("load") == ["eos.core.Config.load", "eos.devices.Battery.load", "Top.load"]

        env.current_document.docname = "doc3"
        indexed.note_object("eos.extra.Thing.load", "method", "id-thing")
        assert methods_named("load")[-1] == "eos.extra.Thing.load"

        indexed.clear_doc("doc2")
        assert methods_named("load") == ["eos.core.Config.load", "eos.extra.Thing.load"]

        entry = ObjectEntry("doc4", "id-merged", "method", False)
        indexed.merge_domaindata({"doc4"}, {"objects": {"eos.merged.M.load": entry}, "modules": {}})
        assert methods_named("load")[-1] == "eos.merged.M.load"

        # In the next three steps the number of objects is the same before and after, so the
        # index is only right if clear_doc, note_object and merge_domaindata each drop it.
        del indexed.objects["eos.merged.M.load"]
        indexed.objects["eos.swapped.S.load"] = ObjectEntry("doc5", "id-swapped", "method", False)
        indexed.clear_doc("doc-without-objects")
        assert methods_named("load")[-1] == "eos.swapped.S.load"

        del indexed.objects["eos.swapped.S.load"]
        env.current_document.docname = "doc6"
        indexed.note_object("eos.noted.N.load", "method", "id-noted")
        assert methods_named("load")[-1] == "eos.noted.N.load"

        del indexed.objects["eos.noted.N.load"]
        other = ObjectEntry("doc7", "id-other", "method", False)
        indexed.merge_domaindata({"doc7"}, {"objects": {"eos.other.O.load": other}, "modules": {}})
        assert methods_named("load")[-1] == "eos.other.O.load"

        # A direct change of the objects that changes their number is noticed as well.
        indexed.objects["eos.direct.D.load"] = ObjectEntry("doc8", "id-direct", "method", False)
        assert methods_named("load")[-1] == "eos.direct.D.load"

    def test_verified_sphinx_version(self, pyxrefindex: ModuleType) -> None:
        """Raise VERIFIED_SPHINX_VERSION only after these tests pass with the new Sphinx."""
        import sphinx

        assert tuple(sphinx.version_info[:3]) == pyxrefindex.VERIFIED_SPHINX_VERSION, (
            "Sphinx was updated. Set VERIFIED_SPHINX_VERSION in docs/_ext/pyxrefindex.py to the "
            "new version and keep it only if the other tests of this class pass."
        )

    def test_setup_keeps_sphinx_domain_for_other_versions(
        self, pyxrefindex: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sphinx

        class App:
            def __init__(self) -> None:
                self.domains: list[type] = []

            def add_domain(self, domain: type, override: bool = False) -> None:
                assert override, "Sphinx refuses to replace the Python domain without override"
                self.domains.append(domain)

        app = App()
        assert pyxrefindex.setup(app)["parallel_read_safe"] is True
        assert app.domains == [pyxrefindex.IndexedPythonDomain]

        monkeypatch.setattr(sphinx, "version_info", (99, 0, 0, "final", 0))
        app = App()
        pyxrefindex.setup(app)
        assert app.domains == []
