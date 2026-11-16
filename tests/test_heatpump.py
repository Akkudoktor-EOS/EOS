"""Pure performance-map contracts, including deliberately incomplete HTML priors."""

import json
import math
from importlib.resources import files
from typing import get_args

import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.genetic.heatpump import Heatpump
from akkudoktoreos.devices.heatpump import (
    HeatPumpCapacityPoint,
    HeatPumpCOPPoint,
    HeatPumpDefrostSettings,
    HeatPumpElectricalSettings,
    HeatPumpOperatingPoint,
    HeatPumpPerformance,
    HeatPumpPresetId,
    HeatPumpProfile,
    WaterHeatingCurve,
    calculate_dew_point,
    evaluate_heat_pump,
    evaluate_heat_pump_electricity,
    evaluate_water_heating_curve,
    get_heat_pump_operating_points,
    get_heat_pump_preset,
    infer_thermal_power_from_electric,
    list_heat_pump_presets,
)
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord


@pytest.fixture(scope="function")
def hp_5kw_24h() -> Heatpump:
    """Heatpump with 5 kW heating power and 24 h prediction."""
    return Heatpump(5000, 24)


class TestHeatpump:
    """Preserve the existing GENETIC heat-pump regression coverage."""

    def test_cop(self, hp_5kw_24h: Heatpump):
        """Testing calculate COP for various outside temperatures."""
        assert hp_5kw_24h.calculate_cop(-10) == 2.0
        assert hp_5kw_24h.calculate_cop(0) == 3.0
        assert hp_5kw_24h.calculate_cop(10) == 4.0
        out_temp_min = -100.1
        out_temp_max = 100.1
        with pytest.raises(ValueError, match=f"'{out_temp_min}' not in range"):
            hp_5kw_24h.calculate_cop(out_temp_min)
        with pytest.raises(ValueError, match=f"'{out_temp_max}' not in range"):
            hp_5kw_24h.calculate_cop(out_temp_max)

    def test_heating_output(self, hp_5kw_24h: Heatpump):
        """Testing calculation of heating output."""
        assert hp_5kw_24h.calculate_heating_output(-10.0) == 5000
        assert hp_5kw_24h.calculate_heating_output(0.0) == 5000
        assert hp_5kw_24h.calculate_heating_output(10.0) == pytest.approx(4939.583)

    def test_heating_power(self, hp_5kw_24h: Heatpump):
        """Testing calculation of heating power."""
        assert hp_5kw_24h.calculate_heat_power(-10.0) == 2104
        assert hp_5kw_24h.calculate_heat_power(0.0) == 1164
        assert hp_5kw_24h.calculate_heat_power(10.0) == 548


@pytest.mark.parametrize(
    "kind", ["brine_to_water", "water_to_water", "brine_to_air", "water_to_air"]
)
def test_source_temperature_is_independent_of_building_weather(kind):
    source = profile(kind=kind)
    warm = WeatherDataRecord(weather_temp_air=25.0)
    cold = WeatherDataRecord(weather_temp_air=-20.0)
    a = evaluate_heat_pump(source, warm, 35.0, source_temperature_c=0.0)
    b = evaluate_heat_pump(source, cold, 35.0, source_temperature_c=0.0)
    assert a.model_dump() == b.model_dump()
    assert a.cop == pytest.approx(4.0)
    with pytest.raises(ValueError, match="explicit source"):
        evaluate_heat_pump(source, warm, 35.0)
    with pytest.raises(ValueError, match="frost correction"):
        evaluate_heat_pump(
            source,
            warm,
            35.0,
            defrost=HeatPumpDefrostSettings(mode="html_heuristic"),
            source_temperature_c=0.0,
        )


def test_operating_map_interpolation_preserves_heat_and_interpolates_electricity():
    payload = profile().model_dump()
    payload["operating_point_interpolation"] = "linear_common_heat"
    payload["operating_points"] = [
        dict(
            outdoor_temperature_c=source,
            delivery_temperature_c=sink,
            thermal_power_w=heat,
            electric_power_w=heat / (4 + source / 10 - (sink - 35) / 10),
            point_id=f"{source}-{sink}-{heat}",
            source="synthetic test",
        )
        for source in (0.0, 10.0)
        for sink in (35.0, 45.0)
        for heat in (1000.0, 3000.0)
    ]
    hp = HeatPumpProfile.model_validate(payload)
    values = get_heat_pump_operating_points(hp, WeatherDataRecord(weather_temp_air=5.0), 40.0)
    assert [p.thermal_power_w for p in values] == [1000.0, 3000.0]
    assert values[0].electric_power_w == pytest.approx(1000 * (1 / 4 + 1 / 5 + 1 / 3 + 1 / 4) / 4)
    assert not get_heat_pump_operating_points(hp, WeatherDataRecord(weather_temp_air=-1.0), 40.0)
    payload["operating_point_interpolation"] = "exact"
    assert not get_heat_pump_operating_points(
        HeatPumpProfile.model_validate(payload), WeatherDataRecord(weather_temp_air=5.0), 40.0
    )


