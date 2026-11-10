"""Optional native (Rust) kernels for Akkudoktor-EOS.

The first kernel is the GENETIC optimizer: a port of ``akkudoktoreos.optimization.genetic``
that returns the same solution as Python for the same request and seed. One run is JSON in,
JSON out (:func:`run_genetic`); EOS builds the request
(``akkudoktoreos.optimization.genetic.native``). Nothing here imports EOS.

EOS uses the kernel only for GENETIC sources it was verified against: ``certified.json`` lists
fingerprints of those sources (computed by EOS, see
``akkudoktoreos.optimization.genetic.native.source_fingerprint``). EOS sources without an entry
run in Python.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from akkudoktoreos_native._core import (
    DOT_KERNELS,
    INTERFACE_VERSION,
    KERNEL_VERSION,
    SELF_CONSUMPTION_TABLE_SHA256,
    NativeRunError,
    dot,
    expected_direct_consumption,
    interp,
    run_genetic,
)

__version__ = KERNEL_VERSION

__all__ = [
    "DOT_KERNELS",
    "INTERFACE_VERSION",
    "KERNEL_VERSION",
    "SELF_CONSUMPTION_TABLE_SHA256",
    "NativeRunError",
    "__version__",
    "certified_entries",
    "dot",
    "expected_direct_consumption",
    "interp",
    "is_certified",
    "run_genetic",
]


@lru_cache(maxsize=1)
def certified_entries() -> tuple[dict[str, Any], ...]:
    """Certified EOS source fingerprints shipped with this package."""
    text = resources.files(__package__).joinpath("certified.json").read_text(encoding="utf-8")
    data = json.loads(text)
    if data.get("format") != 1:
        # Written for another package version: do not guess.
        return ()
    return tuple(data.get("entries", ()))


def is_certified(fingerprint: str) -> bool:
    """Whether the kernel was verified against EOS sources with this fingerprint."""
    return any(entry.get("fingerprint") == fingerprint for entry in certified_entries())
