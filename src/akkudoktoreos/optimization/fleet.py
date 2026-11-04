"""Explicit-input multi-device optimization on a shared AC bus.

Power is AC-side watts, energy is stored Wh and prices are EUR/kWh.
This module has no configuration, measurement or actuator side effects.
"""

from collections.abc import Mapping
from typing import Literal, Self

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class FleetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FleetDevice(FleetModel):
    device_id: str = Field(min_length=1)
    kind: Literal["battery", "ev"]
    capacity_wh: float = Field(gt=0)
    max_energy_wh: float | None = Field(default=None, ge=0)
    initial_energy_wh: float = Field(ge=0)
    reserve_wh: float = Field(default=0, ge=0)
    max_charge_power_w: float = Field(ge=0)
    min_charge_power_w: float = Field(default=0, ge=0)
    max_discharge_power_w: float = Field(default=0, ge=0)
    charge_efficiency: float = Field(default=1, gt=0, le=1)
    discharge_efficiency: float = Field(default=1, gt=0, le=1)
    available: list[bool] | None = None
    charge_levels_w: list[float] | None = Field(default=None, max_length=64)
    target_energy_wh: float = Field(default=0, ge=0)
    target_slot: int | None = Field(default=None, ge=1)
    terminal_value_eur_per_kwh: float = Field(default=0, ge=0)
    discharge_cost_eur_per_kwh: float = Field(default=0, ge=0)
    power_group: str | None = None

    @model_validator(mode="after")
    def check_energy(self) -> Self:
        if max(self.initial_energy_wh, self.reserve_wh, self.target_energy_wh) > self.capacity_wh:
            raise ValueError("Initial energy, reserve and target must not exceed capacity")
        maximum = self.capacity_wh if self.max_energy_wh is None else self.max_energy_wh
        if (
            maximum > self.capacity_wh
            or max(self.initial_energy_wh, self.reserve_wh, self.target_energy_wh) > maximum
        ):
            raise ValueError("Initial energy, reserve and target must not exceed maximum energy")
        if self.min_charge_power_w > self.max_charge_power_w:
            raise ValueError("Minimum charging power exceeds maximum charging power")
        if self.kind == "ev" and self.max_discharge_power_w:
            raise ValueError("EV discharge/V2G is not supported")
        if self.charge_levels_w is not None:
            levels = self.charge_levels_w
            if not levels or 0 not in levels or len(set(levels)) != len(levels):
                raise ValueError("Charge levels must be unique and include zero")
            if any(p < 0 or p > self.max_charge_power_w or not np.isfinite(p) for p in levels):
                raise ValueError("Charge levels must be finite and within the power rating")
            if any(0 < p < self.min_charge_power_w for p in levels):
                raise ValueError("Charge levels below minimum charging power")
        return self


class FleetPowerGroup(FleetModel):
    """Shared AC converter/charger limits, not a model of a DC bus."""

    group_id: str = Field(min_length=1)
    max_charge_power_w: float = Field(ge=0)
    max_discharge_power_w: float = Field(ge=0)


class FleetRequest(FleetModel):
    start_datetime: AwareDatetime
    interval_seconds: Literal[900, 3600] = 900
    load_w: list[float] = Field(min_length=1, max_length=384)
    pv_w: list[float]
    import_price_eur_per_kwh: list[float]
    export_price_eur_per_kwh: list[float]
    import_limit_w: float = Field(ge=0)
    export_limit_w: float = Field(ge=0)
    devices: list[FleetDevice] = Field(default_factory=list, max_length=32)
    power_groups: list[FleetPowerGroup] = Field(default_factory=list, max_length=32)
    allow_battery_export: bool = False
    solver_time_limit_seconds: float = Field(default=30, gt=0, le=120)

    @model_validator(mode="after")
    def check_horizon(self) -> Self:
        n = len(self.load_w)
        arrays = [
            self.load_w,
            self.pv_w,
            self.import_price_eur_per_kwh,
            self.export_price_eur_per_kwh,
        ]
        if any(len(a) != n or any(not np.isfinite(v) for v in a) for a in arrays):
            raise ValueError("Forecasts must be finite and have equal lengths")
        if min(self.load_w + self.pv_w) < 0:
            raise ValueError("Load and PV must be nonnegative AC power")
        if len({d.device_id for d in self.devices}) != len(self.devices):
            raise ValueError("Device IDs must be unique")
        groups = {g.group_id for g in self.power_groups}
        if len(groups) != len(self.power_groups):
            raise ValueError("Power group IDs must be unique")
        for d in self.devices:
            if d.available is not None and len(d.available) != n:
                raise ValueError("Availability must match the horizon")
            if d.target_slot is not None and d.target_slot > n:
                raise ValueError("Target slot must lie within the horizon")
            if d.power_group is not None and d.power_group not in groups:
                raise ValueError("Unknown power group")
        return self


