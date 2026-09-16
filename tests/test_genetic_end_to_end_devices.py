"""Real small optimizer runs covering device contracts across the public result."""

from collections.abc import AsyncGenerator, Callable
from typing import Any

import numpy as np
import pytest
import pytest_asyncio

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.core.emplan import DDBCInstruction
from akkudoktoreos.measurement.measurement import Measurement
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


@pytest.fixture(autouse=True, params=["UTC", "Europe/Berlin"])
def local_clock(request: pytest.FixtureRequest, set_other_timezone: Callable[[str], str]) -> None:
    """Run the same local-wall-clock schedules in UTC and a DST-observing zone.

    Scenario dates intentionally have no fixed offset: each names local midnight,
    a local time window or a local departure in the selected timezone.
    """
    set_other_timezone(request.param)


@pytest_asyncio.fixture
async def isolated_measurement(config_eos: ConfigEOS) -> AsyncGenerator[Measurement, None]:
    """Keep synthetic records out of the process-wide measurement singleton."""
    measurement = get_measurement()
    await measurement.delete_by_datetime(None, None)
    try:
        yield measurement
    finally:
        await measurement.delete_by_datetime(None, None)


def configure(
    config: ConfigEOS,
    *,
    hours: int = 4,
    start: str = "2026-09-16T00:00:00",
    marketing: bool = False,
) -> None:
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": max(48, hours)},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": hours,
                    "tail_horizon_hours": 0,
                    "interval_sec": 900,
                    "individuals": 12,
                    "generations": 10,
                    "terminal_value_mode": "FIXED",
                },
            },
            "feedintariff": {"direct_marketing_enabled": marketing},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime(start))


def parameters(
    *, hours: int = 4, consumers: list[dict[str, Any]] | None = None, **kwargs: Any
) -> GeneticOptimizationParameters:
    slots = hours * 4 + get_ems().start_datetime.hour * 4 + get_ems().start_datetime.minute // 15
    return GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": [0.0] * slots,
                "gesamtlast": [0.0] * slots,
                "strompreis_euro_pro_wh": [0.0003] * slots,
                "einspeiseverguetung_euro_pro_wh": [0.0001] * slots,
                "preis_euro_pro_wh_akku": 0.0,
            },
            "inverter": {"device_id": "inv", "max_power_wh": 10000},
            "forecast_interval_seconds": 900,
            "home_appliances": consumers,
            "pv_battery": None,
            "ev": None,
            **kwargs,
        }
    )


def run(params: GeneticOptimizationParameters) -> tuple[GeneticOptimization, GeneticSolution]:
    optimizer = GeneticOptimization(fixed_seed=42)
    solution = optimizer.optimize_ems(params, ngen=1, individuals=12)
    return optimizer, solution


def run_local_starts(solution: GeneticSolution) -> list[DateTime]:
    """Compare scheduled instants in the run zone, independently of output zone."""
    timezone = get_ems().start_datetime.timezone_name
    assert timezone is not None
    return [moment.in_timezone(timezone) for moment in solution.appliance_starts["washer"]]


def profile(**kwargs: Any) -> dict[str, Any]:
    return {
        "device_id": "washer",
        "load_profile_power_w": [1200.0, 600.0, 300.0],
        "load_profile_interval_seconds": 600,
        **kwargs,
    }


