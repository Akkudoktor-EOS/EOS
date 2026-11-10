"""Tests for the inverter device settings resolved from inverter profiles."""

import dataclasses
import math
from typing import Any, Optional

import pytest

from akkudoktoreos.devices.profile.inverterprofile import (
    InverterProfile,
    InverterProfileSettings,
)
from akkudoktoreos.devices.profile.profilecurve import PrecomputedCurve
from akkudoktoreos.devices.profile.profilestore import get_profile_store
from akkudoktoreos.devices.settings import invertermeasurement
from akkudoktoreos.devices.settings.inverterresolution import (
    INVERTER_DEFAULT_TEMPERATURE_DEGC,
    InverterOperatingPoint,
    InverterResolutionError,
)
from akkudoktoreos.devices.settings.invertersettings import (
    INVERTER_DEFAULT_PROFILE,
    InverterCommonSettings,
    InverterProfileAdjustment,
)


def make_settings(**adjustment: Any) -> InverterCommonSettings:
    return InverterCommonSettings(
        device_id="inverter1",
        battery_id="battery1",
        profile_adjustment=InverterProfileAdjustment(**adjustment),
    )


@pytest.fixture
def generic(config_eos) -> InverterProfile:
    """The packaged generic hybrid inverter profile."""
    return get_profile_store().get(InverterProfileSettings, "generic-hybrid")


@pytest.fixture
def use_profile(monkeypatch):
    """Let all inverter settings use the given compiled profile."""

    def _use(profile: InverterProfile) -> None:
        monkeypatch.setattr(InverterCommonSettings, "get_inverter_profile", lambda self: profile)

    return _use


@pytest.fixture
def measured(monkeypatch) -> dict[str, Optional[float]]:
    """Measured values by key for the inverter measurement access."""
    values: dict[str, Optional[float]] = {}

    async def fake_get_measured_value(key: str, target_datetime: Any) -> Optional[float]:
        return values.get(key)

    monkeypatch.setattr(invertermeasurement, "get_measured_value", fake_get_measured_value)
    return values


class TestDefaults:
    def test_default_profile(self):
        settings = InverterCommonSettings(device_id="inverter1")
        assert settings.inverter_profile == INVERTER_DEFAULT_PROFILE == "generic-hybrid"
        assert settings.profile_adjustment.model_dump() == InverterProfileAdjustment().model_dump()

    def test_generic_profile_needs_a_nominal_power(self, generic):
        settings = InverterCommonSettings(device_id="inverter1")
        with pytest.raises(InverterResolutionError, match="inverter1.*nominal_power_w"):
            settings.resolve_operating_parameters()
        assert settings.effective_nominal_power_w is None
        assert settings.effective_max_power_w is None
        errors, warnings = settings.check_profile()
        assert len(errors) == 1 and "inverter1" in errors[0]
        assert warnings == []

    @pytest.mark.parametrize(
        "adjustment",
        [{"nominal_power_w": 0}, {"max_power_w": 0}, {"max_ac_charge_power_w": -1}],
    )
    def test_rejects_invalid_adjustment(self, adjustment):
        with pytest.raises(ValueError):
            InverterProfileAdjustment(**adjustment)


