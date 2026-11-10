"""Optional native (Rust) kernels for Akkudoktor-EOS.

The first kernel is the GENETIC optimizer: a port of ``akkudoktoreos.optimization.genetic``
that returns the same solution as Python for the same request and seed. One run is JSON in,
JSON out (:func:`run_genetic`); EOS builds the request
(``akkudoktoreos.optimization.genetic.native``). Nothing here imports EOS.
"""

from __future__ import annotations

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
    "dot",
    "expected_direct_consumption",
    "interp",
    "run_genetic",
]
