"""Periodic full charge of a stationary battery (e.g. LFP recalibration)."""

import dataclasses
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, Mock

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.genetic.battery import Battery, SolarPanelBatteryParameters
from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings
from akkudoktoreos.devices.profile.batteryprofileruntime import BatteryProfile
from akkudoktoreos.devices.profile.profilestore import get_profile_store
from akkudoktoreos.devices.settings.batteryresolution import (
    BatteryFullCharge,
    resolve_full_charge,
)
from akkudoktoreos.devices.settings.batterysettings import (
    BatteriesCommonSettings,
    BatteryProfileAdjustment,
)
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.fullcharge import (
    FULL_CHARGE_MIN_LEAD_HOURS,
    enforced_full_charge_deadline,
    full_charge_deadline,
)
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

ems_eos = get_ems(init=True)  # init once

NOW = to_datetime("2026-11-20T12:00:00+01:00")


def _daily(days: int, value: float, now: DateTime = NOW) -> list[tuple[DateTime, Any]]:
    """One SoC sample per day for the last ``days`` days, oldest first."""
    return [(now.subtract(days=day), value) for day in range(days, 0, -1)]


# ---------------------------------------------------------------------------
# Deadline from the measured history
# ---------------------------------------------------------------------------


def test_deadline_counts_from_last_full_charge():
    samples = _daily(21, 0.4) + [(NOW.subtract(days=3), 0.995)]
    assert full_charge_deadline(samples, NOW, 21, 99) == NOW.subtract(days=3).add(days=21)


def test_deadline_uses_the_latest_of_several_full_charges():
    samples = [(NOW.subtract(days=10), 1.0), (NOW.subtract(days=4), 1.0)] + _daily(21, 0.4)
    assert full_charge_deadline(samples, NOW, 21, 99) == NOW.subtract(days=4).add(days=21)


def test_detect_threshold_decides_what_counts_as_full():
    samples = _daily(21, 0.4) + [(NOW.subtract(days=2), 0.98)]
    # 98 % is not full with the default threshold: the whole window is history
    # without a full charge, so the interval counts from the oldest sample.
    assert full_charge_deadline(samples, NOW, 21, 99) == NOW
    assert full_charge_deadline(samples, NOW, 21, 98) == NOW.subtract(days=2).add(days=21)


def test_overdue_deadline_stays_in_the_past():
    """The real deadline decides whether a full charge is due."""
    samples = [(NOW.subtract(days=30), 1.0)] + _daily(29, 0.4)
    # The interval is elapsed time: 21 x 24 h, across the DST change in October.
    expected = NOW.subtract(days=30) + timedelta(days=21)
    assert full_charge_deadline(samples, NOW, 21, 99) == expected
    assert expected.timestamp() < NOW.timestamp()


def test_enforced_deadline_leaves_lead_time():
    lead = NOW.add(hours=FULL_CHARGE_MIN_LEAD_HOURS)
    assert enforced_full_charge_deadline(NOW.subtract(days=9), NOW) == lead
    assert enforced_full_charge_deadline(NOW.add(hours=5), NOW) == lead
    assert enforced_full_charge_deadline(NOW.add(days=3), NOW) == NOW.add(days=3)


def test_short_history_without_full_charge_counts_from_oldest_sample():
    """A fresh installation is not forced into a full charge on day one."""
    samples = _daily(3, 0.4)
    assert full_charge_deadline(samples, NOW, 21, 99) == NOW.subtract(days=3).add(days=21)


@pytest.mark.parametrize("value", [None, np.nan, np.inf, "1.0"])
def test_invalid_and_future_samples_are_ignored(value):
    samples = [(NOW.subtract(days=2), value), (NOW.add(minutes=5), 1.0)] + _daily(5, 0.4)
    assert full_charge_deadline(samples, NOW, 21, 99) == NOW.subtract(days=5).add(days=21)


def test_no_history_gives_no_deadline():
    assert full_charge_deadline([], NOW, 21, 99) is None
    assert full_charge_deadline([(NOW.subtract(days=1), None)], NOW, 21, 99) is None


def test_fractional_interval():
    samples = [(NOW.subtract(days=1), 1.0)]
    assert full_charge_deadline(samples, NOW, 2.5, 99) == NOW.subtract(days=1).add(hours=60)


# ---------------------------------------------------------------------------
# Settings and parameters
# ---------------------------------------------------------------------------


def test_settings_defaults_disable_the_feature(config_eos):
    settings = BatteriesCommonSettings(device_id="battery1")
    assert settings.profile_adjustment.full_charge_interval_days is None
    assert settings.profile_adjustment.full_charge_detect_percentage is None
    assert settings.full_charge_soc_percentage == 100
    assert settings.full_charge_allow_grid is False
    # The default profile sets no interval.
    assert settings.effective_full_charge_interval_days is None
    assert settings.effective_full_charge_detect_percentage == 99


