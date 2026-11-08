"""Tests for the batteryprofile module.

The module exercises the Pydantic validation rules, the interpolation
strategies, the out-of-range behaviors, the hard-cutoff handling, the
precomputed lookup path and the JSON file entry point of the
BatteryProfileSettings configuration model.
"""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.profile.batteryprofile import (
    Axes,
    BatteryProfile,
    BatteryProfileSettings,
    CargeDischargeLimitMap,
    EfficiencyCurve,
    EfficiencyInterpolation,
    HardCutoffs,
    Interpolation,
    OutOfRangeBehavior,
    PrecomputedCargeDischargeLimitMap,
    PrecomputedEfficiencyCurve,
    Units,
)
from akkudoktoreos.devices.profile.profilestore import get_profile_store

# --- helpers -------------------------------------------------------------

# Values of the required fields that the tests do not vary.
TEST_AXES = {
    "temperature_degC": [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50],
    "soc_percent": [0, 5, 10, 20, 40, 60, 80, 90, 95, 100],
}
TEST_CUTOFFS = {
    "charge_allowed_below_degC": -10.0,
    "charge_allowed_above_degC": 55.0,
    "discharge_allowed_below_degC": -15.0,
    "discharge_allowed_above_degC": 60.0,
}
TEST_UNITS = {"temperature": "degC", "soc": "percent", "limit": "C-rate"}
TEST_CURVE = {"c_rate": [0.1, 0.5, 1.0], "efficiency": [0.90, 0.96, 0.92]}


def make_grid(rows: int = 10, cols: int = 10, value: float = 0.5) -> list[list[float]]:
    """Return a constant rows x cols grid filled with value."""
    return [[value for _ in range(cols)] for _ in range(rows)]


def make_limits(
    charge_values: list[list[float]] | None = None,
    discharge_values: list[list[float]] | None = None,
    **overrides,
) -> BatteryProfileSettings:
    """Build a BatteryProfileSettings with sensible defaults.

    Args:
        charge_values: Optional charge grid; defaults to a constant 0.5 grid.
        discharge_values: Optional discharge grid; defaults to a constant 0.5
            grid.
        **overrides: Further keyword arguments forwarded to the model. The
            required fields get test values unless the caller provides them.

    Returns:
        A validated BatteryProfileSettings instance.
    """
    payload: dict[str, Any] = dict(
        battery_model="Test Battery",
        technical_min_soc_percent=0.0,
        technical_max_soc_percent=100.0,
        charge_efficiency=EfficiencyCurve.model_validate(TEST_CURVE),
        discharge_efficiency=EfficiencyCurve.model_validate(TEST_CURVE),
        units=Units.model_validate(TEST_UNITS),
        axes=Axes.model_validate(TEST_AXES),
        hard_cutoffs=HardCutoffs.model_validate(TEST_CUTOFFS),
        charge_limit_crate=CargeDischargeLimitMap(values=charge_values or make_grid()),
        discharge_limit_crate=CargeDischargeLimitMap(values=discharge_values or make_grid()),
    )
    payload.update(overrides)
    return BatteryProfileSettings(**payload)


def make_efficiency_curve(
    c_rate: list[float] | None = None,
    efficiency: list[float] | None = None,
    **overrides,
) -> EfficiencyCurve:
    """Build a validated efficiency curve with useful defaults."""
    payload: dict[str, Any] = {
        "c_rate": c_rate or [0.1, 0.5, 1.0],
        "efficiency": efficiency or [0.90, 0.96, 0.92],
    }
    payload.update(overrides)
    return EfficiencyCurve(**payload)


def make_document(charge_values=None, discharge_values=None, **overrides) -> dict:
    """Return a JSON-serializable document for a full limits configuration.

    Args:
        charge_values: Optional charge grid; defaults to a constant 0.5 grid.
        discharge_values: Optional discharge grid; defaults to a constant 0.5
            grid.
        **overrides: Further top-level keys forwarded to the document.

    Returns:
        A dict that can be written with ``json.dump`` and loaded back.
    """
    doc = {
        "schema_version": "1.1",
        "battery_model": "Test Battery",
        "chemistry": "LFP",
        "nominal_voltage_v": 102.4,
        "technical_min_soc_percent": 0.0,
        "technical_max_soc_percent": 100.0,
        "charge_efficiency": dict(TEST_CURVE),
        "discharge_efficiency": dict(TEST_CURVE),
        "units": dict(TEST_UNITS),
        "interpolation": "bilinear",
        "out_of_range_behavior": "clamp_to_nearest",
        "axes": {
            "temperature_degC": [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50],
            "soc_percent": [0, 5, 10, 20, 40, 60, 80, 90, 95, 100],
        },
        "charge_limit_crate": {
            "description": "Max charge C-rate; rows = temperature, cols = SOC",
            "values": charge_values or make_grid(),
        },
        "discharge_limit_crate": {
            "description": "Max discharge C-rate; rows = temperature, cols = SOC",
            "values": discharge_values or make_grid(),
        },
        "hard_cutoffs": {
            "charge_allowed_below_degC": -10.0,
            "charge_allowed_above_degC": 55.0,
            "discharge_allowed_below_degC": -15.0,
            "discharge_allowed_above_degC": 60.0,
        },
    }
    doc.update(overrides)
    return doc


# --- Units ---------------------------------------------------------------


class TestUnits:
    """Tests for the Units defaults and schema metadata."""

    def test_axis_units_default_but_limit_unit_is_required(self):
        u = Units(limit="C-rate")
        assert u.temperature == "degC"
        assert u.soc == "percent"
        with pytest.raises(ValidationError, match="limit"):
            Units.model_validate({})

    def test_json_schema_contains_descriptions(self):
        schema = Units.model_json_schema()
        assert schema["properties"]["temperature"]["description"]
        assert schema["properties"]["temperature"]["examples"] == ["degC"]


# --- EfficiencyCurve: configuration validation and lookup ------------------


class TestEfficiencyCurveValidation:
    """Tests for C-rate efficiency curve validation."""

    def test_accepts_well_formed_curve(self):
        curve = make_efficiency_curve()
        assert curve.c_rate == [0.1, 0.5, 1.0]
        assert curve.efficiency == [0.90, 0.96, 0.92]
        assert curve.interpolation is EfficiencyInterpolation.LINEAR
        assert curve.out_of_range_behavior is OutOfRangeBehavior.CLAMP_TO_NEAREST

    def test_rejects_negative_c_rate(self):
        with pytest.raises(ValidationError, match="non-negative"):
            make_efficiency_curve(c_rate=[-0.1, 0.5], efficiency=[0.9, 0.95])

    def test_rejects_non_increasing_c_rate(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            make_efficiency_curve(c_rate=[0.1, 0.1], efficiency=[0.9, 0.95])

    def test_rejects_efficiency_outside_valid_range(self):
        for values in ([0.0, 0.9], [-0.1, 0.9], [0.9, 1.01]):
            with pytest.raises(ValidationError, match=r"\(0, 1\]"):
                make_efficiency_curve(c_rate=[0.1, 0.5], efficiency=list(values))

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValidationError, match="same number"):
            make_efficiency_curve(c_rate=[0.1, 0.5, 1.0], efficiency=[0.9, 0.95])

    def test_json_schema_contains_curve_descriptions(self):
        schema = EfficiencyCurve.model_json_schema()
        assert schema["properties"]["c_rate"]["description"]
        assert schema["properties"]["efficiency"]["description"]