def profile(kind="air_to_water", minimum=True, includes=False):
    """Construct a small complete, intentionally synthetic rectangular map."""
    points = [(delivery, outside) for delivery in (35.0, 55.0) for outside in (-5.0, 5.0)]
    return HeatPumpProfile.model_validate(
        {
            "profile_id": "synthetic",
            "heat_pump_type": kind,
            "cop_points": [
                {
                    "delivery_temperature_c": delivery,
                    "outdoor_temperature_c": outside,
                    "cop": 4.0 + outside / 5 - (delivery - 35) / 20,
                }
                for delivery, outside in points
            ],
            "thermal_max_points": [
                {
                    "delivery_temperature_c": delivery,
                    "outdoor_temperature_c": outside,
                    "thermal_power_w": 5000.0 + 100 * outside - 50 * (delivery - 35),
                }
                for delivery, outside in points
            ],
            "thermal_min_points": [
                {
                    "delivery_temperature_c": delivery,
                    "outdoor_temperature_c": outside,
                    "thermal_power_w": 1000.0,
                }
                for delivery, outside in points
            ]
            if minimum
            else [],
            "cop_includes_defrost": includes,
            "provenance": {"source": "Synthetic test data", "quality": "user_supplied"},
        }
    )


def weather(temperature=0.0, humidity=None, dewpoint=None):
    return WeatherDataRecord(
        weather_temp_air=temperature,
        weather_relative_humidity=humidity,
        weather_dew_point=dewpoint,
    )


@pytest.mark.parametrize(
    "delivery,outdoor,expected", [(35.0, -5.0, 3.0), (55.0, 5.0, 4.0), (45.0, 0.0, 3.5)]
)
def test_linear_interpolation_exact_and_interior(delivery, outdoor, expected):
    result = evaluate_heat_pump(profile(), weather(outdoor), delivery)
    assert result.supported
    assert result.cop == pytest.approx(expected)
    assert result.max_electric_power_w * result.cop == pytest.approx(result.max_thermal_power_w)
    assert result.min_electric_power_w is not None
    assert result.min_electric_power_w * result.cop == pytest.approx(1000.0)


def test_order_independent_interpolation_and_json_roundtrip():
    original = profile()
    data = original.model_dump(mode="json")
    for key in ("cop_points", "thermal_max_points", "thermal_min_points"):
        data[key].reverse()
    restored = HeatPumpProfile.model_validate_json(json.dumps(data))
    actual = evaluate_heat_pump(restored, weather(), 45.0)
    expected = evaluate_heat_pump(original, weather(), 45.0)
    assert actual.model_dump() == expected.model_dump()
    assert (
        HeatPumpProfile.model_validate_json(original.model_dump_json()).model_dump()
        == original.model_dump()
    )


@pytest.mark.parametrize("outdoor,delivery", [(-6.0, 35.0), (6.0, 35.0), (0.0, 34.9), (0.0, 55.1)])
def test_no_silent_extrapolation(outdoor, delivery):
    result = evaluate_heat_pump(profile(), weather(outdoor), delivery)
    assert not result.supported
    assert any("outside_support_clamped" in flag for flag in result.quality_flags)
    assert result.cop > 0


def test_sparse_cop_slice_does_not_impute_missing_w55():
    data = profile().model_dump()
    data["cop_points"] = [
        item
        for item in data["cop_points"]
        if item["delivery_temperature_c"] == 35.0 or item["outdoor_temperature_c"] == -5.0
    ]
    sparse = HeatPumpProfile.model_validate(data)
    assert evaluate_heat_pump(sparse, weather(), 35.0).supported
    assert not evaluate_heat_pump(sparse, weather(), 45.0).supported
    assert not evaluate_heat_pump(sparse, weather(), 55.0).supported
    assert evaluate_heat_pump(sparse, weather(-5.0), 55.0).supported


