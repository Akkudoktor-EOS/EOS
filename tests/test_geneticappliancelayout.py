"""Tests for the appliance gene layout of the GENETIC optimizer."""

from typing import Any

import pytest

from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticHomeAppliance,
)
from akkudoktoreos.optimization.genetic.geneticappliancelayout import (
    ApplianceGeneLayout,
    build_appliance_layout,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticHomeApplianceRequest,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


def _layout(
    slot0_datetime: DateTime, horizon_slots: int, **fields: Any
) -> ApplianceGeneLayout:
    """Lay out one DAILY appliance on an hourly grid starting at ``slot0_datetime``."""
    request = GeneticHomeApplianceRequest(
        **{
            "device_id": "dishwasher",
            "consumption_wh": 1000,
            "duration_h": 1,
            "num_cycles": 1,
            "schedule_mode": "DAILY",
            **fields,
        }
    )
    appliance = GeneticHomeAppliance(
        parameters=request.to_parameters(),
        optimization_hours=horizon_slots,
        prediction_hours=horizon_slots,
    )
    return build_appliance_layout(
        [appliance],
        slot0_datetime,
        start_slot=0,
        horizon_end_slot=horizon_slots,
        control_end_slot=horizon_slots,
        slot_duration_h=1.0,
        order_cache={},
    )


class TestBuildApplianceLayout:
    """A DAILY appliance whose open windows leave no room for a cycle it owes is unschedulable."""

    @pytest.mark.parametrize(
        "fields",
        [
            {
                "duration_h": 5,
                "shared_time_windows": {"windows": [{"start_time": "10:00", "end_time": "13:00"}]},
            },
            # The first hourly start in the window is 11:00; the run would end after 12:30.
            {
                "duration_h": 2,
                "shared_time_windows": {"windows": [{"start_time": "10:30", "end_time": "12:30"}]},
            },
            {"deadline_datetime": "2026-09-11T12:00:00Z", "deadline_policy": "STRICT"},
        ],
        ids=["run-longer-than-window", "window-off-the-slot-grid", "past-strict-deadline"],
    )
    def test_daily_appliance_without_any_start_is_rejected(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="has no valid start"):
            _layout(to_datetime("2026-09-12T00:00:00+00:00", in_timezone="UTC"), 48, **fields)

    @pytest.mark.parametrize(
        "start, horizon_slots, fields",
        [
            # 2026-09-14 is a Monday; the only window opens on Saturdays.
            (
                "2026-09-14T00:00:00+00:00",
                48,
                {
                    "shared_time_windows": {
                        "windows": [{"start_time": "10:00", "end_time": "13:00", "day_of_week": 5}]
                    }
                },
            ),
            ("2026-09-14T00:00:00+00:00", 48, {"earliest_start_datetime": "2026-09-20T10:00:00Z"}),
            # Today's cycle is done; the horizon ends at 11:00, inside tomorrow's window.
            (
                "2026-09-12T11:00:00+00:00",
                24,
                {
                    "duration_h": 2,
                    "completed_cycles": 1,
                    "shared_time_windows": {
                        "windows": [{"start_time": "10:00", "end_time": "13:00"}]
                    },
                },
            ),
        ],
        ids=[
            "window-on-another-weekday",
            "earliest-start-after-the-horizon",
            "horizon-ends-in-the-window",
        ],
    )
    def test_daily_appliance_without_an_open_window_is_kept_without_runs(
        self, start: str, horizon_slots: int, fields: dict[str, Any]
    ) -> None:
        """Nothing to plan in this horizon is not an unschedulable appliance."""
        layout = _layout(to_datetime(start, in_timezone="UTC"), horizon_slots, **fields)
        assert layout.n_genes == 0

    def test_daily_appliance_done_for_a_horizon_within_the_day_is_kept_without_runs(self) -> None:
        layout = _layout(
            to_datetime("2026-09-12T00:00:00+00:00", in_timezone="UTC"),
            24,
            completed_cycles=1,
            shared_time_windows={"windows": [{"start_time": "10:00", "end_time": "13:00"}]},
        )
        assert layout.n_genes == 0

    def test_daily_appliance_past_todays_window_runs_tomorrow(self) -> None:
        layout = _layout(
            to_datetime("2026-09-12T14:00:00+00:00", in_timezone="UTC"),
            24,
            shared_time_windows={"windows": [{"start_time": "10:00", "end_time": "13:00"}]},
        )
        assert [str(gene.run_date) for gene in layout.genes] == ["2026-09-13"]
