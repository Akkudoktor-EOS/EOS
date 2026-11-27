"""The native solution of the device by device optimizer as a serializable record.

Kept apart from the code that builds it, so that the server can name the type without
importing the compiled optimizer.
"""

from typing import Any, Optional

from pydantic import ConfigDict, Field

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import DateTime


class StorageControlLimits(PydanticBaseModel):
    """Storage limits and reference state used to interpret the solution factors."""

    capacity_wh: float
    initial_soc_percentage: float
    min_soc_percentage: float
    max_charge_power_w: float
    max_discharge_power_w: float
    max_grid_charge_power_dc_w: float | None = None
    discharging_efficiency: float


class PlanningWarning(PydanticBaseModel):
    """A machine readable note that part of the planning was left out or degraded.

    Codes: ``thermal_not_planned`` (the heat pumps listed in ``devices`` are not planned; the
    electrical planning ran without them, ``message`` names the cause);
    ``thermal_heat_plan_failed`` (the solver could not compute the heat plan of the heat pumps
    listed in ``devices`` during the search, the heat plan of the other results was kept,
    ``message`` gives how often; or the heat plan violated the allowed values and was replaced
    by the last valid heat plan or by no heat, ``message`` names the device);
    ``thermal_protection_violated`` (the planned room temperature of the zone of the heat pump
    listed in ``devices`` falls below its protection minimum, for example during a long heating
    block; ``message`` names the zone, the first slot and the lowest temperature);
    ``vehicle_target_unreachable`` (the plan charges the vehicle as much as it can but does not
    reach its target, ``message`` gives the state reached and the shortfall);
    ``vehicle_deadline_adjusted`` (the deadline of the vehicle has passed or is shorter than a
    slot and is judged at the end of the first slot);
    ``battery_soc_missing`` and ``vehicle_soc_missing`` (neither the request nor a fresh
    measurement gave the state of charge; the device is planned as 0 %).
    """

    code: str = Field(json_schema_extra={"description": "Stable identifier of the kind of note."})
    message: str = Field(json_schema_extra={"description": "Cause in plain words."})
    devices: list[str] = Field(
        default_factory=list, json_schema_extra={"description": "Device ids concerned."}
    )


class TailPlanSlot(PydanticBaseModel):
    """One slot of the optimal battery path inside the tail (diagnostic, never a control).

    The fields are those of the tail plan of the former optimizer; ``hour_from_start`` counts
    from the start of the control horizon's slot 0.
    """

    slot: int
    hour_from_start: float
    action: str
    alternative_action: str = ""
    decision_margin_euro: float = 0.0
    soc_start_percentage: float
    soc_end_percentage: float
    pv_wh: float
    load_wh: float
    grid_import_wh: float
    grid_export_wh: float
    battery_charge_wh: float
    battery_discharge_wh: float
    import_price_euro_per_kwh: float
    feed_in_tariff_euro_per_kwh: float
    slot_value_euro: float
    remaining_value_euro: float
    ac_charge_factor: float
    dc_charge_allowed: int
    discharge_allowed: int
    battery_grid_export_factor: float


class TailDiagnostics(PydanticBaseModel):
    """Forecast summary of the tail."""

    slots: int = 0
    slot_hours: float = 0.0
    soc_grid_points: int = 0
    min_import_price_euro_per_kwh: float = 0.0
    max_import_price_euro_per_kwh: float = 0.0
    min_feed_in_tariff_euro_per_kwh: float = 0.0
    max_feed_in_tariff_euro_per_kwh: float = 0.0
    negative_import_price_slots: int = 0
    positive_battery_export_slots: int = 0


class TerminalValueCurveReport(PydanticBaseModel):
    """Value of the usable battery energy at the end of the control horizon."""

    energy_wh: list[float]
    value_euro: list[float]
    operating_value_euro: list[float]
    continuation_value_euro: list[float]
    marginal_euro_per_kwh: list[float]
    residual_energy_wh: float
    window_slots: int


class TerminalValueReport(PydanticBaseModel):
    """What the planning credited for the battery energy at the end of the control horizon.

    The names are those of ``terminal_value`` of the former optimizer, so the consumers of that
    record keep working. ``tail_plan`` is the battery path in the tail after the control
    horizon, derived from the value curve; it stays empty without a tail (``effective_tail_hours``
    is 0), and ``reason`` then says why the tail is shorter than requested.
    """

    control_horizon_hours: float
    requested_tail_hours: float
    effective_tail_hours: float
    tail_end_hour: float
    continuation_mode: str
    mode: str
    battery_energy_wh: float
    credited_euro: float
    tail_operating_euro: float = 0.0
    continuation_value_euro: float = 0.0
    curve: Optional[TerminalValueCurveReport] = None
    continuation_curve: Optional[TerminalValueCurveReport] = None
    tail_diagnostics: Optional[TailDiagnostics] = None
    tail_plan: list[TailPlanSlot] = Field(default_factory=list)
    reason: str = ""


class Ohmrakel42Solution(PydanticBaseModel):
    """The native OHMRAKEL42 solution with the plan and its figures."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(json_schema_extra={"description": "Unique ID of the solution."})
    generated_at: DateTime = Field(json_schema_extra={"description": "Time of the solution."})
    start_datetime: DateTime = Field(
        json_schema_extra={"description": "Start of slot 0 (the run time rounded to the grid)."}
    )
    observation_datetime: DateTime = Field(
        json_schema_extra={"description": "Moment of the run, within slot 0."}
    )
    interval_seconds: int = Field(json_schema_extra={"description": "Duration of a slot [s]."})
    first_slot_fraction: float = Field(
        json_schema_extra={"description": "Part of slot 0 that was still ahead of the run."}
    )
    battery_ids: list[str] = Field(default_factory=list)
    vehicle_ids: list[str] = Field(default_factory=list)
    appliance_ids: list[str] = Field(default_factory=list)
    thermal_ids: list[str] = Field(default_factory=list)
    energy_management_plan_id: str | None = None
    optimization_solution_id: str | None = None
    storage_limits: dict[str, StorageControlLimits] = Field(
        default_factory=dict,
        description="Exact model inputs for the battery and vehicle controls, by device ID.",
    )
    controls: dict[str, list[float]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": (
                "The plan: per device and control one value per slot, keyed "
                "'<device id>_<control>' (grid_charge, discharge_limit, pv_charge, export, "
                "charge, heat_w); appliance starts are slots (-1 = not run) per cycle."
            )
        },
    )
    objective: dict[str, float] = Field(
        default_factory=dict,
        json_schema_extra={"description": "The parts of the objective of the plan [EUR]."},
    )
    start_objective_eur: float = Field(
        default=0.0, json_schema_extra={"description": "Objective of the start plan [EUR]."}
    )
    statistics: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"description": "Search effort: passes, moves, simulations, time."},
    )
    terminal_value: Optional[TerminalValueReport] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Valuation of the battery energy at the end of the control horizon with the "
                "tail after it: curve, figures and the battery path of the tail (null without "
                "a battery)."
            )
        },
    )
    warm_started: bool = Field(
        default=False, json_schema_extra={"description": "The search started from the last plan."}
    )
    warnings: list[PlanningWarning] = Field(
        default_factory=list,
        json_schema_extra={
            "description": (
                "Parts of the planning that were left out, degraded or adjusted, for example "
                "heat pumps that could not be planned (code thermal_not_planned) while the "
                "rest was, or a vehicle target that is out of reach "
                "(code vehicle_target_unreachable)."
            )
        },
    )