class TestResolution:
    def test_reference_point_is_45_degc(self, generic):
        settings = make_settings(nominal_power_w=10000)
        parameters = settings.resolve_operating_parameters()
        assert parameters.temperature_degC == INVERTER_DEFAULT_TEMPERATURE_DEGC == 45.0
        # The generic profile is not derated at 45 degC.
        assert parameters.power_fraction == pytest.approx(1.0)
        assert parameters.nominal_power_w == pytest.approx(10000)
        assert parameters.max_power_w == pytest.approx(10000)
        assert parameters.max_ac_charge_power_w == pytest.approx(10000)
        assert settings.effective_max_power_w == pytest.approx(10000)

    def test_efficiencies_come_from_the_profile(self, generic):
        settings = make_settings(nominal_power_w=10000)
        parameters = settings.resolve_operating_parameters()
        # DC→AC at the reference load fraction 0.06 of the maximum power.
        assert parameters.dc_to_ac_efficiency == pytest.approx(generic.dc_to_ac_efficiency_at(0.06))
        # AC→DC at the AC charge limit, here the full nominal power.
        assert parameters.ac_to_dc_efficiency == pytest.approx(generic.ac_to_dc_efficiency_at(1.0))
        assert settings.effective_dc_to_ac_efficiency == pytest.approx(
            parameters.dc_to_ac_efficiency
        )
        assert settings.effective_ac_to_dc_efficiency == pytest.approx(
            parameters.ac_to_dc_efficiency
        )

    def test_derating_at_the_operating_point(self, generic):
        settings = make_settings(nominal_power_w=10000)
        parameters = settings.resolve_operating_parameters(InverterOperatingPoint(60.0))
        assert parameters.power_fraction == pytest.approx(0.6)
        assert parameters.max_power_w == pytest.approx(6000)
        assert parameters.max_ac_charge_power_w == pytest.approx(6000)

    def test_curves_are_over_the_load_fraction_of_the_maximum_power(self, generic):
        settings = make_settings(nominal_power_w=10000)
        parameters = settings.resolve_operating_parameters(InverterOperatingPoint(60.0))
        # 3000 W are 0.3 of the nominal and 0.5 of the derated maximum power.
        curve = PrecomputedCurve.from_points(
            [x for x, _ in parameters.dc_to_ac_efficiency_curve],
            [y for _, y in parameters.dc_to_ac_efficiency_curve],
        )
        assert curve.lookup(0.5) == pytest.approx(generic.dc_to_ac_efficiency_at(0.3))
        # The curve ends at the maximum power, 0.6 of the nominal power.
        assert parameters.dc_to_ac_efficiency_curve[-1] == pytest.approx(
            (1.0, generic.dc_to_ac_efficiency_at(0.6))
        )
        assert all(0.0 <= x <= 1.0 for x, _ in parameters.dc_to_ac_efficiency_curve)

    def test_curve_keeps_two_points_when_heavily_limited(self, generic):
        # 400 W are below the first breakpoint (0.05 of 10 kW = 500 W).
        settings = make_settings(nominal_power_w=10000, max_power_w=400)
        curve = settings.resolve_operating_parameters().dc_to_ac_efficiency_curve
        assert [x for x, _ in curve] == [0.0, 1.0]
        assert [y for _, y in curve] == pytest.approx([0.88, 0.88])
        settings.to_genetic_param()

    def test_no_power_at_the_operating_point(self, generic):
        settings = make_settings(nominal_power_w=10000)
        with pytest.raises(InverterResolutionError, match="allows no power"):
            settings.resolve_operating_parameters(InverterOperatingPoint(70.0))

    def test_installation_limits_only_lower(self, generic):
        settings = make_settings(nominal_power_w=10000, max_power_w=8000, max_ac_charge_power_w=3000)
        parameters = settings.resolve_operating_parameters()
        assert parameters.max_power_w == pytest.approx(8000)
        assert parameters.max_ac_charge_power_w == pytest.approx(3000)
        assert parameters.ac_to_dc_efficiency == pytest.approx(generic.ac_to_dc_efficiency_at(0.3))
        assert settings.check_profile() == ([], [])

    def test_derating_below_the_installation_limit(self, generic):
        settings = make_settings(nominal_power_w=10000, max_power_w=8000)
        parameters = settings.resolve_operating_parameters(InverterOperatingPoint(60.0))
        assert parameters.max_power_w == pytest.approx(6000)

    def test_zero_ac_charge_limit_disables_ac_charging(self, generic):
        settings = make_settings(nominal_power_w=10000, max_ac_charge_power_w=0)
        parameters = settings.resolve_operating_parameters()
        assert parameters.max_ac_charge_power_w == 0
        assert math.isfinite(parameters.ac_to_dc_efficiency)
        assert settings.to_genetic_param().max_ac_charge_power_w == 0

    def test_model_ac_charge_limit_is_derated(self, generic, use_profile):
        use_profile(dataclasses.replace(generic, nominal_power_w=10000, max_ac_charge_power_w=5000))
        settings = make_settings()
        assert settings.resolve_operating_parameters().max_ac_charge_power_w == pytest.approx(5000)
        derated = settings.resolve_operating_parameters(InverterOperatingPoint(60.0))
        assert derated.max_ac_charge_power_w == pytest.approx(3000)

    def test_profile_nominal_power_and_adjustment(self, generic, use_profile):
        use_profile(dataclasses.replace(generic, nominal_power_w=6000))
        assert make_settings().effective_nominal_power_w == pytest.approx(6000)
        assert make_settings(nominal_power_w=8000).effective_nominal_power_w == pytest.approx(8000)

    def test_missing_profile(self, config_eos):
        settings = InverterCommonSettings(
            device_id="inverter1",
            inverter_profile="does-not-exist",
            profile_adjustment=InverterProfileAdjustment(nominal_power_w=10000),
        )
        with pytest.raises(InverterResolutionError, match="does-not-exist.*not usable"):
            settings.resolve_operating_parameters()
        assert settings.effective_max_power_w is None
        errors, _ = settings.check_profile()
        assert "does-not-exist" in errors[0]

    def test_warns_about_limits_without_effect(self, generic):
        settings = make_settings(nominal_power_w=5000, max_power_w=8000, max_ac_charge_power_w=6000)
        errors, warnings = settings.check_profile()
        assert errors == []
        assert len(warnings) == 2
        assert "profile_adjustment.max_power_w" in warnings[0]
        assert "profile_adjustment.max_ac_charge_power_w" in warnings[1]


