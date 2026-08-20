"""Common type aliases used throughout AkkudoktorEOS.

This module centralizes reusable type definitions shared across multiple
packages. Defining common types here avoids duplication of complex type
annotations (such as Literal aliases), ensures consistent typing across the
code base, and helps prevent circular import dependencies between modules.

The aliases defined in this module describe common concepts and API contracts,
including resampling methods, interpolation and fill methods, boundary
handling, and other shared parameter types.

Guidelines:
- Import shared type aliases from this module instead of redefining them.
- Keep this module lightweight and free of runtime dependencies wherever
possible.
- Only define reusable types here. Implementation-specific types should
remain in the modules where they are used.

The contents of this module are intended for static type checking and
documentation and have no significant runtime behavior.
"""

from typing import Literal, TypeAlias

FillMethod: TypeAlias = Literal[
    "linear",
    "time",
    "ffill",
    "bfill",
]
"""Method used to fill missing values before or after resampling.

- "linear": Linear interpolation.
- "time": Time-based interpolation.
- "ffill": Forward-fill using the previous value.
- "bfill": Backward-fill using the next value.
"""

ResampleMethod: TypeAlias = Literal[
    "first",
    "mean",
    "interval_mean",
]
"""Method used to aggregate multiple samples within a resampling interval.

- "first": Use the first sample.
- "mean": Arithmetic mean of the samples.
- "interval_mean": Time-weighted mean assuming piecewise-constant values.
"""

BoundaryMode: TypeAlias = Literal[
    "strict",
    "context",
]
"""Controls whether resampling includes context outside the requested time range.

- "strict": Use only data inside the requested interval.
- "context": Include one sample before and after for correct interpolation/resampling.
"""