def test_profile_adjustment_sets_the_full_charge(config_eos):
    settings = BatteriesCommonSettings(
        device_id="battery1",
        profile_adjustment=BatteryProfileAdjustment(
            full_charge_interval_days=21, full_charge_detect_percentage=98
        ),
    )
    assert settings.effective_full_charge_interval_days == 21
    assert settings.effective_full_charge_detect_percentage == 98


@pytest.mark.parametrize(
    "extra",
    [
        {"profile_adjustment": {"full_charge_interval_days": 0}},
        {"profile_adjustment": {"full_charge_interval_days": -1}},
        {"profile_adjustment": {"full_charge_detect_percentage": 0}},
        {"profile_adjustment": {"full_charge_detect_percentage": 101}},
        {"full_charge_soc_percentage": 0},
        {"full_charge_soc_percentage": 101},
        {
            "full_charge_soc_percentage": 95,
            "profile_adjustment": {"full_charge_detect_percentage": 99},
        },
    ],
)
def test_invalid_settings_are_rejected(extra):
    with pytest.raises(ValidationError):
        BatteriesCommonSettings(device_id="battery1", **extra)


def _profile(**changes: Any) -> BatteryProfile:
    """The generic LFP profile with changed fields."""
    return dataclasses.replace(
        get_profile_store().get(BatteryProfileSettings, "generic-lfp"), **changes
    )


def test_resolve_full_charge_from_profile(config_eos):
    settings = BatteriesCommonSettings(device_id="battery1")
    profile = _profile(full_charge_interval_days=14.0, full_charge_detect_percentage=97)
    assert resolve_full_charge(settings, profile) == BatteryFullCharge(
        interval_days=14.0, soc_percentage=100, detect_percentage=97
    )


def test_resolve_full_charge_adjustment_wins_over_profile(config_eos):
    settings = BatteriesCommonSettings(
        device_id="battery1",
        profile_adjustment=BatteryProfileAdjustment(
            full_charge_interval_days=30, full_charge_detect_percentage=98
        ),
    )
    profile = _profile(full_charge_interval_days=14.0, full_charge_detect_percentage=97)
    assert resolve_full_charge(settings, profile) == BatteryFullCharge(
        interval_days=30, soc_percentage=100, detect_percentage=98
    )


def test_resolve_full_charge_without_profile_uses_adjustment_and_defaults():
    settings = BatteriesCommonSettings(
        device_id="battery1",
        profile_adjustment=BatteryProfileAdjustment(full_charge_interval_days=21),
    )
    assert resolve_full_charge(settings, None) == BatteryFullCharge(
        interval_days=21, soc_percentage=100, detect_percentage=99
    )


def test_resolve_full_charge_caps_target_and_threshold(config_eos):
    """The target stays within the technical bounds, the threshold below the target."""
    settings = BatteriesCommonSettings(device_id="battery1")
    profile = _profile(full_charge_interval_days=21.0, technical_max_soc_percent=95.5)
    assert resolve_full_charge(settings, profile) == BatteryFullCharge(
        interval_days=21.0, soc_percentage=95, detect_percentage=95
    )


def test_parameters_reject_target_above_max_soc():
    with pytest.raises(ValidationError):
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=10000,
            max_soc_percentage=90,
            full_charge_soc_percentage=100,
        )


def test_parameters_parse_naive_deadline_as_local_time():
    parameters = SolarPanelBatteryParameters.model_validate(
        {
            "device_id": "battery1",
            "capacity_wh": 10000,
            "full_charge_soc_percentage": 100,
            "full_charge_deadline_datetime": "2026-11-21T07:00:00",
        }
    )
    assert parameters.full_charge_deadline_datetime == to_datetime("2026-11-21T07:00:00")


# ---------------------------------------------------------------------------
# Resolving the configuration
# ---------------------------------------------------------------------------


