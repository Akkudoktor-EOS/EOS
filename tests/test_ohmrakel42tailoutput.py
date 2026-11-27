"""The tail plan of the lookahead is part of the solution and of the compatible response.

The tail plan is derived forwards from the value curve the search used; it must not change
what is planned. It follows the decisions of the backward recursion, respects the battery
limits and says honestly when the tail is shorter than asked for.
"""

from typing import Any, Optional

import numpy as np
import pytest
from test_requestfixtures import (  # noqa: F401  (fake_measurement_fixture registers the fixture)
    fake_measurement_fixture,
    forecast_series,
    resolve_task,
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.optimization import solution as solution_module
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.planner import run_planning
from akkudoktoreos.optimization.solution import PlanningOutcome
from akkudoktoreos.optimization.solutionmodel import TerminalValueReport
from akkudoktoreos.optimization.tailvalue import tail_plan
from akkudoktoreos.server.rest.optimizecompatmodels import ConfigOptimizationResponse

DEVICES: dict[str, Any] = {
    "batteries": {
        "bat1": {
            "profile_adjustment": {"nominal_capacity_wh": 8000, "max_charge_power_w": 5000},
            "charge_rates": [0.0, 0.25, 0.5, 0.75, 1.0],
            "min_soc_percentage": 10,
            "discharge_rates": [0.5, 1.0],
            "adapter_supports_discharge_limit": True,
            "levelized_cost_of_storage_amt_kwh": 0.02,
        }
    },
    "inverters": {"inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}},
}
SOC = {"bat1": 40}


def optimization(horizon: int, tail: int, mode: str = "AUTO") -> dict[str, Any]:
    """Return the optimization block with a control horizon and a tail."""
    return {
        "optimization": {
            "algorithm": "OHMRAKEL42",
            "genetic": {
                "horizon_hours": horizon,
                "tail_horizon_hours": tail,
                "terminal_value_mode": mode,
                "interval_sec": 3600,
            },
        }
    }


async def planned(
    config_eos: ConfigEOS, horizon: int = 12, tail: int = 24, hours: int | None = None
) -> PlanningOutcome:
    """Plan the test input with the given horizon and tail; ``hours`` cuts the forecasts."""
    series = forecast_series("optimize_input_1.json", 3600)
    if hours is not None:
        series = {key: values[:hours] for key, values in series.items()}
    task = await resolve_task(
        config_eos, DEVICES, SOC, extra=optimization(horizon, tail), forecasts=series
    )
    return run_planning(task)


def report_of(outcome: PlanningOutcome) -> TerminalValueReport:
    """Return the terminal value report of an outcome."""
    report = outcome.solution.terminal_value
    assert report is not None
    return report


@pytest.mark.asyncio
async def test_tail_plan_covers_the_tail_with_the_old_fields(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    outcome = await planned(config_eos, horizon=12, tail=24)
    report = report_of(outcome)
    assert report.mode == "TAIL"
    assert (report.control_horizon_hours, report.requested_tail_hours) == (12.0, 24.0)
    assert (report.effective_tail_hours, report.tail_end_hour) == (24.0, 36.0)
    assert report.reason == ""
    plan = report.tail_plan
    assert [slot.slot for slot in plan] == list(range(24))
    assert [slot.hour_from_start for slot in plan] == [12.0 + i for i in range(24)]
    assert report.tail_diagnostics is not None and report.tail_diagnostics.slots == 24
    assert report.curve is not None and len(report.curve.energy_wh) == 101
    # The compatible response carries this very record (one type, no second format).
    field = ConfigOptimizationResponse.model_fields["terminal_value"]
    assert field.annotation == Optional[TerminalValueReport]
    legacy = report.model_dump()
    assert {
        "tail_plan",
        "tail_diagnostics",
        "curve",
        "tail_operating_euro",
        "continuation_value_euro",
    } <= set(legacy)
    assert legacy["tail_plan"][3]["hour_from_start"] == 15.0


@pytest.mark.asyncio
async def test_tail_plan_is_consistent_with_the_value_curve_and_the_battery_limits(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    outcome = await planned(config_eos, horizon=12, tail=24)
    report = report_of(outcome)
    limits = outcome.solution.storage_limits["bat1"]
    plan = report.tail_plan
    assert plan[0].soc_start_percentage == pytest.approx(
        100
        * (
            limits.min_soc_percentage / 100 * limits.capacity_wh
            + report.battery_energy_wh
            / (
                limits.discharging_efficiency
                * outcome.task.problem.inverters[0].dc_to_ac_efficiency
            )
        )
        / limits.capacity_wh,
        rel=0.1,
    )
    for before, after in zip(plan, plan[1:]):
        assert after.soc_start_percentage == pytest.approx(before.soc_end_percentage)
    for slot in plan:
        assert limits.min_soc_percentage - 1e-6 <= slot.soc_end_percentage <= 100 + 1e-6
        assert slot.battery_charge_wh >= 0 and slot.battery_discharge_wh >= 0
        assert slot.grid_import_wh >= 0 and slot.grid_export_wh >= 0
        # The energy of the slot is what the battery moved.
        delta = (slot.soc_end_percentage - slot.soc_start_percentage) / 100 * limits.capacity_wh
        assert delta == pytest.approx(
            slot.battery_charge_wh - slot.battery_discharge_wh, abs=1e-6, rel=1e-6
        )
        assert slot.alternative_action != slot.action
        assert slot.decision_margin_euro >= 0
    # The path earns what the curve promises for the state it starts in (grid interpolation).
    curve = report.curve
    assert curve is not None
    promised = float(np.interp(report.battery_energy_wh, curve.energy_wh, curve.value_euro))
    walked = sum(slot.slot_value_euro for slot in plan) + plan[-1].remaining_value_euro
    assert walked == pytest.approx(promised, abs=0.02 + 0.02 * abs(promised))
    assert plan[0].slot_value_euro + plan[0].remaining_value_euro == pytest.approx(
        promised, abs=0.02 + 0.02 * abs(promised)
    )
    # Operating and continuation parts add up to the credit.
    assert report.tail_operating_euro + report.continuation_value_euro == pytest.approx(
        report.credited_euro
    )


@pytest.mark.asyncio
async def test_the_output_changes_neither_the_plan_nor_the_objective(
    config_eos: ConfigEOS, fake_measurement: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_tail = await planned(config_eos, horizon=12, tail=24)
    monkeypatch.setattr(solution_module, "tail_plan", lambda *args, **kwargs: [])
    monkeypatch.setattr(solution_module, "tail_diagnostics", lambda *args, **kwargs: None)
    without = await planned(config_eos, horizon=12, tail=24)
    assert without.solution.terminal_value is not None
    assert without.solution.terminal_value.tail_plan == []
    assert with_tail.solution.controls == without.solution.controls
    assert with_tail.solution.objective == without.solution.objective
    assert with_tail.plan.battery_grid_charge.tolist() == without.plan.battery_grid_charge.tolist()
    assert with_tail.breakdown == without.breakdown
    for key in ("battery_energy_wh", "credited_euro", "tail_operating_euro"):
        assert getattr(report_of(with_tail), key) == getattr(report_of(without), key)


@pytest.mark.asyncio
async def test_a_shortened_tail_is_reported_as_such(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    outcome = await planned(config_eos, horizon=12, tail=24, hours=30)
    report = report_of(outcome)
    assert (report.requested_tail_hours, report.effective_tail_hours) == (24.0, 8.0)
    assert report.tail_end_hour == 20.0  # control 12 h + 8 h
    assert len(report.tail_plan) == 8
    assert report.tail_plan[-1].hour_from_start == 19.0
    assert "Tail shortened" in report.reason and "24 h" in report.reason and "8 h" in report.reason
    assert report.tail_diagnostics is not None and report.tail_diagnostics.slots == 8


@pytest.mark.asyncio
async def test_a_missing_tail_gives_an_empty_plan_and_says_why(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    outcome = await planned(config_eos, horizon=12, tail=24, hours=22)
    report = report_of(outcome)
    assert report.effective_tail_hours == 0.0 and report.requested_tail_hours == 24.0
    assert report.tail_plan == [] and report.tail_diagnostics is None
    assert "No tail" in report.reason
    assert report.tail_operating_euro == 0.0  # only the continuation value remains
    none_requested = report_of(await planned(config_eos, horizon=12, tail=0))
    assert none_requested.tail_plan == []
    assert "No tail requested" in none_requested.reason


@pytest.mark.asyncio
async def test_a_curve_without_lookahead_has_no_trace(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    task = await resolve_task(
        config_eos,
        DEVICES,
        SOC,
        extra=optimization(12, 24),
        forecasts=forecast_series("optimize_input_1.json", 3600),
    )
    curve = Objective(task.problem, task.objective).curve
    assert curve.trace is not None
    assert len(tail_plan(curve, 5000.0, 12.0)) == 24
    assert tail_plan(type(curve)(), 1000.0, 12.0) == []
