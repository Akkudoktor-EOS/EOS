"""Stress test of the heat plan solver (HiGHS) from short-lived and concurrent threads.

HiGHS crashes the process (access violation / segmentation fault) when a thread that solved
ends or when solves run concurrently, so ``heatplan.plan_heat`` hands every solve to one
long-lived worker. This script is the proof that the worker holds: it calls ``plan_heat`` at
least ``--solves`` times from threads that start, solve and end, one after the other and many at
once, and checks that every solution equals the reference. Run it as its own process; a crash of
the solver shows as an exit code other than 0 (no server, no port, nothing on disk):

    python tests/heatplan_stress.py [--solves 600]

With ``--leave-running`` the process ends while solves are still queued on the worker (the end of
the process with a running worker).

The script needs nothing but the ``akkudoktoreos`` package (no test modules, no pytest), so the
install script of a bundle can run it on the target machine before the service is stopped. It is
used by ``test_thermalsearch.py``.
"""

import argparse
import sys
import threading
from typing import Any

import numpy as np

from akkudoktoreos.devices.heatpump import HeatPumpElectricalSettings, HeatPumpProfile
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.optimization.kernel import Plan, PlanningProblem, simulate
from akkudoktoreos.optimization.objective import Objective, ObjectiveSettings
from akkudoktoreos.optimization.search import SearchSettings, heatplan
from akkudoktoreos.optimization.search.moves import MoveContext
from akkudoktoreos.thermal.planninginput import build_thermal_device
from akkudoktoreos.thermal.thermaldistribution import DirectDistributionSettings
from akkudoktoreos.thermal.thermalmodels import ThermalBuildingRC2Parameters, ThermalComfortBand

SLOTS = 48


def _profile() -> HeatPumpProfile:
    """Return a small air to air heat pump."""
    return HeatPumpProfile.model_validate(
        {
            "profile_id": "stress_air_to_air",
            "heat_pump_type": "air_to_air",
            "cop_points": [
                {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "cop": c}
                for t, c in [(-20, 2.0), (-7, 2.6), (2, 3.3), (7, 4.0), (20, 5.0)]
            ],
            "thermal_max_points": [
                {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "thermal_power_w": q}
                for t, q in [(-20, 4000.0), (-7, 5000.0), (2, 5800.0), (7, 6300.0), (20, 7000.0)]
            ],
            "thermal_min_points": [
                {
                    "outdoor_temperature_c": t,
                    "delivery_temperature_c": 20.0,
                    "thermal_power_w": 1200.0,
                }
                for t in (-20, 20)
            ],
            "provenance": {
                "source": "scenario",
                "quality": "user_supplied",
                "electric_power_basis": "total_system",
            },
        }
    )


def reference_call() -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Return the arguments of ``plan_heat`` for a small cold house (as the search passes them)."""
    unit = build_thermal_device(
        ThermalBuildingRC2Parameters(
            room_loss_per_hour=0.12,
            room_mass_exchange_per_hour=0.6,
            mass_capacity_ratio=4.0,
            heat_gain_kelvin_per_kwh=1.8,
        ),
        DirectDistributionSettings(),
        _profile(),
        outdoor_c=np.full(SLOTS, -3.0),
        comfort_bands=[
            ThermalComfortBand(
                min_temperature_c=20.0, target_temperature_c=21.0, max_temperature_c=23.0
            )
        ]
        * SLOTS,
        initial_temperature_c=[21.0, 21.0],
        delivery_temperature_c=20.0,
        electrical=HeatPumpElectricalSettings(
            standby_power_w=5.0,
            running_auxiliary_power_w=20.0,
            startup_energy_wh=30.0,
            extras_are_outside_cop=True,
        ),
        reference_room_c=21.0,
    )
    problem = PlanningProblem(
        slot_duration_h=0.25,
        pv_wh=np.zeros(SLOTS),
        load_wh=np.full(SLOTS, 60.0),
        import_price_per_wh=np.where(np.arange(SLOTS) % 24 < 8, 2e-4, 5e-4),
        feed_in_tariff_per_wh=np.zeros(SLOTS),
        inverters=(InverterParams(max_power_w=8000.0, battery_index=None),),
        thermal=(unit,),
    )
    objective = Objective(problem, ObjectiveSettings())
    captured: dict[str, Any] = {}
    original = heatplan.plan_heat

    def record(*args: Any, **kwargs: Any) -> Any:
        captured["call"] = (args, kwargs)
        return original(*args, **kwargs)

    heatplan.plan_heat = record  # type: ignore[assignment]
    try:
        context = MoveContext(problem, objective, SearchSettings())
        context.plan_thermal(Plan.idle(problem), simulate(problem, Plan.idle(problem)), 0)
    finally:
        heatplan.plan_heat = original  # type: ignore[assignment]
    args, kwargs = captured["call"]
    return args, kwargs


def run(solves: int, leave_running: bool) -> int:
    """Solve ``solves`` times from short-lived and concurrent threads; return an exit code."""
    args, kwargs = reference_call()
    expected = heatplan.plan_heat(*args, **kwargs)
    if expected is None:
        print("reference solve failed", file=sys.stderr)
        return 2
    failures: list[str] = []
    lock = threading.Lock()
    count = 0

    def solve() -> None:
        nonlocal count
        found = heatplan.plan_heat(*args, **kwargs)
        with lock:
            count += 1
            if found is None or not np.array_equal(found[0], expected[0]):
                failures.append("solution differs from the reference")

    # 1. one short-lived thread per solve, ending before the next one starts
    for _ in range(solves // 2):
        thread = threading.Thread(target=solve)
        thread.start()
        thread.join()
    # 2. many threads at once, in waves
    remaining = solves - solves // 2
    while remaining > 0:
        wave = [threading.Thread(target=solve) for _ in range(min(8, remaining))]
        for thread in wave:
            thread.start()
        for thread in wave:
            thread.join()
        remaining -= len(wave)
    if failures or count < solves:
        print(f"{len(failures)} failures in {count} solves: {failures[:3]}", file=sys.stderr)
        return 1
    print(f"ok: {count} solves from short-lived and concurrent threads")
    if leave_running:
        # The process ends while the worker still has solves queued.
        for _ in range(20):
            threading.Thread(
                target=heatplan.plan_heat, args=args, kwargs=kwargs, daemon=True
            ).start()
        print("leaving with solves queued")
    return 0


def main() -> None:
    """Parse the options and exit with the code of ``run``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solves", type=int, default=600)
    parser.add_argument("--leave-running", action="store_true")
    options = parser.parse_args()
    code = run(options.solves, options.leave_running)
    sys.stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