def test_missing_minimum_is_not_invented():
    result = evaluate_heat_pump(profile(minimum=False), weather(), 35.0)
    assert result.min_thermal_power_w is None
    assert result.min_electric_power_w is None
    assert "minimum_modulation_unknown" in result.quality_flags
    assert result.supported


@pytest.mark.parametrize("field", ["cop_points", "thermal_max_points", "thermal_min_points"])
def test_duplicate_coordinates_rejected(field):
    data = profile().model_dump()
    data[field] = (*data[field], data[field][0])
    with pytest.raises(ValidationError, match="duplicate"):
        HeatPumpProfile.model_validate(data)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True])
def test_nonfinite_and_boolean_rating_values_rejected(bad):
    with pytest.raises(ValidationError):
        HeatPumpCOPPoint(outdoor_temperature_c=0.0, delivery_temperature_c=35.0, cop=bad)
    with pytest.raises(ValidationError):
        HeatPumpCapacityPoint(
            outdoor_temperature_c=bad, delivery_temperature_c=35.0, thermal_power_w=1000.0
        )


def test_nonpositive_cop_negative_capacity_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        HeatPumpCOPPoint(outdoor_temperature_c=0.0, delivery_temperature_c=35.0, cop=0.0)
    with pytest.raises(ValidationError):
        HeatPumpCapacityPoint(
            outdoor_temperature_c=0.0, delivery_temperature_c=35.0, thermal_power_w=-1.0
        )
    with pytest.raises(ValidationError):
        HeatPumpDefrostSettings.model_validate({"guaranteed_defrost": True})
    with pytest.raises(ValidationError):
        HeatPumpDefrostSettings.model_validate({"allow_unknown_cop_basis": 1})


def test_capacity_minimum_must_not_exceed_maximum():
    data = profile().model_dump()
    data["thermal_min_points"][0]["thermal_power_w"] = 10000.0
    with pytest.raises(ValidationError, match="Minimum"):
        HeatPumpProfile.model_validate(data)


def test_profiles_and_results_are_immutable():
    value = profile()
    with pytest.raises(ValidationError):
        value.profile_id = "changed"
    with pytest.raises(ValidationError):
        value.cop_points[0].cop = 2.0
    result = evaluate_heat_pump(value, weather(), 35.0)
    with pytest.raises(ValidationError):
        setattr(result, "cop", 2.0)
    assert (
        HeatPumpPerformance.model_validate_json(result.model_dump_json()).model_dump()
        == result.model_dump()
    )


def test_dew_point_known_values_and_saturation():
    assert calculate_dew_point(20.0, 50.0) == pytest.approx(9.25517, abs=1e-4)
    assert calculate_dew_point(-3.0, 100.0) == pytest.approx(-3.0)
    result = evaluate_heat_pump(profile(), weather(0.0, 90.0), 35.0)
    assert result.dew_point_c == pytest.approx(calculate_dew_point(0.0, 90.0))
    assert "dew_point_magnus_derived" in result.quality_flags


@pytest.mark.parametrize("rh", [0.0, -1.0, 100.1, math.nan, True])
def test_dew_point_rejects_invalid_or_nonfinite_humidity(rh):
    with pytest.raises(ValueError):
        calculate_dew_point(0.0, rh)


def test_zero_humidity_preserves_missing_dewpoint():
    result = evaluate_heat_pump(profile(), weather(0.0, 0.0), 35.0)
    assert result.dew_point_c is None
    assert "finite_dew_point_unavailable_at_zero_humidity" in result.quality_flags


def test_supplied_weather_dew_point_is_preserved():
    result = evaluate_heat_pump(profile(), weather(0.0, 90.0, -2.0), 35.0)
    assert result.dew_point_c == -2.0
    assert "dew_point_magnus_derived" not in result.quality_flags
    with pytest.raises(ValueError, match="Dew point"):
        evaluate_heat_pump(profile(), weather(0.0, 90.0, 2.0), 35.0)


@pytest.mark.parametrize("basis", [True, None])
def test_defrost_is_skipped_when_included_or_basis_unknown(basis):
    result = evaluate_heat_pump(
        profile(includes=basis),
        weather(0.0, 95.0),
        35.0,
        4000.0,
        HeatPumpDefrostSettings(mode="html_heuristic"),
    )
    assert result.cop == 4.0
    assert result.defrost_multiplier == 1.0
    assert any("defrost_correction_skipped" in flag for flag in result.quality_flags)