class TestEfficiencyCurveLookup:
    """Tests for the configuration model's reference lookup implementation."""

    def test_linear_interpolation(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.8, 1.0]
        )
        assert curve.lookup(0.1) == pytest.approx(0.8)
        assert curve.lookup(0.3) == pytest.approx(0.9)
        assert curve.lookup(0.5) == pytest.approx(1.0)

    def test_nearest_interpolation(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5, 1.0],
            efficiency=[0.8, 0.9, 0.95],
            interpolation=EfficiencyInterpolation.NEAREST,
        )
        assert curve.lookup(0.2) == pytest.approx(0.8)
        assert curve.lookup(0.4) == pytest.approx(0.9)

    def test_nearest_tie_selects_lower_breakpoint(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.8, 0.9],
            interpolation=EfficiencyInterpolation.NEAREST,
        )
        assert curve.lookup(0.3) == pytest.approx(0.8)

    def test_none_interpolation_requires_exact_breakpoint(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.8, 0.9],
            interpolation=EfficiencyInterpolation.NONE,
        )
        assert curve.lookup(0.5) == pytest.approx(0.9)
        assert curve.lookup(0.3) is None

    def test_out_of_range_clamps_to_endpoints(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.8, 0.9]
        )
        assert curve.lookup(0.0) == pytest.approx(0.8)
        assert curve.lookup(1.0) == pytest.approx(0.9)

    def test_out_of_range_rejects_query(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.8, 0.9],
            out_of_range_behavior=OutOfRangeBehavior.REJECT,
        )
        assert curve.lookup(0.0) is None
        assert curve.lookup(1.0) is None
        assert curve.lookup(0.3) == pytest.approx(0.85)


# --- PrecomputedEfficiencyCurve: standalone runtime lookup ------------------


class TestPrecomputedEfficiencyCurve:
    """Tests for the immutable precomputed efficiency-curve representation."""

    @pytest.mark.parametrize(
        "interpolation",
        [
            EfficiencyInterpolation.LINEAR,
            EfficiencyInterpolation.NEAREST,
            EfficiencyInterpolation.NONE,
        ],
    )
    @pytest.mark.parametrize(
        "out_of_range_behavior",
        [
            OutOfRangeBehavior.CLAMP_TO_NEAREST,
            OutOfRangeBehavior.REJECT,
        ],
    )
    def test_precomputed_lookup_matches_reference(
        self, interpolation, out_of_range_behavior
    ):
        curve = make_efficiency_curve(
            c_rate=[0.05, 0.2, 0.5, 1.0],
            efficiency=[0.88, 0.94, 0.97, 0.91],
            interpolation=interpolation,
            out_of_range_behavior=out_of_range_behavior,
        )
        precomputed = PrecomputedEfficiencyCurve.from_curve(curve)
        queries = [-0.1, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.2]
        for c_rate in queries:
            expected = curve.lookup(c_rate)
            actual = precomputed.lookup(c_rate)
            if expected is None:
                assert actual is None
            else:
                assert actual == pytest.approx(expected, abs=1e-12)

    def test_precomputes_segment_coefficients_and_midpoints(self):
        curve = make_efficiency_curve(
            c_rate=[0.1, 0.5, 1.0],
            efficiency=[0.8, 1.0, 0.9],
        )
        precomputed = PrecomputedEfficiencyCurve.from_curve(curve)
        assert len(precomputed.slopes) == 2
        assert len(precomputed.intercepts) == 2
        assert precomputed.midpoints == pytest.approx((0.3, 0.75))

    def test_runtime_data_is_immutable(self):
        precomputed = PrecomputedEfficiencyCurve.from_curve(make_efficiency_curve())
        with pytest.raises((AttributeError, TypeError)):
            precomputed.c_rate = (0.0, 1.0)  # type: ignore[misc]


class TestBatteryProfileEfficiency:
    """Tests for efficiency-curve conversion and access on BatteryProfile."""

    def test_to_limits_precomputes_both_efficiency_curves(self):
        charge_curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.9, 0.96]
        )
        discharge_curve = make_efficiency_curve(
            c_rate=[0.1, 0.5], efficiency=[0.91, 0.95]
        )
        profile = make_limits(
            charge_efficiency=charge_curve,
            discharge_efficiency=discharge_curve,
        ).to_limits()

        assert isinstance(profile.charge_efficiency, PrecomputedEfficiencyCurve)
        assert isinstance(profile.discharge_efficiency, PrecomputedEfficiencyCurve)

    def test_charge_and_discharge_efficiencies_are_independent(self):
        profile = make_limits(
            charge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.8, 0.9]
            ),
            discharge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.7, 0.95]
            ),
        ).to_limits()

        assert profile.charge_efficiency_at(0.3) == pytest.approx(0.85)
        assert profile.discharge_efficiency_at(0.3) == pytest.approx(0.825)

    def test_efficiency_accessors_use_absolute_c_rate(self):
        profile = make_limits(
            charge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.8, 0.9]
            ),
            discharge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.7, 0.9]
            ),
        ).to_limits()

        assert profile.charge_efficiency_at(-0.3) == pytest.approx(0.85)
        assert profile.discharge_efficiency_at(-0.3) == pytest.approx(0.8)

    @pytest.mark.parametrize("field", ["charge_efficiency", "discharge_efficiency"])
    def test_efficiency_curves_are_required(self, field):
        with pytest.raises(ValidationError, match=field):
            make_limits(**{field: None})

    def test_profile_carries_new_model_characteristics(self):
        profile = make_limits(
            max_charge_power_w=5000.0,
            max_discharge_power_w=4500.0,
            technical_min_soc_percent=5.0,
            technical_max_soc_percent=95.0,
        ).to_limits()

        assert profile.max_charge_power_w == pytest.approx(5000.0)
        assert profile.max_discharge_power_w == pytest.approx(4500.0)
        assert profile.technical_min_soc_percent == pytest.approx(5.0)
        assert profile.technical_max_soc_percent == pytest.approx(95.0)

    def test_profile_carries_full_charge_characteristics(self):
        profile = make_limits(
            full_charge_interval_days=21.0, full_charge_detect_percentage=98
        ).to_limits()

        assert profile.full_charge_interval_days == pytest.approx(21.0)
        assert profile.full_charge_detect_percentage == 98

    def test_full_charge_characteristics_default_to_no_periodic_full_charge(self):
        profile = make_limits().to_limits()

        assert profile.full_charge_interval_days is None
        assert profile.full_charge_detect_percentage == 99

    @pytest.mark.parametrize(
        "overrides",
        [
            {"full_charge_interval_days": 0.0},
            {"full_charge_interval_days": float("inf")},
            {"full_charge_detect_percentage": 0},
            {"full_charge_detect_percentage": 101},
        ],
    )
    def test_rejects_invalid_full_charge_characteristics(self, overrides):
        with pytest.raises(ValidationError):
            make_limits(**overrides)


