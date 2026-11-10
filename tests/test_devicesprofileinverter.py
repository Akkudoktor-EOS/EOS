"""Tests for inverter profile documents and their runtime form."""

import copy
from typing import Any, Iterator

import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.profile.inverterprofile import (
    InverterProfile,
    InverterProfileSettings,
)
from akkudoktoreos.devices.profile.profilestore import (
    ProfileStore,
    get_profile_store,
    profile_type,
)

DOCUMENT: dict[str, Any] = {
    "inverter_model": "Test inverter",
    "nominal_power_w": 10000.0,
    "max_ac_charge_power_w": 6000.0,
    "dc_to_ac_efficiency": {
        "load_fraction": [0.1, 0.5, 1.0],
        "efficiency": [0.90, 0.97, 0.96],
    },
    "ac_to_dc_efficiency": {
        "load_fraction": [0.1, 1.0],
        "efficiency": [0.88, 0.95],
    },
    "power_derating": {
        "temperature_degC": [40.0, 60.0],
        "power_fraction": [1.0, 0.5],
    },
}


def make_document(**changes: Any) -> dict[str, Any]:
    document = copy.deepcopy(DOCUMENT)
    document.update(changes)
    return document


def make_profile(**changes: Any) -> InverterProfile:
    return InverterProfileSettings.model_validate(make_document(**changes)).compile_profile()


class TestDocument:
    def test_registered_as_inverter_kind(self):
        assert profile_type("inverter") is InverterProfileSettings

    def test_minimal_generic_document(self):
        document = make_document(nominal_power_w=None, max_ac_charge_power_w=None)
        del document["power_derating"]
        settings = InverterProfileSettings.model_validate(document)
        assert settings.nominal_power_w is None
        assert settings.power_derating is None
        assert settings.ac_charge_limits_total_charge is False

    @pytest.mark.parametrize("field", ["dc_to_ac_efficiency", "ac_to_dc_efficiency"])
    def test_efficiency_curves_are_required(self, field):
        document = make_document()
        del document[field]
        with pytest.raises(ValidationError, match=field):
            InverterProfileSettings.model_validate(document)

    def test_rejects_unknown_keys(self):
        with pytest.raises(ValidationError, match="nominal_power_kw"):
            InverterProfileSettings.model_validate(make_document(nominal_power_kw=10.0))

    @pytest.mark.parametrize(
        "curve",
        [
            {"load_fraction": [0.5, 0.1], "efficiency": [0.9, 0.9]},
            {"load_fraction": [-0.1, 1.0], "efficiency": [0.9, 0.9]},
            {"load_fraction": [0.1, 1.0], "efficiency": [0.0, 0.9]},
            {"load_fraction": [0.1, 1.0], "efficiency": [0.9, 1.1]},
            {"load_fraction": [0.1, 0.5, 1.0], "efficiency": [0.9, 0.9]},
            {"load_fraction": [0.1], "efficiency": [0.9]},
        ],
    )
    def test_rejects_invalid_efficiency_curves(self, curve):
        with pytest.raises(ValidationError):
            InverterProfileSettings.model_validate(make_document(dc_to_ac_efficiency=curve))

    @pytest.mark.parametrize(
        "curve",
        [
            {"temperature_degC": [60.0, 40.0], "power_fraction": [1.0, 0.5]},
            {"temperature_degC": [40.0, 60.0], "power_fraction": [1.0, 1.5]},
            {"temperature_degC": [40.0, 60.0], "power_fraction": [1.0, -0.1]},
        ],
    )
    def test_rejects_invalid_derating(self, curve):
        with pytest.raises(ValidationError):
            InverterProfileSettings.model_validate(make_document(power_derating=curve))

    @pytest.mark.parametrize("field", ["nominal_power_w", "max_ac_charge_power_w"])
    @pytest.mark.parametrize("value", [0.0, -1.0, float("inf")])
    def test_rejects_invalid_powers(self, field, value):
        with pytest.raises(ValidationError):
            InverterProfileSettings.model_validate(make_document(**{field: value}))

    def test_compile_rejects_options(self):
        with pytest.raises(TypeError):
            InverterProfileSettings.model_validate(make_document()).compile_profile(t_step=1.0)


class TestRuntime:
    def test_carries_the_model_data(self):
        profile = make_profile(ac_charge_limits_total_charge=True)
        assert profile.inverter_model == "Test inverter"
        assert profile.nominal_power_w == pytest.approx(10000.0)
        assert profile.max_ac_charge_power_w == pytest.approx(6000.0)
        assert profile.ac_charge_limits_total_charge is True

    def test_efficiencies_interpolate_linearly(self):
        profile = make_profile()
        assert profile.dc_to_ac_efficiency_at(0.3) == pytest.approx(0.935)
        assert profile.ac_to_dc_efficiency_at(0.55) == pytest.approx(0.915)

    def test_efficiencies_clamp_beyond_the_curve(self):
        profile = make_profile()
        assert profile.dc_to_ac_efficiency_at(0.0) == pytest.approx(0.90)
        assert profile.dc_to_ac_efficiency_at(1.5) == pytest.approx(0.96)

    def test_derating(self):
        profile = make_profile()
        assert profile.power_fraction_at(20.0) == pytest.approx(1.0)
        assert profile.power_fraction_at(50.0) == pytest.approx(0.75)
        assert profile.power_fraction_at(80.0) == pytest.approx(0.5)

    def test_without_derating_full_power(self):
        document = make_document()
        del document["power_derating"]
        profile = InverterProfileSettings.model_validate(document).compile_profile()
        assert profile.power_fraction_at(70.0) == pytest.approx(1.0)

    def test_is_immutable(self):
        profile = make_profile()
        with pytest.raises(AttributeError):
            profile.nominal_power_w = 1.0  # type: ignore[misc]


class TestPackagedGenericHybrid:
    @pytest.fixture
    def profile(self, config_eos) -> Iterator[InverterProfile]:
        ProfileStore.reset_instance()
        yield get_profile_store().get(InverterProfileSettings, "generic-hybrid")
        ProfileStore.reset_instance()

    def test_is_generic(self, profile):
        assert profile.nominal_power_w is None
        assert profile.max_ac_charge_power_w is None

    def test_full_power_up_to_45_degc(self, profile):
        assert profile.power_fraction_at(-30.0) == pytest.approx(1.0)
        assert profile.power_fraction_at(45.0) == pytest.approx(1.0)
        assert profile.power_fraction_at(60.0) == pytest.approx(0.6)
        assert profile.power_fraction_at(65.0) == pytest.approx(0.0)

    def test_realistic_efficiencies(self, profile):
        for load_fraction in (0.05, 0.1, 0.3, 0.5, 1.0):
            assert 0.85 <= profile.dc_to_ac_efficiency_at(load_fraction) < 1.0
            assert 0.85 <= profile.ac_to_dc_efficiency_at(load_fraction) < 1.0