def test_html_frost_formula_is_explicit_and_optional():
    original = profile(includes=False)
    plain = evaluate_heat_pump(original, weather(0.0, 95.0), 35.0, 4000.0)
    heuristic = evaluate_heat_pump(
        original,
        weather(0.0, 95.0),
        35.0,
        4000.0,
        HeatPumpDefrostSettings(mode="html_heuristic"),
    )
    assert heuristic.estimated_evaporator_temperature_c == pytest.approx(-2.9)
    assert heuristic.defrost_multiplier == pytest.approx(0.88)
    assert heuristic.cop == pytest.approx(plain.cop * 0.88)
    assert heuristic.max_thermal_power_w == plain.max_thermal_power_w
    assert heuristic.max_electric_power_w > plain.max_electric_power_w
    assert "defrost_heuristic_cop_penalty_applied" in heuristic.quality_flags


def test_unknown_defrost_basis_requires_explicit_opt_in():
    result = evaluate_heat_pump(
        profile(includes=None),
        weather(0.0, 95.0),
        35.0,
        4000.0,
        HeatPumpDefrostSettings(mode="html_heuristic", allow_unknown_cop_basis=True),
    )
    assert result.defrost_multiplier < 1
    assert "cop_defrost_basis_unknown" in result.quality_flags


@pytest.mark.parametrize(
    "minimum,load,humidity", [(False, 4000.0, 95.0), (True, None, 95.0), (True, 4000.0, None)]
)
def test_frost_missing_inputs_does_not_invent_data(minimum, load, humidity):
    result = evaluate_heat_pump(
        profile(minimum=minimum),
        weather(0.0, humidity),
        35.0,
        load,
        HeatPumpDefrostSettings(mode="html_heuristic"),
    )
    assert result.defrost_multiplier == 1.0
    assert "defrost_correction_skipped_missing_inputs" in result.quality_flags


def test_low_load_does_not_create_html_frost_penalty_or_cycling_loss():
    result = evaluate_heat_pump(
        profile(),
        weather(0.0, 95.0),
        35.0,
        500.0,
        HeatPumpDefrostSettings(mode="html_heuristic"),
    )
    assert result.cop == 4.0
    assert result.defrost_multiplier == 1.0


def test_air_to_air_uses_its_own_indoor_rating_axis():
    data = profile(kind="air_to_air").model_dump()
    for key in ("cop_points", "thermal_max_points", "thermal_min_points"):
        for point in data[key]:
            point["delivery_temperature_c"] -= 15.0
    air = HeatPumpProfile.model_validate(data)
    assert evaluate_heat_pump(air, weather(), 20.0).supported
    assert not evaluate_heat_pump(air, weather(), 19.0).supported
    assert air.heat_pump_type == "air_to_air"


@pytest.mark.parametrize("missing", [None, math.nan, math.inf])
def test_missing_or_invalid_temperature_cannot_be_planned(missing):
    with pytest.raises(ValueError):
        evaluate_heat_pump(profile(), weather(missing), 35.0)


def test_catalog_has_25_numeric_unverified_profiles_and_seven_unknown_minima():
    identifiers = list_heat_pump_presets()
    assert len(identifiers) == 26
    assert identifiers == tuple(sorted(identifiers))
    assert identifiers == tuple(sorted(get_args(HeatPumpPresetId)))
    presets = [
        get_heat_pump_preset(identifier)
        for identifier in identifiers
        if identifier != "panasonic_vz12ske"
    ]
    assert len(presets) == 25
    assert sum(not preset.thermal_min_points for preset in presets) == 7
    for preset in presets:
        assert preset.profile_id in identifiers
        assert preset.heat_pump_type == "air_to_water"
        assert preset.provenance.quality == "unverified_html"
        assert preset.cop_includes_defrost is None
        assert all(point.delivery_temperature_c == 35.0 for point in preset.thermal_max_points)
        assert not evaluate_heat_pump(preset, weather(), 55.0).supported
    with pytest.raises(ValueError, match="Unknown heat-pump preset"):
        get_heat_pump_preset("does-not-exist")


def test_packaged_catalog_contains_only_declared_profile_fields():
    catalog = json.loads(files("akkudoktoreos").joinpath("heatpumppresets.json").read_text("utf-8"))
    assert (
        catalog["source_sha256"]
        == "d736b640594185d2a71d6ce941b38320108d9c0f8612796a5ab6b0461620951c"
    )
    for item in catalog["profiles"].values():
        assert set(item) <= set(HeatPumpProfile.model_fields)
        assert "C:\\Users" not in json.dumps(item)


