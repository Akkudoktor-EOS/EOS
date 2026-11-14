"""Tests for the batterysettings module.

The module exercises the configuration fields of BatteriesCommonSettings, the
computed fields that are evaluated on access at the reference operating point,
the battery profile that is always present (default ``generic-lfp``), the
operating point and the charge / discharge power read asynchronously from
measurements, and the conversion to the GENETIC and GENETIC0 optimization
parameters.

The battery profile loader and the measurement access are replaced by autouse
fixtures, so most tests do not depend on the configuration singleton, the
measurement store or the packaged data directory. Unless a test installs another
profile, settings use a plain profile (8000 Wh, charge 0.5 C, discharge 0.4 C,
flat efficiency curves of 0.88, technical SoC bounds 0 % and 100 %) and no
measurement is available. TestMeasurementAccess uses the real measurement store.
"""

import dataclasses
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.profile.batteryprofile import (
    Axes,
    BatteryProfile,
    BatteryProfileSettings,
    ChargeDischargeLimitMap,
    EfficiencyCurve,
    HardCutoffs,
    OutOfRangeBehavior,
    Units,
)
from akkudoktoreos.devices.profile.profilestore import get_profile_store
from akkudoktoreos.devices.settings import batterymeasurement
from akkudoktoreos.devices.settings.batterysettings import (
    BATTERY_DEFAULT_PROFILE,
    BATTERY_DEFAULT_SOC_FACTOR,
    BATTERY_DEFAULT_TEMPERATURE_DEGC,
    BatteriesCommonSettings,
    BatteryOperatingParameters,
    BatteryOperatingPoint,
    BatteryProfileAdjustment,
    BatteryResolutionError,
)

# The real measurement accessor, captured before any fixture replaces it.
REAL_GET_MEASURED_VALUE = batterymeasurement.get_measured_value

# Capacity and flat efficiency of the plain test profile.
PROFILE_CAPACITY_WH = 8000
PROFILE_EFFICIENCY = 0.88

# Axes of the test profiles: temperatures [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50],
# SoC [0, 5, 10, 20, 40, 60, 80, 90, 95, 100].
TEST_AXES = {
    "temperature_degC": [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50],
    "soc_percent": [0, 5, 10, 20, 40, 60, 80, 90, 95, 100],
}
# Hard cutoffs of the test profiles: charging -10 ... 55 degC, discharging -15 ... 60 degC.
TEST_CUTOFFS = {
    "charge_allowed_below_degC": -10.0,
    "charge_allowed_above_degC": 55.0,
    "discharge_allowed_below_degC": -15.0,
    "discharge_allowed_above_degC": 60.0,
}
ROW_0_DEGC = 2
ROW_25_DEGC = 6
COLUMN_40_PERCENT = 4
COLUMN_60_PERCENT = 5

PARAMETER_METHODS = [
    "to_genetic_pv_bat_param",
    "to_genetic_ev_bat_param",
    "to_genetic0_pv_bat_param",
    "to_genetic0_ev_bat_param",
]

COMPUTED_FIELDS = [
    "effective_nominal_capacity_wh",
    "effective_max_charge_power_w",
    "effective_max_discharge_power_w",
    "effective_charging_efficiency",
    "effective_discharging_efficiency",
    "effective_min_soc_percentage",
    "effective_max_soc_percentage",
]

# Fields that moved into the profile adjustment: (former field, adjustment field).
MOVED_TO_ADJUSTMENT = [
    ("nominal_capacity_wh", "nominal_capacity_wh"),
    ("max_charge_power_limit_w", "max_charge_power_w"),
    ("max_discharge_power_limit_w", "max_discharge_power_w"),
]

# Computed fields that used to read measurements and are gone now.
REMOVED_COMPUTED_FIELDS = ["current_soc_factor", "current_temperature_degc"]

# Fields that used to be configuration fields and are computed now.
FORMER_CONFIGURATION_FIELDS = [
    "capacity_wh",
    "charging_efficiency",
    "discharging_efficiency",
    "max_charge_power_w",
    "max_discharge_power_w",
]


# --- helpers -------------------------------------------------------------


def make_grid(value: float, rows: int = 10, cols: int = 10) -> list[list[float]]:
    """Return a constant rows x cols grid filled with value."""
    return [[value for _ in range(cols)] for _ in range(rows)]


def make_profile_config(
    charge: float | list[list[float]] = 0.5,
    discharge: float | list[list[float]] = 0.4,
    **overrides: Any,
) -> BatteryProfileSettings:
    """Build a validated BatteryProfileSettings.

    Without overrides the profile is the plain test profile: 8000 Wh, flat
    efficiency curves of 0.88, technical SoC bounds 0 % and 100 %, C-rate limits.

    Args:
        charge: Constant charge limit or a full 10 x 10 grid.
        discharge: Constant discharge limit or a full 10 x 10 grid.
        **overrides: Further keyword arguments forwarded to the model, e.g.
            ``nominal_capacity_wh=None`` for a generic profile.

    Returns:
        A validated BatteryProfileSettings instance.
    """
    charge_values = make_grid(charge) if isinstance(charge, (int, float)) else charge
    discharge_values = make_grid(discharge) if isinstance(discharge, (int, float)) else discharge
    payload: dict[str, Any] = dict(
        battery_model="Test Battery",
        nominal_capacity_wh=float(PROFILE_CAPACITY_WH),
        technical_min_soc_percent=0.0,
        technical_max_soc_percent=100.0,
        charge_efficiency=make_curve([0.0, 2.0], [PROFILE_EFFICIENCY, PROFILE_EFFICIENCY]),
        discharge_efficiency=make_curve([0.0, 2.0], [PROFILE_EFFICIENCY, PROFILE_EFFICIENCY]),
        units=Units(limit="C-rate"),
        axes=Axes.model_validate(TEST_AXES),
        hard_cutoffs=HardCutoffs.model_validate(TEST_CUTOFFS),
        charge_limit_crate=ChargeDischargeLimitMap(values=charge_values),
        discharge_limit_crate=ChargeDischargeLimitMap(values=discharge_values),
    )
    payload.update(overrides)
    return BatteryProfileSettings(**payload)


def make_profile(
    charge: float | list[list[float]] = 0.5,
    discharge: float | list[list[float]] = 0.4,
    **overrides: Any,
) -> BatteryProfile:
    """Build a ready-to-use BatteryProfile, see make_profile_config."""
    return make_profile_config(charge, discharge, **overrides).to_limits()


def make_curve(c_rate: list[float], efficiency: list[float]) -> EfficiencyCurve:
    """Build a validated efficiency curve with linear interpolation."""
    return EfficiencyCurve(c_rate=c_rate, efficiency=efficiency)


def charge_inhibited_below_30() -> HardCutoffs:
    """Cutoffs that inhibit charging at the default temperature of 25 degC."""
    return HardCutoffs.model_validate(
        {**TEST_CUTOFFS, "charge_allowed_below_degC": 30.0, "charge_allowed_above_degC": 55.0}
    )


def discharge_inhibited_below_30() -> HardCutoffs:
    """Cutoffs that inhibit discharging at the default temperature of 25 degC."""
    return HardCutoffs.model_validate(
        {
            **TEST_CUTOFFS,
            "discharge_allowed_below_degC": 30.0,
            "discharge_allowed_above_degC": 60.0,
        }
    )


def make_settings(**overrides: Any) -> BatteriesCommonSettings:
    """Build a BatteriesCommonSettings with device_id battery1.

    The keyword arguments ``capacity_wh``, ``charge_limit_w`` and
    ``discharge_limit_w`` go into the profile adjustment as
    ``nominal_capacity_wh``, ``max_charge_power_w`` and ``max_discharge_power_w``.
    """
    adjustment: dict[str, Any] = {}
    for short_name, name in (
        ("capacity_wh", "nominal_capacity_wh"),
        ("charge_limit_w", "max_charge_power_w"),
        ("discharge_limit_w", "max_discharge_power_w"),
    ):
        if short_name in overrides:
            adjustment[name] = overrides.pop(short_name)
    payload: dict[str, Any] = {"device_id": "battery1"}
    if adjustment:
        payload["profile_adjustment"] = adjustment
    payload.update(overrides)
    return BatteriesCommonSettings(**payload)


async def current(settings: BatteriesCommonSettings) -> BatteryOperatingParameters:
    """Resolve the parameters at the measured operating point."""
    return await settings.resolve_current_operating_parameters()


# --- fixtures ------------------------------------------------------------


@pytest.fixture(autouse=True)
def measurements(monkeypatch: pytest.MonkeyPatch) -> dict[str, Optional[float]]:
    """Replace the measurement access by a dict keyed by measurement key.

    Keys missing from the dict yield None, i.e. no measurement available.
    The dict is returned, so tests set values like
    ``measurements["battery1-soc-factor"] = 0.4`` at any time.
    """
    values: dict[str, Optional[float]] = {}

    async def fake_get_measured_value(key: str, target_datetime: Any) -> Optional[float]:
        return values.get(key)

    monkeypatch.setattr(batterymeasurement, "get_measured_value", fake_get_measured_value)
    return values


