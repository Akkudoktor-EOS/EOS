"""Battery reserve for unplanned consumption (issue #1550)."""

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.config.configabc import TimeWindowSequence
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.genetic.reserve import (
    default_reserve_time_windows,
    reserve_shortfall,
    reserve_targets,
    reserve_window_mask,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

ems_eos = get_ems(init=True)  # init once

# A fixed day without a daylight saving change, as local time of the test run.
DAY = "2026-11-20"


def _windows(*windows: dict[str, Any]) -> TimeWindowSequence:
    return TimeWindowSequence.model_validate({"windows": list(windows)})


# ---------------------------------------------------------------------------
# Windows and targets
# ---------------------------------------------------------------------------


def test_default_window_is_six_to_ten_pm():
    slot0 = to_datetime(f"{DAY}T00:00:00")
    mask = reserve_window_mask(default_reserve_time_windows(), slot0, 0, 24, 3600)
    assert [hour for hour, inside in enumerate(mask) if inside] == list(range(6, 22))


def test_window_mask_on_quarter_hours_from_the_current_slot():
    slot0 = to_datetime(f"{DAY}T00:00:00")
    windows = _windows({"start_time": "05:30", "duration": "1 hour"})
    # Slots 20 ... 27 are 05:00 ... 06:45.
    mask = reserve_window_mask(windows, slot0, 20, 28, 900)
    assert mask == [False, False, True, True, True, True, False, False]


def test_window_mask_follows_local_time_across_daylight_saving_change():
    """On the 25 h day of the autumn change, 06:00 local is 7 h after midnight."""
    slot0 = to_datetime("2026-10-25T00:00:00+02:00", in_timezone="Europe/Berlin")
    mask = reserve_window_mask(default_reserve_time_windows(), slot0, 0, 25, 3600)
    assert mask.index(True) == 7
    assert sum(mask) == 16


def test_window_restricted_to_a_weekday():
    slot0 = to_datetime(f"{DAY}T00:00:00")  # a Friday
    # Saturdays only (0 = Monday), so the window is on the second day.
    windows = _windows({"start_time": "17:00", "duration": "4 hours", "day_of_week": 5})
    mask = reserve_window_mask(windows, slot0, 0, 48, 3600)
    assert [slot for slot, inside in enumerate(mask) if inside] == [41, 42, 43, 44]


def test_targets_require_the_reserve_from_window_start_to_end():
    in_window = [False, False, True, True, False]
    targets = reserve_targets(40.0, in_window, start_soc_percent=10.0)
    assert targets.floor_percent == [0.0, 0.0, 40.0, 40.0, 0.0]
    # Boundary 2 opens the window, boundary 4 closes it.
    assert targets.required_percent == [0.0, 0.0, 40.0, 40.0, 40.0, 0.0]
    assert targets.relaxed_slots == 0


def test_targets_hold_the_start_soc_in_an_active_window_below_the_reserve():
    in_window = [True, True, False, False, True]
    targets = reserve_targets(40.0, in_window, start_soc_percent=15.0)
    assert targets.relaxed_slots == 2
    assert targets.floor_percent == [15.0, 15.0, 0.0, 0.0, 40.0]
    assert targets.required_percent == [0.0, 15.0, 15.0, 0.0, 40.0, 40.0]


def test_targets_in_an_active_window_at_the_reserve_are_not_relaxed():
    targets = reserve_targets(40.0, [True, True], start_soc_percent=40.0)
    assert targets.relaxed_slots == 0
    assert targets.floor_percent == [40.0, 40.0]


def test_shortfall_counts_only_required_boundaries_below_the_reserve():
    required = [0.0, 40.0, 40.0, 0.0]
    assert reserve_shortfall(required, [0.0, 30.0, 45.0, 0.0]) == pytest.approx(10.0)
    assert reserve_shortfall(required, [0.0, 40.0, 40.0, 0.0]) == 0.0
    assert reserve_shortfall(required, [0.0, float("nan"), 39.0]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


def _optimizer_config(config_eos: ConfigEOS, hours: int = 24) -> None:
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


def _parameters(
    prices: list[float],
    initial_soc: int = 10,
    hours: int = 24,
    **battery_extra: Any,
) -> GeneticOptimizationParameters:
    """No PV, a constant load, a battery that loses 10 % per conversion."""
    return GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * hours,
                "strompreis_euro_pro_wh": prices,
                "einspeiseverguetung_euro_pro_wh": [0.00007] * hours,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [200.0] * hours,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": initial_soc,
                "min_soc_percentage": 10,
                "max_soc_percentage": 100,
                "charging_efficiency": 0.9,
                "discharging_efficiency": 0.9,
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


def _run(
    config_eos: ConfigEOS, start: DateTime, parameters: GeneticOptimizationParameters
) -> GeneticSolution:
    _optimizer_config(config_eos)
    ems_eos.set_start_datetime(start)
    CacheEnergyManagementStore().clear()
    return GeneticOptimization(fixed_seed=42).optimize_ems(
        parameters=parameters, start_hour=start.hour, ngen=40
    )


def _soc(solution: GeneticSolution) -> list[float]:
    return list(solution.result.battery_soc_per_hour)


# Nearly flat prices: charging is not worth the conversion losses on its own.
# 03:00-05:00 is a little cheaper, so a required charge goes there.
WINTER_PRICES = [0.000300 if 3 <= hour < 5 else 0.000310 for hour in range(24)]


def test_reserve_targets_of_a_run(config_eos: ConfigEOS):
    _optimizer_config(config_eos)
    ems_eos.set_start_datetime(to_datetime(f"{DAY}T00:00:00"))
    optimization = GeneticOptimization(fixed_seed=1)
    optimization._slot0_datetime = optimization.ems.start_datetime

    assert optimization._battery_reserve_targets(_parameters(WINTER_PRICES)) is None

    targets = optimization._battery_reserve_targets(
        _parameters(WINTER_PRICES, reserve_soc_percentage=40)
    )
    assert targets is not None
    assert [slot for slot, floor in enumerate(targets.floor_percent) if floor] == list(
        range(6, 22)
    )

    # A window outside the horizon: no reserve in this run.
    outside = {"windows": [{"start_time": "06:00", "duration": "1 hour", "date": "2027-01-01"}]}
    assert (
        optimization._battery_reserve_targets(
            _parameters(
                WINTER_PRICES, reserve_soc_percentage=40, reserve_soc_time_windows=outside
            )
        )
        is None
    )


def test_reserve_is_charged_before_the_window_and_kept(config_eos: ConfigEOS):
    start = to_datetime(f"{DAY}T00:00:00")
    without = _soc(_run(config_eos, start, _parameters(WINTER_PRICES)))
    # Without a reserve the battery is not charged for nothing.
    assert max(without) < 15.0

    solution = _run(config_eos, start, _parameters(WINTER_PRICES, reserve_soc_percentage=40))
    soc = _soc(solution)
    # The reserve is there when the window opens at 06:00 and until it closes.
    assert soc[6] >= 39.0
    assert min(soc[6:22]) >= 39.0
    # Charged in the cheaper slots before the window.
    ac_charge = solution.ac_charge
    assert sum(ac_charge[3:5]) > 0.0
    assert sum(ac_charge[6:22]) == 0.0
    # Slots that start at the reserve allow discharging, so the real battery
    # can cover unplanned consumption from the reserve.
    for slot in range(6, 22):
        if soc[slot] <= 40.0 + 1e-6 and ac_charge[slot] == 0.0:
            assert solution.discharge_allowed[slot] == 1, slot


def test_used_reserve_is_not_refilled_inside_the_window(config_eos: ConfigEOS):
    """Started at 18:00 below the reserve: hold until 22:00, refill for 06:00."""
    start = to_datetime(f"{DAY}T18:00:00")
    # The forecasts start at midnight of the start day; EOS trims them to now.
    # Expensive day and evening, cheap night 22:00-06:00.
    hours = 18 + 24
    prices = [0.000250 if (hour % 24 < 6 or hour % 24 >= 22) else 0.000400 for hour in range(hours)]
    solution = _run(
        config_eos,
        start,
        _parameters(prices, initial_soc=15, hours=hours, reserve_soc_percentage=40),
    )
    soc = _soc(solution)
    # No refill at the evening price, no planned discharge below the start SoC.
    assert sum(solution.ac_charge[:4]) == 0.0
    assert min(soc[:5]) >= 15.0 - 1e-6
    # Refilled in the night for the window from 06:00 (slot 12).
    assert soc[12] >= 39.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_reserve_reaches_the_optimization_parameters(
    config_eos: ConfigEOS, monkeypatch
):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {"interval_sec": 3600, "horizon_hours": 24, "tail_horizon_hours": 0},
            },
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {
                    "storage": {
                        "min_soc_percentage": 10,
                        "reserve_soc_percentage": 40,
                        "reserve_soc_time_windows": {
                            "windows": [{"start_time": "17:00", "duration": "5 hours"}]
                        },
                        "profile_adjustment": {"nominal_capacity_wh": 10000},
                    }
                },
                "electric_vehicles": {},
                "inverters": {
                    "inverter": {
                        "battery_id": "storage",
                        "profile_adjustment": {"nominal_power_w": 5000},
                    }
                },
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-11-20T10:00:00Z", in_timezone="UTC")
    ems = SimpleNamespace(
        start_datetime=start, observation_datetime=start, genetic_solution=lambda: None
    )
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    measurement = Mock(key_to_lists=AsyncMock(return_value=([], [])))
    monkeypatch.setattr(ConfigOptimizationRequest, "measurement", measurement)
    data = {
        "soc": {"storage": 40},
        "forecasts": {
            "pv_forecast_wh": [0.0] * 48,
            "total_load": [200.0] * 48,
            "electricity_price_per_wh": [0.0003] * 48,
            "feed_in_tariff_per_wh": [0.00008] * 48,
        },
    }

    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()

    battery: Optional[Any] = parameters.pv_battery
    assert battery is not None
    assert battery.reserve_soc_percentage == 40
    assert battery.reserve_soc_time_windows is not None
    assert battery.reserve_soc_time_windows.windows[0].start_time.hour == 17
