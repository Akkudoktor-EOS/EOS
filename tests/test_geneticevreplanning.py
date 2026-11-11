"""EV charging that a client re-plans every few minutes (GENETIC).

A client runs the optimization again and again while the vehicle charges, for
example every five minutes, and applies the EV charge factor of the current
slot (slot 0) until the next run. A run that starts inside a slot only has the
rest of that slot left to charge in: the energy of slot 0 is the factor times
the charging power times that rest. The factor itself stays a share of the
charging power that the client applies for the rest of the slot.

The replay test follows a user report from an hourly grid: target 80 % by
04:00, the vehicle at 34 % at midnight, cheap import until 05:00.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.genetic.battery import Battery, ElectricVehicleParameters
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime

TIMEZONE = "Europe/Berlin"
DAY = "2026-11-08"
DEADLINE = f"{DAY}T04:00:00+01:00"
HORIZON_HOURS = 6
CAPACITY_WH = 63000  # 34 % -> 80 % stores 28.98 kWh
MAX_CHARGE_POWER_W = 10500
EIGHTHS = [index / 8 for index in range(9)]
CHEAP = 0.0001  # 0.10 per kWh, 00:00 - 05:00
EXPENSIVE = 0.0004  # 0.40 per kWh


@pytest.fixture
def night(config_eos: ConfigEOS, set_other_timezone: Callable[[str], str]) -> ConfigEOS:
    set_other_timezone(TIMEZONE)
    configure(config_eos)
    return config_eos


def configure(config: ConfigEOS, *, interval_sec: int = 3600) -> None:
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": HORIZON_HOURS,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval_sec,
                    "terminal_value_mode": "FIXED",
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )


def start_run(moment: str) -> None:
    """Start the energy management (and with it slot 0) at a local moment of the night."""
    get_ems(init=True).set_start_datetime(to_datetime(f"{DAY}T{moment}", in_timezone=TIMEZONE))


def night_parameters(
    soc_percentage: int, *, slots_per_hour: int = 1, **ev: Any
) -> GeneticOptimizationParameters:
    """Inputs from midnight on, as the API takes them: cheap until 05:00, no PV, no load."""
    slots = 48 * slots_per_hour
    prices = [CHEAP if slot < 5 * slots_per_hour else EXPENSIVE for slot in range(slots)]
    return GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": [0.0] * slots,
                "gesamtlast": [0.0] * slots,
                "strompreis_euro_pro_wh": prices,
                "einspeiseverguetung_euro_pro_wh": [0.0] * slots,
                "preis_euro_pro_wh_akku": 0.0,
            },
            "inverter": {"device_id": "inv", "max_power_wh": 20000},
            "forecast_interval_seconds": 3600 // slots_per_hour,
            "pv_battery": None,
            "ev": {
                "device_id": "car",
                "capacity_wh": CAPACITY_WH,
                "initial_soc_percentage": soc_percentage,
                "min_soc_percentage": 80,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "max_charge_power_w": MAX_CHARGE_POWER_W,
                "charge_rates": EIGHTHS,
                "min_soc_deadline_datetime": DEADLINE,
                **ev,
            },
        }
    )


def optimize(
    moment: str, soc_percentage: int, *, slots_per_hour: int = 1, **ev: Any
) -> tuple[GeneticOptimization, GeneticSolution]:
    """One small, seeded run at a local moment of the night."""
    start_run(moment)
    optimizer = GeneticOptimization(fixed_seed=7)
    solution = optimizer.optimize_ems(
        night_parameters(soc_percentage, slots_per_hour=slots_per_hour, **ev),
        ngen=15,
        individuals=40,
    )
    return optimizer, solution


def slot0_factor(solution: GeneticSolution) -> float:
    return (solution.ev_charge_hours_float or [0.0])[0]


# ---------------------------------------------------------------------------
# The EV device: slot 0 only for its rest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot_duration_h,share", [(1.0, 0.5), (0.25, 1 / 3)])
def test_ev_charges_only_the_rest_of_slot_0(slot_duration_h: float, share: float) -> None:
    """Hand-calculated: 8 kW charger, no losses. Slot 0 has ``share`` of it left:
    full power stores 8000 W * slot hours * share there, and the whole slot later."""
    ev = Battery(
        ElectricVehicleParameters(
            device_id="car",
            capacity_wh=40000,
            charging_efficiency=1.0,
            max_charge_power_w=8000,
            initial_soc_percentage=10,
            min_soc_percentage=80,
        ),
        prediction_hours=4,
        slot_duration_h=slot_duration_h,
        first_slot_share=share,
    )
    ev.charge_array[:] = 1
    full_slot_wh = 8000 * slot_duration_h
    assert ev.charge_energy(None, 0, charge_factor=1.0) == (pytest.approx(full_slot_wh * share), 0)
    assert ev.charge_energy(None, 1, charge_factor=0.5) == (pytest.approx(full_slot_wh / 2), 0)
    ev.reset()
    ev.charge_array[:] = 1
    # The share survives a reset (it belongs to the run, not to one candidate).
    assert ev.charge_energy(None, 0, charge_factor=0.5)[0] == pytest.approx(
        full_slot_wh * share / 2
    )


@pytest.mark.parametrize("share", [0.0, -0.1, 1.5])
def test_first_slot_share_outside_of_its_range_is_rejected(share: float) -> None:
    with pytest.raises(ValueError, match="first_slot_share"):
        Battery(
            ElectricVehicleParameters(device_id="car", capacity_wh=40000),
            prediction_hours=4,
            first_slot_share=share,
        )


@pytest.mark.parametrize(
    "interval_sec,moment,expected",
    [
        (3600, "14:00:00", 1.0),
        (3600, "14:10:00", 50 / 60),
        (3600, "03:58:00", 2 / 60),
        (900, "10:00:00", 1.0),
        (900, "10:07:00", 8 / 15),
        (900, "10:14:00", 1 / 15),
    ],
)
def test_current_slot_share_is_the_rest_of_the_slot(
    night: ConfigEOS, interval_sec: int, moment: str, expected: float
) -> None:
    configure(night, interval_sec=interval_sec)
    start_run(moment)
    optimizer = GeneticOptimization(fixed_seed=1)
    optimizer._slot0_datetime = optimizer.ems.start_datetime
    assert optimizer._current_slot_share() == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Runs inside the deadline's slot (60- and 15-minute grid)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slots_per_hour,moment,soc,rest_h,expected_factor",
    [
        # Hourly, run at 03:30: 74 % -> 80 % is 3780 Wh in 30 minutes, i.e. 72 %
        # of 10.5 kW; the next eighth is 0.75. Counting the whole hour asked for
        # 0.375, which stores only 1969 Wh until 04:00.
        (1, "03:30:00", 74, 0.5, 0.75),
        # 15 minutes, run at 03:50: 79 % -> 80 % is 630 Wh in 10 minutes, i.e.
        # 36 % of 10.5 kW -> 0.375. Counting the whole quarter asked for 0.25.
        (4, "03:50:00", 79, 10 / 60, 0.375),
    ],
)
def test_run_inside_the_last_slot_asks_for_the_factor_the_rest_needs(
    night: ConfigEOS,
    slots_per_hour: int,
    moment: str,
    soc: int,
    rest_h: float,
    expected_factor: float,
) -> None:
    configure(night, interval_sec=3600 // slots_per_hour)
    optimizer, solution = optimize(moment, soc, slots_per_hour=slots_per_hour)
    assert slot0_factor(solution) == pytest.approx(expected_factor)
    # The seed asks for the same factor.
    seed = optimizer._heuristic_ev_schedule(prefer_pv=False)
    assert optimizer.ev_possible_charge_values[seed[0]] == pytest.approx(expected_factor)
    # The simulated slot 0 stores the factor's power for the rest of the slot only,
    # and that reaches the target at the deadline (the start of slot 1).
    soc_per_slot = solution.result.ev_soc_per_hour
    stored_wh = (soc_per_slot[1] - soc_per_slot[0]) / 100.0 * CAPACITY_WH
    assert stored_wh == pytest.approx(expected_factor * MAX_CHARGE_POWER_W * rest_h)
    assert soc_per_slot[1] >= 80.0


def test_deadline_inside_the_current_slot_counts_its_share_of_the_rest(night: ConfigEOS) -> None:
    """Run at 03:20 for a departure at 03:40: slot 0 is charged for 40 minutes, the
    first 20 of them count. 74 % -> 80 % (3780 Wh) in 20 minutes needs 1.0 (3500 Wh
    stored by 03:40 is the most possible), as before: the deadline's share is now
    taken of the rest of the slot, which leaves the counted energy unchanged."""
    _, solution = optimize("03:20:00", 74, min_soc_deadline_datetime=f"{DAY}T03:40:00+01:00")
    assert slot0_factor(solution) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Replay of the user report: re-planned every five minutes
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """One run of the replay."""

    minute: int  # minutes after midnight
    soc_percentage: float  # true SoC at the run
    factor: float  # slot 0 factor the run returned


def replay(
    *, start_minute: int, end_minute: int, soc_percentage: float, step_minutes: int = 5
) -> tuple[float, list[Step]]:
    """Re-plan every few minutes and apply each run's slot 0 factor until the next run.

    The factor is applied at the charging power times the factor (what a client
    sends to the wallbox). The vehicle reports whole percent, rounded down here,
    so the target counts as reached only once it is.

    Returns:
        The SoC at ``end_minute`` and the runs.
    """
    steps: list[Step] = []
    for minute in range(start_minute, end_minute, step_minutes):
        hour, minute_of_hour = divmod(minute, 60)
        _, solution = optimize(
            f"{hour:02d}:{minute_of_hour:02d}:00", math.floor(soc_percentage + 1e-9)
        )
        factor = slot0_factor(solution)
        steps.append(Step(minute, soc_percentage, factor))
        stored_wh = factor * MAX_CHARGE_POWER_W * step_minutes / 60
        soc_percentage = min(soc_percentage + stored_wh / CAPACITY_WH * 100.0, 100.0)
    return soc_percentage, steps


def factors_per_hour(steps: list[Step]) -> dict[int, list[float]]:
    hours: dict[int, list[float]] = {}
    for step in steps:
        hours.setdefault(step.minute // 60, []).append(step.factor)
    return hours


def test_replanned_every_five_minutes_reaches_the_target_by_the_deadline(
    night: ConfigEOS,
) -> None:
    """The user report, replayed: 34 % at 00:00, 80 % due at 04:00, a run every 5 min.

    Every run counted the whole current hour as still ahead. The factor fell with
    each run inside an hour (03:00-04:00: 0.375 down to 0.125), and the vehicle
    reached only 78.6 % by 04:00. With only the rest of the current slot counted,
    the target is reached, and in the deadline's hour no run asks for less than
    the rest of the hour needs.
    """
    soc_at_deadline, steps = replay(start_minute=0, end_minute=4 * 60, soc_percentage=34.0)
    print(f"SoC at 04:00: {soc_at_deadline:.2f} %, factors per hour: {factors_per_hour(steps)}")
    assert soc_at_deadline >= 80.0
    for step in steps:
        if step.minute < 3 * 60 or step.soc_percentage >= 80.0:
            continue
        needed_wh = (80.0 - step.soc_percentage) / 100.0 * CAPACITY_WH
        rest_h = (4 * 60 - step.minute) / 60
        assert step.factor >= min(needed_wh / (MAX_CHARGE_POWER_W * rest_h), 1.0) - 1e-9, step