@pytest.mark.parametrize(
    "delivery,load",
    [
        (math.nan, 100.0),
        (math.inf, 100.0),
        (True, 100.0),
        (35.0, -1.0),
        (35.0, math.nan),
        (35.0, True),
    ],
)
def test_nonfinite_and_negative_runtime_requests_rejected(delivery, load):
    with pytest.raises(ValueError):
        evaluate_heat_pump(profile(), weather(), delivery, load)


def test_inconsistent_capacities_between_differently_sampled_curves_rejected():
    data = profile().model_dump()
    data["thermal_min_points"] = [
        {"outdoor_temperature_c": 0.0, "delivery_temperature_c": 45.0, "thermal_power_w": 6000.0}
    ]
    inconsistent = HeatPumpProfile.model_validate(data)
    with pytest.raises(ValueError, match="Interpolated minimum"):
        evaluate_heat_pump(inconsistent, weather(), 45.0)


@pytest.mark.parametrize(
    "temperature,humidity,load",
    [(4.0, 95.0, 4000.0), (-5.0, 95.0, 4000.0), (0.0, 88.0, 4000.0), (0.0, 95.0, 1200.0)],
)
def test_html_frost_requires_all_strict_thresholds(temperature, humidity, load):
    result = evaluate_heat_pump(
        profile(),
        weather(temperature, humidity),
        35.0,
        load,
        HeatPumpDefrostSettings(mode="html_heuristic"),
    )
    assert result.defrost_multiplier == 1.0


def electrical_settings() -> HeatPumpElectricalSettings:
    return HeatPumpElectricalSettings(
        standby_power_w=10.0,
        running_auxiliary_power_w=30.0,
        startup_energy_wh=20.0,
        extras_are_outside_cop=True,
    )


@pytest.mark.parametrize(
    "field", ["standby_power_w", "running_auxiliary_power_w", "startup_energy_wh"]
)
def test_positive_electric_extras_require_explicit_system_boundary(field):
    with pytest.raises(ValidationError, match="outside the COP boundary"):
        HeatPumpElectricalSettings.model_validate({field: 1.0})
    accepted = HeatPumpElectricalSettings.model_validate(
        {field: 1.0, "extras_are_outside_cop": True}
    )
    assert getattr(accepted, field) == 1.0


@pytest.mark.parametrize(
    "running,was_running,power,expected,started",
    [
        (True, False, 2000.0, 155.0, True),
        (True, True, 2000.0, 135.0, False),
        (False, True, 0.0, 2.5, False),
        (False, False, 0.0, 2.5, False),
    ],
)
def test_electricity_counts_each_component_and_transition_once(
    running, was_running, power, expected, started
):
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    result = evaluate_heat_pump_electricity(
        performance, power, 900.0, running, was_running, electrical_settings()
    )
    assert result.total_energy_wh == pytest.approx(expected)
    assert result.mean_electric_power_w == pytest.approx(expected * 4)
    assert result.started is started
    assert result.base_energy_wh == pytest.approx(power / 4 * 0.25)
    assert result.standby_energy_wh == 2.5
    assert result.auxiliary_energy_wh == (7.5 if running else 0)
    assert result.startup_energy_wh == (20 if started else 0)


def test_interval_partition_preserves_energy_and_single_startup():
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    whole = evaluate_heat_pump_electricity(
        performance, 2000.0, 3600.0, True, False, electrical_settings()
    )
    parts = [
        evaluate_heat_pump_electricity(
            performance, 2000.0, 900.0, True, k > 0, electrical_settings()
        )
        for k in range(4)
    ]
    assert sum(part.total_energy_wh for part in parts) == pytest.approx(whole.total_energy_wh)
    assert sum(part.started for part in parts) == 1
    assert sum(part.startup_energy_wh for part in parts) == 20.0


def test_unknown_extra_energy_is_not_silently_declared_zero():
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    omitted = evaluate_heat_pump_electricity(
        performance, 0.0, 900.0, False, False, HeatPumpElectricalSettings()
    )
    explicit = evaluate_heat_pump_electricity(
        performance,
        0.0,
        900.0,
        False,
        False,
        HeatPumpElectricalSettings(
            standby_power_w=0.0, running_auxiliary_power_w=0.0, startup_energy_wh=0.0
        ),
    )
    assert omitted.total_energy_wh == explicit.total_energy_wh == 0.0
    assert sum(flag.startswith("unmodelled_electricity:") for flag in omitted.quality_flags) == 3
    assert not any(flag.startswith("unmodelled_electricity:") for flag in explicit.quality_flags)