@pytest.fixture
def measurement_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every measurement query, answering None for all of them."""
    calls: list[str] = []

    async def fake_get_measured_value(key: str, target_datetime: Any) -> Optional[float]:
        calls.append(key)
        return None

    monkeypatch.setattr(batterymeasurement, "get_measured_value", fake_get_measured_value)
    return calls


@pytest.fixture
def install_profile(monkeypatch: pytest.MonkeyPatch):
    """Return a function that makes the profile store return a given profile.

    The returned function yields the list of profile names that were requested,
    so tests can assert how often the loader ran. Calling it again replaces the
    previously installed profile.
    """

    def _install(profile: Any) -> list[str]:
        calls: list[str] = []

        def fake_get(document_type: Any, name: str, **options: Any) -> Any:
            calls.append(name)
            return profile

        monkeypatch.setattr(get_profile_store(), "get", fake_get)
        return calls

    return _install


@pytest.fixture
def broken_profile_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every profile load fail with FileNotFoundError."""

    def missing(document_type, name, **options):
        raise FileNotFoundError(f"No battery profile for '{name}'")

    monkeypatch.setattr(get_profile_store(), "get", missing)


@pytest.fixture(autouse=True)
def _plain_default_profile(install_profile) -> None:
    """Install the plain profile that settings use unless a test installs another."""
    install_profile(make_profile())


@pytest.fixture
def make_profiled_settings(install_profile):
    """Return a factory for settings that use the given in-memory profile."""

    def _make(profile: Any, **overrides: Any) -> BatteriesCommonSettings:
        install_profile(profile)
        return make_settings(**overrides)

    return _make


@pytest.fixture
def full_profile() -> BatteryProfile:
    """Profile with efficiency curves and technical SoC bounds.

    At the default operating point: charge 0.5 C, discharge 0.4 C.
    """
    return make_profile(
        charge_efficiency=make_curve([0.1, 0.5, 1.0], [0.90, 0.96, 0.92]),
        discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
        technical_min_soc_percent=5.0,
        technical_max_soc_percent=95.0,
    )


# --- Defaults and validation ------------------------------------------------


class TestDefaultsAndValidation:
    """Tests for field defaults and validation."""

    def test_default_battery_profile(self):
        assert BATTERY_DEFAULT_PROFILE == "generic-lfp"
        assert make_settings().battery_profile == "generic-lfp"

    def test_battery_profile_is_not_optional(self):
        with pytest.raises(ValidationError):
            make_settings(battery_profile=None)

    def test_battery_profile_must_not_be_empty(self):
        with pytest.raises(ValidationError):
            make_settings(battery_profile="")

    def test_profile_adjustment_defaults_to_no_adjustment(self):
        settings = make_settings()
        assert settings.profile_adjustment.model_dump() == BatteryProfileAdjustment().model_dump()
        assert settings.profile_adjustment.nominal_capacity_wh is None
        assert settings.profile_adjustment.max_charge_power_w is None
        assert settings.profile_adjustment.max_discharge_power_w is None

    def test_old_name_battery_model_is_gone(self):
        assert "battery_model" not in BatteriesCommonSettings.model_fields
        assert "battery_profile" in BatteriesCommonSettings.model_fields

    @pytest.mark.parametrize("field", FORMER_CONFIGURATION_FIELDS)
    def test_former_configuration_fields_are_not_fields_any_more(self, field):
        assert field not in BatteriesCommonSettings.model_fields

    @pytest.mark.parametrize("former, adjustment_field", MOVED_TO_ADJUSTMENT)
    def test_fields_moved_into_the_profile_adjustment(self, former, adjustment_field):
        assert former not in BatteriesCommonSettings.model_fields
        assert adjustment_field in BatteryProfileAdjustment.model_fields

    def test_adjustment_fields_are_named_after_profile_fields(self):
        for name in BatteryProfileAdjustment.model_fields:
            assert name in BatteryProfileSettings.model_fields

    def test_operating_point_defaults(self):
        assert BATTERY_DEFAULT_SOC_FACTOR == pytest.approx(0.5)
        assert BATTERY_DEFAULT_TEMPERATURE_DEGC == pytest.approx(25.0)

    def test_unusable_profile_resolves_to_none(self, broken_profile_loader):
        settings = make_settings()
        for field in COMPUTED_FIELDS:
            assert getattr(settings, field) is None

    def test_unusable_profile_raises_naming_the_battery(self, broken_profile_loader):
        with pytest.raises(BatteryResolutionError, match="Battery 'battery1'"):
            make_settings().resolve_operating_parameters()

    @pytest.mark.parametrize("value", [0, -1])
    def test_rejects_non_positive_nominal_capacity(self, value):
        with pytest.raises(ValidationError):
            make_settings(capacity_wh=value)

    @pytest.mark.parametrize("field", ["charge_limit_w", "discharge_limit_w"])
    @pytest.mark.parametrize("value", [0, -1.0])
    def test_rejects_non_positive_power_limits(self, field, value):
        with pytest.raises(ValidationError):
            make_settings(**{field: value})

    def test_rejects_min_soc_above_max_soc(self):
        with pytest.raises(ValidationError, match="min_soc_percentage"):
            make_settings(min_soc_percentage=80, max_soc_percentage=20)

    def test_reserve_is_off_by_default(self):
        settings = make_settings()
        assert settings.reserve_soc_percentage is None
        assert settings.reserve_soc_time_windows is None

    @pytest.mark.parametrize("reserve", [5, 91, -1, 101])
    def test_rejects_reserve_outside_soc_range(self, reserve):
        with pytest.raises(ValidationError):
            make_settings(min_soc_percentage=10, max_soc_percentage=90, reserve_soc_percentage=reserve)

    @pytest.mark.parametrize("reserve", [10, 40, 90])
    def test_accepts_reserve_within_soc_range(self, reserve):
        settings = make_settings(
            min_soc_percentage=10, max_soc_percentage=90, reserve_soc_percentage=reserve
        )
        assert settings.reserve_soc_percentage == reserve

    def test_rejects_min_charge_power_above_installation_limit(self):
        with pytest.raises(ValidationError, match="min_charge_power_w"):
            make_settings(min_charge_power_w=6000.0, charge_limit_w=5000.0)

    def test_min_charge_power_without_installation_limit_is_accepted(self):
        # The profile decides the limit, which is not known at validation time.
        settings = make_settings(min_charge_power_w=6000.0)
        assert settings.profile_adjustment.max_charge_power_w is None


class TestJsonSchema:
    """Tests for the schema of configuration fields and computed fields."""

    @pytest.mark.parametrize("field", ["battery_profile", "profile_adjustment"])
    def test_configuration_fields_are_documented(self, field):
        schema = BatteriesCommonSettings.model_json_schema()
        assert schema["properties"][field]["description"]
        assert schema["properties"][field]["examples"]

    @pytest.mark.parametrize(
        "field", ["nominal_capacity_wh", "max_charge_power_w", "max_discharge_power_w"]
    )
    def test_adjustment_fields_are_documented(self, field):
        schema = BatteryProfileAdjustment.model_json_schema()
        assert schema["properties"][field]["description"]
        assert schema["properties"][field]["examples"]

    def test_profile_and_adjustment_are_not_required(self):
        schema = BatteriesCommonSettings.model_json_schema()
        assert schema["properties"]["battery_profile"]["default"] == "generic-lfp"
        assert "battery_profile" not in schema.get("required", [])
        assert "profile_adjustment" not in schema.get("required", [])

    @pytest.mark.parametrize("field", FORMER_CONFIGURATION_FIELDS)
    def test_former_configuration_fields_are_not_in_validation_schema(self, field):
        schema = BatteriesCommonSettings.model_json_schema(mode="validation")
        assert field not in schema["properties"]

    @pytest.mark.parametrize("field", COMPUTED_FIELDS)
    def test_computed_fields_are_part_of_serialization_schema(self, field):
        schema = BatteriesCommonSettings.model_json_schema(mode="serialization")
        assert field in schema["properties"]

    def test_capacity_wh_is_neither_field_nor_computed_field(self):
        """'capacity_wh' must not appear in settings, dumps or schemas.

        An old config file containing 'capacity_wh' is then unambiguously legacy.
        """
        assert "capacity_wh" not in BatteriesCommonSettings.model_fields
        assert "capacity_wh" not in BatteriesCommonSettings.model_computed_fields
        for mode in ("validation", "serialization"):
            schema = BatteriesCommonSettings.model_json_schema(mode=mode)
            assert "capacity_wh" not in schema["properties"]
        assert "capacity_wh" not in make_settings().model_dump()


# --- Computed fields ----------------------------------------------------------------


