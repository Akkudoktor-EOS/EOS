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
profile, settings use a plain profile (charge 0.5 C, discharge 0.4 C, no
efficiency curves) and no measurement is available. TestMeasurementAccess uses
the real measurement store.
"""

import dataclasses
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.profile.batteryprofile import (
    BatteryProfile,
    BatteryProfileSettings,
    CargeDischargeLimitMap,
    EfficiencyCurve,
    HardCutoffs,
    Units,
)
from akkudoktoreos.devices.profile.profilestore import get_profile_store
from akkudoktoreos.devices.settings import batterymeasurement
from akkudoktoreos.devices.settings.batterysettings import (
    BATTERY_DEFAULT_CAPACITY_WH,
    BATTERY_DEFAULT_CHARGING_EFFICIENCY,
    BATTERY_DEFAULT_DISCHARGING_EFFICIENCY,
    BATTERY_DEFAULT_MAX_CHARGE_POWER_W,
    BATTERY_DEFAULT_MAX_DISCHARGE_POWER_W,
    BATTERY_DEFAULT_PROFILE,
    BATTERY_DEFAULT_SOC_FACTOR,
    BATTERY_DEFAULT_TEMPERATURE_DEGC,
    BatteriesCommonSettings,
    BatteryOperatingParameters,
    BatteryOperatingPoint,
)

# The real measurement accessor, captured before any fixture replaces it.
REAL_GET_MEASURED_VALUE = batterymeasurement.get_measured_value

# Default axes: temperatures [-10, -5, 0, 5, 10, 15, 25, 35, 45, 50],
# SoC [0, 5, 10, 20, 40, 60, 80, 90, 95, 100].
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
    "resolved_nominal_capacity_wh",
    "max_charge_power_w",
    "max_discharge_power_w",
    "charging_efficiency",
    "discharging_efficiency",
    "effective_min_soc_percentage",
    "effective_max_soc_percentage",
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

    Args:
        charge: Constant charge limit or a full 10 x 10 grid.
        discharge: Constant discharge limit or a full 10 x 10 grid.
        **overrides: Further keyword arguments forwarded to the model.

    Returns:
        A validated BatteryProfileSettings instance.
    """
    charge_values = make_grid(charge) if isinstance(charge, (int, float)) else charge
    discharge_values = make_grid(discharge) if isinstance(discharge, (int, float)) else discharge
    payload: dict[str, Any] = dict(
        battery_model="Test Battery",
        charge_limit_crate=CargeDischargeLimitMap(values=charge_values),
        discharge_limit_crate=CargeDischargeLimitMap(values=discharge_values),
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
    return HardCutoffs(charge_allowed_below_degC=30.0, charge_allowed_above_degC=55.0)


def discharge_inhibited_below_30() -> HardCutoffs:
    """Cutoffs that inhibit discharging at the default temperature of 25 degC."""
    return HardCutoffs(discharge_allowed_below_degC=30.0, discharge_allowed_above_degC=60.0)


def make_settings(**overrides: Any) -> BatteriesCommonSettings:
    """Build a BatteriesCommonSettings with device_id battery1."""
    payload: dict[str, Any] = {"device_id": "battery1"}
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

    def test_optional_overrides_default_to_none(self):
        settings = make_settings()
        assert settings.nominal_capacity_wh is None
        assert settings.max_charge_power_limit_w is None
        assert settings.max_discharge_power_limit_w is None

    def test_old_name_battery_model_is_gone(self):
        assert "battery_model" not in BatteriesCommonSettings.model_fields
        assert "battery_profile" in BatteriesCommonSettings.model_fields

    @pytest.mark.parametrize("field", FORMER_CONFIGURATION_FIELDS)
    def test_former_configuration_fields_are_not_fields_any_more(self, field):
        assert field not in BatteriesCommonSettings.model_fields

    def test_builtin_defaults_keep_previous_behavior(self):
        assert BATTERY_DEFAULT_CAPACITY_WH == 8000
        assert BATTERY_DEFAULT_CHARGING_EFFICIENCY == pytest.approx(0.88)
        assert BATTERY_DEFAULT_DISCHARGING_EFFICIENCY == pytest.approx(0.88)
        assert BATTERY_DEFAULT_MAX_CHARGE_POWER_W == pytest.approx(5000.0)
        assert BATTERY_DEFAULT_MAX_DISCHARGE_POWER_W == pytest.approx(5000.0)

    def test_operating_point_defaults(self):
        assert BATTERY_DEFAULT_SOC_FACTOR == pytest.approx(0.5)
        assert BATTERY_DEFAULT_TEMPERATURE_DEGC == pytest.approx(25.0)

    def test_unusable_profile_resolves_to_builtin_defaults(self, broken_profile_loader):
        settings = make_settings()
        assert settings.resolved_nominal_capacity_wh == BATTERY_DEFAULT_CAPACITY_WH
        assert settings.charging_efficiency == pytest.approx(BATTERY_DEFAULT_CHARGING_EFFICIENCY)
        assert settings.discharging_efficiency == pytest.approx(
            BATTERY_DEFAULT_DISCHARGING_EFFICIENCY
        )
        assert settings.max_charge_power_w == pytest.approx(BATTERY_DEFAULT_MAX_CHARGE_POWER_W)
        assert settings.max_discharge_power_w == pytest.approx(
            BATTERY_DEFAULT_MAX_DISCHARGE_POWER_W
        )
        assert settings.effective_min_soc_percentage == 0
        assert settings.effective_max_soc_percentage == 100

    @pytest.mark.parametrize("value", [0, -1])
    def test_rejects_non_positive_nominal_capacity(self, value):
        with pytest.raises(ValidationError):
            make_settings(nominal_capacity_wh=value)

    @pytest.mark.parametrize("field", ["max_charge_power_limit_w", "max_discharge_power_limit_w"])
    @pytest.mark.parametrize("value", [0, -1.0])
    def test_rejects_non_positive_power_limits(self, field, value):
        with pytest.raises(ValidationError):
            make_settings(**{field: value})

    def test_rejects_min_soc_above_max_soc(self):
        with pytest.raises(ValidationError, match="min_soc_percentage"):
            make_settings(min_soc_percentage=80, max_soc_percentage=20)

    def test_rejects_min_charge_power_above_installation_limit(self):
        with pytest.raises(ValidationError, match="min_charge_power_w"):
            make_settings(min_charge_power_w=6000.0, max_charge_power_limit_w=5000.0)

    def test_min_charge_power_without_installation_limit_is_accepted(self):
        # The profile decides the limit, which is not known at validation time.
        settings = make_settings(min_charge_power_w=6000.0)
        assert settings.max_charge_power_limit_w is None


class TestJsonSchema:
    """Tests for the schema of configuration fields and computed fields."""

    @pytest.mark.parametrize(
        "field",
        [
            "battery_profile",
            "nominal_capacity_wh",
            "max_charge_power_limit_w",
            "max_discharge_power_limit_w",
        ],
    )
    def test_configuration_fields_are_documented(self, field):
        schema = BatteriesCommonSettings.model_json_schema()
        assert schema["properties"][field]["description"]
        assert schema["properties"][field]["examples"]

    def test_only_battery_profile_has_a_non_null_default(self):
        schema = BatteriesCommonSettings.model_json_schema()
        assert schema["properties"]["battery_profile"]["default"] == "generic-lfp"
        assert "nominal_capacity_wh" not in schema.get("required", [])

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
        make_settings(nominal_capacity_wh=10000)
        assert measurement_calls == []
        assert calls == []

    def test_not_evaluated_by_reading_configuration_fields(self, measurement_calls):
        settings = make_settings(nominal_capacity_wh=10000)
        _ = settings.nominal_capacity_wh
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
        settings = make_settings(nominal_capacity_wh=10000)
        assert settings.resolved_nominal_capacity_wh == 10000
        assert settings.max_charge_power_w == pytest.approx(5000.0)
        settings.nominal_capacity_wh = 6000
        assert settings.resolved_nominal_capacity_wh == 6000
        assert settings.max_charge_power_w == pytest.approx(3000.0)
        settings.nominal_capacity_wh = None
        assert settings.resolved_nominal_capacity_wh == BATTERY_DEFAULT_CAPACITY_WH

    def test_power_ignores_measured_temperature(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        assert settings.max_charge_power_w == pytest.approx(5000.0)

    def test_efficiency_ignores_measured_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        measurements["battery1-charge-power-w"] = 2500.0
        assert settings.charging_efficiency == pytest.approx(0.96)

    def test_model_dump_contains_computed_fields(self):
        dumped = make_settings(nominal_capacity_wh=10000).model_dump()
        for field in COMPUTED_FIELDS:
            assert field in dumped
        assert dumped["resolved_nominal_capacity_wh"] == 10000
        assert dumped["max_charge_power_w"] == pytest.approx(5000.0)
        assert dumped["max_discharge_power_w"] == pytest.approx(4000.0)

    def test_model_dump_json_contains_computed_fields(self):
        dumped = make_settings(nominal_capacity_wh=10000).model_dump_json()
        assert '"resolved_nominal_capacity_wh":10000' in dumped.replace(" ", "")

    def test_round_trip_keeps_configuration_and_ignores_computed_values(self):
        original = make_settings(
            battery_profile="my-profile", nominal_capacity_wh=10000, max_charge_power_limit_w=3000.0
        )
        restored = BatteriesCommonSettings.model_validate_json(original.model_dump_json())
        assert restored.battery_profile == "my-profile"
        assert restored.nominal_capacity_wh == 10000
        assert restored.max_charge_power_limit_w == pytest.approx(3000.0)
        assert restored.resolved_nominal_capacity_wh == 10000
        assert restored.max_charge_power_w == pytest.approx(3000.0)

    def test_serialization_survives_missing_profile(self, broken_profile_loader):
        dumped = make_settings(battery_profile="does-not-exist").model_dump()
        assert dumped["resolved_nominal_capacity_wh"] == BATTERY_DEFAULT_CAPACITY_WH
        assert dumped["charging_efficiency"] == pytest.approx(BATTERY_DEFAULT_CHARGING_EFFICIENCY)
        assert dumped["max_charge_power_w"] == pytest.approx(BATTERY_DEFAULT_MAX_CHARGE_POWER_W)

    def test_serialization_survives_inhibited_charging(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        dumped = settings.model_dump()
        # Falls back to the built-in default instead of raising.
        assert dumped["max_charge_power_w"] == pytest.approx(BATTERY_DEFAULT_MAX_CHARGE_POWER_W)

    def test_lenient_fallback_keeps_profile_capacity(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30(), nominal_capacity_wh=12000.0)
        settings = make_profiled_settings(profile)
        assert settings.resolved_nominal_capacity_wh == 12000
        assert settings.max_charge_power_w == pytest.approx(BATTERY_DEFAULT_MAX_CHARGE_POWER_W)

    def test_lenient_fallback_keeps_installation_limits(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(
            profile,
            nominal_capacity_wh=10000,
            max_charge_power_limit_w=2000.0,
            max_discharge_power_limit_w=1500.0,
        )
        assert settings.max_charge_power_w == pytest.approx(2000.0)
        assert settings.max_discharge_power_w == pytest.approx(1500.0)

    def test_computed_fields_match_strict_resolution(self, make_profiled_settings, full_profile):
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert settings.resolved_nominal_capacity_wh == resolved.capacity_wh
        assert settings.charging_efficiency == pytest.approx(resolved.charging_efficiency)
        assert settings.discharging_efficiency == pytest.approx(resolved.discharging_efficiency)
        assert settings.max_charge_power_w == pytest.approx(resolved.max_charge_power_w)
        assert settings.max_discharge_power_w == pytest.approx(resolved.max_discharge_power_w)
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
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        measurements["battery1-soc-factor"] = 0.4
        assert (await current(settings)).max_charge_power_w == pytest.approx(2000.0)

    def test_sync_resolution_ignores_measurements(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert resolved.temperature_degC == pytest.approx(25.0)
        assert resolved.soc_factor == pytest.approx(0.5)
        assert resolved.max_charge_power_w == pytest.approx(5000.0)

    def test_explicit_operating_point_is_used(self, make_profiled_settings, measurements):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 25.0
        cold = settings.resolve_operating_parameters(
            BatteryOperatingPoint(temperature_degC=0.0, soc_factor=0.5)
        )
        assert cold.max_charge_power_w == pytest.approx(1000.0)
        assert cold.temperature_degC == pytest.approx(0.0)


# --- Capacity ------------------------------------------------------------------


class TestCapacityResolution:
    """Tests for capacity_wh: nominal_capacity_wh > profile > built-in default."""

    def test_explicit_capacity_is_used(self):
        assert make_settings(nominal_capacity_wh=10000).resolved_nominal_capacity_wh == 10000

    def test_default_capacity_when_profile_has_none(self):
        assert make_settings().resolved_nominal_capacity_wh == BATTERY_DEFAULT_CAPACITY_WH

    def test_profile_capacity_is_used_when_not_configured(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        assert make_profiled_settings(profile).resolved_nominal_capacity_wh == 12000

    def test_settings_capacity_overrides_profile_capacity(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        assert make_profiled_settings(profile, nominal_capacity_wh=7000).resolved_nominal_capacity_wh == 7000

    def test_profile_capacity_is_rounded_to_whole_wh(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=9999.6)
        assert make_profiled_settings(profile).resolved_nominal_capacity_wh == 10000

    def test_profile_without_capacity_falls_back_to_default(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=None)
        assert make_profiled_settings(profile).resolved_nominal_capacity_wh == BATTERY_DEFAULT_CAPACITY_WH

    def test_resolve_nominal_capacity_wh_matches_computed_field(self, make_profiled_settings):
        profile = make_profile(nominal_capacity_wh=12000.0)
        settings = make_profiled_settings(profile)
        assert settings.resolve_nominal_capacity_wh() == settings.resolved_nominal_capacity_wh == 12000

    def test_power_scales_with_profile_capacity(self, make_profiled_settings):
        profile = make_profile(charge=0.5, nominal_capacity_wh=8000.0)
        resolved = make_profiled_settings(profile).resolve_operating_parameters()
        assert resolved.capacity_wh == 8000
        assert resolved.max_charge_power_w == pytest.approx(4000.0)

    def test_power_scales_with_settings_capacity(self, make_profiled_settings):
        profile = make_profile(charge=0.5, nominal_capacity_wh=8000.0)
        resolved = make_profiled_settings(
            profile, nominal_capacity_wh=10000
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
        settings = make_settings(nominal_capacity_wh=10000)
        settings.get_battery_profile()
        settings.resolve_operating_parameters()
        _ = settings.resolved_nominal_capacity_wh
        _ = settings.max_charge_power_w
        settings.to_genetic_pv_bat_param()
        assert calls == ["generic-lfp"] * 5

    def test_changed_store_profile_is_used_by_existing_instance(self, install_profile):
        install_profile(make_profile())
        settings = make_settings(nominal_capacity_wh=10000)
        before = settings.max_charge_power_w
        install_profile(make_profile(charge=0.25))
        assert settings.max_charge_power_w == pytest.approx(before / 2)

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
        with pytest.raises(FileNotFoundError, match="does-not-exist"):
            settings.resolve_operating_parameters()
        with pytest.raises(FileNotFoundError, match="does-not-exist"):
            settings.resolve_nominal_capacity_wh()

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_missing_profile_error_is_propagated_by_conversions(
        self, broken_profile_loader, method
    ):
        settings = make_settings(battery_profile="does-not-exist")
        with pytest.raises(FileNotFoundError, match="does-not-exist"):
            getattr(settings, method)()


# --- Maximum charge power -------------------------------------------------------


class TestMaxChargePower:
    """Tests for the maximum charge power."""

    def test_c_rate_limit_is_scaled_by_capacity(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(charge=0.5), nominal_capacity_wh=10000)
        assert settings.max_charge_power_w == pytest.approx(5000.0)

    def test_profile_power_limit_caps_c_rate_power(self, make_profiled_settings):
        profile = make_profile(charge=0.5, max_charge_power_w=3000.0)
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.max_charge_power_w == pytest.approx(3000.0)

    def test_installation_limit_below_profile_limit_wins(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5), nominal_capacity_wh=10000, max_charge_power_limit_w=2500.0
        )
        assert settings.max_charge_power_w == pytest.approx(2500.0)

    def test_installation_limit_cannot_exceed_profile_limit(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5), nominal_capacity_wh=10000, max_charge_power_limit_w=9000.0
        )
        assert settings.max_charge_power_w == pytest.approx(5000.0)

    def test_power_unit_is_used_as_is(self, make_profiled_settings):
        profile = make_profile(charge=3000.0, units=Units(limit="W"))
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.max_charge_power_w == pytest.approx(3000.0)

    def test_current_unit_is_converted_with_nominal_voltage(self, make_profiled_settings):
        profile = make_profile(charge=20.0, units=Units(limit="A"), nominal_voltage_v=100.0)
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.max_charge_power_w == pytest.approx(2000.0)

    def test_current_unit_without_nominal_voltage_raises(self, make_profiled_settings):
        profile = make_profile(charge=20.0, units=Units(limit="A"))
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        with pytest.raises(ValueError, match="nominal_voltage_v"):
            settings.resolve_operating_parameters()

    def test_unsupported_unit_raises(self, make_profiled_settings):
        profile = make_profile(charge=1.0, units=Units(limit="kW"))
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        with pytest.raises(ValueError, match="Unsupported limit unit"):
            settings.resolve_operating_parameters()

    def test_inhibited_charging_raises(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        with pytest.raises(ValueError, match="inhibits charging"):
            settings.resolve_operating_parameters()

    @pytest.mark.asyncio
    async def test_charging_is_inhibited_at_measured_cold_temperature(
        self, make_profiled_settings, measurements
    ):
        settings = make_profiled_settings(make_profile(), nominal_capacity_wh=10000)
        measurements["battery1-temperature-degc"] = -12.0
        with pytest.raises(ValueError, match="inhibits charging"):
            await current(settings)

    def test_zero_charge_power_raises(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(charge=0.0), nominal_capacity_wh=10000)
        with pytest.raises(ValueError, match="no charge power"):
            settings.resolve_operating_parameters()


# --- Maximum discharge power ----------------------------------------------------


class TestMaxDischargePower:
    """Tests for the maximum discharge power."""

    def test_c_rate_limit_is_scaled_by_capacity(self, make_profiled_settings):
        settings = make_profiled_settings(make_profile(discharge=0.4), nominal_capacity_wh=10000)
        assert settings.max_discharge_power_w == pytest.approx(4000.0)

    def test_profile_power_limit_caps_c_rate_power(self, make_profiled_settings):
        profile = make_profile(discharge=0.4, max_discharge_power_w=2000.0)
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.max_discharge_power_w == pytest.approx(2000.0)

    def test_installation_limit_below_profile_limit_wins(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(discharge=0.4),
            nominal_capacity_wh=10000,
            max_discharge_power_limit_w=1500.0,
        )
        assert settings.max_discharge_power_w == pytest.approx(1500.0)

    def test_installation_limit_cannot_exceed_profile_limit(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(discharge=0.4),
            nominal_capacity_wh=10000,
            max_discharge_power_limit_w=9000.0,
        )
        assert settings.max_discharge_power_w == pytest.approx(4000.0)

    def test_charge_and_discharge_limits_are_independent(self, make_profiled_settings):
        settings = make_profiled_settings(
            make_profile(charge=0.5, discharge=0.2),
            nominal_capacity_wh=10000,
            max_charge_power_limit_w=1000.0,
        )
        assert settings.max_charge_power_w == pytest.approx(1000.0)
        assert settings.max_discharge_power_w == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_discharge_follows_measured_temperature(
        self, make_profiled_settings, measurements
    ):
        grid = make_grid(0.4)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(discharge=grid), nominal_capacity_wh=10000)
        measurements["battery1-temperature-degc"] = 0.0
        assert (await current(settings)).max_discharge_power_w == pytest.approx(1000.0)
        # The computed field stays at the reference operating point.
        assert settings.max_discharge_power_w == pytest.approx(4000.0)

    def test_inhibited_discharging_yields_zero_without_raising(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=discharge_inhibited_below_30())
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.resolve_operating_parameters().max_discharge_power_w == 0.0
        assert settings.max_discharge_power_w == 0.0

    def test_inhibited_discharging_does_not_block_conversions(self, make_profiled_settings):
        profile = make_profile(hard_cutoffs=discharge_inhibited_below_30())
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.to_genetic_pv_bat_param().max_charge_power_w == pytest.approx(5000.0)


# --- Efficiencies ----------------------------------------------------------------


class TestEfficiencyAtMaximumPower:
    """Tests for efficiencies without a usable power measurement."""

    def test_charge_efficiency_at_max_charge_power(self, make_profiled_settings, full_profile):
        # 5000 W on 10000 Wh -> 0.5 C -> 0.96
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert settings.charging_efficiency == pytest.approx(0.96)

    def test_discharge_efficiency_at_max_discharge_power(self, make_profiled_settings, full_profile):
        # 4000 W on 10000 Wh -> 0.4 C -> 0.90 + 0.05 * 0.3 / 0.4 = 0.9375
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert settings.discharging_efficiency == pytest.approx(0.9375)

    def test_installation_limit_changes_c_rate(self, make_profiled_settings):
        # Charge capped to 2500 W -> 0.25 C -> 0.90 + 0.06 * 0.15 / 0.4 = 0.9225
        profile = make_profile(charge_efficiency=make_curve([0.1, 0.5, 1.0], [0.90, 0.96, 0.92]))
        settings = make_profiled_settings(
            profile, nominal_capacity_wh=10000, max_charge_power_limit_w=2500.0
        )
        assert settings.charging_efficiency == pytest.approx(0.9225)

    def test_profile_power_limit_changes_c_rate(self, make_profiled_settings):
        # Discharge capped to 2000 W -> 0.2 C -> 0.90 + 0.05 * 0.1 / 0.4 = 0.9125
        profile = make_profile(
            discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
            max_discharge_power_w=2000.0,
        )
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.discharging_efficiency == pytest.approx(0.9125)

    def test_efficiency_depends_on_c_rate_not_on_capacity(
        self, make_profiled_settings, full_profile
    ):
        # Same 0.5 C limit on a smaller capacity: lower power, same C-rate.
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=5000)
        assert settings.max_charge_power_w == pytest.approx(2500.0)
        assert settings.charging_efficiency == pytest.approx(0.96)

    def test_missing_curves_fall_back_to_defaults(self):
        settings = make_settings(nominal_capacity_wh=10000)
        assert settings.charging_efficiency == pytest.approx(BATTERY_DEFAULT_CHARGING_EFFICIENCY)
        assert settings.discharging_efficiency == pytest.approx(
            BATTERY_DEFAULT_DISCHARGING_EFFICIENCY
        )

    def test_inhibited_discharging_falls_back_to_default_efficiency(self, make_profiled_settings):
        profile = make_profile(
            discharge_efficiency=make_curve([0.1, 0.5], [0.90, 0.95]),
            hard_cutoffs=discharge_inhibited_below_30(),
        )
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert settings.discharging_efficiency == pytest.approx(
            BATTERY_DEFAULT_DISCHARGING_EFFICIENCY
        )


class TestEfficiencyAtMeasuredPower:
    """Tests for efficiencies taken at the measured charge / discharge power."""

    @pytest.mark.asyncio
    async def test_charge_efficiency_at_measured_charge_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2500 W -> 0.25 C -> 0.9225 (maximum power would give 0.96)
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.9225)

    @pytest.mark.asyncio
    async def test_discharge_efficiency_at_measured_discharge_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2000 W -> 0.2 C -> 0.9125 (maximum power would give 0.9375)
        measurements["battery1-discharge-power-w"] = 2000.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_directions_are_independent(self, make_profiled_settings, full_profile, measurements):
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.9225)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9375)

    @pytest.mark.asyncio
    async def test_measured_power_scales_with_capacity(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 2500 W on 5000 Wh is 0.5 C -> 0.96
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=5000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)

    @pytest.mark.asyncio
    async def test_negative_measurement_is_used_as_magnitude(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-discharge-power-w"] = -2000.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_zero_measurement_falls_back_to_maximum_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-charge-power-w"] = 0.0
        measurements["battery1-discharge-power-w"] = 0.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9375)

    @pytest.mark.parametrize("measured", [float("nan"), float("inf")])
    @pytest.mark.asyncio
    async def test_non_finite_measurement_falls_back_to_maximum_power(
        self, make_profiled_settings, full_profile, measurements, measured
    ):
        measurements["battery1-charge-power-w"] = measured
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.96)

    @pytest.mark.asyncio
    async def test_measurement_beyond_curve_is_clamped_by_profile(
        self, make_profiled_settings, full_profile, measurements
    ):
        # 20000 W -> 2 C is beyond the curve, clamped to the last point 0.92.
        measurements["battery1-charge-power-w"] = 20000.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_measured_power_is_not_limited_by_maximum_power(
        self, make_profiled_settings, full_profile, measurements
    ):
        # Installation limit 1000 W, measured 5000 W (0.5 C): measurement wins.
        measurements["battery1-charge-power-w"] = 5000.0
        settings = make_profiled_settings(
            full_profile, nominal_capacity_wh=10000, max_charge_power_limit_w=1000.0
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
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        assert (await current(settings)).discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.asyncio
    async def test_measured_power_without_curve_falls_back_to_default(
        self, make_profiled_settings, measurements
    ):
        measurements["battery1-charge-power-w"] = 2500.0
        settings = make_profiled_settings(make_profile(), nominal_capacity_wh=10000)
        assert (await current(settings)).charging_efficiency == pytest.approx(BATTERY_DEFAULT_CHARGING_EFFICIENCY)

    def test_explicit_powers_are_used(
        self, make_profiled_settings, full_profile, measurements
    ):
        measurements["battery1-charge-power-w"] = 5000.0
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        assert isinstance(resolved, BatteryOperatingParameters)
        assert dataclasses.asdict(resolved) == pytest.approx(
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
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        resolved = settings.resolve_operating_parameters()
        measurements["battery1-charge-power-w"] = 2500.0
        assert resolved.charging_efficiency == pytest.approx(0.96)


# --- Optimization parameters ---------------------------------------------------


class TestOptimizationParameters:
    """Tests that the conversions use the battery profile at the operating point."""

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_values_come_from_profile(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        params = getattr(settings, method)()
        assert params.capacity_wh == settings.resolved_nominal_capacity_wh
        assert params.charging_efficiency == pytest.approx(settings.charging_efficiency)
        assert params.discharging_efficiency == pytest.approx(settings.discharging_efficiency)
        assert params.max_charge_power_w == pytest.approx(settings.max_charge_power_w)
        assert params.min_soc_percentage == settings.effective_min_soc_percentage
        assert params.max_soc_percentage == settings.effective_max_soc_percentage

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    @pytest.mark.asyncio
    async def test_parameters_use_measured_operating_point(
        self, make_profiled_settings, measurements, method
    ):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
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
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
        params = getattr(settings, method)(await current(settings))
        assert params.charging_efficiency == pytest.approx(0.9225)
        assert params.discharging_efficiency == pytest.approx(0.9125)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_without_snapshot_reference_point_is_used(
        self, make_profiled_settings, measurement_calls, method
    ):
        grid = make_grid(0.5)
        grid[ROW_0_DEGC] = [0.1] * 10
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        assert getattr(settings, method)().max_charge_power_w == pytest.approx(5000.0)
        assert measurement_calls == []

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_snapshot_is_converted_as_given(self, make_profiled_settings, full_profile, method):
        settings = make_profiled_settings(full_profile, nominal_capacity_wh=10000)
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
            full_profile, nominal_capacity_wh=10000, max_charge_power_limit_w=2000.0
        )
        assert getattr(settings, method)().max_charge_power_w == pytest.approx(2000.0)

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_user_soc_range_is_clamped_into_technical_bounds(
        self, make_profiled_settings, full_profile, method
    ):
        settings = make_profiled_settings(
            full_profile, nominal_capacity_wh=10000, min_soc_percentage=10, max_soc_percentage=90
        )
        params = getattr(settings, method)()
        assert params.min_soc_percentage == 10
        assert params.max_soc_percentage == 90

    @pytest.mark.parametrize("method", PARAMETER_METHODS)
    def test_inhibited_charging_surfaces_as_error(self, make_profiled_settings, method):
        profile = make_profile(hard_cutoffs=charge_inhibited_below_30())
        settings = make_profiled_settings(profile, nominal_capacity_wh=10000)
        with pytest.raises(ValueError, match="inhibits charging"):
            getattr(settings, method)()

    def test_genetic_pv_receives_storage_cost(self):
        params = make_settings(levelized_cost_of_storage_amt_kwh=0.12).to_genetic_pv_bat_param()
        assert params.levelized_cost_of_storage_kwh == pytest.approx(0.12)

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
        settings = make_profiled_settings(make_profile(charge=grid), nominal_capacity_wh=10000)
        now = to_datetime()
        await get_measurement().update_value(now, {"battery1-temperature-degc": 0.0})
        resolved = await settings.resolve_current_operating_parameters(now)
        assert resolved.max_charge_power_w == pytest.approx(1000.0)