@pytest.fixture
def full_charge_request(config_eos, monkeypatch):
    """A configured battery with a 30 h control horizon from 10:15 UTC."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "interval_sec": 900,
                    "horizon_hours": 30,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                    "individuals": 10,
                    "generations": 10,
                    "seed": 42,
                },
            },
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {
                    "storage": {
                        "max_soc_percentage": 90,
                        "profile_adjustment": {
                            "nominal_capacity_wh": 10000,
                            "max_charge_power_w": 5000,
                            "full_charge_interval_days": 21,
                        },
                    }
                },
                "electric_vehicles": {},
                "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 5000}},
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-09-12T10:15:00Z", in_timezone="UTC")
    ems = SimpleNamespace(
        start_datetime=start, observation_datetime=start, genetic_solution=lambda: None
    )
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    measurement = Mock(key_to_lists=AsyncMock(return_value=([], [])))
    monkeypatch.setattr(ConfigOptimizationRequest, "measurement", measurement)
    data = {
        "soc": {"storage": 40},
        "forecasts": {
            "pv_forecast_wh": [0.0] * 192,
            "total_load": [200.0] * 192,
            "electricity_price_per_wh": [0.0003] * 192,
            "feed_in_tariff_per_wh": [0.00008] * 192,
        },
    }
    return config_eos, start, measurement, data


def _history(start: DateTime, full_days_ago: Optional[float]) -> tuple[list[DateTime], list[float]]:
    """Hourly SoC history over 30 days, full once ``full_days_ago`` (if given)."""
    dates = [start.subtract(hours=hour) for hour in range(30 * 24, 0, -1)]
    values = [0.4] * len(dates)
    if full_days_ago is not None:
        index = len(dates) - int(full_days_ago * 24)
        values[index] = 1.0
    return dates, values


@pytest.mark.asyncio
async def test_feature_disabled_reads_no_history(full_charge_request):
    config, _, measurement, data = full_charge_request
    config.devices.batteries["storage"].profile_adjustment.full_charge_interval_days = None
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 90
    assert parameters.pv_battery.full_charge_soc_percentage is None
    measurement.key_to_lists.assert_not_called()


@pytest.mark.asyncio
async def test_not_due_within_horizon_leaves_battery_unchanged(full_charge_request):
    _, start, measurement, data = full_charge_request
    measurement.key_to_lists.return_value = _history(start, full_days_ago=5)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 90
    assert parameters.pv_battery.full_charge_soc_percentage is None
    assert parameters.pv_battery.full_charge_deadline_datetime is None
    # The history window covers the whole interval.
    kwargs = measurement.key_to_lists.call_args.kwargs
    assert kwargs["key"] == "storage-soc-factor"
    assert kwargs["start_datetime"] == start.subtract(days=21)


@pytest.mark.asyncio
async def test_due_without_grid_only_raises_the_maximum(full_charge_request):
    _, start, measurement, data = full_charge_request
    measurement.key_to_lists.return_value = _history(start, full_days_ago=20.5)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 100
    assert parameters.pv_battery.full_charge_soc_percentage is None
    assert parameters.pv_battery.full_charge_deadline_datetime is None


@pytest.mark.asyncio
async def test_due_with_grid_enforces_target_and_deadline(full_charge_request):
    config, start, measurement, data = full_charge_request
    config.devices.batteries["storage"].full_charge_allow_grid = True
    measurement.key_to_lists.return_value = _history(start, full_days_ago=None)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 100
    assert parameters.pv_battery.full_charge_soc_percentage == 100
    # Overdue: the optimizer gets the minimum lead time.
    assert parameters.pv_battery.full_charge_deadline_datetime == start.add(
        hours=FULL_CHARGE_MIN_LEAD_HOURS
    )


@pytest.mark.asyncio
async def test_overdue_full_charge_is_due_with_a_horizon_shorter_than_the_lead_time(
    full_charge_request,
):
    """Regression: the lead time must not push an overdue full charge out of the horizon."""
    config, start, measurement, data = full_charge_request
    config.merge_settings_from_dict({"optimization": {"genetic": {"horizon_hours": 12}}})
    config.devices.batteries["storage"].full_charge_allow_grid = True
    measurement.key_to_lists.return_value = _history(start, full_days_ago=None)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 100
    assert parameters.pv_battery.full_charge_soc_percentage == 100
    assert parameters.pv_battery.full_charge_deadline_datetime == start.add(
        hours=FULL_CHARGE_MIN_LEAD_HOURS
    )


@pytest.mark.asyncio
async def test_missing_history_does_not_schedule_a_full_charge(full_charge_request):
    config, _, measurement, data = full_charge_request
    config.devices.batteries["storage"].full_charge_allow_grid = True
    measurement.key_to_lists.side_effect = KeyError("storage-soc-factor")
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 90
    assert parameters.pv_battery.full_charge_soc_percentage is None


@pytest.mark.asyncio
async def test_profile_interval_schedules_the_full_charge(full_charge_request, monkeypatch):
    """The interval of the profile applies without a profile adjustment."""
    config, start, measurement, data = full_charge_request
    device = config.devices.batteries["storage"]
    device.profile_adjustment.full_charge_interval_days = None
    device.full_charge_allow_grid = True
    profile = _profile(full_charge_interval_days=21.0)
    monkeypatch.setattr(BatteriesCommonSettings, "get_battery_profile", lambda self: profile)
    measurement.key_to_lists.return_value = _history(start, full_days_ago=None)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 100
    assert parameters.pv_battery.full_charge_soc_percentage == 100


@pytest.mark.asyncio
async def test_full_charge_target_stays_within_technical_bounds(full_charge_request, monkeypatch):
    config, start, measurement, data = full_charge_request
    config.devices.batteries["storage"].full_charge_allow_grid = True
    profile = _profile(technical_max_soc_percent=95.0)
    monkeypatch.setattr(BatteriesCommonSettings, "get_battery_profile", lambda self: profile)
    measurement.key_to_lists.return_value = _history(start, full_days_ago=None)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.max_soc_percentage == 95
    assert parameters.pv_battery.full_charge_soc_percentage == 95


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


def _optimizer_config(config_eos: ConfigEOS, hours: int) -> None:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": hours},
            "optimization": {
                "genetic": {
                    "individuals": 100,
                    "generations": 40,
                    "tail_horizon_hours": 0,
                    "horizon_hours": hours,
                    "interval_sec": 3600,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                }
            },
        }
    )


def _full_charge_parameters(hours: int, **battery_extra) -> GeneticOptimizationParameters:
    """No PV and expensive energy: without a target nothing would be charged."""
    return GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * hours,
                "strompreis_euro_pro_wh": [0.0004] * hours,
                "einspeiseverguetung_euro_pro_wh": [0.00007] * hours,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [200.0] * hours,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 30,
                "min_soc_percentage": 0,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
                "max_charge_power_w": 5000,
                **battery_extra,
            },
            inverter={
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_ac_charge_power_w": 5000,
            },
            ev=None,
        )
    )


def test_full_charge_slot_resolution(config_eos: ConfigEOS):
    _optimizer_config(config_eos, 24)
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    optimization._slot0_datetime = optimization.ems.start_datetime
    slot0 = optimization._slot0_datetime

    assert optimization._full_charge_slot(_full_charge_parameters(24)) is None
    parameters = _full_charge_parameters(24, full_charge_soc_percentage=100)
    assert optimization._full_charge_slot(parameters) == optimization.control_end_slot
    parameters = _full_charge_parameters(
        24, full_charge_soc_percentage=100, full_charge_deadline_datetime=slot0.add(hours=10)
    )
    assert optimization._full_charge_slot(parameters) == 10
    parameters = _full_charge_parameters(
        24, full_charge_soc_percentage=100, full_charge_deadline_datetime=slot0.add(hours=100)
    )
    assert optimization._full_charge_slot(parameters) == optimization.control_end_slot


def test_maximum_soc_before_deadline_counts(config_eos: ConfigEOS):
    _optimizer_config(config_eos, 24)
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    optimization.simulation.battery = MagicMock(spec=Battery)
    optimization.simulation.battery.current_soc_percentage.return_value = 40.0
    result = {"akku_soc_pro_stunde": [30.0, 100.0, 60.0, 50.0]}

    optimization._full_charge_deadline_slot = None
    assert optimization._battery_max_soc_until_full_charge_deadline(result, 10) is None

    # Slot 11 starts after the charge in slot 10 finished.
    optimization._full_charge_deadline_slot = 11
    assert optimization._battery_max_soc_until_full_charge_deadline(result, 10) == 100.0
    optimization._full_charge_deadline_slot = 10
    assert optimization._battery_max_soc_until_full_charge_deadline(result, 10) == 30.0

    # Deadline at the end also counts the SoC after the last slot.
    result = {"akku_soc_pro_stunde": [30.0, 20.0]}
    optimization.simulation.battery.current_soc_percentage.return_value = 95.0
    optimization._full_charge_deadline_slot = 99
    assert optimization._battery_max_soc_until_full_charge_deadline(result, 10) == 95.0


def _run(config_eos: ConfigEOS, **battery_extra) -> list[float]:
    hours = 24
    _optimizer_config(config_eos, hours)
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()
    solution = GeneticOptimization(fixed_seed=42).optimize_ems(
        parameters=_full_charge_parameters(hours, **battery_extra), start_hour=0, ngen=40
    )
    return list(solution.result.battery_soc_per_hour)


def test_due_full_charge_is_reached_before_deadline(config_eos: ConfigEOS):
    without = _run(config_eos)
    assert max(without) < 99.0

    slot0 = to_datetime().set(hour=0, minute=0, second=0, microsecond=0)
    soc = _run(
        config_eos,
        full_charge_soc_percentage=100,
        full_charge_deadline_datetime=slot0.add(hours=12),
    )
    # Slot 12 starts at the deadline; its start-of-slot SoC includes every
    # charge that finished in time.
    assert max(soc[:13]) >= 99.0