# --- Axes ----------------------------------------------------------------


class TestAxes:
    """Tests for axis validation."""

    def test_axes_are_required(self):
        with pytest.raises(ValidationError, match="temperature_degC"):
            Axes.model_validate({"soc_percent": [0, 100]})
        with pytest.raises(ValidationError, match="soc_percent"):
            Axes.model_validate({"temperature_degC": [0, 10]})

    def test_temperature_must_be_strictly_increasing(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            Axes(temperature_degC=[0, 5, 5, 10], soc_percent=[0, 50, 100])

    def test_soc_must_be_strictly_increasing(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            Axes(temperature_degC=[0, 10], soc_percent=[0, 50, 40])

    def test_soc_bounds_enforced(self):
        with pytest.raises(ValidationError, match=r"\[0, 100\]"):
            Axes(temperature_degC=[0, 10], soc_percent=[-1, 50])
        with pytest.raises(ValidationError, match=r"\[0, 100\]"):
            Axes(temperature_degC=[0, 10], soc_percent=[50, 101])

    def test_min_length_enforced(self):
        with pytest.raises(ValidationError):
            Axes(temperature_degC=[0], soc_percent=[0, 100])


# --- CargeDischargeLimitMap ------------------------------------------------------------


class TestCargeDischargeLimitMap:
    """Tests for CargeDischargeLimitMap value validation."""

    def test_accepts_well_formed_grid(self):
        m = CargeDischargeLimitMap(values=make_grid(2, 3, 0.0))
        assert len(m.values) == 2
        assert len(m.values[0]) == 3

    def test_rejects_negative_values(self):
        with pytest.raises(ValidationError, match="non-negative"):
            CargeDischargeLimitMap(values=[[0.1, -0.1], [0.2, 0.3]])

    def test_rejects_single_row(self):
        with pytest.raises(ValidationError):
            CargeDischargeLimitMap(values=[[0.1, 0.2]])

    def test_description_is_optional(self):
        assert CargeDischargeLimitMap(values=make_grid()).description is None


# --- HardCutoffs ---------------------------------------------------------


class TestHardCutoffs:
    """Tests for cutoff ordering validation."""

    def test_all_cutoffs_are_required(self):
        for field in TEST_CUTOFFS:
            values = {k: v for k, v in TEST_CUTOFFS.items() if k != field}
            with pytest.raises(ValidationError, match=field):
                HardCutoffs.model_validate(values)

    def test_charge_bounds_must_be_ordered(self):
        with pytest.raises(ValidationError, match="charge cutoff"):
            HardCutoffs.model_validate(
                {
                    **TEST_CUTOFFS,
                    "charge_allowed_below_degC": 60.0,
                    "charge_allowed_above_degC": 50.0,
                }
            )

    def test_discharge_bounds_must_be_ordered(self):
        with pytest.raises(ValidationError, match="discharge cutoff"):
            HardCutoffs.model_validate(
                {
                    **TEST_CUTOFFS,
                    "discharge_allowed_below_degC": 60.0,
                    "discharge_allowed_above_degC": 50.0,
                }
            )


# --- BatteryProfileSettings: validation -----------------------------------


class TestBatteryProfileSettingsValidation:
    """Tests for cross-field validation of BatteryProfileSettings."""

    def test_minimal_config_is_valid(self):
        limits = make_limits()
        assert limits.interpolation is Interpolation.BILINEAR
        assert limits.out_of_range_behavior is OutOfRangeBehavior.CLAMP_TO_NEAREST

    def test_charge_grid_must_match_axes_rows(self):
        grid = make_grid(rows=3, cols=10)
        with pytest.raises(ValidationError, match="charge_limit_crate.*rows"):
            make_limits(charge_values=grid)

    def test_charge_grid_must_match_axes_cols(self):
        grid = make_grid(rows=10, cols=4)
        with pytest.raises(ValidationError, match="charge_limit_crate.*columns"):
            make_limits(charge_values=grid)

    def test_discharge_grid_must_match_axes(self):
        grid = make_grid(rows=3, cols=10)
        with pytest.raises(ValidationError, match="discharge_limit_crate.*rows"):
            make_limits(discharge_values=grid)

    def test_custom_axes_respected(self):
        axes = Axes(temperature_degC=[0, 10, 20], soc_percent=[0, 50, 100])
        limits = make_limits(
            charge_values=make_grid(3, 3),
            discharge_values=make_grid(3, 3),
            axes=axes,
        )
        assert len(limits.charge_limit_crate.values) == 3

    def test_custom_axes_shape_mismatch_rejected(self):
        axes = Axes(temperature_degC=[0, 10, 20], soc_percent=[0, 50, 100])
        with pytest.raises(ValidationError, match="charge_limit_crate"):
            make_limits(
                charge_values=make_grid(10, 10),
                discharge_values=make_grid(3, 3),
                axes=axes,
            )


# --- BatteryProfileSettings: interpolation --------------------------------


class TestInterpolationBilinear:
    """Tests for bilinear interpolation."""

    def test_exact_grid_point_returns_stored_value(self):
        values = make_grid(2, 2, 0.0)
        values[0][0] = 1.0
        values[0][1] = 2.0
        values[1][0] = 3.0
        values[1][1] = 4.0
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
        )
        assert limits.max_charge_crate(0.0, 0.0) == pytest.approx(1.0)
        assert limits.max_charge_crate(10.0, 100.0) == pytest.approx(4.0)

    def test_midpoint_is_average_of_corners(self):
        values = [[1.0, 2.0], [3.0, 4.0]]
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
        )
        # Center of the cell: 0.25*(1+2+3+4) = 2.5
        assert limits.max_charge_crate(5.0, 50.0) == pytest.approx(2.5)

    def test_one_direction_linear(self):
        values = [[1.0, 2.0], [3.0, 4.0]]
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
        )
        # Halfway along SOC only, at the first temperature row: (1+2)/2 = 1.5
        assert limits.max_charge_crate(0.0, 50.0) == pytest.approx(1.5)
        # Halfway along temperature only, at the first SOC column: (1+3)/2 = 2.0
        assert limits.max_charge_crate(5.0, 0.0) == pytest.approx(2.0)