def test_real_optimizer_keeps_reverse_cycle_window_identity(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    consumer = profile(
        num_cycles=2,
        time_windows={
            "windows": [
                {"start_time": "02:00", "duration": "30 minutes", "value": 0},
                {"start_time": "01:00", "duration": "30 minutes", "value": 1},
            ]
        },
    )
    _, solution = run(parameters(consumers=[consumer]))
    assert [value.hour for value in run_local_starts(solution)] == [1, 2]
    assert sum(solution.result.home_appliance_energy_wh["washer"]) == pytest.approx(700.0)
    assert sum(solution.result.grid_consumption_wh_per_hour) == pytest.approx(700.0)
    assert solution.result.total_costs == pytest.approx(0.21)


def test_real_daily_optimizer_skips_completed_cycles_only_on_first_day(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos, hours=28)
    consumer = profile(
        num_cycles=2,
        completed_cycles=1,
        schedule_mode="DAILY",
        time_windows={
            "windows": [
                {"start_time": "01:00", "duration": "30 minutes", "value": 0},
                {"start_time": "02:00", "duration": "30 minutes", "value": 1},
            ]
        },
    )
    _, solution = run(parameters(hours=28, consumers=[consumer]))
    assert [(value.day, value.hour) for value in run_local_starts(solution)] == [
        (16, 2),
        (17, 1),
        (17, 2),
    ]
    assert sum(solution.result.home_appliance_energy_wh["washer"]) == pytest.approx(1050.0)


def test_real_best_effort_multicycle_prioritizes_delay_over_cheaper_prices(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos)
    consumer = profile(num_cycles=2, min_cycle_gap_h=1, deadline_datetime="2026-09-15T23:00:00")
    params = parameters(consumers=[consumer])
    params.ems.electricity_price_per_wh = [0.001] * 8 + [-0.001] * 8
    _, solution = run(params)
    assert [(value.hour, value.minute) for value in run_local_starts(solution)] == [
        (0, 0),
        (1, 30),
    ]
    assert solution.appliance_deadline_missed["washer"]
    assert sum(solution.result.home_appliance_energy_wh["washer"]) == pytest.approx(700.0)


def test_real_mixed_best_effort_cycle_status_survives_later_strict_cycle(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos)
    consumer = profile(
        num_cycles=2,
        deadline_datetime="2026-09-16T01:00:00",
        time_windows={
            "windows": [
                {"start_time": "02:00", "duration": "2 hours", "value": 0},
                {"start_time": "00:00", "duration": "30 minutes", "value": 1},
            ]
        },
    )
    params = parameters(consumers=[consumer])
    params.ems.electricity_price_per_wh = [0.001] * 12 + [-0.001] * 4
    _, solution = run(params)
    assert [(value.hour, value.minute) for value in run_local_starts(solution)] == [
        (0, 0),
        (2, 0),
    ]
    assert solution.appliance_deadline_missed["washer"]


def test_real_warm_start_handles_changed_completed_cycle_layout(config_eos: ConfigEOS) -> None:
    configure(config_eos)
    consumer = profile(
        num_cycles=2,
        time_windows={
            "windows": [
                {"start_time": "01:00", "duration": "30 minutes", "value": 0},
                {"start_time": "02:00", "duration": "30 minutes", "value": 1},
            ]
        },
    )
    optimizer, first = run(parameters(consumers=[consumer]))
    consumer["completed_cycles"] = 1
    followup = parameters(
        consumers=[consumer],
        start_solution=first.start_solution,
        start_solution_datetime=first.start_solution_datetime,
    )
    second = optimizer.optimize_ems(followup, ngen=1, individuals=12)
    assert len(second.appliance_starts["washer"]) == 1
    assert run_local_starts(second)[0].hour == 2
    assert sum(second.result.home_appliance_energy_wh["washer"]) == pytest.approx(350.0)
    assert first.start_solution is not None and second.start_solution is not None
    assert len(second.start_solution) == len(first.start_solution) - 1


@pytest.mark.parametrize("deadline", ["2026-09-15T23:00:00", "2026-09-16T00:01:00"])
def test_real_ev_does_not_credit_energy_delivered_after_departure(
    config_eos: ConfigEOS, deadline: str
) -> None:
    configure(config_eos)
    params = parameters(
        ev={
            "device_id": "car",
            "capacity_wh": 4000,
            "initial_soc_percentage": 0,
            "min_soc_percentage": 25,
            "charging_efficiency": 1.0,
            "max_charge_power_w": 4000,
            "charge_rates": [0.0, 1.0],
            "min_soc_deadline_datetime": deadline,
        }
    )
    optimizer, solution = run(params)
    assert optimizer._ev_soc_deadline_slot == 0
    assert (
        optimizer._ev_soc_at_deadline({"EAuto_SoC_pro_Stunde": solution.result.ev_soc_per_hour}, 0)
        == 0.0
    )


def test_real_ev_target_across_midnight_uses_elapsed_slots(config_eos: ConfigEOS) -> None:
    configure(config_eos, start="2026-09-16T23:30:00")
    params = parameters(
        ev={
            "device_id": "car",
            "capacity_wh": 4000,
            "initial_soc_percentage": 0,
            "min_soc_percentage": 50,
            "charging_efficiency": 1.0,
            "max_charge_power_w": 4000,
            "charge_rates": [0.0, 1.0],
            "min_soc_deadline_datetime": "2026-09-17T00:00:00",
        }
    )
    params.ems.electricity_price_per_wh[-16:] = [0.001] * 2 + [0.00001] * 14
    optimizer, solution = run(params)
    assert optimizer._ev_soc_deadline_slot == 2
    assert solution.result.ev_soc_per_hour[2] >= 50
    assert solution.ev_charge_hours_float is not None
    assert solution.ev_charge_hours_float[:2] == [1.0, 1.0]


@pytest.mark.parametrize(
    "marketing,lcos,export_expected",
    [(False, 0.0, False), (True, 0.0, True), (True, 0.2, True), (True, 2.0, False)],
)
def test_real_export_respects_marketing_gate_and_storage_cost(
    config_eos: ConfigEOS, marketing: bool, lcos: float, export_expected: bool
) -> None:
    configure(config_eos, marketing=marketing)
    params = parameters(
        pv_battery={
            "device_id": "battery",
            "capacity_wh": 1000,
            "initial_soc_percentage": 100,
            "charging_efficiency": 1.0,
            "discharging_efficiency": 1.0,
            "min_soc_percentage": 0,
            "max_charge_power_w": 4000,
            "grid_export_rates": [0.5, 1.0],
            "levelized_cost_of_storage_kwh": lcos,
        },
        inverter={"device_id": "inv", "max_power_wh": 10000, "battery_id": "battery"},
    )
    params.ems.feed_in_tariff_per_wh = [0.00001] + [0.001] * 4 + [0.00001] * 11
    _, solution = run(params)
    exported = sum(solution.result.grid_feed_in_wh_per_hour)
    if export_expected:
        assert exported == pytest.approx(1000.0)
        assert solution.result.total_revenue == pytest.approx(1.0)
        assert solution.result.total_costs == pytest.approx(lcos)
        assert any(solution.battery_grid_export_allowed)
    else:
        assert exported == pytest.approx(0.0)
        assert not any(solution.battery_grid_export_allowed)
    assert np.isfinite(solution.result.total_balance)


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_key", [None, "washer.completed_today"])
async def test_real_measurement_completed_cycles_reach_request_and_optimizer(
    config_eos: ConfigEOS, custom_key: str | None, isolated_measurement: Measurement
) -> None:
    configure(config_eos)
    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "max_batteries": 0,
                "batteries": {},
                "max_electric_vehicles": 0,
                "electric_vehicles": {},
                "max_inverters": 1,
                "inverters": {"inv": {"max_power_w": 10000}},
                "max_home_appliances": 1,
                "home_appliances": {
                    "washer": profile(
                        num_cycles=2,
                        cycles_completed_measurement_key=custom_key,
                        cycle_time_windows={
                            "windows": [
                                {"start_time": "01:00", "duration": "30 minutes", "value": 0},
                                {"start_time": "02:00", "duration": "30 minutes", "value": 1},
                            ]
                        },
                    )
                },
            }
        }
    )
    key = custom_key or "washer.cycles_completed"
    assert key in config_eos.devices.measurement_keys
    measurement = isolated_measurement
    zero = get_ems().start_datetime
    await measurement.update_value(zero.subtract(days=1), key, 2.0)
    await measurement.update_value(zero, key, 1.0)
    await measurement.update_value(zero.add(seconds=1), key, 2.0)
    dates, counts = await measurement.key_to_lists(
        key=key, start_datetime=zero, end_datetime=zero.add(seconds=1)
    )
    assert len(dates) == 1 and counts == [1.0]
    prepared = await ConfigOptimizationRequest.model_validate(
        {
            "forecasts": {
                "pv_forecast_wh": [0.0] * 16,
                "total_load": [0.0] * 16,
                "electricity_price_per_wh": [0.0003] * 16,
                "feed_in_tariff_per_wh": [0.0001] * 16,
            }
        }
    ).resolve()
    assert prepared.home_appliances is not None
    assert prepared.home_appliances[0].completed_cycles == 1
    _, solution = run(prepared)
    assert [start.hour for start in run_local_starts(solution)] == [2]
    assert sum(solution.result.home_appliance_energy_wh["washer"]) == pytest.approx(350.0)


