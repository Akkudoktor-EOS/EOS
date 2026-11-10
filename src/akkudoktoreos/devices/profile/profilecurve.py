"""One-dimensional curves of device profiles.

Device profiles describe values that depend on one quantity as curves of
breakpoints: the efficiency of a battery over the C-rate, the efficiency of an
inverter over its load fraction, the power derating of an inverter over the
ambient temperature. This module holds what all of them share:

- ``CurveInterpolation`` and ``OutOfRangeBehavior``: how a curve is read between
  and beyond its breakpoints.
- ``reference_curve_lookup``: the slow per-query reading that defines the
  semantics of a curve.
- ``PrecomputedCurve``: the immutable runtime form with precomputed segment
  coefficients, used by controllers and the simulation.
- ``validate_curve_points``: the checks every curve document applies.

The document models name their axes after the quantity (e.g. ``c_rate``,
``load_fraction``, ``temperature_degC``) and convert to the neutral ``x`` / ``y``
form of this module.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Self, Sequence


class CurveInterpolation(str, Enum):
    """Interpolation strategy of a one-dimensional curve.

    Attributes:
        LINEAR: Interpolate linearly between neighbouring breakpoints.
        NEAREST: Take the value of the nearest breakpoint; a query exactly
            between two breakpoints takes the lower one.
        NONE: Only exact breakpoints yield a value; anything else yields None.
    """

    LINEAR = "linear"
    NEAREST = "nearest"
    NONE = "none"


class OutOfRangeBehavior(str, Enum):
    """Behavior when a query lies outside the axis range but inside cutoffs.

    Attributes:
        CLAMP_TO_NEAREST: Return the value at the nearest edge.
        REJECT: Return None for the out-of-range query.
    """

    CLAMP_TO_NEAREST = "clamp_to_nearest"
    REJECT = "reject"


def validate_curve_points(x: Sequence[float], y: Sequence[float], x_name: str, y_name: str) -> None:
    """Check that a curve has strictly increasing breakpoints and one value each.

    Args:
        x: Breakpoints.
        y: Values at the breakpoints.
        x_name: Name of the breakpoint field, for error messages.
        y_name: Name of the value field, for error messages.

    Raises:
        ValueError: If the breakpoints are not strictly increasing or the
            lengths differ.
    """
    if any(right <= left for left, right in zip(x, x[1:])):
        raise ValueError(f"{x_name} breakpoints must be strictly increasing")
    if len(x) != len(y):
        raise ValueError(f"{x_name} and {y_name} must contain the same number of values")


def reference_curve_lookup(
    x: Sequence[float],
    y: Sequence[float],
    interpolation: CurveInterpolation,
    out_of_range_behavior: OutOfRangeBehavior,
    query: float,
) -> Optional[float]:
    """Return the value of a curve at ``query``.

    Reference semantics for ``PrecomputedCurve.lookup``.

    Args:
        x: Strictly increasing breakpoints.
        y: Values at the breakpoints.
        interpolation: Interpolation between breakpoints.
        out_of_range_behavior: Behavior outside the breakpoints.
        query: Point to evaluate.

    Returns:
        The value, or None when the query is outside the curve and the
        out-of-range behavior is REJECT, or when interpolation is NONE and the
        query is not an exact breakpoint.
    """
    if query < x[0] or query > x[-1]:
        if out_of_range_behavior == OutOfRangeBehavior.REJECT:
            return None
        return y[0] if query < x[0] else y[-1]

    if interpolation == CurveInterpolation.NONE:
        try:
            return y[list(x).index(query)]
        except ValueError:
            return None

    if interpolation == CurveInterpolation.NEAREST:
        index = min(range(len(x)), key=lambda i: abs(x[i] - query))
        return y[index]

    for index in range(len(x) - 1):
        lower = x[index]
        upper = x[index + 1]
        if lower <= query <= upper:
            fraction = (query - lower) / (upper - lower)
            return y[index] + (y[index + 1] - y[index]) * fraction
    return y[-1]


@dataclass(frozen=True)
class PrecomputedCurve:
    """Immutable runtime representation of a one-dimensional curve.

    Segment slopes/intercepts and nearest-neighbor midpoints are calculated
    once, so runtime lookups avoid recalculating interpolation coefficients or
    scanning every breakpoint.

    Attributes:
        x: Strictly increasing breakpoints.
        y: Values at the breakpoints.
        interpolation: Interpolation between breakpoints.
        out_of_range_behavior: Behavior outside the breakpoints.
        slopes: Slope of each linear segment.
        intercepts: Intercept of each linear segment.
        midpoints: Midpoints between neighbouring breakpoints.
    """

    x: tuple[float, ...]
    y: tuple[float, ...]
    interpolation: CurveInterpolation
    out_of_range_behavior: OutOfRangeBehavior
    slopes: tuple[float, ...]
    intercepts: tuple[float, ...]
    midpoints: tuple[float, ...]

    @classmethod
    def from_points(
        cls,
        x: Iterable[float],
        y: Iterable[float],
        interpolation: CurveInterpolation = CurveInterpolation.LINEAR,
        out_of_range_behavior: OutOfRangeBehavior = OutOfRangeBehavior.CLAMP_TO_NEAREST,
    ) -> Self:
        """Build the lookup data from validated breakpoints and values.

        Raises:
            ValueError: If the points are not a valid curve, see
                ``validate_curve_points``, or there are fewer than two.
        """
        xs = tuple(float(value) for value in x)
        ys = tuple(float(value) for value in y)
        if len(xs) < 2:
            raise ValueError("a curve needs at least two breakpoints")
        validate_curve_points(xs, ys, "x", "y")
        slopes = tuple((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(len(xs) - 1))
        intercepts = tuple(ys[i] - slopes[i] * xs[i] for i in range(len(slopes)))
        midpoints = tuple((xs[i] + xs[i + 1]) / 2.0 for i in range(len(xs) - 1))
        return cls(
            x=xs,
            y=ys,
            interpolation=interpolation,
            out_of_range_behavior=out_of_range_behavior,
            slopes=slopes,
            intercepts=intercepts,
            midpoints=midpoints,
        )

    def lookup(self, query: float) -> Optional[float]:
        """Return the value at ``query``, see ``reference_curve_lookup``."""
        if query < self.x[0]:
            if self.out_of_range_behavior == OutOfRangeBehavior.REJECT:
                return None
            return self.y[0]
        if query > self.x[-1]:
            if self.out_of_range_behavior == OutOfRangeBehavior.REJECT:
                return None
            return self.y[-1]

        if self.interpolation == CurveInterpolation.NONE:
            index = bisect_left(self.x, query)
            if index < len(self.x) and self.x[index] == query:
                return self.y[index]
            return None

        if self.interpolation == CurveInterpolation.NEAREST:
            # bisect_left preserves the min() tie-break of the reference: lower point.
            index = bisect_left(self.midpoints, query)
            return self.y[index]

        if query == self.x[-1]:
            return self.y[-1]
        index = bisect_right(self.x, query) - 1
        return self.slopes[index] * query + self.intercepts[index]