class FleetDevicePlan(FleetModel):
    charge_w: list[float]
    discharge_w: list[float]
    energy_wh: list[float]  # n+1 interval boundaries, including initial energy


class FleetSolution(FleetModel):
    status: Literal["optimal", "feasible", "infeasible", "limit", "error"]
    message: str
    start_datetime: AwareDatetime
    interval_seconds: int
    objective_eur: float | None = None
    relative_gap: float | None = None
    grid_cost_eur: float | None = None
    import_w: list[float] = Field(default_factory=list)
    export_w: list[float] = Field(default_factory=list)
    curtailed_pv_w: list[float] = Field(default_factory=list)
    devices: dict[str, FleetDevicePlan] = Field(default_factory=dict)
    parameters: FleetRequest | None = None


def optimize_fleet(request: FleetRequest) -> FleetSolution:
    """Solve a mixed integer energy balance; never return an infeasible schedule.

    Reserve limits withdrawal, including when initial energy is below reserve.
    Targets are required at the END of target_slot (default: horizon end).
    EV availability affects charging; driving consumption must be represented
    in a subsequent request's initial energy, not inferred here.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    n = len(request.load_w)
    hours = request.interval_seconds / 3600
    lower: list[float] = []
    upper: list[float] = []
    costs: list[float] = []
    integer: list[int] = []
    rows: list[int] = []
    cols: list[int] = []
    values: list[float] = []
    row_lower: list[float] = []
    row_upper: list[float] = []

    def var(size: int, cap: float, cost: float = 0, binary: bool = False) -> list[int]:
        ids = list(range(len(lower), len(lower) + size))
        lower.extend([0.0] * size)
        upper.extend([cap] * size)
        costs.extend([cost] * size)
        integer.extend([int(binary)] * size)
        return ids

    def constraint(terms: Mapping[int, float], lo: float, hi: float) -> None:
        for col, value in terms.items():
            rows.append(len(row_lower))
            cols.append(col)
            values.append(value)
        row_lower.append(lo)
        row_upper.append(hi)

    imports = var(n, request.import_limit_w)
    exports = var(n, request.export_limit_w)
    curtailed = var(n, max(request.pv_w))
    grid_direction = var(n, 1, binary=True)
    balances = []
    for t in range(n):
        costs[imports[t]] = request.import_price_eur_per_kwh[t] * hours / 1000
        costs[exports[t]] = -request.export_price_eur_per_kwh[t] * hours / 1000
        upper[curtailed[t]] = request.pv_w[t]
        constraint({imports[t]: 1, grid_direction[t]: -request.import_limit_w}, -np.inf, 0)
        constraint(
            {exports[t]: 1, grid_direction[t]: request.export_limit_w},
            -np.inf,
            request.export_limit_w,
        )
        balances.append({imports[t]: 1, exports[t]: -1, curtailed[t]: -1})

    device_vars = {}
    for d in request.devices:
        charge = var(n, d.max_charge_power_w)
        discharge = var(n, d.max_discharge_power_w, hours * d.discharge_cost_eur_per_kwh / 1000)
        energy = var(n + 1, d.capacity_wh if d.max_energy_wh is None else d.max_energy_wh)
        direction = var(n, 1, binary=True)
        lower[energy[0]] = upper[energy[0]] = d.initial_energy_wh
        costs[energy[-1]] = -d.terminal_value_eur_per_kwh / 1000
        lower[energy[d.target_slot or n]] = max(
            lower[energy[d.target_slot or n]], d.target_energy_wh
        )
        for t in range(n):
            balances[t][charge[t]] = -1
            balances[t][discharge[t]] = 1
            if d.available is not None and not d.available[t]:
                upper[charge[t]] = upper[discharge[t]] = 0
            constraint({charge[t]: 1, direction[t]: -d.max_charge_power_w}, -np.inf, 0)
            if d.min_charge_power_w:
                charging = var(1, 1, binary=True)[0]
                constraint({charge[t]: 1, charging: -d.max_charge_power_w}, -np.inf, 0)
                constraint({charge[t]: 1, charging: -d.min_charge_power_w}, 0, np.inf)
            constraint(
                {discharge[t]: 1, direction[t]: d.max_discharge_power_w},
                -np.inf,
                d.max_discharge_power_w,
            )
            constraint(
                {
                    energy[t + 1]: 1,
                    energy[t]: -1,
                    charge[t]: -hours * d.charge_efficiency,
                    discharge[t]: hours / d.discharge_efficiency,
                },
                0,
                0,
            )
            # Conditional reserve: no invented energy when starting below reserve.
            constraint(
                {
                    energy[t]: 1,
                    discharge[t]: -hours / d.discharge_efficiency,
                    direction[t]: d.capacity_wh,
                },
                d.reserve_wh,
                np.inf,
            )
            if d.charge_levels_w is not None:
                choices = var(len(d.charge_levels_w), 1, binary=True)
                constraint(dict.fromkeys(choices, 1), 1, 1)
                constraint(
                    {charge[t]: 1, **{i: -p for i, p in zip(choices, d.charge_levels_w)}}, 0, 0
                )
        device_vars[d.device_id] = (charge, discharge, energy)

    if not request.allow_battery_export:
        for t in range(n):
            terms = {
                device_vars[d.device_id][1][t]: 1.0 for d in request.devices if d.kind == "battery"
            }
            terms.update(
                {device_vars[d.device_id][0][t]: -1.0 for d in request.devices if d.kind == "ev"}
            )
            constraint(terms, -np.inf, request.load_w[t])
            for d in request.devices:
                if d.kind == "battery":
                    constraint(
                        {
                            device_vars[d.device_id][1][t]: 1,
                            grid_direction[t]: -d.max_discharge_power_w,
                        },
                        -np.inf,
                        0,
                    )

    for g in request.power_groups:
        direction = var(n, 1, binary=True)
        members = [device_vars[d.device_id] for d in request.devices if d.power_group == g.group_id]
        for t in range(n):
            constraint(
                {**{c[t]: 1 for c, _, _ in members}, direction[t]: -g.max_charge_power_w},
                -np.inf,
                0,
            )
            constraint(
                {**{s[t]: 1 for _, s, _ in members}, direction[t]: g.max_discharge_power_w},
                -np.inf,
                g.max_discharge_power_w,
            )
    for t in range(n):
        net_load = request.load_w[t] - request.pv_w[t]
        constraint(balances[t], net_load, net_load)

    matrix = coo_matrix(
        (np.array(values), (np.array(rows), np.array(cols))),
        shape=(len(row_lower), len(lower)),
    ).tocsc()
    result = milp(
        np.array(costs),
        integrality=np.array(integer),
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, row_lower, row_upper),
        options={"time_limit": request.solver_time_limit_seconds, "mip_rel_gap": 0.0},
    )
    status: Literal["optimal", "infeasible", "limit", "error"] = "error"
    if result.status == 0:
        status = "optimal"
    elif result.status == 1:
        status = "limit"
    elif result.status == 2:
        status = "infeasible"
    solution = FleetSolution(
        status=status,
        message=str(result.message),
        start_datetime=request.start_datetime,
        interval_seconds=request.interval_seconds,
    )
    if result.status not in (0, 1) or result.x is None:
        return solution
    x = result.x
    if x is None or not np.all(np.isfinite(x)) or not np.isfinite(result.fun):
        solution.status = "error"
        solution.message = "Solver returned nonfinite output"
        return solution
    # Independently check numerical feasibility before exposing a schedule.
    ax = matrix @ x
    if (
        np.any(x < np.array(lower) - 1e-5)
        or np.any(x > np.array(upper) + 1e-5)
        or np.any(ax < np.array(row_lower) - 1e-5)
        or np.any(ax > np.array(row_upper) + 1e-5)
        or np.any(
            np.abs(x[np.array(integer, dtype=bool)] - np.round(x[np.array(integer, dtype=bool)]))
            > 1e-5
        )
    ):
        solution.status = "error"
        solution.message = "Solver output failed feasibility verification"
        return solution
    solution.objective_eur = float(result.fun)
    solution.parameters = request
    if result.status == 1:
        solution.status = "feasible"
    gap = getattr(result, "mip_gap", None)
    if gap is not None and np.isfinite(gap):
        solution.relative_gap = float(gap)
    solution.grid_cost_eur = float(sum(costs[i] * x[i] for i in imports + exports))
    solution.import_w = x[imports].tolist()
    solution.export_w = x[exports].tolist()
    solution.curtailed_pv_w = x[curtailed].tolist()
    solution.devices = {
        key: FleetDevicePlan(
            charge_w=x[c].tolist(), discharge_w=x[s].tolist(), energy_wh=x[e].tolist()
        )
        for key, (c, s, e) in device_vars.items()
    }
    return solution