class TestComputedFieldEvaluation:
    """Tests that computed fields are evaluated on access and never raise."""

    def test_not_evaluated_during_construction(self, measurement_calls, install_profile):
        calls = install_profile(make_profile())
        make_settings(capacity_wh=10000)
        assert measurement_calls == []
        assert calls == []

    def test_not_evaluated_by_reading_configuration_fields(self, measurement_calls):
        settings = make_settings(capacity_wh=10000)
        _ = settings.profile_adjustment.nominal_capacity_wh
        _ = settings.battery_profile
        assert measurement_calls == []

    def test_never_read_measurements(self, measurement_calls):
        settings = make_settings()
        for field in COMPUTED_FIELDS:
            _ = getattr(settings, field)
        settings.model_dump()
        settings.model_dump_json()
        assert measurement_calls == []

    @pytest.mark.parametrize("field", REMOVED_COMPUTED_FIELDS)
    def test_measurement_based_computed_fields_are_gone(self, field):
        assert field not in BatteriesCommonSettings.model_computed_fields
        assert field not in make_settings().model_dump()

    def test_follows_changed_configuration(self):
        settings = make_settings(capacity_wh=10000)
        assert settings.effective_nominal_capacity_wh == 10000
        assert settings.effective_max_charge_power_w == pytest.approx(5000.0)
        settings.profile_adjustment.nominal_capacity_wh = 6000
        assert settings.effective_nominal_capacity_wh == 6000
        assert settings.effective_max_charge_power_w == pytest.approx(3000.0)
        settings.profile_adjustment.nominal_capacity_wh = None
        assert settings.effective_nominal_capacity_wh == PROFILE_CAPACITY_WH

    def test_power_ignores_measured_temperature(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        assert settings.effective_max_charge_power_w == pytest.approx(5000.0)

    def test_efficiency_ignores_measured_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        measurements["battery1-charge-power-w"] = 2500.0
        assert settings.effective_charging_efficiency == pytest.approx(0.96)

    def test_model_dump_contains_computed_fields(self):
        dumped = make_settings(capacity_wh=10000).model_dump()
        for field in COMPUTED_FIELDS:
            assert field in dumped
        assert dumped["effective_nominal_capacity_wh"] == 10000
        assert dumped["effective_max_charge_power_w"] == pytest.approx(5000.0)
        assert dumped["effective_max_discharge_power_w"] == pytest.approx(4000.0)

    def test_model_dump_json_contains_computed_fields(self):
        dumped = make_settings(capacity_wh=10000).model_dump_json()
        assert '"effective_nominal_capacity_wh":10000' in dumped.replace(" ", "")

    def test_round_trip_keeps_configuration_and_ignores_computed_values(self):
        original = make_settings(
            battery_profile="my-profile", capacity_wh=10000, charge_limit_w=3000.0
        )
        restored = BatteriesCommonSettings.model_validate_json(original.model_dump_json())
        assert restored.battery_profile == "my-profile"
        assert restored.profile_adjustment.nominal_capacity_wh == 10000
        assert restored.profile_adjustment.max_charge_power_w == pytest.approx(3000.0)
        assert restored.effective_nominal_capacity_wh == 10000
        assert restored.effective_max_charge_power_w == pytest.approx(3000.0)

    def test_serialization_survives_missing_profile(self, broken_profile_loader):
        dumped = make_settings(battery_profile="does-not-exist").model_dump()
        for field in COMPUTED_FIELDS:
            assert dumped[field] is None

    def test_serialization_survives_inhibited_charging(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        dumped = settings.model_dump()
        # The battery resolves; it may only not charge at the reference point.
        assert dumped["effective_max_charge_power_w"] == 0.0
        assert dumped["effective_nominal_capacity_wh"] == 10000

    def test_generic_profile_without_capacity_is_not_resolvable(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(nominal_capacity_wh=None))
        assert settings.effective_nominal_capacity_wh is None
        with pytest.raises(BatteryResolutionError, match="profile_adjustment.nominal_capacity_wh"):
            settings.resolve_operating_parameters()

    def test_generic_profile_takes_capacity_from_adjustment(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(nominal_capacity_wh=None), capacity_wh=6000)
        assert settings.effective_nominal_capacity_wh == 6000
        assert settings.effective_max_charge_power_w == pytest.approx(3000.0)

    def test_computed_fields_match_strict_resolution(self, make_profiled_settings, full_profile):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert settings.effective_nominal_capacity_wh == resolved.capacity_wh
        assert settings.effective_charging_efficiency == pytest.approx(resolved.charging_efficiency)
        assert settings.effective_discharging_efficiency == pytest.approx(resolved.discharging_efficiency)
        assert settings.effective_max_charge_power_w == pytest.approx(resolved.max_charge_power_w)
        assert settings.effective_max_discharge_power_w == pytest.approx(resolved.max_discharge_power_w)
        assert settings.effective_min_soc_percentage == resolved.min_soc_percentage
        assert settings.effective_max_soc_percentage == resolved.max_soc_percentage


# --- Operating point and measurement keys ------------------------------------------


class TestOperatingPoint:
    """Tests for SoC and temperature taken from measurements."""

    def test_measurement_keys(self):
        settings = make_settings()
        assert settings.measurement_key_soc_factor == "battery1-soc-factor"
        assert settings.measurement_key_temperature_degc == "battery1-temperature-degc"
        assert settings.measurement_key_charge_power_w == "battery1-charge-power-w"
        assert settings.measurement_key_discharge_power_w == "battery1-discharge-power-w"

    def test_new_keys_are_listed_in_measurement_keys(self):
        settings = make_settings()
        for key in (
            settings.measurement_key_temperature_degc,
            settings.measurement_key_charge_power_w,
            settings.measurement_key_discharge_power_w,
        ):
            assert key in settings.measurement_keys

    @pytest.mark.asyncio
    async def test_defaults_without_measurement(self):
        point = await make_settings().measure_operating_point()
        assert point == BatteryOperatingPoint()
        assert point.soc_factor == pytest.approx(0.5)
        assert point.temperature_degC == pytest.approx(25.0)
        assert point.charge_power_w is None
        assert point.discharge_power_w is None

    @pytest.mark.asyncio
    async def test_measured_values_are_used(self, measurements):
        measurements["battery1-soc-factor"] = 0.35
        measurements["battery1-temperature-degc"] = 12.5
        point = await make_settings().measure_operating_point()
        assert point.soc_factor == pytest.approx(0.35)
        assert point.temperature_degC == pytest.approx(12.5)

    @pytest.mark.asyncio
    async def test_only_one_measurement_available(self, measurements):
        measurements["battery1-temperature-degc"] = 10.0
        point = await make_settings().measure_operating_point()
        assert point.soc_factor == pytest.approx(0.5)
        assert point.temperature_degC == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_follows_changed_measurement(self, measurements):
        settings = make_settings()
        measurements["battery1-soc-factor"] = 0.8
        assert (await settings.measure_operating_point()).soc_factor == pytest.approx(0.8)
        measurements["battery1-soc-factor"] = 0.2
        assert (await settings.measure_operating_point()).soc_factor == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_reads_every_operating_point_key(self, measurement_calls):
        await make_settings().measure_operating_point()
        assert sorted(measurement_calls) == sorted(
            [
                "battery1-soc-factor",
                "battery1-temperature-degc",
                "battery1-charge-power-w",
                "battery1-discharge-power-w",
            ]
        )

    @pytest.mark.parametrize("measured, expected", [(-0.2, 0.0), (1.3, 1.0)])
    @pytest.mark.asyncio
    async def test_soc_factor_is_clamped(self, measurements, measured, expected):
        measurements["battery1-soc-factor"] = measured
        point = await make_settings().measure_operating_point()
        assert point.soc_factor == pytest.approx(expected)

    @pytest.mark.parametrize("measured", [float("nan"), float("inf")])
    @pytest.mark.asyncio
    async def test_non_finite_measurements_fall_back_to_defaults(self, measurements, measured):
        measurements["battery1-soc-factor"] = measured
        measurements["battery1-temperature-degc"] = measured
        point = await make_settings().measure_operating_point()
        assert point.soc_factor == pytest.approx(0.5)
        assert point.temperature_degC == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_powers_are_positive_magnitudes(self, measurements):
        measurements["battery1-charge-power-w"] = 2500.0
        measurements["battery1-discharge-power-w"] = -2000.0
        point = await make_settings().measure_operating_point()
        assert point.charge_power_w == pytest.approx(2500.0)
        assert point.discharge_power_w == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_keys_follow_device_id(self, measurements):
        measurements["house-battery-soc-factor"] = 0.9
        point = await make_settings(device_id="house-battery").measure_operating_point()
        assert point.soc_factor == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_failing_measurement_access_falls_back_to_defaults(self, monkeypatch):
        # Use the real accessor, but let the measurement subsystem fail.
        import akkudoktoreos.core.coreabc as coreabc

        def broken():
            raise RuntimeError("measurement store not initialised")

        monkeypatch.setattr(batterymeasurement, "get_measured_value", REAL_GET_MEASURED_VALUE)
        monkeypatch.setattr(coreabc, "get_measurement", broken)
        point = await make_settings().measure_operating_point()
        assert point == BatteryOperatingPoint()

    @pytest.mark.asyncio
    async def test_profile_is_evaluated_at_measured_temperature(
        self, make_profiled_settings, measurements
    ):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        assert (await current(settings)).max_charge_power_w == pytest.approx(5000.0)
        measurements["battery1-temperature-degc"] = 0.0
        assert (await current(settings)).max_charge_power_w == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_profile_is_evaluated_at_measured_soc(
        self, make_profiled_settings, measurements
    ):
        grid = make_grid(0.5)
        for row in grid:
            row[COLUMN_40_PERCENT] = 0.2
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        measurements["battery1-soc-factor"] = 0.4
        assert (await current(settings)).max_charge_power_w == pytest.approx(2000.0)

    def test_sync_resolution_ignores_measurements(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        resolved = settings.resolve_operating_parameters()
        assert resolved.temperature_degC == pytest.approx(25.0)
        assert resolved.max_charge_power_w == pytest.approx(5000.0)

    def test_default_operating_point_is_25_degc_and_half_soc(self, make_profiled_settings):
        # A grid that only holds 0.5 around 25 degC / 50 % (halfway between the
        # 40 % and 60 % columns); every other cell holds 0.1.
        grid = make_grid(0.1)
        grid[ROW_25_DEGC][COLUMN_40_PERCENT] = 0.5
        grid[ROW_25_DEGC][COLUMN_60_PERCENT] = 0.5
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert resolved.temperature_degC == pytest.approx(25.0)
        assert resolved.soc_factor == pytest.approx(0.5)
        assert resolved.max_charge_power_w == pytest.approx(5000.0)

    def test_explicit_operating_point_is_used(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 25.0
        cold = settings.resolve_operating_parameters(
            BatteryOperatingPoint(temperature_degC=0.0, soc_factor=0.5)
        )
        assert cold.max_charge_power_w == pytest.approx(1000.0)
        assert cold.temperature_degC == pytest.approx(0.0)


# --- Capacity ------------------------------------------------------------------


class TestCapacityResolution:
    """Tests for the capacity: profile adjustment > profile, no built-in default."""

    def test_explicit_capacity_is_used(self):
        assert make_settings(capacity_wh=10000).effective_nominal_capacity_wh == 10000

    def test_plain_test_profile_capacity(self):
        assert make_settings().effective_nominal_capacity_wh == PROFILE_CAPACITY_WH

    def test_profile_capacity_is_used_when_not_configured(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        assert make_profiled_settings(profile).effective_nominal_capacity_wh == 12000

    def test_adjustment_capacity_replaces_profile_capacity(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        assert make_profiled_settings(profile, capacity_wh=7000).effective_nominal_capacity_wh == 7000

    def test_profile_capacity_is_rounded_to_whole_wh(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=9999.6)
        assert make_profiled_settings(profile).effective_nominal_capacity_wh == 10000

    def test_profile_without_capacity_requires_the_adjustment(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(nominal_capacity_wh=None))
        with pytest.raises(BatteryResolutionError, match="Battery 'battery1'.*generic"):
            settings.resolve_nominal_capacity_wh()

    def test_resolve_nominal_capacity_wh_matches_computed_field(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        settings = make_profiled_settings(profile)
        assert settings.resolve_nominal_capacity_wh() == settings.effective_nominal_capacity_wh == 12000

    def test_power_scales_with_profile_capacity(self, make_profiled_settings):
        profile = make_profile(charge=0.5, nominal_capacity_wh=8000.0)
        resolved = make_profiled_settings(profile).resolve_operating_parameters()
        assert resolved.capacity_wh == 8000
        assert resolved.max_charge_power_w == pytest.approx(4000.0)

    def test_power_scales_with_adjustment_capacity(self, make_profiled_settings):
        profile = make_profile(charge=0.5, nominal_capacity_wh=8000.0)
        resolved = make_profiled_settings(
            profile, capacity_wh=10000
        ).resolve_operating_parameters()
        assert resolved.capacity_wh == 10000
        assert resolved.max_charge_power_w == pytest.approx(5000.0)


# --- Loading the battery profile --------------------------------------------------


class TestGetBatteryProfile:
    """Tests for lazy loading and caching of the battery profile."""

    def test_loads_default_profile_by_name(self, install_profile):
        profile = make_profile()
        calls = install_profile(profile)
        settings = make_settings()
        assert settings.get_battery_profile() is profile
        assert calls == ["generic-lfp"]

    def test_loads_configured_profile_by_name(self, install_profile):
        calls = install_profile(make_profile())
        make_settings(battery_profile="byd-hvs").get_battery_profile()
        assert calls == ["byd-hvs"]

    def test_loading_is_lazy(self, install_profile):
        calls = install_profile(make_profile())
        make_settings(battery_profile="generic-lfp")
        assert calls == []

    def test_profile_is_always_taken_from_the_store(self, install_profile):
        # Caching is the job of the profile store (see test_devicesprofilebattery),
        # so a changed profile file is picked up by existing settings instances.
        calls = install_profile(make_profile())
        settings = make_settings(capacity_wh=10000)
        settings.get_battery_profile()
        settings.resolve_operating_parameters()
        _ = settings.effective_nominal_capacity_wh
        _ = settings.effective_max_charge_power_w
        settings.to_genetic_pv_bat_param()
        assert calls == ["generic-lfp"] * 5

    def test_dump_resolves_the_battery_once(self, install_profile, monkeypatch):
        """All effective_* fields of one dump share one resolution."""
        from akkudoktoreos.devices.settings import batterysettings

        install_profile(make_profile())
        settings = make_settings(capacity_wh=10000)
        resolutions: list[int] = []
        original = batterysettings.resolve_battery_parameters

        def counting(*args, **kwargs):
            resolutions.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(batterysettings, "resolve_battery_parameters", counting)
        dumped = settings.model_dump()
        assert dumped["effective_max_charge_power_w"] is not None
        assert len(resolutions) == 1

    def test_changed_settings_are_resolved_again(self, install_profile):
        install_profile(make_profile())
        settings = make_settings(capacity_wh=10000)
        before = settings.effective_nominal_capacity_wh
        settings.profile_adjustment.nominal_capacity_wh = 5000
        assert before == 10000
        assert settings.effective_nominal_capacity_wh == 5000

    @pytest.mark.parametrize(
        ("field", "value", "effective", "expected"),
        [
            ("min_soc_percentage", 20, "effective_min_soc_percentage", 20),
            ("max_soc_percentage", 80, "effective_max_soc_percentage", 80),
        ],
    )
    def test_changed_soc_settings_are_resolved_again(
        self, install_profile, field, value, effective, expected
    ):
        install_profile(make_profile())
        settings = make_settings(capacity_wh=10000)
        _ = getattr(settings, effective)
        setattr(settings, field, value)
        assert getattr(settings, effective) == expected

    def test_changed_store_profile_is_used_by_existing_instance(self, install_profile):
        install_profile(make_profile())
        settings = make_settings(capacity_wh=10000)
        before = settings.effective_max_charge_power_w
        assert before is not None
        install_profile(make_profile(charge=0.25))
        assert settings.effective_max_charge_power_w == pytest.approx(before / 2)

    def test_profile_is_reloaded_when_profile_name_changes(self, install_profile):
        calls = install_profile(make_profile())
        settings = make_settings(battery_profile="profile-a")
        settings.get_battery_profile()
        settings.battery_profile = "profile-b"
        settings.get_battery_profile()
        assert calls == ["profile-a", "profile-b"]

    def test_missing_profile_error_is_propagated_by_strict_methods(self, broken_profile_loader):
        settings = make_settings(battery_profile="does-not-exist")
        with pytest.raises(FileNotFoundError, match="does-not-exist"):
            settings.get_battery_profile()
        # The resolution names the battery and keeps the cause.
        with pytest.raises(BatteryResolutionError, match="Battery 'battery1'.*does-not-exist"):
            settings.resolve_operating_parameters()
        with pytest.raises(BatteryResolutionError, match="does-not-exist") as raised:
            settings.resolve_nominal_capacity_wh()
        assert isinstance(raised.value.__cause__, FileNotFoundError)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_missing_profile_error_is_propagated_by_conversions(
        self, broken_profile_loader, method
    ):
        settings = make_settings(battery_profile="does-not-exist")
        with pytest.raises(BatteryResolutionError, match="does-not-exist"):
            getattr(settings, method)()


# --- Maximum charge power -------------------------------------------------------


class TestMaxChargePower:
    """Tests for the maximum charge power."""

    def test_c_rate_limit_is_scaled_by_capacity(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(charge=0.5), capacity_wh=10000)
        assert settings.effective_max_charge_power_w == pytest.approx(5000.0)

    def test_profile_power_limit_caps_c_rate_power(self, make_profiled_settings):
        profile = make_profile(charge=0.5, max_charge_power_w=3000.0)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_max_charge_power_w == pytest.approx(3000.0)

    def test_installation_limit_below_profile_limit_wins(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5), capacity_wh=10000, charge_limit_w=2500.0
        )
        assert settings.effective_max_charge_power_w == pytest.approx(2500.0)

    def test_installation_limit_cannot_exceed_profile_limit(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5), capacity_wh=10000, charge_limit_w=9000.0
        )
        assert settings.effective_max_charge_power_w == pytest.approx(5000.0)

    def test_power_unit_is_used_as_is(self, make_profiled_settings):
        profile = make_profile(charge=3000.0, units=Units(limit="W"))
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_max_charge_power_w == pytest.approx(3000.0)

    def test_current_unit_is_converted_with_nominal_voltage(self, make_profiled_settings):
        profile = make_profile(charge=20.0, units=Units(limit="A"), nominal_voltage_v=100.0)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_max_charge_power_w == pytest.approx(2000.0)

    def test_current_unit_without_nominal_voltage_is_rejected(self):
        """The profile document is rejected, not only its resolution."""
        with pytest.raises(ValueError, match="nominal_voltage_v"):
            make_profile_config(charge=20.0, units=Units(limit="A"))

    @pytest.mark.parametrize(
        "units", [{"limit": "kW"}, {"limit": "C-rate", "temperature": "degF"},
                  {"limit": "C-rate", "soc": "fraction"}],
    )
    def test_unsupported_unit_is_rejected(self, units):
        with pytest.raises(ValueError):
            Units(**units)

    def test_unsupported_unit_of_a_compiled_profile_raises(self, make_profiled_settings):
        """Defensive: a compiled profile that bypassed validation fails with a resolution error."""
        profile = dataclasses.replace(make_profile(charge=1.0), limit_unit="kW")
        settings = make_profiled_settings(profile, capacity_wh=10000)
        with pytest.raises(BatteryResolutionError, match="Unsupported limit unit"):
            settings.resolve_operating_parameters()

    def test_inhibited_charging_yields_zero_without_raising(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.resolve_operating_parameters().max_charge_power_w == 0.0

    @pytest.mark.asyncio
    async def test_charging_is_inhibited_at_measured_cold_temperature(
        self, make_profiled_settings, measurements
    ):
        """A cold battery may not charge, but the parameters stay valid for the optimization."""
        settings = make_profiled_settings(make_profile(), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = -12.0
        parameters = await current(settings)
        assert parameters.max_charge_power_w == 0.0
        assert settings.to_genetic_pv_bat_param(parameters).max_charge_power_w == 0.0
        assert settings.to_genetic0_pv_bat_param(parameters).max_charge_power_w == 0.0

    def test_zero_charge_power_yields_zero(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(charge=0.0), capacity_wh=10000)
        assert settings.resolve_operating_parameters().max_charge_power_w == 0.0


# --- Maximum discharge power ----------------------------------------------------


class TestMaxDischargePower:
    """Tests for the maximum discharge power."""

    def test_c_rate_limit_is_scaled_by_capacity(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(discharge=0.4), capacity_wh=10000)
        assert settings.effective_max_discharge_power_w == pytest.approx(4000.0)

    def test_profile_power_limit_caps_c_rate_power(self, make_profiled_settings):
        profile = make_profile(discharge=0.4, max_discharge_power_w=2000.0)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_max_discharge_power_w == pytest.approx(2000.0)

    def test_installation_limit_below_profile_limit_wins(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(discharge=0.4),
            capacity_wh=10000,
            discharge_limit_w=1500.0,
        )
        assert settings.effective_max_discharge_power_w == pytest.approx(1500.0)

    def test_installation_limit_cannot_exceed_profile_limit(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(discharge=0.4),
            capacity_wh=10000,
            discharge_limit_w=9000.0,
        )
        assert settings.effective_max_discharge_power_w == pytest.approx(4000.0)

    def test_charge_and_discharge_limits_are_independent(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5, discharge=0.2),
            capacity_wh=10000,
            charge_limit_w=1000.0,
        )
        assert settings.effective_max_charge_power_w == pytest.approx(1000.0)
        assert settings.effective_max_discharge_power_w == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_discharge_follows_measured_temperature(
        self, make_profiled_settings, measurements
    ):
        grid = make_grid(0.4)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(discharge=grid), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        assert (await current(settings)).max_discharge_power_w == pytest.approx(1000.0)
        # The computed field stays at the reference operating point.
        assert settings.effective_max_discharge_power_w == pytest.approx(4000.0)

    def test_inhibited_discharging_yields_zero_without_raising(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=discharge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.resolve_operating_parameters().max_discharge_power_w == 0.0
        assert settings.effective_max_discharge_power_w == 0.0

    def test_inhibited_discharging_does_not_block_conversions(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=discharge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.to_genetic_pv_bat_param().max_charge_power_w == pytest.approx(5000.0)


# --- Efficiencies ----------------------------------------------------------------


class TestEfficiencyAtMaximumPower:
    """Tests for efficiencies without a usable power measurement."""

    def test_charge_efficiency_at_max_charge_power(self, make_profiled_settings, full_profile):
        # 5000 W on 10000 Wh -> 0.5 C -> 0.96
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert settings.effective_charging_efficiency == pytest.approx(0.96)

    def test_discharge_efficiency_at_max_discharge_power(self, make_profiled_settings, full_profile):
        # 4000 W on 10000 Wh -> 0.4 C -> 0.90 + 0.05 * 0.3 / 0.4 = 0.9375
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert settings.effective_discharging_efficiency == pytest.approx(0.9375)

    def test_installation_limit_changes_c_rate(self, make_profiled_settings):
        # Charge capped to 2500 W -> 0.25 C -> 0.90 + 0.06 * 0.15 / 0.4 = 0.9225
        profile = make_profile(charge_efficiency=make_curve([0.1, 0.5, 1.0], [0.90, 0.96, 0.92]))
        settings = make_profiled_settings(
            profile, capacity_wh=10000, charge_limit_w=2500.0
        )
        assert settings.effective_charging_efficiency == pytest.approx(0.9225)

    def test_profile_power_limit_changes_c_rate(self, make_profiled_settings):
        # Discharge capped to 2000 W -> 0.2 C -> 0.90 + 0.05 * 0.1 / 0.4 = 0.9125
        profile = make_profile(
            discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
            max_discharge_power_w=2000.0,
        )
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_discharging_efficiency == pytest.approx(0.9125)

    def test_efficiency_depends_on_c_rate_not_on_capacity(
        self, make_profiled_settings, full_profile
    ):
        # Same 0.5 C limit on a smaller capacity: lower power, same C-rate.
        settings = make_profiled_settings(full_profile, capacity_wh=5000)
        assert settings.effective_max_charge_power_w == pytest.approx(2500.0)
        assert settings.effective_charging_efficiency == pytest.approx(0.96)

    def test_flat_curves_give_their_efficiency(self):
        settings = make_settings(capacity_wh=10000)
        assert settings.effective_charging_efficiency == pytest.approx(PROFILE_EFFICIENCY)
        assert settings.effective_discharging_efficiency == pytest.approx(PROFILE_EFFICIENCY)

    def test_inhibited_discharging_uses_efficiency_at_lowest_c_rate(self, make_profiled_settings):
        profile = make_profile(
            discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
            hard_cutoffs=discharge_inhibited_below_30(),
        )
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_max_discharge_power_w == pytest.approx(0.0)
        assert settings.effective_discharging_efficiency == pytest.approx(0.90)

    def test_rejected_c_rate_uses_nearest_curve_breakpoint(self, make_profiled_settings):
        curve = EfficiencyCurve(
            c_rate=[0.1, 1.0],
            efficiency=[0.90, 0.92],
            out_of_range_behavior=OutOfRangeBehavior.REJECT,
        )
        # 1.5 C is beyond the curve, which rejects it.
        profile = make_profile(charge=1.5, charge_efficiency=curve)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert settings.effective_charging_efficiency == pytest.approx(0.92)


class TestEfficiencyAtMeasuredPower:
    """Tests for efficiencies taken at the measured charge / discharge power."""

    @pytest.mark.asyncio
    async def test_charge_efficiency_at_measured_charge_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2500 W -> 0.25 C -> 0.9225 (maximum power would give 0.96)
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.9225)

    @pytest.mark.asyncio
    async def test_discharge_efficiency_at_measured_discharge_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2000 W -> 0.2 C -> 0.9125 (maximum power would give 0.9375)
        measurements["battery1-discharge-power-w"] = 2000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_directions_are_independent(self, make_profiled_settings, full_profile, measurements):
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.9225)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9375)

    @pytest.mark.asyncio
    async def test_measured_power_scales_with_capacity(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2500 W on 5000 Wh is 0.5 C -> 0.96
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, capacity_wh=5000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)

    @pytest.mark.asyncio
    async def test_negative_measurement_is_used_as_magnitude(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-discharge-power-w"] = -2000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_zero_measurement_falls_back_to_maximum_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-charge-power-w"] = 0.0
        measurements["battery1-discharge-power-w"] = 0.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9375)

    @pytest.mark.parametrize("measured", [float("nan"), float("inf")])
    @pytest.mark.asyncio
    async def test_non_finite_measurement_falls_back_to_maximum_power(
        self, make_profiled_settings, full_profile, measurements, measured
    ):
        measurements["battery1-charge-power-w"] = measured
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)

    @pytest.mark.asyncio
    async def test_measurement_beyond_curve_is_clamped_by_profile(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 20000 W -> 2 C is beyond the curve, clamped to the last point 0.92.
        measurements["battery1-charge-power-w"] = 20000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_measured_power_is_not_limited_by_maximum_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # Installation limit 1000 W, measured 5000 W (0.5 C): measurement wins.
        measurements["battery1-charge-power-w"] = 5000.0
        settings = make_profiled_settings(
            full_profile, capacity_wh=10000, charge_limit_w=1000.0
        )
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)

    @pytest.mark.asyncio
    async def test_measured_discharge_power_used_while_discharging_is_inhibited(
        self, make_profiled_settings, measurements
    ):
        profile = make_profile(
            discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
            hard_cutoffs=discharge_inhibited_below_30(),
        )
        measurements["battery1-discharge-power-w"] = 2000.0
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_measured_power_on_flat_curve_gives_its_efficiency(
        self, make_profiled_settings, measurements
    ):
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(make_profile(), capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(PROFILE_EFFICIENCY)

    def test_explicit_powers_are_used(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-charge-power-w"] = 5000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        resolved = settings.resolve_operating_parameters(
            BatteryOperatingPoint(charge_power_w=2500.0, discharge_power_w=2000.0)
        )
        assert resolved.charging_efficiency == pytest.approx(0.9225)
        assert resolved.discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_measured_power_does_not_change_maximum_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-charge-power-w"] = 2500.0
        measurements["battery1-discharge-power-w"] = 2000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        assert (await current(settings)).max_charge_power_w == pytest.approx(5000.0)
        assert (await current(settings)).max_discharge_power_w == pytest.approx(4000.0)


# --- SoC range -----------------------------------------------------------------


class TestSocRangeResolution:
    """Tests that the user's SoC range is clamped into the technical bounds."""

    def test_technical_bounds_apply_to_default_range(self, make_profiled_settings):
        profile = make_profile(technical_min_soc_percent=5.0, technical_max_soc_percent=95.0)
        resolved = make_profiled_settings(profile).resolve_operating_parameters()
        assert resolved.min_soc_percentage == 5
        assert resolved.max_soc_percentage == 95

    def test_technical_bounds_are_rounded_inwards(self, make_profiled_settings):
        profile = make_profile(technical_min_soc_percent=4.2, technical_max_soc_percent=95.8)
        resolved = make_profiled_settings(profile).resolve_operating_parameters()
        assert resolved.min_soc_percentage == 5
        assert resolved.max_soc_percentage == 95

    def test_stricter_user_range_is_kept(self, make_profiled_settings):
        profile = make_profile(technical_min_soc_percent=5.0, technical_max_soc_percent=95.0)
        settings = make_profiled_settings(profile, min_soc_percentage=10, max_soc_percentage=90)
        resolved = settings.resolve_operating_parameters()
        assert resolved.min_soc_percentage == 10
        assert resolved.max_soc_percentage == 90

    def test_profile_without_bounds_keeps_user_range(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(), min_soc_percentage=10, max_soc_percentage=90
        )
        resolved = settings.resolve_operating_parameters()
        assert resolved.min_soc_percentage == 10
        assert resolved.max_soc_percentage == 90

    def test_empty_range_after_clamping_raises(self, make_profiled_settings):
        profile = make_profile(technical_min_soc_percent=60.0)
        settings = make_profiled_settings(profile, max_soc_percentage=50)
        with pytest.raises(ValueError, match="empty"):
            settings.resolve_operating_parameters()

    def test_effective_soc_fields_use_technical_bounds(self, make_profiled_settings):
        profile = make_profile(technical_min_soc_percent=5.0, technical_max_soc_percent=95.0)
        settings = make_profiled_settings(profile)
        assert settings.effective_min_soc_percentage == 5
        assert settings.effective_max_soc_percentage == 95


# --- Strict resolution -----------------------------------------------------------


class TestResolveOperatingParameters:
    """Tests for the consistent strict snapshot."""

    def test_snapshot_contains_all_values(self, make_profiled_settings, full_profile):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert isinstance(resolved, BatteryOperatingParameters)
        values = dataclasses.asdict(resolved)
        # The snapshot brings the profile it was resolved from (see the conversion tests).
        assert values.pop("profile") is not None
        assert values == pytest.approx(
            {
                "temperature_degC": 25.0,
                "soc_factor": 0.5,
                "capacity_wh": 10000,
                "charging_efficiency": 0.96,
                "discharging_efficiency": 0.9375,
                "max_charge_power_w": 5000.0,
                "max_discharge_power_w": 4000.0,
                "min_soc_percentage": 5,
                "max_soc_percentage": 95,
            }
        )

    def test_snapshot_is_immutable(self):
        resolved = make_settings().resolve_operating_parameters()
        with pytest.raises((AttributeError, TypeError)):
            resolved.capacity_wh = 1  # type: ignore[misc]

    def test_snapshot_is_not_affected_by_later_measurement_changes(
        self, make_profiled_settings, full_profile, measurements
    ):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        measurements["battery1-charge-power-w"] = 2500.0
        assert resolved.charging_efficiency == pytest.approx(0.96)


# --- Optimization parameters ---------------------------------------------------


class TestOptimizationParameters:
    """Tests that the conversions use the battery profile at the operating point."""

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_values_come_from_profile(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        params = getattr(settings, method)()
        assert params.device_id == "battery1"
        assert params.capacity_wh == 10000
        assert params.max_charge_power_w == pytest.approx(5000.0)
        assert params.charging_efficiency == pytest.approx(0.96)
        assert params.discharging_efficiency == pytest.approx(0.9375)
        assert params.min_soc_percentage == 5
        assert params.max_soc_percentage == 95

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_parameters_match_computed_fields(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        params = getattr(settings, method)()
        assert params.capacity_wh == settings.effective_nominal_capacity_wh
        assert params.charging_efficiency == pytest.approx(settings.effective_charging_efficiency)
        assert params.discharging_efficiency == pytest.approx(settings.effective_discharging_efficiency)
        assert params.max_charge_power_w == pytest.approx(settings.effective_max_charge_power_w)
        assert params.min_soc_percentage == settings.effective_min_soc_percentage
        assert params.max_soc_percentage == settings.effective_max_soc_percentage

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    @pytest.mark.asyncio
    async def test_parameters_use_measured_operating_point(
        self, make_profiled_settings, measurements, method
    ):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        params = getattr(settings, method)(await current(settings))
        assert params.max_charge_power_w == pytest.approx(1000.0)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    @pytest.mark.asyncio
    async def test_parameters_use_measured_power_for_efficiency(
        self, make_profiled_settings, full_profile, measurements, method
    ):
        measurements["battery1-charge-power-w"] = 2500.0
        measurements["battery1-discharge-power-w"] = 2000.0
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        params = getattr(settings, method)(await current(settings))
        assert params.charging_efficiency == pytest.approx(0.9225)
        assert params.discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_without_snapshot_reference_point_is_used(
        self, make_profiled_settings, measurement_calls, method
    ):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        assert getattr(settings, method)().max_charge_power_w == pytest.approx(5000.0)
        assert measurement_calls == []

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_snapshot_is_converted_as_given(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        snapshot = settings.resolve_operating_parameters(
            BatteryOperatingPoint(charge_power_w=2500.0)
        )
        params = getattr(settings, method)(snapshot)
        assert params.capacity_wh == snapshot.capacity_wh
        assert params.charging_efficiency == pytest.approx(snapshot.charging_efficiency)
        assert params.max_charge_power_w == pytest.approx(snapshot.max_charge_power_w)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_capacity_comes_from_profile_when_not_configured(
        self, make_profiled_settings, method
    ):
        profile = make_profile(charge=0.5, nominal_capacity_wh=8000.0)
        params = getattr(make_profiled_settings(profile), method)()
        assert params.capacity_wh == 8000
        assert params.max_charge_power_w == pytest.approx(4000.0)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_installation_limit_is_applied(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(
            full_profile, capacity_wh=10000, charge_limit_w=2000.0
        )
        assert getattr(settings, method)().max_charge_power_w == pytest.approx(2000.0)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_user_soc_range_is_clamped_into_technical_bounds(
        self, make_profiled_settings, full_profile, method
    ):
        settings = make_profiled_settings(
            full_profile, capacity_wh=10000, min_soc_percentage=10, max_soc_percentage=90
        )
        params = getattr(settings, method)()
        assert params.min_soc_percentage == 10
        assert params.max_soc_percentage == 90

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_inhibited_charging_gives_valid_parameters(self, make_profiled_settings, method):
        """The optimization gets valid parameters that do not allow charging."""
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        assert getattr(settings, method)().max_charge_power_w == 0.0

    def test_genetic_pv_receives_storage_cost(self):
        params = make_settings(levelized_cost_of_storage_amt_kwh=0.12).to_genetic_pv_bat_param()
        assert params.levelized_cost_of_storage_kwh == pytest.approx(0.12)

    def test_genetic_pv_receives_reserve(self):
        windows = {"windows": [{"start_time": "17:00", "duration": "4 hours"}]}
        params = make_settings(
            min_soc_percentage=10, reserve_soc_percentage=40, reserve_soc_time_windows=windows
        ).to_genetic_pv_bat_param()
        assert params.reserve_soc_percentage == 40
        assert params.reserve_soc_time_windows is not None
        assert params.reserve_soc_time_windows.windows[0].start_time.hour == 17

    def test_genetic_pv_without_reserve(self):
        params = make_settings().to_genetic_pv_bat_param()
        assert params.reserve_soc_percentage is None
        assert params.reserve_soc_time_windows is None

    @pytest.mark.parametrize("reserve, expected", [(2, 5), (50, 50), (98, 95)])
    def test_genetic_pv_reserve_is_kept_within_technical_bounds(
        self, make_profiled_settings, full_profile, reserve, expected
    ):
        settings = make_profiled_settings(
            full_profile, capacity_wh=10000, reserve_soc_percentage=reserve
        )
        assert settings.to_genetic_pv_bat_param().reserve_soc_percentage == expected

    def test_genetic_ev_receives_soc_deadline_settings(self):
        params = make_settings(min_soc_max_duration_h=6.0).to_genetic_ev_bat_param()
        assert params.min_soc_max_duration_h == pytest.approx(6.0)
        assert params.min_soc_deadline_datetime is None


# --- Real measurement store ------------------------------------------------------


class TestMeasurementAccess:
    """Tests that the operating point is read from the real measurement store.

    These tests use the real (async) accessor, so a missing ``await`` on
    ``DataSequence.key_to_value`` surfaces here and not only in production.
    """

    @pytest.fixture(autouse=True)
    def _real_measurement_access(self, monkeypatch: pytest.MonkeyPatch, config_eos):
        from akkudoktoreos.core.coreabc import get_measurement

        monkeypatch.setattr(batterymeasurement, "get_measured_value", REAL_GET_MEASURED_VALUE)
        config_eos.merge_settings_from_dict(
            {"devices": {"batteries": {"battery1": {"device_id": "battery1"}}}}
        )
        get_measurement()._db_reset_state()
        yield
        get_measurement()._db_reset_state()

    @pytest.mark.asyncio
    async def test_measured_values_are_read(self):
        from akkudoktoreos.core.coreabc import get_measurement
        from akkudoktoreos.utils.datetimeutil import to_datetime

        now = to_datetime()
        await get_measurement().update_value(
            now,
            {
                "battery1-soc-factor": 0.35,
                "battery1-temperature-degc": 12.5,
                "battery1-charge-power-w": 2500.0,
            },
        )
        point = await make_settings().measure_operating_point(now)
        assert point.soc_factor == pytest.approx(0.35)
        assert point.temperature_degC == pytest.approx(12.5)
        assert point.charge_power_w == pytest.approx(2500.0)
        assert point.discharge_power_w is None

    @pytest.mark.asyncio
    async def test_empty_store_yields_reference_point(self):
        point = await make_settings().measure_operating_point()
        assert point == BatteryOperatingPoint()

    @pytest.mark.asyncio
    async def test_measurement_outside_age_window_is_ignored(self):
        from akkudoktoreos.core.coreabc import get_measurement
        from akkudoktoreos.utils.datetimeutil import to_datetime

        now = to_datetime()
        await get_measurement().update_value(
            now.subtract(hours=3), {"battery1-soc-factor": 0.9}
        )
        point = await make_settings().measure_operating_point(now)
        assert point.soc_factor == pytest.approx(BATTERY_DEFAULT_SOC_FACTOR)

    @pytest.mark.asyncio
    async def test_current_resolution_uses_real_measurements(self, make_profiled_settings):
        from akkudoktoreos.core.coreabc import get_measurement
        from akkudoktoreos.utils.datetimeutil import to_datetime

        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), capacity_wh=10000)
        now = to_datetime()
        await get_measurement().update_value(now, {"battery1-temperature-degc": 0.0})
        resolved = await settings.resolve_current_operating_parameters(now)
        assert resolved.max_charge_power_w == pytest.approx(1000.0)


class TestGeneticPowerCurves:
    """The GENETIC parameters carry the profile as SoC tables and efficiency curves."""

    # SoC axis [0, 5, 10, 20, 40, 60, 80, 90, 95, 100]: full 1.0 C up to 80 %,
    # then tapering to 0.5 C at 100 %.
    TAPER = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.6, 0.5]
    # Temperature axis [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50]: 0.2 C at 10 degC,
    # 0.5 C at 25 degC.
    BY_TEMPERATURE = [0.1, 0.1, 0.1, 0.15, 0.2, 0.3, 0.5, 0.5, 0.5, 0.5]

    @staticmethod
    def snapshot(temperature_degC: float = 25.0) -> BatteryOperatingParameters:
        """Return a resolved snapshot that does not depend on a profile."""
        return BatteryOperatingParameters(
            temperature_degC=temperature_degC,
            soc_factor=0.5,
            capacity_wh=10000,
            charging_efficiency=0.9,
            discharging_efficiency=0.9,
            max_charge_power_w=3000.0,
            max_discharge_power_w=2000.0,
            min_soc_percentage=0,
            max_soc_percentage=100,
        )

    @pytest.mark.parametrize("converter", ["to_genetic_pv_bat_param", "to_genetic_ev_bat_param"])
    def test_tables_follow_soc(self, make_profiled_settings, converter):
        profile = make_profile(charge=[list(self.TAPER) for _ in range(10)], discharge=0.4)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        parameters = getattr(settings, converter)()

        charge = parameters.charge_power_limit_w_by_soc
        discharge = parameters.discharge_power_limit_w_by_soc
        assert charge is not None and len(charge) == 101
        assert discharge is not None and len(discharge) == 101
        assert charge[50] == pytest.approx(10000.0)
        assert charge[80] == pytest.approx(10000.0)
        assert charge[90] == pytest.approx(8000.0)
        assert charge[100] == pytest.approx(5000.0)
        assert all(a >= b for a, b in zip(charge[80:], charge[81:]))
        assert discharge == pytest.approx([4000.0] * 101)
        assert parameters.max_charge_power_w == pytest.approx(10000.0)
        assert parameters.max_discharge_power_w == pytest.approx(4000.0)

    @pytest.mark.asyncio
    async def test_rated_power_is_the_peak_over_soc(self, make_profiled_settings, measurements):
        """At a high measured SoC the snapshot is tapered, the rated power is not."""
        profile = make_profile(charge=[list(self.TAPER) for _ in range(10)], discharge=0.4)
        settings = make_profiled_settings(profile, capacity_wh=10000)
        measurements["battery1-soc-factor"] = 1.0
        snapshot = await current(settings)
        assert snapshot.max_charge_power_w == pytest.approx(5000.0)

        parameters = settings.to_genetic_pv_bat_param(snapshot)
        assert parameters.max_charge_power_w == pytest.approx(10000.0)
        assert parameters.charge_power_limit_w_by_soc[100] == pytest.approx(5000.0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "temperature, expected_w",
        [(10.0, 2000.0), (None, 5000.0)],  # None: no measurement, 25 degC assumed
    )
    async def test_tables_at_measured_temperature(
        self, make_profiled_settings, measurements, temperature, expected_w
    ):
        grid = [[value] * 10 for value in self.BY_TEMPERATURE]
        settings = make_profiled_settings(
            make_profile(charge=grid, discharge=grid), capacity_wh=10000
        )
        if temperature is not None:
            measurements["battery1-temperature-degc"] = temperature

        parameters = settings.to_genetic_pv_bat_param(await current(settings))
        assert parameters.charge_power_limit_w_by_soc == pytest.approx([expected_w] * 101)
        assert parameters.discharge_power_limit_w_by_soc == pytest.approx([expected_w] * 101)

    def test_installation_limits_cap_tables(self, make_profiled_settings):
        profile = make_profile(charge=[list(self.TAPER) for _ in range(10)], discharge=0.4)
        settings = make_profiled_settings(
            profile,
            capacity_wh=10000,
            charge_limit_w=6000,
            discharge_limit_w=1000,
        )
        parameters = settings.to_genetic_pv_bat_param()
        assert max(parameters.charge_power_limit_w_by_soc) == pytest.approx(6000.0)
        assert parameters.charge_power_limit_w_by_soc[100] == pytest.approx(5000.0)
        assert parameters.discharge_power_limit_w_by_soc == pytest.approx([1000.0] * 101)
        assert parameters.max_charge_power_w == pytest.approx(6000.0)
        assert parameters.max_discharge_power_w == pytest.approx(1000.0)

    def test_efficiency_curves_over_power_fraction(self, make_profiled_settings, full_profile):
        settings = make_profiled_settings(full_profile, capacity_wh=10000)
        parameters = settings.to_genetic_pv_bat_param()

        charging = parameters.charging_efficiency_curve
        discharging = parameters.discharging_efficiency_curve
        assert charging is not None and discharging is not None
        assert [point[0] for point in charging] == pytest.approx([i / 20 for i in range(21)])
        # Rated charge power 0.5 C: fraction 1.0 is 0.5 C, fraction 0.2 is 0.1 C.
        assert charging[-1][1] == pytest.approx(0.96)
        assert charging[4][1] == pytest.approx(0.90)
        # Rated discharge power 0.4 C: between 0.90 at 0.1 C and 0.95 at 0.5 C.
        assert discharging[-1][1] == pytest.approx(0.9375)

    def test_efficiency_curves_come_from_the_profile(self):
        parameters = make_settings(capacity_wh=10000).to_genetic_pv_bat_param()
        assert parameters.charge_power_limit_w_by_soc is not None
        assert parameters.charging_efficiency_curve is not None
        assert parameters.discharging_efficiency_curve is not None
        assert {e for _, e in parameters.charging_efficiency_curve} == {PROFILE_EFFICIENCY}

    def test_no_tables_if_charging_is_inhibited_at_every_soc(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        snapshot = self.snapshot(temperature_degC=20.0)
        assert settings.resolve_power_curves(snapshot) is None

        parameters = settings.to_genetic_pv_bat_param(snapshot)
        assert parameters.charge_power_limit_w_by_soc is None
        assert parameters.max_charge_power_w == pytest.approx(snapshot.max_charge_power_w)

    def test_snapshot_without_profile_converts_without_tables(self, broken_profile_loader):
        """A snapshot resolved without profile is converted as is: no load, no abort."""
        snapshot = self.snapshot()
        settings = make_settings(battery_profile="does-not-exist")
        converted = settings.to_genetic_pv_bat_param(snapshot)
        assert converted.max_charge_power_w == snapshot.max_charge_power_w
        assert converted.max_discharge_power_w == snapshot.max_discharge_power_w
        assert converted.charge_power_limit_w_by_soc is None


# --- Profile check ---------------------------------------------------------------


class TestProfileCheck:
    """Tests for check_profile, which reports problems instead of raising."""

    def test_resolvable_battery_has_no_problems(self):
        assert make_settings(capacity_wh=10000).check_profile() == ([], [])

    def test_missing_profile_is_an_error(self, broken_profile_loader):
        errors, warnings = make_settings(battery_profile="does-not-exist").check_profile()
        assert len(errors) == 1
        assert "Battery 'battery1'" in errors[0]
        assert "does-not-exist" in errors[0]
        assert warnings == []

    def test_generic_profile_without_capacity_is_an_error(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(nominal_capacity_wh=None))
        errors, _ = settings.check_profile()
        assert len(errors) == 1
        assert "profile_adjustment.nominal_capacity_wh" in errors[0]

    def test_power_limit_above_profile_is_a_warning(self):
        # The plain profile allows 0.5 C of 10 kWh = 5 kW charging, 0.4 C = 4 kW discharging.
        settings = make_settings(capacity_wh=10000, charge_limit_w=6000.0, discharge_limit_w=3000.0)
        errors, warnings = settings.check_profile()
        assert errors == []
        assert len(warnings) == 1
        assert "profile_adjustment.max_charge_power_w" in warnings[0]
        # The profile value applies.
        assert settings.effective_max_charge_power_w == pytest.approx(5000.0)
        assert settings.effective_max_discharge_power_w == pytest.approx(3000.0)

    def test_power_limit_below_profile_is_no_warning(self):
        settings = make_settings(capacity_wh=10000, charge_limit_w=2000.0)
        assert settings.check_profile() == ([], [])

    def test_no_charging_at_the_reference_point_is_an_error(self, make_profiled_settings):
        """A profile that cannot charge at the reference temperature is a configuration error."""
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        errors, _ = make_profiled_settings(profile, capacity_wh=10000).check_profile()
        assert len(errors) == 1
        assert "allows no charging at the reference operating point" in errors[0]


# --- Defaults for the optimization ------------------------------------------------


@pytest.fixture
def logged_errors():
    """Collect the messages logged at ERROR level."""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    yield messages
    logger.remove(sink_id)


class TestOptimizationDefaults:
    """resolve_operating_parameters_or_defaults never fails for a configuration error."""

    def test_resolvable_battery_matches_strict_resolution(self, logged_errors):
        settings = make_settings(capacity_wh=10000)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert parameters == settings.resolve_operating_parameters()
        assert use_tables is True
        assert logged_errors == []

    def test_missing_profile_uses_adjustment_and_former_defaults(
        self, broken_profile_loader, logged_errors
    ):
        settings = make_settings(battery_profile="does-not-exist", charge_limit_w=3000.0)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is False
        assert parameters.capacity_wh == 8000
        # The adjustment applies where set.
        assert parameters.max_charge_power_w == pytest.approx(3000.0)
        assert parameters.max_discharge_power_w == pytest.approx(5000.0)
        assert parameters.charging_efficiency == pytest.approx(0.88)
        assert parameters.discharging_efficiency == pytest.approx(0.88)
        assert len(logged_errors) == 1
        message = logged_errors[0]
        assert "Battery 'battery1'" in message and "does-not-exist" in message
        assert "nominal capacity 8000 Wh" in message
        assert "maximum discharge power 5000.0 W" in message
        # Not a default: the configured charge limit.
        assert "maximum charge power" not in message

    def test_unusable_profile_uses_the_generic_profile(self, monkeypatch, logged_errors):
        generic = make_profile(nominal_capacity_wh=None)
        requested: list[str] = []

        def get(document_type, name, **options):
            requested.append(name)
            if name == BATTERY_DEFAULT_PROFILE:
                return generic
            raise FileNotFoundError(f"No battery profile for '{name}'")

        monkeypatch.setattr(get_profile_store(), "get", get)
        settings = make_settings(battery_profile="does-not-exist", capacity_wh=10000)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert requested == ["does-not-exist", BATTERY_DEFAULT_PROFILE]
        # The generic profile applies: its tables and 0.5 C of the configured capacity.
        assert use_tables is True
        assert parameters.profile is generic
        assert parameters.capacity_wh == 10000
        assert parameters.max_charge_power_w == pytest.approx(5000.0)
        assert len(logged_errors) == 1
        assert "does-not-exist" in logged_errors[0]
        assert logged_errors[0].endswith(
            f"Using defaults for the optimization: battery profile '{BATTERY_DEFAULT_PROFILE}'."
        )

    def test_unusable_generic_profile_uses_the_former_defaults(
        self, broken_profile_loader, logged_errors
    ):
        settings = make_settings(battery_profile=BATTERY_DEFAULT_PROFILE)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is False
        assert parameters.charging_efficiency == pytest.approx(0.88)
        assert len(logged_errors) == 1
        assert f"battery profile '{BATTERY_DEFAULT_PROFILE}'" not in logged_errors[0].split(
            "Using defaults"
        )[1]

    def test_generic_profile_without_capacity_uses_default_capacity_only(
        self, make_profiled_settings, logged_errors
    ):
        settings = make_profiled_settings(make_profile(nominal_capacity_wh=None))
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is True
        assert parameters.capacity_wh == 8000
        # The profile still applies: 0.5 C of the default capacity.
        assert parameters.max_charge_power_w == pytest.approx(4000.0)
        assert len(logged_errors) == 1
        assert "profile_adjustment.nominal_capacity_wh" in logged_errors[0]
        assert logged_errors[0].endswith("Using defaults for the optimization: nominal capacity 8000 Wh.")

    def test_soc_range_outside_technical_bounds_uses_technical_range(
        self, make_profiled_settings, logged_errors
    ):
        profile = make_profile(technical_min_soc_percent=5.0, technical_max_soc_percent=90.0)
        settings = make_profiled_settings(
            profile, capacity_wh=10000, min_soc_percentage=95, max_soc_percentage=100
        )
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is True
        assert (parameters.min_soc_percentage, parameters.max_soc_percentage) == (5, 90)
        assert len(logged_errors) == 1
        assert "SoC range 5 ... 90 %" in logged_errors[0]

    def test_unconvertible_discharge_limit_gives_defaults(
        self, make_profiled_settings, logged_errors
    ):
        """With charging inhibited, a discharge limit that cannot be converted is still reported.

        Current limits without nominal voltage cannot be converted to power. The
        optimization gets the defaults instead of an exception.
        """
        # Only a compiled profile that bypassed validation can miss the voltage.
        profile = dataclasses.replace(
            make_profile(
                charge=20.0,
                discharge=20.0,
                units=Units(limit="A"),
                nominal_voltage_v=100.0,
                hard_cutoffs=charge_inhibited_below_30(),
            ),
            nominal_voltage_v=None,
        )
        settings = make_profiled_settings(profile, capacity_wh=10000)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is False
        assert parameters.max_discharge_power_w > 0
        assert len(logged_errors) == 1
        assert "nominal_voltage_v" in logged_errors[0]

    @pytest.mark.parametrize("method", ["to_genetic_pv_bat_param", "to_genetic_ev_bat_param"])
    def test_conversion_uses_the_profile_of_the_snapshot(self, monkeypatch, method):
        """A profile deleted between resolution and conversion does not abort the run."""
        settings = make_settings(capacity_wh=10000)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is True
        expected = getattr(settings, method)(parameters, use_tables)

        def missing(document_type, name, **options):
            raise FileNotFoundError(f"No battery profile for '{name}'")

        monkeypatch.setattr(get_profile_store(), "get", missing)
        converted = getattr(settings, method)(parameters, use_tables)
        assert converted.model_dump() == expected.model_dump()

    def test_inhibited_charging_is_no_configuration_error(
        self, make_profiled_settings, logged_errors
    ):
        """Inhibited charging gives valid parameters with 0 W charge power and no error."""
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, capacity_wh=10000)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is True
        assert parameters.max_charge_power_w == 0.0
        assert logged_errors == []

    @pytest.mark.parametrize("error", [PermissionError, IsADirectoryError])
    def test_unreadable_profile_uses_adjustment(self, monkeypatch, logged_errors, error):
        def unreadable(document_type, name, **options):
            raise error(f"Battery profile '{name}' is not readable")

        monkeypatch.setattr(get_profile_store(), "get", unreadable)
        settings = make_settings(charge_limit_w=3000.0)
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults()
        assert use_tables is False
        assert parameters.max_charge_power_w == pytest.approx(3000.0)
        assert len(logged_errors) == 1
        assert "is not readable" in logged_errors[0]

    def test_report_false_logs_nothing(self, broken_profile_loader, logged_errors):
        make_settings().resolve_operating_parameters_or_defaults(report=False)
        assert logged_errors == []

    @pytest.mark.parametrize("method", ["to_genetic_pv_bat_param", "to_genetic_ev_bat_param"])
    def test_conversion_without_profile_tables(self, broken_profile_loader, method):
        settings = make_settings(battery_profile="does-not-exist")
        parameters, use_tables = settings.resolve_operating_parameters_or_defaults(report=False)
        converted = getattr(settings, method)(parameters, use_tables)
        assert converted.capacity_wh == 8000
        assert converted.max_charge_power_w == pytest.approx(5000.0)
        assert converted.charge_power_limit_w_by_soc is None
        assert converted.charging_efficiency_curve is None

    @pytest.mark.asyncio
    async def test_current_operating_point_variant(self, broken_profile_loader, measurements):
        measurements["battery1-temperature-degc"] = 10.0
        settings = make_settings()
        parameters, use_tables = await settings.resolve_current_operating_parameters_or_defaults(
            report=False
        )
        assert use_tables is False
        assert parameters.temperature_degC == pytest.approx(10.0)