@pytest.mark.parametrize(
    "power,running", [(1.0, False), (-1.0, True), (500.0, True), (6000.0, True)]
)
def test_electric_accounting_rejects_inconsistent_or_unsupported_heat(power, running):
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    with pytest.raises(ValueError):
        evaluate_heat_pump_electricity(
            performance, power, 900.0, running, False, electrical_settings()
        )
    unsupported = evaluate_heat_pump(profile(), weather(-10.0), 35.0)
    with pytest.raises(ValueError, match="supported"):
        evaluate_heat_pump_electricity(
            unsupported, 2000.0, 900.0, True, False, electrical_settings()
        )


@pytest.mark.parametrize("duration", [0.0, -1.0, math.nan, math.inf, True])
def test_electric_accounting_requires_finite_positive_duration(duration):
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    with pytest.raises(ValueError):
        evaluate_heat_pump_electricity(
            performance, 2000.0, duration, True, False, electrical_settings()
        )


@pytest.mark.parametrize(
    "field", ["standby_power_w", "running_auxiliary_power_w", "startup_energy_wh"]
)
@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, True])
def test_electric_configuration_rejects_negative_nonfinite_or_boolean_values(field, value):
    with pytest.raises(ValidationError):
        HeatPumpElectricalSettings.model_validate({field: value, "extras_are_outside_cop": True})


def curve() -> WaterHeatingCurve:
    return WaterHeatingCurve(
        design_outdoor_temperature_c=-10.0,
        heating_limit_outdoor_temperature_c=15.0,
        min_flow_temperature_c=25.0,
        max_flow_temperature_c=50.0,
        reference_room_temperature_c=20.0,
        room_target_correction_gain=2.0,
    )


@pytest.mark.parametrize(
    "outdoor,target,expected,requested",
    [
        (-10.0, 20.0, 50.0, True),
        (15.0, 20.0, 25.0, False),
        (0.0, 20.0, 40.0, True),
        (0.0, 22.0, 44.0, True),
        (-20.0, 22.0, 50.0, True),
        (25.0, 18.0, 25.0, False),
    ],
)
def test_water_curve_uses_outdoor_room_target_and_final_bounds(
    outdoor, target, expected, requested
):
    result = evaluate_water_heating_curve(curve(), weather(outdoor), target)
    assert result.flow_temperature_c == expected
    assert result.heating_requested is requested
    assert "outdoor_threshold_not_a_recovery_veto" in result.quality_flags


def test_water_curve_config_json_roundtrip_is_explicit_and_immutable():
    original = curve()
    restored = WaterHeatingCurve.model_validate_json(original.model_dump_json())
    assert restored.model_dump() == original.model_dump()
    with pytest.raises(ValidationError):
        WaterHeatingCurve.model_validate({})
    with pytest.raises(ValidationError):
        setattr(original, "min_flow_temperature_c", 0.0)


@pytest.mark.parametrize(
    "update",
    [
        {"design_outdoor_temperature_c": 15.0},
        {"heating_limit_outdoor_temperature_c": -20.0},
        {"min_flow_temperature_c": 51.0},
        {"room_target_correction_gain": -1.0},
        {"reference_room_temperature_c": math.nan},
    ],
)
def test_water_curve_rejects_undefined_or_inverted_parameters(update):
    with pytest.raises(ValidationError):
        WaterHeatingCurve.model_validate({**curve().model_dump(), **update})


def test_water_curve_requires_current_finite_native_inputs():
    with pytest.raises(ValueError):
        evaluate_water_heating_curve(curve(), weather(None), 20.0)
    with pytest.raises(ValueError):
        evaluate_water_heating_curve(curve(), weather(0.0), math.nan)


def test_auxiliary_electricity_is_not_delivered_thermal_heat():
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    before = performance.model_dump()
    result = evaluate_heat_pump_electricity(
        performance, 2000.0, 900.0, True, False, electrical_settings()
    )
    assert performance.model_dump() == before
    assert result.total_energy_wh > result.base_energy_wh
    assert "startup_electricity_not_assumed_delivered_heat" in result.quality_flags


@pytest.mark.parametrize("power", [1000.0 - 1e-8, 5000.0 + 1e-8])
def test_solver_capacity_roundoff_is_accepted_without_clipping(power):
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    result = evaluate_heat_pump_electricity(
        performance, power, 900.0, True, False, electrical_settings()
    )
    assert result.base_energy_wh == pytest.approx(power / 4 * 0.25)
    assert "capacity_boundary_roundoff_tolerance_1e-5_w" in result.quality_flags