class TestInterpolationNearest:
    """Tests for nearest-neighbor interpolation."""

    def test_snaps_to_nearest_point(self):
        values = [[1.0, 2.0], [3.0, 4.0]]
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
            interpolation=Interpolation.NEAREST,
        )
        assert limits.max_charge_crate(1.0, 10.0) == pytest.approx(1.0)
        assert limits.max_charge_crate(9.0, 90.0) == pytest.approx(4.0)


class TestInterpolationNone:
    """Tests for strict grid-point-only interpolation."""

    def test_off_grid_returns_none(self):
        values = [[1.0, 2.0], [3.0, 4.0]]
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
            interpolation=Interpolation.NONE,
        )
        assert limits.max_charge_crate(5.0, 50.0) is None

    def test_on_grid_returns_value(self):
        values = [[1.0, 2.0], [3.0, 4.0]]
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
            interpolation=Interpolation.NONE,
        )
        assert limits.max_charge_crate(10.0, 100.0) == pytest.approx(4.0)


# --- BatteryProfileSettings: out-of-range behavior ------------------------


class TestOutOfRange:
    """Tests for clamping versus rejection outside the axis range."""

    def _limits(self, behavior: OutOfRangeBehavior) -> BatteryProfileSettings:
        # Axes deliberately narrower than the hard cutoffs so that queries can
        # land outside the axis but inside the cutoffs.
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        cutoffs = HardCutoffs(
            charge_allowed_below_degC=-20.0,
            charge_allowed_above_degC=40.0,
            discharge_allowed_below_degC=-20.0,
            discharge_allowed_above_degC=40.0,
        )
        return make_limits(
            charge_values=make_grid(2, 2, 0.7),
            discharge_values=make_grid(2, 2, 0.7),
            axes=axes,
            hard_cutoffs=cutoffs,
            out_of_range_behavior=behavior,
        )

    def test_clamp_returns_edge_value(self):
        limits = self._limits(OutOfRangeBehavior.CLAMP_TO_NEAREST)
        assert limits.max_charge_crate(15.0, 50.0) == pytest.approx(0.7)
        assert limits.max_charge_crate(5.0, 200.0) == pytest.approx(0.7)

    def test_reject_returns_none(self):
        limits = self._limits(OutOfRangeBehavior.REJECT)
        assert limits.max_charge_crate(15.0, 50.0) is None
        assert limits.max_charge_crate(5.0, 200.0) is None
        assert limits.max_charge_crate(5.0, 50.0) == pytest.approx(0.7)


# --- BatteryProfileSettings: hard cutoffs ---------------------------------


class TestHardCutoffsRuntime:
    """Tests that hard cutoffs override the maps."""

    def test_charge_below_lower_cutoff_returns_none(self):
        limits = make_limits()
        c = limits.hard_cutoffs
        assert limits.max_charge_crate(c.charge_allowed_below_degC - 0.1, 50.0) is None

    def test_charge_above_upper_cutoff_returns_none(self):
        limits = make_limits()
        c = limits.hard_cutoffs
        assert limits.max_charge_crate(c.charge_allowed_above_degC + 0.1, 50.0) is None

    def test_discharge_below_lower_cutoff_returns_none(self):
        limits = make_limits()
        c = limits.hard_cutoffs
        assert limits.max_discharge_crate(c.discharge_allowed_below_degC - 0.1, 50.0) is None

    def test_charge_and_discharge_cutoffs_are_independent(self):
        limits = make_limits()
        assert limits.max_discharge_crate(-12.0, 50.0) is not None
        assert limits.max_charge_crate(-12.0, 50.0) is None

    def test_inside_cutoffs_returns_map_value(self):
        limits = make_limits(charge_values=make_grid(value=0.42))
        assert limits.max_charge_crate(25.0, 50.0) == pytest.approx(0.42)


# --- BatteryProfileSettings: accessors ------------------------------------


class TestAccessors:
    """Tests that the two accessors read their respective maps."""

    def test_charge_and_discharge_use_different_maps(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        charge = make_grid(2, 2, 0.3)
        discharge = make_grid(2, 2, 0.9)
        limits = make_limits(
            charge_values=charge,
            discharge_values=discharge,
            axes=axes,
        )
        assert limits.max_charge_crate(5.0, 50.0) == pytest.approx(0.3)
        assert limits.max_discharge_crate(5.0, 50.0) == pytest.approx(0.9)


# --- PrecomputedCargeDischargeLimitMap: standalone ------------------------------------


class TestPrecomputedCargeDischargeLimitMap:
    """Tests for the PrecomputedCargeDischargeLimitMap dataclass on its own."""

    def _map(self, fill=None) -> PrecomputedCargeDischargeLimitMap:
        grid = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float)
        return PrecomputedCargeDischargeLimitMap(
            grid=grid,
            t_min=0.0,
            t_step=10.0,
            s_min=0.0,
            s_step=100.0,
            n_temp=2,
            n_soc=2,
            fill=fill,
        )

    def test_lookup_returns_stored_value_at_grid_points(self):
        m = self._map()
        assert m.lookup(0.0, 0.0) == pytest.approx(1.0)
        assert m.lookup(10.0, 100.0) == pytest.approx(4.0)

    def test_lookup_rounds_to_nearest_cell(self):
        m = self._map()
        assert m.lookup(1.0, 10.0) == pytest.approx(1.0)
        assert m.lookup(9.0, 90.0) == pytest.approx(4.0)

    def test_lookup_out_of_bounds_returns_fill(self):
        m = self._map(fill=0.0)
        # Below the lower bound: previously rounded to index 0 by mistake
        assert m.lookup(-5.0, 0.0) == pytest.approx(0.0)
        assert m.lookup(20.0, 0.0) == pytest.approx(0.0)
        assert m.lookup(0.0, 200.0) == pytest.approx(0.0)

    def test_lookup_out_of_bounds_returns_none_when_fill_is_none(self):
        m = self._map(fill=None)
        assert m.lookup(-5.0, 0.0) is None
        assert m.lookup(20.0, 0.0) is None
        assert m.lookup(0.0, 200.0) is None

    def test_lookup_many_matches_lookup(self):
        m = self._map(fill=None)
        temps = np.array([0.0, 10.0, 5.0, -5.0])
        socs = np.array([0.0, 100.0, 50.0, 50.0])
        result = m.lookup_many(temps, socs)
        expected = [m.lookup(t, s) for t, s in zip(temps, socs)]
        for got, exp in zip(result, expected):
            if exp is None:
                assert np.isnan(got)
            else:
                assert got == pytest.approx(exp)

    def test_lookup_many_uses_fill(self):
        m = self._map(fill=0.0)
        temps = np.array([-5.0, 0.0, 20.0])
        socs = np.array([0.0, 0.0, 0.0])
        result = m.lookup_many(temps, socs)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(1.0)
        assert result[2] == pytest.approx(0.0)

    def test_lookup_many_preserves_shape(self):
        m = self._map()
        temps = np.array([[0.0, 10.0], [10.0, 0.0]])
        socs = np.array([[0.0, 100.0], [0.0, 100.0]])
        result = m.lookup_many(temps, socs)
        assert result.shape == temps.shape


