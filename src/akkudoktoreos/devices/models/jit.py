"""Compilation switch for the numeric device and kernel functions.

``njit`` compiles a function with numba (nopython mode, cached on disk). With the
environment variable ``NUMBA_DISABLE_JIT=1`` numba leaves the functions as plain
Python, so everything also runs (slowly) without a compiler, e.g. for debugging.
"""

from typing import Any, Callable, TypeVar, cast

from numba import njit as _njit

F = TypeVar("F", bound=Callable[..., Any])


def njit(function: F) -> F:
    """Compile ``function`` with numba in nopython mode, cached between runs."""
    return cast(F, _njit(cache=True, nogil=True)(function))