@pytest.mark.parametrize("power", [1000.0 - 1e-3, 5000.0 + 1e-3])
def test_solver_tolerance_does_not_accept_physical_capacity_violation(power):
    performance = evaluate_heat_pump(profile(), weather(), 35.0)
    with pytest.raises(ValueError):
        evaluate_heat_pump_electricity(
            performance, power, 900.0, True, False, electrical_settings()
        )


def test_documented_air_preset_is_separate_from_unverified_water_presets():
    device = get_heat_pump_preset("panasonic_vz12ske")
    assert device.heat_pump_type == "air_to_air"
    assert device.provenance.quality == "manufacturer_documented"
    assert device.minimum_operating_outdoor_temperature_c == -30.0
    assert device.maximum_operating_outdoor_temperature_c == 24.0
    assert len(device.operating_points) == 10
    assert device.cop_includes_defrost is None
    nominal = next(
        point for point in device.operating_points if point.point_id == "panasonic_nominal"
    )
    assert nominal.outdoor_temperature_c == 7.0
    assert nominal.delivery_temperature_c == 20.0
    assert nominal.thermal_power_w / nominal.electric_power_w == pytest.approx(5.04)


def test_exact_operating_slice_preserves_real_partload_and_nominal_pairing():
    device = get_heat_pump_preset("panasonic_vz12ske")
    points = get_heat_pump_operating_points(device, weather(7.0), 20.0)
    assert [point.thermal_power_w for point in points] == [1450.0, 4200.0]
    assert [point.thermal_power_w / point.electric_power_w for point in points] == pytest.approx(
        [7.8, 5.04]
    )
    assert points[0].electric_power_w < points[1].electric_power_w
    assert [
        point.thermal_power_w
        for point in get_heat_pump_operating_points(device, weather(-7.0), 20.0)
    ] == [3720.0, 5600.0]
    assert not any(point.thermal_power_w == 9200.0 for point in points)
    assert not any(point.thermal_power_w == 600.0 for point in points)


@pytest.mark.parametrize(
    "outdoor,delivery", [(6.9, 20.0), (7.0, 20.1), (8.0, 20.0), (-35.0, 20.0), (25.0, 20.0)]
)
def test_operating_slices_never_borrow_different_weather_or_indoor_rating(outdoor, delivery):
    device = get_heat_pump_preset("panasonic_vz12ske")
    assert get_heat_pump_operating_points(device, weather(outdoor), delivery) == ()


def test_scalar_cop_path_cannot_silently_apply_nominal_cop_to_maximum_load():
    device = get_heat_pump_preset("panasonic_vz12ske")
    result = evaluate_heat_pump(device, weather(7.0), 20.0, 9200.0)
    assert not result.supported
    assert "load_axis_requires_segment_planning" in result.quality_flags
    below_range = evaluate_heat_pump(device, weather(-35.0), 20.0)
    assert "outside_declared_operating_temperature_range" in below_range.quality_flags


def test_operating_maps_reject_duplicate_loads_and_decreasing_electrical_power():
    device = get_heat_pump_preset("panasonic_vz12ske")
    data = device.model_dump()
    data["operating_points"] = (*data["operating_points"], data["operating_points"][0])
    with pytest.raises(ValidationError, match="duplicate heat-load"):
        HeatPumpProfile.model_validate(data)
    data = device.model_dump()
    for point in data["operating_points"]:
        if point["point_id"] == "eurovent_partload_c":
            point["electric_power_w"] = 1000.0
    with pytest.raises(ValidationError, match="nondecreasing"):
        HeatPumpProfile.model_validate(data)


def test_operating_points_reject_blank_source_and_undefined_power():
    with pytest.raises(ValidationError):
        HeatPumpOperatingPoint(
            outdoor_temperature_c=7.0,
            delivery_temperature_c=20.0,
            thermal_power_w=1000.0,
            electric_power_w=0.0,
            point_id="test",
            source="https://example.invalid",
        )
    with pytest.raises(ValidationError):
        HeatPumpOperatingPoint(
            outdoor_temperature_c=7.0,
            delivery_temperature_c=20.0,
            thermal_power_w=1000.0,
            electric_power_w=300.0,
            point_id="test",
            source=" ",
        )


def inverse_profile() -> HeatPumpProfile:
    data = profile().model_dump()
    data["operating_points"] = [
        {
            "outdoor_temperature_c": 0.0,
            "delivery_temperature_c": 35.0,
            "thermal_power_w": q,
            "electric_power_w": p,
            "point_id": f"synthetic{index}",
            "source": "Synthetic inverse test",
        }
        for index, (q, p) in enumerate(((1000.0, 200.0), (2000.0, 1000.0), (3000.0, 1200.0)))
    ]
    return HeatPumpProfile.model_validate(data)


