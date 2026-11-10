"""Tests for the one-dimensional curves shared by the device profiles."""

import numpy as np
import pytest

from akkudoktoreos.devices.profile.profilecurve import (
    CurveInterpolation,
    OutOfRangeBehavior,
    PrecomputedCurve,
    reference_curve_lookup,
    validate_curve_points,
)

X = (0.0, 0.2, 0.5, 1.0)
Y = (0.80, 0.95, 0.97, 0.96)


class TestValidateCurvePoints:
    def test_accepts_increasing_points(self):
        validate_curve_points(X, Y, "load_fraction", "efficiency")

    @pytest.mark.parametrize("x", [(0.0, 0.0, 1.0), (0.0, 0.5, 0.4)])
    def test_rejects_not_strictly_increasing(self, x):
        with pytest.raises(ValueError, match="load_fraction breakpoints must be strictly"):
            validate_curve_points(x, (1.0, 1.0, 1.0), "load_fraction", "efficiency")

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same number of values"):
            validate_curve_points(X, Y[:-1], "load_fraction", "efficiency")


class TestPrecomputedCurve:
    @pytest.mark.parametrize("interpolation", list(CurveInterpolation))
    @pytest.mark.parametrize("behavior", list(OutOfRangeBehavior))
    def test_lookup_matches_reference(self, interpolation, behavior):
        curve = PrecomputedCurve.from_points(X, Y, interpolation, behavior)
        queries = [-0.5, 0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 0.6]
        for query in queries:
            expected = reference_curve_lookup(X, Y, interpolation, behavior, query)
            actual = curve.lookup(query)
            if expected is None:
                assert actual is None
            else:
                assert actual == pytest.approx(expected)

    def test_linear_interpolation(self):
        curve = PrecomputedCurve.from_points(X, Y)
        assert curve.lookup(0.1) == pytest.approx(0.875)
        assert curve.lookup(0.75) == pytest.approx(0.965)

    def test_clamps_by_default(self):
        curve = PrecomputedCurve.from_points(X, Y)
        assert curve.lookup(-1.0) == pytest.approx(0.80)
        assert curve.lookup(2.0) == pytest.approx(0.96)

    def test_nearest_tie_takes_lower_breakpoint(self):
        curve = PrecomputedCurve.from_points(X, Y, CurveInterpolation.NEAREST)
        assert curve.lookup(0.1) == pytest.approx(0.80)

    def test_none_interpolation_only_at_breakpoints(self):
        curve = PrecomputedCurve.from_points(X, Y, CurveInterpolation.NONE)
        assert curve.lookup(0.5) == pytest.approx(0.97)
        assert curve.lookup(0.4) is None

    def test_reject_out_of_range(self):
        curve = PrecomputedCurve.from_points(X, Y, out_of_range_behavior=OutOfRangeBehavior.REJECT)
        assert curve.lookup(-0.1) is None
        assert curve.lookup(1.1) is None

    def test_precomputes_coefficients(self):
        curve = PrecomputedCurve.from_points(X, Y)
        assert len(curve.slopes) == len(X) - 1
        assert curve.midpoints == pytest.approx((0.1, 0.35, 0.75))
        for i, slope in enumerate(curve.slopes):
            assert slope * X[i] + curve.intercepts[i] == pytest.approx(Y[i])

    def test_accepts_numpy_input(self):
        curve = PrecomputedCurve.from_points(np.array(X), np.array(Y))
        assert curve.x == X

    @pytest.mark.parametrize(
        "x, y",
        [((0.0,), (1.0,)), ((0.0, 0.0), (1.0, 1.0)), ((0.0, 1.0), (1.0,))],
    )
    def test_rejects_invalid_points(self, x, y):
        with pytest.raises(ValueError):
            PrecomputedCurve.from_points(x, y)

    def test_is_immutable(self):
        curve = PrecomputedCurve.from_points(X, Y)
        with pytest.raises(AttributeError):
            curve.x = (0.0, 1.0)  # type: ignore[misc]