# --- BatteryProfileSettings: precompute_charge_discharge_limit_map -----------------------------------


class TestPrecompute:
    """Tests for the precompute_charge_discharge_limit_map path on BatteryProfileSettings."""

    def test_returns_two_precomputed_maps(self):
        charge, discharge = make_limits().precompute_charge_discharge_limit_map()
        assert isinstance(charge, PrecomputedCargeDischargeLimitMap)
        assert isinstance(discharge, PrecomputedCargeDischargeLimitMap)

    def test_charge_and_discharge_use_different_maps(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        limits = make_limits(
            charge_values=make_grid(2, 2, 0.3),
            discharge_values=make_grid(2, 2, 0.9),
            axes=axes,
        )
        charge, discharge = limits.precompute_charge_discharge_limit_map(t_step=1.0, s_step=1.0)
        assert charge.lookup(5.0, 50.0) == pytest.approx(0.3)
        assert discharge.lookup(5.0, 50.0) == pytest.approx(0.9)

    def test_precomputed_matches_reference_at_axis_points(self):
        # Non-trivial, non-uniform grid so any interpolation bug shows up.
        axes = Axes(
            temperature_degC=[-10, 0, 10, 25, 40],
            soc_percent=[0, 20, 50, 80, 100],
        )
        charge_values = [
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [0.1, 0.2, 0.3, 0.2, 0.05],
            [0.4, 0.5, 0.6, 0.4, 0.1],
            [0.5, 0.6, 0.7, 0.5, 0.15],
            [0.3, 0.4, 0.5, 0.3, 0.1],
        ]
        limits = make_limits(
            charge_values=charge_values,
            discharge_values=charge_values,
            axes=axes,
        )
        charge_map, discharge_map = limits.precompute_charge_discharge_limit_map(t_step=1.0, s_step=1.0)
        for t in axes.temperature_degC:
            for s in axes.soc_percent:
                ref_charge = limits.max_charge_crate(t, s)
                ref_discharge = limits.max_discharge_crate(t, s)
                if ref_charge is None:
                    assert charge_map.lookup(t, s) is None
                else:
                    assert charge_map.lookup(t, s) == pytest.approx(ref_charge, abs=1e-9)
                if ref_discharge is None:
                    assert discharge_map.lookup(t, s) is None
                else:
                    assert discharge_map.lookup(t, s) == pytest.approx(ref_discharge, abs=1e-9)

    def test_precomputed_without_cutoffs_covers_axis_only(self):
        limits = make_limits()
        charge_map, _ = limits.precompute_charge_discharge_limit_map(apply_cutoffs=False)
        assert charge_map.t_min == pytest.approx(limits.axes.temperature_degC[0])

    def test_precomputed_grid_point_matches_reference(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        values = [[1.0, 2.0], [3.0, 4.0]]
        limits = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
        )
        charge_map, _ = limits.precompute_charge_discharge_limit_map(t_step=10.0, s_step=100.0, apply_cutoffs=False)
        assert charge_map.lookup(0.0, 0.0) == pytest.approx(1.0)
        assert charge_map.lookup(10.0, 100.0) == pytest.approx(4.0)

    def test_cutoffs_are_baked_in_by_default(self):
        # Discharge down to -15, charge only to -10.
        limits = make_limits()
        charge_map, discharge_map = limits.precompute_charge_discharge_limit_map()
        # -12 degC: discharge still fine, charge inhibited
        assert charge_map.lookup(-12.0, 50.0) is None
        assert discharge_map.lookup(-12.0, 50.0) is not None

    def test_cutoffs_can_be_disabled(self):
        limits = make_limits()
        charge_map, _ = limits.precompute_charge_discharge_limit_map(apply_cutoffs=False)
        # Inside the axis and above the charge cutoff: a real value.
        value = charge_map.lookup(-9.0, 50.0)
        assert value is not None

    def test_rejects_non_positive_step(self):
        limits = make_limits()
        with pytest.raises(ValueError, match="positive"):
            limits.precompute_charge_discharge_limit_map(t_step=0.0)
        with pytest.raises(ValueError, match="positive"):
            limits.precompute_charge_discharge_limit_map(s_step=-1.0)

    def test_out_of_range_semantics_preserved_reject(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        cutoffs = HardCutoffs(
            charge_allowed_below_degC=-20.0,
            charge_allowed_above_degC=40.0,
            discharge_allowed_below_degC=-20.0,
            discharge_allowed_above_degC=40.0,
        )
        limits = make_limits(
            charge_values=make_grid(2, 2, 0.7),
            discharge_values=make_grid(2, 2, 0.7),
            axes=axes,
            hard_cutoffs=cutoffs,
            out_of_range_behavior=OutOfRangeBehavior.REJECT,
        )
        charge_map, _ = limits.precompute_charge_discharge_limit_map()
        # Outside the axis, inside the cutoff -> None (REJECT)
        assert charge_map.lookup(15.0, 50.0) is None

    def test_out_of_range_semantics_preserved_clamp(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        cutoffs = HardCutoffs(
            charge_allowed_below_degC=-20.0,
            charge_allowed_above_degC=40.0,
            discharge_allowed_below_degC=-20.0,
            discharge_allowed_above_degC=40.0,
        )
        limits = make_limits(
            charge_values=make_grid(2, 2, 0.7),
            discharge_values=make_grid(2, 2, 0.7),
            axes=axes,
            hard_cutoffs=cutoffs,
            out_of_range_behavior=OutOfRangeBehavior.CLAMP_TO_NEAREST,
        )
        charge_map, _ = limits.precompute_charge_discharge_limit_map()
        # Outside the axis, inside the cutoff -> clamped edge value 0.7
        assert charge_map.lookup(15.0, 50.0) == pytest.approx(0.7)

    def test_lookup_many_matches_single_lookup(self):
        limits = make_limits()
        charge_map, _ = limits.precompute_charge_discharge_limit_map()
        temps = np.array([25.0, 30.0, -12.0, 45.0])
        socs = np.array([50.0, 60.0, 50.0, 90.0])
        many = charge_map.lookup_many(temps, socs)
        for t, s, got in zip(temps, socs, many):
            single = charge_map.lookup(float(t), float(s))
            if single is None:
                assert np.isnan(got)
            else:
                assert got == pytest.approx(single)


# --- BatteryProfile: the user-facing object ------------------------------


class TestBatteryProfile:
    """Tests for the BatteryProfile wrapper returned to the controller."""

    def test_to_limits_carries_metadata(self):
        config = make_limits(
            chemistry="LFP",
            nominal_voltage_v=102.4,
            max_charge_power_w=5000.0,
            max_discharge_power_w=4500.0,
            technical_min_soc_percent=5.0,
            technical_max_soc_percent=95.0,
        )
        limits = config.to_limits()
        assert isinstance(limits, BatteryProfile)
        assert limits.battery_model == "Test Battery"
        assert limits.chemistry == "LFP"
        assert limits.nominal_voltage_v == pytest.approx(102.4)
        assert limits.max_charge_power_w == pytest.approx(5000.0)
        assert limits.max_discharge_power_w == pytest.approx(4500.0)
        assert limits.technical_min_soc_percent == pytest.approx(5.0)
        assert limits.technical_max_soc_percent == pytest.approx(95.0)
        assert limits.limit_unit == "C-rate"

    def test_accessors_delegate_to_precomputed_maps(self):
        axes = Axes(temperature_degC=[0, 10], soc_percent=[0, 100])
        config = make_limits(
            charge_values=make_grid(2, 2, 0.3),
            discharge_values=make_grid(2, 2, 0.9),
            axes=axes,
        )
        limits = config.to_limits()
        assert limits.max_charge_crate(5.0, 50.0) == pytest.approx(0.3)
        assert limits.max_discharge_crate(5.0, 50.0) == pytest.approx(0.9)

    def test_accessors_return_none_outside_cutoffs(self):
        limits = make_limits().to_limits()
        assert limits.max_charge_crate(-40.0, 50.0) is None
        assert limits.max_discharge_crate(80.0, 50.0) is None

    def test_accessors_match_reference_implementation(self):
        axes = Axes(
            temperature_degC=[-10, 0, 10, 25, 40],
            soc_percent=[0, 20, 50, 80, 100],
        )
        values = [
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [0.1, 0.2, 0.3, 0.2, 0.05],
            [0.4, 0.5, 0.6, 0.4, 0.1],
            [0.5, 0.6, 0.7, 0.5, 0.15],
            [0.3, 0.4, 0.5, 0.3, 0.1],
        ]
        config = make_limits(
            charge_values=values,
            discharge_values=values,
            axes=axes,
        )
        limits = config.to_limits()
        for t in axes.temperature_degC:
            for s in axes.soc_percent:
                ref_charge = config.max_charge_crate(t, s)
                ref_discharge = config.max_discharge_crate(t, s)
                if ref_charge is None:
                    assert limits.max_charge_crate(t, s) is None
                else:
                    assert limits.max_charge_crate(t, s) == pytest.approx(
                        ref_charge, abs=1e-9
                    )
                if ref_discharge is None:
                    assert limits.max_discharge_crate(t, s) is None
                else:
                    assert limits.max_discharge_crate(t, s) == pytest.approx(
                        ref_discharge, abs=1e-9
                    )


# --- JSON file entry point ----------------------------------------------


class TestFromJsonFile:
    """Tests for the BatteryProfileSettings.from_json_file entry point."""

    def test_loads_valid_document(self, tmp_path: Path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(make_document()), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(path)
        assert isinstance(limits, BatteryProfile)
        assert limits.battery_model == "Test Battery"
        assert limits.chemistry == "LFP"

    def test_accepts_string_path(self, tmp_path: Path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(make_document()), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(str(path))
        assert isinstance(limits, BatteryProfile)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            BatteryProfileSettings.from_json_file(tmp_path / "missing.json")

    def test_invalid_json_raises(self, tmp_path: Path):
        path = tmp_path / "limits.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(ValidationError):
            BatteryProfileSettings.from_json_file(path)

    def test_invalid_document_raises(self, tmp_path: Path):
        doc = make_document()
        # Shape mismatch: 3 rows for a 10-entry temperature axis
        doc["charge_limit_crate"]["values"] = make_grid(3, 10)
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValidationError, match="charge_limit_crate"):
            BatteryProfileSettings.from_json_file(path)

    def test_custom_steps_are_respected(self, tmp_path: Path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(make_document()), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(path, t_step=5.0, s_step=10.0)
        assert limits.charge_map.t_step == pytest.approx(5.0)
        assert limits.charge_map.s_step == pytest.approx(10.0)

    def test_loaded_limits_answer_queries(self, tmp_path: Path):
        doc = make_document(charge_values=make_grid(value=0.42))
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(path)
        assert limits.max_charge_crate(25.0, 50.0) == pytest.approx(0.42)

    def test_loaded_efficiency_curves_are_precomputed(self, tmp_path: Path):
        doc = make_document(
            charge_efficiency={
                "c_rate": [0.1, 0.5],
                "efficiency": [0.9, 0.96],
            },
            discharge_efficiency={
                "c_rate": [0.1, 0.5],
                "efficiency": [0.91, 0.95],
            },
        )
        path = tmp_path / "battery-profile.json"
        path.write_text(json.dumps(doc), encoding="utf-8")

        profile = BatteryProfileSettings.from_json_file(path)

        assert isinstance(profile.charge_efficiency, PrecomputedEfficiencyCurve)
        assert isinstance(profile.discharge_efficiency, PrecomputedEfficiencyCurve)
        assert profile.charge_efficiency_at(0.3) == pytest.approx(0.93)
        assert profile.discharge_efficiency_at(0.3) == pytest.approx(0.93)

    def test_loaded_cutoffs_are_honored(self, tmp_path: Path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(make_document()), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(path)
        # Below the charge cutoff but inside the discharge cutoff
        assert limits.max_charge_crate(-12.0, 50.0) is None
        assert limits.max_discharge_crate(-12.0, 50.0) is not None

    def test_round_trip_from_model(self, tmp_path: Path):
        original = make_limits(charge_values=make_grid(value=0.25))
        path = tmp_path / "limits.json"
        path.write_text(original.model_dump_json(), encoding="utf-8")
        limits = BatteryProfileSettings.from_json_file(path)
        assert limits.max_charge_crate(25.0, 50.0) == pytest.approx(0.25)


# --- Integration test --------------------------------------------------

class TestFromBatteryModel:
    """Tests for the BatteryProfileSettings.from_battery_model entry point.

    These tests exercise the real package data directory and the real
    generic-lfp.json file that ships with it. They are integration tests of
    name resolution plus the JSON-loading and precompute_charge_discharge_limit_map path.
    """

    def test_loads_generic_lfp(self):
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert isinstance(limits, BatteryProfile)
        # The shipped file must at least declare a model name and a unit.
        assert limits.battery_model
        assert limits.limit_unit

    def test_generic_lfp_answers_queries(self):
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        # Inside the precomputed grid, both accessors must return a number.
        assert limits.max_charge_crate(20.0, 50.0) is not None
        assert limits.max_discharge_crate(20.0, 50.0) is not None

    def test_generic_lfp_charge_prohibited_below_zero(self):
        # The generic-lfp file sets the charge hard cutoff at 0 degC.
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert limits.max_charge_crate(-1.0, 50.0) is None

    def test_generic_lfp_discharge_allowed_between_cutoffs(self):
        # Discharge is allowed from -10 degC to 60 degC.
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert limits.max_discharge_crate(-11.0, 50.0) is None
        assert limits.max_discharge_crate(20.0, 50.0) is not None
        assert limits.max_discharge_crate(61.0, 50.0) is None

    def test_generic_lfp_has_efficiency_curves(self):
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert limits.charge_efficiency_at(0.25) == pytest.approx(0.97)
        assert limits.discharge_efficiency_at(0.25) == pytest.approx(0.97)

    def test_generic_lfp_efficiency_falls_with_c_rate(self):
        # DC pack efficiency: resistive losses grow with current.
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        charge = [limits.charge_efficiency_at(c) or 0.0 for c in (0.05, 0.25, 0.5, 1.0)]
        discharge = [limits.discharge_efficiency_at(c) or 0.0 for c in (0.05, 0.25, 0.5)]
        assert charge == sorted(charge, reverse=True)
        assert discharge == sorted(discharge, reverse=True)
        assert all(0.9 < e <= 1.0 for e in charge + discharge)

    def test_generic_lfp_efficiency_is_clamped_beyond_curve(self):
        limits = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert limits.charge_efficiency_at(2.0) == pytest.approx(0.94)
        assert limits.discharge_efficiency_at(1.0) == pytest.approx(0.96)

    def test_missing_model_raises_filenotfound(self):
        with pytest.raises(FileNotFoundError, match="does-not-exist"):
            BatteryProfileSettings.from_battery_model("does-not-exist")

    def test_error_message_names_expected_path(self):
        with pytest.raises(FileNotFoundError, match=".json"):
            BatteryProfileSettings.from_battery_model("does-not-exist")

    def test_custom_steps_are_respected(self):
        limits = BatteryProfileSettings.from_battery_model(
            "generic-lfp", t_step=5.0, s_step=10.0
        )
        assert limits.charge_map.t_step == pytest.approx(5.0)
        assert limits.charge_map.s_step == pytest.approx(10.0)

    def test_returns_same_metadata_as_from_json_file(self):
        # Both entry points must produce an equivalent BatteryProfile for the
        # same file; this guards against a divergence in name resolution.
        from_model = BatteryProfileSettings.from_battery_model("generic-lfp")

        profile_path, exists = get_profile_store().path(BatteryProfileSettings, "generic-lfp")
        assert exists
        from_path = BatteryProfileSettings.from_json_file(profile_path)

        assert from_model.battery_model == from_path.battery_model
        assert from_model.chemistry == from_path.chemistry
        assert from_model.limit_unit == from_path.limit_unit

# --- nominal capacity -----------------------------------------------------


class TestNominalCapacity:
    """Tests for the optional nominal_capacity_wh of a battery profile."""

    def test_defaults_to_none(self):
        assert make_limits().nominal_capacity_wh is None
        assert make_limits().to_limits().nominal_capacity_wh is None

    def test_is_carried_into_the_compiled_profile(self):
        profile = make_limits(nominal_capacity_wh=10240.0).to_limits()
        assert profile.nominal_capacity_wh == pytest.approx(10240.0)

    @pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
    def test_invalid_values_are_rejected(self, value):
        with pytest.raises(ValidationError):
            make_limits(nominal_capacity_wh=value)

    def test_survives_json_round_trip(self):
        original = make_limits(nominal_capacity_wh=5120.0)
        restored = BatteryProfileSettings.model_validate_json(original.model_dump_json())
        assert restored.nominal_capacity_wh == pytest.approx(5120.0)

    def test_documents_without_capacity_stay_valid(self):
        document = json.loads(make_limits().model_dump_json())
        del document["nominal_capacity_wh"]
        assert BatteryProfileSettings.model_validate(document).nominal_capacity_wh is None

    def test_schema_describes_the_field(self):
        props = BatteryProfileSettings.model_json_schema()["properties"]
        assert "description" in props["nominal_capacity_wh"]


# --- profile store --------------------------------------------------------


class TestProfileStore:
    """Tests for battery profiles in the profile store: lookup, saving, caching."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self, config_eos):
        get_profile_store().clear_cache()
        yield
        get_profile_store().clear_cache()

    def test_packaged_profile_is_found(self):
        path, exists = get_profile_store().path(BatteryProfileSettings, "generic-lfp")
        assert exists
        assert path == get_profile_store().package_dir(BatteryProfileSettings) / "generic-lfp.json"
        assert path.parent.name == "battery"

    def test_missing_profile_points_to_user_dir(self):
        path, exists = get_profile_store().path(BatteryProfileSettings, "does-not-exist")
        assert not exists
        assert path == get_profile_store().user_dir(BatteryProfileSettings) / "does-not-exist.json"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            get_profile_store().path(BatteryProfileSettings, "")

    def test_user_profile_overrides_packaged_profile(self):
        user_path = make_limits(battery_model="generic-lfp").save_profile()
        path, exists = get_profile_store().path(BatteryProfileSettings, "generic-lfp")
        assert exists
        assert path == user_path
        assert get_profile_store().load(BatteryProfileSettings, "generic-lfp").battery_model == (
            "generic-lfp"
        )

    def test_save_and_load_round_trip(self):
        original = make_limits(battery_model="my-battery", charge_values=make_grid(value=0.3))
        path = original.save_profile()
        assert path == get_profile_store().user_dir(BatteryProfileSettings) / "my-battery.json"
        assert BatteryProfileSettings.load_profile("my-battery").model_dump() == original.model_dump()

    def test_load_missing_profile_names_expected_path(self):
        with pytest.raises(FileNotFoundError, match=r"No battery profile.*does-not-exist\.json"):
            BatteryProfileSettings.load_profile("does-not-exist")
        with pytest.raises(FileNotFoundError, match="does-not-exist.json"):
            get_profile_store().get(BatteryProfileSettings, "does-not-exist")

    def test_compiled_profile_is_cached(self):
        first = get_profile_store().get(BatteryProfileSettings, "generic-lfp")
        second = BatteryProfileSettings.from_battery_model("generic-lfp")
        assert first is second

        # Other precomputation steps are a different compiled profile.
        coarse = get_profile_store().get(BatteryProfileSettings, "generic-lfp", t_step=5.0)
        assert coarse is not first
        assert coarse.charge_map.t_step == pytest.approx(5.0)
        assert BatteryProfileSettings.from_battery_model("generic-lfp", t_step=5.0) is coarse

    def test_unknown_compile_option_raises(self):
        with pytest.raises(TypeError, match="Unknown battery profile options"):
            get_profile_store().get(BatteryProfileSettings, "generic-lfp", u_step=1.0)

    def test_document_is_a_battery_profile_type(self):
        assert BatteryProfileSettings.profile_kind == "battery"
        assert isinstance(make_limits().compile_profile(), BatteryProfile)

    def test_changed_file_is_recompiled(self):
        make_limits(battery_model="edited", charge_values=make_grid(value=0.2)).save_profile()
        before = get_profile_store().get(BatteryProfileSettings, "edited")
        assert before.max_charge_crate(25.0, 50.0) == pytest.approx(0.2)

        # A different size changes the cache key even if mtime has coarse resolution.
        make_limits(
            battery_model="edited",
            charge_values=make_grid(value=0.25),
            chemistry="LFP",
        ).save_profile()
        after = get_profile_store().get(BatteryProfileSettings, "edited")
        assert after is not before
        assert after.max_charge_crate(25.0, 50.0) == pytest.approx(0.25)

    def test_compiled_grids_are_read_only(self):
        profile = get_profile_store().get(BatteryProfileSettings, "generic-lfp")
        with pytest.raises(ValueError):
            profile.charge_map.grid[0, 0] = 1.0


# --- JSON schema ---------------------------------------------------------


class TestJsonSchema:
    """Tests that the generated JSON schema carries descriptions and examples."""

    def test_control_battery_limits_schema(self):
        schema = BatteryProfileSettings.model_json_schema()
        props = schema["properties"]
        assert "description" in props["interpolation"]
        assert props["interpolation"]["examples"] == ["bilinear"]
        assert "description" in props["hard_cutoffs"]
        assert "description" in props["battery_model"]

    def test_axes_schema_examples(self):
        schema = Axes.model_json_schema()
        assert schema["properties"]["temperature_degC"]["examples"]
        assert schema["properties"]["soc_percent"]["examples"]


# --- Round-trip ----------------------------------------------------------


class TestRoundTrip:
    """Tests that a config survives a JSON round-trip unchanged."""

    def test_round_trip_preserves_efficiency_curves(self):
        original = make_limits(
            charge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.9, 0.96]
            ),
            discharge_efficiency=make_efficiency_curve(
                c_rate=[0.1, 0.5], efficiency=[0.91, 0.95],
                interpolation=EfficiencyInterpolation.NEAREST,
                out_of_range_behavior=OutOfRangeBehavior.REJECT,
            ),
        )
        restored = BatteryProfileSettings.model_validate_json(
            original.model_dump_json()
        )

        assert restored.model_dump() == original.model_dump()
        assert restored.charge_efficiency is not None
        assert restored.discharge_efficiency is not None
        assert restored.charge_efficiency.lookup(0.3) == pytest.approx(0.93)
        assert restored.discharge_efficiency.lookup(0.3) == pytest.approx(0.91)

    def test_round_trip_preserves_values(self):
        original = make_limits(charge_values=make_grid(value=0.25))
        dumped = original.model_dump_json()
        restored = BatteryProfileSettings.model_validate_json(dumped)
        assert restored.model_dump() == original.model_dump()
        assert restored.max_charge_crate(25.0, 50.0) == pytest.approx(0.25)


# --- Strict profile documents --------------------------------------------


class TestStrictProfile:
    """A profile contains every value the computation uses, and nothing unknown."""

    @pytest.mark.parametrize(
        "field",
        [
            "technical_min_soc_percent",
            "technical_max_soc_percent",
            "charge_efficiency",
            "discharge_efficiency",
            "units",
            "axes",
            "hard_cutoffs",
            "charge_limit_crate",
            "discharge_limit_crate",
        ],
    )
    def test_required_fields(self, field):
        document = make_document()
        del document[field]
        with pytest.raises(ValidationError, match=field):
            BatteryProfileSettings.model_validate(document)

    @pytest.mark.parametrize(
        "field",
        ["nominal_capacity_wh", "max_charge_power_w", "max_discharge_power_w", "chemistry"],
    )
    def test_optional_fields(self, field):
        document = make_document()
        document.pop(field, None)
        assert getattr(BatteryProfileSettings.model_validate(document), field) is None

    def test_unknown_top_level_key_is_rejected(self):
        # A typo of nominal_capacity_wh must not be ignored silently.
        document = make_document(nominal_capacity_kwh=10.0)
        with pytest.raises(ValidationError, match="nominal_capacity_kwh"):
            BatteryProfileSettings.model_validate(document)

    @pytest.mark.parametrize("section", ["units", "axes", "hard_cutoffs", "charge_efficiency"])
    def test_unknown_nested_key_is_rejected(self, section):
        document = make_document()
        document[section] = {**document[section], "unknown_key": 1}
        with pytest.raises(ValidationError, match="unknown_key"):
            BatteryProfileSettings.model_validate(document)

    @pytest.mark.parametrize("section", ["charge_efficiency", "hard_cutoffs"])
    def test_sections_take_a_description(self, section):
        document = make_document()
        document[section] = {**document[section], "description": "Data sheet values"}
        profile = BatteryProfileSettings.model_validate(document)
        assert getattr(profile, section).description == "Data sheet values"

    def test_technical_soc_bounds_must_be_ordered(self):
        document = make_document(technical_min_soc_percent=90.0, technical_max_soc_percent=10.0)
        with pytest.raises(ValidationError, match="technical_min_soc_percent"):
            BatteryProfileSettings.model_validate(document)

    def test_bundled_generic_profile_is_valid_and_generic(self, config_eos):
        path = (
            config_eos.package_root_path / "data" / "profile" / "battery" / "generic-lfp.json"
        )
        profile = BatteryProfileSettings.model_validate(json.loads(path.read_text()))
        # Generic: C-rate limits, no capacity of its own.
        assert profile.nominal_capacity_wh is None
        assert profile.units.limit == "C-rate"
        assert profile.technical_min_soc_percent == 0.0
        assert profile.technical_max_soc_percent == 100.0