def inverse_extras() -> HeatPumpElectricalSettings:
    return HeatPumpElectricalSettings(
        standby_power_w=5.0,
        running_auxiliary_power_w=50.0,
        startup_energy_wh=0.0,
        extras_are_outside_cop=True,
    )


def test_inverse_paired_curve_uses_adjacent_segment_and_removes_declared_losses():
    result = infer_thermal_power_from_electric(
        inverse_profile(), weather(), 35.0, 655.0, inverse_extras()
    )
    assert result.thermal_power_w == pytest.approx(1500.0)
    assert result.estimated is True
    assert "thermal_power_inferred_from_electricity_not_measured" in result.quality_flags
    assert "declared_nonthermal_electricity_subtracted" in result.quality_flags
    assert "paired_operating_points_inverse_interpolation" in result.quality_flags


def test_inverse_fixed_cop_counts_no_auxiliary_or_standby_as_heat():
    result = infer_thermal_power_from_electric(profile(), weather(), 35.0, 555.0, inverse_extras())
    assert result.thermal_power_w == pytest.approx(2000.0)
    assert "fixed_cop_inverse_estimate_at_declared_rating" in result.quality_flags
    with pytest.raises(ValueError, match="supported"):
        infer_thermal_power_from_electric(profile(), weather(-10.0), 35.0, 555.0, inverse_extras())


def test_inverse_rejects_ambiguous_flat_electric_segment():
    data = inverse_profile().model_dump()
    data["operating_points"][1]["electric_power_w"] = 200.0
    ambiguous = HeatPumpProfile.model_validate(data)
    with pytest.raises(ValueError, match="Ambiguous"):
        infer_thermal_power_from_electric(ambiguous, weather(), 35.0, 255.0, inverse_extras())


def test_inverse_never_extrapolates_load_weather_or_indoor_rating():
    device = inverse_profile()
    for temperature, delivery, power in (
        (0.0, 35.0, 254.0),
        (0.0, 35.0, 1256.0),
        (0.1, 35.0, 655.0),
        (0.0, 35.1, 655.0),
    ):
        with pytest.raises(ValueError):
            infer_thermal_power_from_electric(
                device, weather(temperature), delivery, power, inverse_extras()
            )


def test_inverse_singleton_point_accepts_only_its_exact_electric_rating():
    device = get_heat_pump_preset("panasonic_vz12ske")
    point = get_heat_pump_operating_points(device, weather(2.0), 20.0)[0]
    none = HeatPumpElectricalSettings()
    result = infer_thermal_power_from_electric(
        device, weather(2.0), 20.0, point.electric_power_w, none
    )
    assert result.thermal_power_w == point.thermal_power_w
    with pytest.raises(ValueError):
        infer_thermal_power_from_electric(
            device, weather(2.0), 20.0, point.electric_power_w + 1.0, none
        )


def test_inverse_declared_standby_band_estimates_zero_without_compressor_heat():
    for power in (0.0, 3.0, 5.0):
        result = infer_thermal_power_from_electric(
            inverse_profile(), weather(), 35.0, power, inverse_extras()
        )
        assert result.thermal_power_w == 0.0
        assert "electric_snapshot_in_declared_off_standby_band" in result.quality_flags
    with pytest.raises(ValueError):
        infer_thermal_power_from_electric(
            profile(minimum=False), weather(), 35.0, 0.0, HeatPumpElectricalSettings()
        )


def test_inverse_unknown_components_remain_explicitly_unknown():
    result = infer_thermal_power_from_electric(
        profile(), weather(), 35.0, 500.0, HeatPumpElectricalSettings()
    )
    assert result.thermal_power_w == 2000.0
    assert sum(flag.startswith("unmodelled_electricity:") for flag in result.quality_flags) == 3


def test_inverse_cannot_strip_start_energy_from_a_snapshot():
    settings = HeatPumpElectricalSettings(startup_energy_wh=10.0, extras_are_outside_cop=True)
    with pytest.raises(ValueError, match="startup energy"):
        infer_thermal_power_from_electric(profile(), weather(), 35.0, 500.0, settings)


def test_inverse_rejects_negative_nonfinite_or_boolean_electrical_input():
    for power in (-1.0, math.nan, math.inf, True):
        with pytest.raises(ValueError):
            infer_thermal_power_from_electric(profile(), weather(), 35.0, power, inverse_extras())
