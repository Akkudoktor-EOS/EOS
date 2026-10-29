"""Dependency imports used by the API must not rely on deprecated aliases."""

import subprocess
import sys


def test_starlette_testclient_import_without_deprecated_anyio_alias():
    # A fresh interpreter exercises the import even if test collection imported it already.
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", "import starlette.testclient"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
