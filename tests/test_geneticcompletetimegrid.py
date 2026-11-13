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
            "general": {"latitude": 52.52, "longitude": 13.405},
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
    get_ems(init=True).set_start_datetime(
        to_datetime("2026-09-16T00:00:00+02:00", in_timezone="Europe/Berlin")
    )
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


@pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
@pytest.mark.parametrize("offset", ["+02:00", "+01:00"])
@pytest.mark.parametrize("as_json_string", [False, True])
def test_snapshot_and_warm_start_preserve_aware_instants(
    config_eos, set_other_timezone, host_timezone, offset, as_json_string
):
    import json
    from pathlib import Path

    from akkudoktoreos.optimization.genetic.configrequest import (
        ConfigOptimizationRequest,
    )
    from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution

    set_other_timezone(host_timezone)
    expected = to_datetime(f"2026-10-25T02:30:00{offset}", in_timezone="Europe/Berlin")
    supplied = expected.to_iso8601_string() if as_json_string else expected
    payload = json.loads(
        (Path(__file__).parent / "testdata/genetic/optimize_result_1.json").read_text()
    )
    payload["start_solution_datetime"] = supplied
    native = GeneticSolution.model_validate(payload)
    parameters = GeneticOptimizationParameters.model_validate(
        native.parameters.model_dump() | {"start_solution_datetime": supplied}
    )
    request = ConfigOptimizationRequest.model_validate({"start_solution_datetime": supplied})
    for model in (native, parameters, request):
        value = model.start_solution_datetime
        assert value is not None
        assert value.timestamp() == expected.timestamp()
        assert value.utcoffset() == expected.utcoffset()
        if not as_json_string:
            assert value.timezone_name == "Europe/Berlin"
            assert value.fold == expected.fold
        restored = type(model).model_validate_json(model.model_dump_json())
        assert restored.start_solution_datetime is not None
        assert restored.start_solution_datetime.timestamp() == expected.timestamp()
        assert restored.start_solution_datetime.utcoffset() == expected.utcoffset()


def _partial_first_slot_run(config_eos, timestamp: str, initial_soc_percentage: int = 20):
    """Run GENETIC with AC charge at full power in slot 0 only.

    1 h slots, 2 h control horizon, no tail. Battery 10 kWh (at 20 % unless
    given), 2000 W charge power, ideal efficiencies; base load 400 Wh per hour,
    no PV.
    """
    config_eos.merge_settings_from_dict(
        {
            "general": {"latitude": 52.52, "longitude": 13.405},
            "prediction": {"hours": 24},
            "optimization": {
                "genetic": {
                    "interval_sec": 3600,
                    "horizon_hours": 2,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                }
            },
        }
    )
    start = to_datetime(timestamp).in_timezone("Europe/Berlin")
    get_ems(init=True).set_start_datetime(start)
    count = start.hour + 2
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": [0.0] * count,
                "total_load": [400.0] * count,
                "electricity_price_per_wh": [0.0001] * count,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": initial_soc_percentage,
                "min_soc_percentage": 0,
                "max_soc_percentage": 100,
                "max_charge_power_w": 2000,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
            },
            "inverter": {
                "device_id": "inverter1",
                "battery_id": "battery1",
                "max_power_wh": 5000,
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
            },
            "ev": None,
        }
    )
    optimizer = GeneticOptimization(fixed_seed=42)

    def full_ac_charge_in_first_slot(*args, **kwargs):
        genome = optimizer.create_individual()
        genome[:] = [0] * len(genome)
        genome[0] = 3 * len(optimizer.bat_possible_charge_values) - 1
        return genome, {}

    with patch.object(optimizer, "optimize", side_effect=full_ac_charge_in_first_slot):
        solution = optimizer.optimize_ems(parameters)
    return optimizer, solution


def test_running_slot_is_planned_for_the_remaining_time_only(config_eos):
    """A run at 10:45 has 15 min of slot 0 left, not a full hour.

    Charge power, load and grid energy of slot 0 shrink to a quarter; slot 1
    is a full slot again.
    """
    optimizer, solution = _partial_first_slot_run(config_eos, "2026-09-16T10:45:00+02:00")

    assert optimizer.first_slot_fraction == pytest.approx(0.25)
    assert solution.first_slot_fraction == pytest.approx(0.25)
    result = solution.result
    # Slot 0: 15 min of 400 W base load and 2000 W charge power. The load of
    # the result includes the AC charge energy.
    assert result.grid_consumption_wh_per_hour[0] == pytest.approx(100.0 + 500.0)
    assert result.load_wh_per_hour[0] == pytest.approx(100.0 + 500.0)
    # SoC at the start of each slot: 500 Wh stored in 15 min, 20 % -> 25 %.
    assert result.battery_soc_per_hour[:2] == pytest.approx([20.0, 25.0])
    # Slot 1 is a full slot again.
    assert result.grid_consumption_wh_per_hour[1] == pytest.approx(400.0)


def test_run_on_a_slot_boundary_plans_a_full_first_slot(config_eos):
    """A run exactly at the start of the slot keeps the full slot."""
    optimizer, solution = _partial_first_slot_run(config_eos, "2026-09-16T10:00:00+02:00")

    assert optimizer.first_slot_fraction == pytest.approx(1.0)
    assert solution.first_slot_fraction == pytest.approx(1.0)
    result = solution.result
    assert result.grid_consumption_wh_per_hour[0] == pytest.approx(400.0 + 2000.0)
    assert result.battery_soc_per_hour[:2] == pytest.approx([20.0, 40.0])


def test_running_slot_ac_charge_factor_is_not_cut_for_a_full_slot(config_eos):
    """The plan clamps the AC charge factor to the headroom of the remaining time.

    At 90 % there are 1000 Wh of headroom. A full hour at 2000 W would exceed it
    and the factor would be cut to 0.5; the 15 min left store only 500 Wh, so
    full power is sent for the running slot.
    """
    _, solution = _partial_first_slot_run(
        config_eos, "2026-09-16T10:45:00+02:00", initial_soc_percentage=90
    )
    assert solution.result.battery_soc_per_hour[:2] == pytest.approx([90.0, 95.0])
    plan = solution.energy_management_plan()
    battery_instructions = [
        instruction for instruction in plan.instructions if instruction.resource_id == "battery1"
    ]
    assert battery_instructions[0].operation_mode_factor == pytest.approx(1.0)
