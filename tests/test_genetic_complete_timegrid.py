"""Native GENETIC results retain the elapsed-time grid and immutable run inputs."""

from unittest.mock import patch

import numpy as np
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp",
    ["2026-03-29T03:15:00+02:00", "2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00"],
)
async def test_native_result_retains_dst_grid_and_owned_inputs(config_eos, timestamp):
    config_eos.merge_settings_from_dict(
        {
            "general": {"timezone": "Europe/Berlin"},
            "prediction": {"hours": 24},
            "optimization": {
                "genetic": {
                    "interval_sec": 900,
                    "horizon_hours": 1,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                }
            },
        }
    )
    start = to_datetime(timestamp).in_timezone("Europe/Berlin")
    get_ems(init=True).set_start_datetime(start)
    elapsed_slots = int((start - start.start_of("day")).total_seconds() // 900)
    count = elapsed_slots + 4
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "forecast_interval_seconds": 900,
            "ems": {
                "pv_forecast_wh": [0.0] * count,
                "total_load": [25.0] * count,
                "electricity_price_per_wh": [0.0003] * count,
                "feed_in_tariff_per_wh": [0.00005] * count,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "owned_battery",
                "capacity_wh": 1000,
                "initial_soc_percentage": 50,
            },
            "inverter": {
                "device_id": "owned_inverter",
                "battery_id": "owned_battery",
                "max_power_wh": 1000,
            },
            "ev": None,
        }
    )
    optimizer = GeneticOptimization(fixed_seed=42)
    native = optimizer.optimize_ems(parameters, ngen=1, individuals=6)
    repeat = GeneticOptimization(fixed_seed=42).optimize_ems(parameters, ngen=1, individuals=6)
    assert native.start_solution == repeat.start_solution
    assert native.interval_seconds == 900
    assert native.start_solution_datetime == start
    assert native.controls_start_at_now
    assert len(native.result.load_wh_per_hour) == 4
    assert native.parameters.ems.total_load == [25.0] * 4
    parameters.ems.total_load[elapsed_slots] = 999.0
    get_ems().set_start_datetime(start.add(days=1))
    config_eos.optimization.genetic.interval_sec = 3600
    with patch(
        "akkudoktoreos.optimization.genetic.geneticsolution.get_prediction",
        side_effect=AssertionError("Native serialization must not reread providers"),
    ):
        generic = await native.optimization_solution()
        plan = native.energy_management_plan()
    assert generic.valid_from == start
    assert generic.valid_until == start.add(hours=1)
    assert plan.valid_from == start
    assert plan.valid_until is None
    assert all(
        start.timestamp() <= item.execution_time.timestamp() < start.add(hours=1).timestamp()
        for item in plan.instructions
    )
    forecast = generic.prediction.to_dataframe()
    np.testing.assert_allclose(forecast["loadforecast_energy_wh"], [25.0] * 4)
    np.testing.assert_allclose(forecast["elec_price_amt_kwh"], [0.3] * 4)
    assert all(
        (right - left).total_seconds() == 900
        for left, right in zip(forecast.index, forecast.index[1:])
    )
    assert "owned_battery_soc_factor" in generic.solution.to_dataframe().columns


def test_native_quarter_hour_temperatures_average_when_coarsened(config_eos):
    config_eos.merge_settings_from_dict(
        {"optimization": {"genetic": {"interval_sec": 3600, "horizon_hours": 1}}}
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-09-16T00:00:00+02:00"))
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "forecast_interval_seconds": 900,
            "temperature_forecast": [10, 12, 14, 16],
            "ems": {
                "pv_forecast_wh": [25.0] * 4,
                "total_load": [50.0] * 4,
                "electricity_price_per_wh": [0.0003] * 4,
                "feed_in_tariff_per_wh": 0.00005,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": None,
            "ev": None,
            "inverter": None,
        }
    )
    normalized = GeneticOptimization()._parameters_for_slot_grid(parameters)
    assert normalized.temperature_forecast == [13.0]
    assert normalized.ems.pv_forecast_wh == [100.0]
    assert normalized.ems.total_load == [200.0]