@pytest.mark.asyncio
async def test_real_multiple_consumers_keep_separate_solution_channels(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos)
    consumers = [
        profile(
            shared_time_windows={"windows": [{"start_time": "01:00", "duration": "30 minutes"}]}
        ),
        profile(
            device_id="dryer",
            load_profile_power_w=[2000.0],
            load_profile_interval_seconds=900,
            shared_time_windows={"windows": [{"start_time": "01:00", "duration": "15 minutes"}]},
        ),
    ]
    _, solution = run(parameters(consumers=consumers))
    assert sum(solution.result.home_appliance_energy_wh["washer"]) == pytest.approx(350.0)
    assert sum(solution.result.home_appliance_energy_wh["dryer"]) == pytest.approx(500.0)
    assert sum(solution.result.grid_consumption_wh_per_hour) == pytest.approx(850.0)
    exported = await solution.optimization_solution()
    table = exported.solution.to_dataframe()
    assert table["washer_energy_wh"].sum() == pytest.approx(350.0)
    assert table["dryer_energy_wh"].sum() == pytest.approx(500.0)
    assert table.index[4].hour == 1
    assert table["washer_run_op_mode"].tolist()[4:6] == [1.0, 1.0]
    assert table["dryer_run_op_mode"].tolist()[4:6] == [1.0, 0.0]


@pytest.mark.asyncio
async def test_profile_zero_power_phase_keeps_device_running_until_complete(
    config_eos: ConfigEOS,
) -> None:
    configure(config_eos)
    consumer = profile(
        load_profile_power_w=[1200.0, 0.0, 1200.0],
        load_profile_interval_seconds=900,
        shared_time_windows={"windows": [{"start_time": "01:00", "duration": "45 minutes"}]},
    )
    _, solution = run(parameters(consumers=[consumer]))
    exported = await solution.optimization_solution()
    table = exported.solution.to_dataframe()
    assert table["washer_energy_wh"].tolist()[4:7] == [300.0, 0.0, 300.0]
    assert table["washer_run_op_mode"].tolist()[4:7] == [1.0, 1.0, 1.0]
    plan = solution.energy_management_plan()
    assert {item.resource_id for item in plan.instructions} == {"washer"}
    commands = [
        (item.execution_time.hour, item.execution_time.minute, str(item.operation_mode_id))
        for item in plan.instructions
        if isinstance(item, DDBCInstruction)
    ]
    assert commands == [(0, 0, "OFF"), (1, 0, "RUN"), (1, 45, "OFF")]
