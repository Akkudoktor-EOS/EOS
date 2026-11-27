import pytest

from akkudoktoreos.thermal.thermaldiagnostics import planning_components


def test_effective_gain_never_claims_separate_defrost_or_cop():
    result = planning_components(
        mode="effective_electric",
        model={"electric_gain_kelvin_per_kwh": 3},
        weather=[{"weather_temp_air": 10, "weather_relative_humidity": 70}],
        maximum_w=2500,
        minimum_w=None,
        distribution="direct",
    )
    assert result["weather"][0]["dew_point_c"] == pytest.approx(4.775, abs=0.01)
    assert result["defrost"]["status"] == "not_separately_modelled"
    assert result["cop_separately_identified"] is False


def test_missing_humidity_does_not_invent_dew_point():
    result = planning_components(
        mode="effective_electric",
        model={},
        weather=[{"weather_temp_air": 1}],
        maximum_w=2500,
        minimum_w=0,
        distribution="direct",
    )
    assert result["weather"][0]["dew_point_c"] is None


def test_provider_dew_point_keeps_its_provenance():
    result = planning_components(
        mode="heat_pump",
        model={},
        weather=[{"weather_temp_air": 1, "weather_dew_point": -2}],
        maximum_w=2500,
        minimum_w=0,
        distribution="direct",
    )
    assert result["weather"][0]["dew_point_c"] == -2
    assert result["weather"][0]["dew_point_source"] == "weather_provider"
    assert result["defrost"]["event_prediction_available"] is False