class TestMeasurement:
    def test_temperature_measurement_key(self):
        settings = make_settings(nominal_power_w=10000)
        assert settings.measurement_key_temperature_degc == "inverter1-temperature-degc"
        assert "inverter1-temperature-degc" in settings.measurement_keys

    @pytest.mark.asyncio
    async def test_measured_temperature(self, measured):
        measured["inverter1-temperature-degc"] = 52.5
        point = await make_settings().measure_operating_point()
        assert point == InverterOperatingPoint(temperature_degC=52.5)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [None, float("nan"), float("inf")])
    async def test_missing_temperature_defaults_to_45_degc(self, measured, value):
        measured["inverter1-temperature-degc"] = value
        point = await make_settings().measure_operating_point()
        assert point.temperature_degC == 45.0

    @pytest.mark.asyncio
    async def test_resolution_at_the_measured_temperature(self, generic, measured):
        settings = make_settings(nominal_power_w=10000)
        measured["inverter1-temperature-degc"] = 60.0
        parameters = await settings.resolve_current_operating_parameters()
        assert parameters.max_power_w == pytest.approx(6000)


class TestConversion:
    def test_genetic_parameters(self, generic):
        settings = make_settings(nominal_power_w=10000, max_ac_charge_power_w=4000)
        parameters = settings.resolve_operating_parameters(InverterOperatingPoint(60.0))
        genetic = settings.to_genetic_param(parameters)
        assert genetic.device_id == "inverter1"
        assert genetic.battery_id == "battery1"
        assert genetic.max_power_wh == pytest.approx(6000)
        assert genetic.max_ac_charge_power_w == pytest.approx(4000)
        assert genetic.dc_to_ac_efficiency == pytest.approx(parameters.dc_to_ac_efficiency)
        assert genetic.ac_to_dc_efficiency == pytest.approx(parameters.ac_to_dc_efficiency)
        assert genetic.dc_to_ac_efficiency_curve == [
            tuple(point) for point in parameters.dc_to_ac_efficiency_curve
        ]
        assert genetic.ac_charge_limits_total_charge is False

    def test_genetic0_parameters(self, generic):
        settings = make_settings(nominal_power_w=10000)
        genetic0 = settings.to_genetic0_param()
        assert genetic0.max_power_wh == pytest.approx(10000)
        assert genetic0.dc_to_ac_efficiency == pytest.approx(settings.effective_dc_to_ac_efficiency)
        assert genetic0.ac_to_dc_efficiency == pytest.approx(settings.effective_ac_to_dc_efficiency)


@pytest.fixture
def logged_errors():
    """Collect the messages logged at ERROR level."""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    yield messages
    logger.remove(sink_id)


class TestOptimizationDefaults:
    """The parameter preparation of the optimizations never fails (defaults with an error log)."""

    @pytest.mark.asyncio
    async def test_derated_to_zero_uses_the_reference_temperature(
        self, generic, measured, logged_errors
    ):
        """generic-hybrid derates to 0 W at 65 degC; the run gets the 45 degC parameters."""
        settings = make_settings(nominal_power_w=10000)
        measured["inverter1-temperature-degc"] = 70.0
        parameters = await settings.resolve_current_operating_parameters_or_defaults()
        assert parameters.max_power_w == pytest.approx(10000)
        assert parameters.temperature_degC == INVERTER_DEFAULT_TEMPERATURE_DEGC
        assert len(logged_errors) == 1
        assert "allows no power at 70.0 degC" in logged_errors[0]
        # The conversion gets valid parameters.
        assert settings.to_genetic_param(parameters).max_power_wh == pytest.approx(10000)

    def test_missing_nominal_power_gives_the_fallback(self, generic, logged_errors):
        parameters = make_settings().resolve_operating_parameters_or_defaults()
        assert parameters.nominal_power_w == pytest.approx(10000)
        assert len(logged_errors) == 1
        assert "nominal power 10000 W" in logged_errors[0]

    def test_unusable_profile_uses_the_generic_profile(self, generic, logged_errors):
        settings = InverterCommonSettings(
            device_id="inverter1",
            battery_id="battery1",
            inverter_profile="does-not-exist",
            profile_adjustment=InverterProfileAdjustment(nominal_power_w=8000),
        )
        parameters = settings.resolve_operating_parameters_or_defaults()
        assert parameters.nominal_power_w == pytest.approx(8000)
        assert parameters.dc_to_ac_efficiency < 1.0  # from the generic profile curves
        assert len(logged_errors) == 1
        assert f"inverter profile '{INVERTER_DEFAULT_PROFILE}'" in logged_errors[0]

    @pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, IsADirectoryError])
    def test_without_any_usable_profile(self, monkeypatch, logged_errors, error):
        def missing(document_type, name, **options):
            raise error(f"Inverter profile '{name}' is not readable")

        monkeypatch.setattr(get_profile_store(), "get", missing)
        settings = make_settings(nominal_power_w=8000, max_power_w=6000)
        parameters = settings.resolve_operating_parameters_or_defaults()
        assert parameters.max_power_w == pytest.approx(6000)
        assert parameters.dc_to_ac_efficiency == pytest.approx(1.0)
        assert len(logged_errors) == 1
        assert "constant efficiency 1" in logged_errors[0]

    def test_report_false_logs_nothing(self, generic, logged_errors):
        make_settings().resolve_operating_parameters_or_defaults(report=False)
        assert logged_errors == []
