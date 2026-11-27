"""Simulation of a plan: single run, partial run from a slot, and batch evaluation."""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from akkudoktoreos.devices.models import thermal as thm
from akkudoktoreos.optimization.kernel import core
from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem


@dataclass(frozen=True)
class ThermalStart:
    """State of the thermal devices at the start of a slot (for a simulation from there).

    Attributes:
        temperature_c: Temperatures per thermal device.
        share: Run share of each heat pump in the slot before (0 ... 1).
    """

    temperature_c: tuple[np.ndarray, ...]
    share: tuple[float, ...]


@dataclass(frozen=True, eq=False)
class ThermalResult:
    """Trajectories of the thermal devices of a simulated plan.

    Attributes:
        start_slot: First simulated slot.
        temperature_c: Per device ``(slots - start_slot + 1, states)``: temperatures at the start
            of every simulated slot and after the last one [°C]; room first.
        heat_w: ``(devices, slots - start_slot)`` heat power [W].
        electric_wh: Electric energy per slot [Wh] (also in the load of the house).
        share: ``(devices, slots - start_slot)`` run share (heat over the highest heat of the slot).
        under_kh: Kelvin hours below the comfort minimum, per device.
        over_kh: Kelvin hours above the comfort maximum.
        protection_kh: Kelvin hours below the protection minimum.
        initial_share: Run share of each heat pump before ``start_slot``.
    """

    start_slot: int
    temperature_c: tuple[np.ndarray, ...]
    heat_w: np.ndarray
    electric_wh: np.ndarray
    share: np.ndarray
    under_kh: np.ndarray
    over_kh: np.ndarray
    protection_kh: np.ndarray
    initial_share: tuple[float, ...]

    @property
    def started(self) -> np.ndarray:
        """Return 1.0 where a slot switches the heat pump on (share from 0 to above 0).

        Derived from the run share for reporting (the NUC trial script reads it); the start
        energy itself is the continuous ramp of the share, not this flag.
        """
        before = np.concatenate(
            (np.array(self.initial_share, dtype=float)[:, None], self.share[:, :-1]), axis=1
        )
        return np.asarray(
            (self.share > thm.RUN_TOLERANCE_W) & (before <= thm.RUN_TOLERANCE_W), dtype=float
        )

    def state_at(self, slot: int) -> ThermalStart:
        """Return the thermal state at the start of ``slot`` (within the simulated range)."""
        index = slot - self.start_slot
        if not 0 <= index <= self.heat_w.shape[1]:
            raise ValueError(f"Slot {slot} is outside of the simulated thermal range.")
        share = tuple(
            float(self.share[d, index - 1]) if index > 0 else self.initial_share[d]
            for d in range(self.heat_w.shape[0])
        )
        return ThermalStart(tuple(t[index].copy() for t in self.temperature_c), share)


@dataclass(frozen=True, eq=False)
class SimulationResult:
    """Time series and totals of a simulated plan.

    Per-slot arrays cover the simulated slots ``start_slot ... slots - 1``. Device arrays
    are ordered batteries first, then vehicles, shape ``(devices, slots)``.

    Attributes:
        start_slot: First simulated slot.
        load_wh: Total load [Wh], including appliances, vehicle charging and grid charging.
        grid_import_wh: Grid import [Wh].
        grid_export_wh: Grid feed-in [Wh].
        cost_eur: Import cost plus storage cost per slot [EUR].
        revenue_eur: Feed-in revenue per slot [EUR].
        losses_wh: Conversion, charging, curtailment and unpaid feed-in losses [Wh].
        appliance_wh: Appliance load [Wh].
        import_price_per_wh: Import price [EUR/Wh].
        feed_in_tariff_per_wh: Feed-in tariff [EUR/Wh].
        import_excess_wh: Import above the grid connection limit per slot [Wh] (0 without limit).
        export_curtailed_wh: Feed-in the inverter curtailed because of the feed-in limit [Wh].
        energy_balance_residual_wh: Sources minus sinks per slot; zero up to rounding.
        vehicle_loss_wh: Charging losses of the vehicles [Wh].
        soc_pct: Battery state of charge at the start of each slot [%].
        vehicle_soc_pct: Vehicle state of charge at the start of each slot [%].
        soc_wh: Storage state of charge at the start of each slot [Wh], batteries then vehicles.
        battery_power_w: Net battery power at the DC side, positive charging [W].
        battery_discharge_ac_wh: Energy the batteries deliver at the AC side [Wh].
        battery_pv_charge_wh: Raw energy batteries take from PV surplus [Wh].
        battery_grid_charge_ac_wh: AC energy drawn from the grid to charge batteries [Wh].
        vehicle_power_w: Vehicle charge power at the vehicle side [W].
        charge_raw_wh: Raw charge energy per storage and slot [Wh].
        charge_stored_wh: Stored charge energy per storage and slot [Wh].
        discharge_raw_wh: Energy taken from each storage per slot [Wh].
        discharge_dc_wh: Energy delivered at the DC side per storage and slot [Wh].
        final_soc_wh: State of charge of the storages after the last slot [Wh].
        total_cost_eur: Sum of ``cost_eur``.
        total_revenue_eur: Sum of ``revenue_eur``.
        total_balance_eur: Cost minus revenue (the objective of the former simulation).
        total_losses_wh: Sum of ``losses_wh``.
        total_import_excess_wh: Sum of ``import_excess_wh``.
        total_export_curtailed_wh: Sum of ``export_curtailed_wh``.
        thermal: Trajectories of the thermal devices, None without thermal devices.
    """

    start_slot: int
    load_wh: np.ndarray
    grid_import_wh: np.ndarray
    grid_export_wh: np.ndarray
    cost_eur: np.ndarray
    revenue_eur: np.ndarray
    losses_wh: np.ndarray
    appliance_wh: np.ndarray
    import_price_per_wh: np.ndarray
    feed_in_tariff_per_wh: np.ndarray
    import_excess_wh: np.ndarray
    export_curtailed_wh: np.ndarray
    energy_balance_residual_wh: np.ndarray
    vehicle_loss_wh: np.ndarray
    soc_pct: np.ndarray
    vehicle_soc_pct: np.ndarray
    soc_wh: np.ndarray
    battery_power_w: np.ndarray
    battery_discharge_ac_wh: np.ndarray
    battery_pv_charge_wh: np.ndarray
    battery_grid_charge_ac_wh: np.ndarray
    vehicle_power_w: np.ndarray
    charge_raw_wh: np.ndarray
    charge_stored_wh: np.ndarray
    discharge_raw_wh: np.ndarray
    discharge_dc_wh: np.ndarray
    final_soc_wh: np.ndarray
    total_cost_eur: float
    total_revenue_eur: float
    total_balance_eur: float
    total_losses_wh: float
    total_import_excess_wh: float
    total_export_curtailed_wh: float
    thermal: Optional[ThermalResult] = None


def initial_state(problem: PlanningProblem) -> np.ndarray:
    """Return the state of charge of all storages at slot 0 [Wh], batteries then vehicles."""
    return np.array(problem.kernel_arrays()["initial_soc_wh"], dtype=float)


def _plan_arrays(problem: PlanningProblem, plan: Plan) -> tuple[np.ndarray, ...]:
    plan.validate(problem)
    return (
        np.ascontiguousarray(plan.battery_grid_charge, dtype=float),
        np.ascontiguousarray(plan.battery_discharge_limit, dtype=float),
        np.ascontiguousarray(plan.battery_pv_charge, dtype=float),
        np.ascontiguousarray(plan.battery_export, dtype=float),
        np.ascontiguousarray(plan.vehicle_charge, dtype=float),
        np.ascontiguousarray(plan.appliance_starts, dtype=np.int64),
    )


def initial_thermal_state(problem: PlanningProblem) -> ThermalStart:
    """Return the state of the thermal devices at slot 0."""
    return ThermalStart(
        tuple(unit.initial_temperature_c.copy() for unit in problem.thermal),
        tuple(1.0 if unit.initial_running else 0.0 for unit in problem.thermal),
    )


def simulate_thermal(
    problem: PlanningProblem,
    plan: Plan,
    start_slot: int = 0,
    start: Optional[ThermalStart] = None,
) -> ThermalResult:
    """Simulate the thermal devices of a plan from ``start_slot`` on.

    Raises:
        ValueError: If a heat power lies outside the operating table of its slot.
    """
    slots = problem.slots
    length = slots - start_slot
    devices = len(problem.thermal)
    if start is None:
        if start_slot != 0:
            raise ValueError("A thermal simulation from a later slot needs its start state.")
        start = initial_thermal_state(problem)
    temperatures = []
    heat = np.zeros((devices, length))
    electric = np.zeros((devices, length))
    share = np.zeros((devices, length))
    measures = np.zeros((devices, 3))
    for device, unit in enumerate(problem.thermal):
        trajectory = np.zeros((length + 1, unit.states))
        heat_row = np.ascontiguousarray(plan.thermal_heat_w[device], dtype=float)
        status = thm.run_thermal(
            unit.arrays(),
            start_slot,
            np.ascontiguousarray(start.temperature_c[device], dtype=float),
            start.share[device],
            heat_row,
            trajectory,
            electric[device],
            share[device],
        )
        if status != thm.STATUS_OK:
            raise ValueError(
                f"Thermal device {device}: the heat {heat_row[status - 1]:.1f} W of slot "
                f"{status - 1} is outside of its operating table."
            )
        thm.comfort_hours(
            trajectory,
            start_slot,
            unit.comfort_min_c,
            unit.comfort_max_c,
            unit.protection_min_c,  # type: ignore[arg-type]
            unit.slot_hours,
            unit.under_weight,  # type: ignore[arg-type]
            measures[device],
        )
        temperatures.append(trajectory)
        heat[device] = heat_row[start_slot:]
    return ThermalResult(
        start_slot=start_slot,
        temperature_c=tuple(temperatures),
        heat_w=heat,
        electric_wh=electric,
        share=share,
        under_kh=measures[:, 0].copy(),
        over_kh=measures[:, 1].copy(),
        protection_kh=measures[:, 2].copy(),
        initial_share=start.share,
    )


def thermal_load_wh(problem: PlanningProblem, result: ThermalResult) -> np.ndarray:
    """Return the electricity of all thermal devices per slot of the whole horizon [Wh]."""
    load = np.zeros(problem.slots)
    load[result.start_slot :] = result.electric_wh.sum(axis=0)
    return load


def _kernel_arguments(
    problem: PlanningProblem, load: Optional[np.ndarray] = None
) -> tuple[Any, ...]:
    data = problem.kernel_arrays()
    return (
        data["constants"],
        problem.pv_wh,
        problem.load_wh if load is None else load,
        problem.import_price_per_wh,
        problem.feed_in_tariff_per_wh,
        data["load_axis"],
        data["load_levels"],
        data["load_values"],
        data["storage_fields"],
        data["storage_tables"],
        data["storage_rates"],
        data["storage_available"],
        len(problem.batteries),
        data["inverter_fields"],
        data["curve_x"],
        data["curve_y"],
        data["run_energy"],
        data["run_slots"],
        data["pinned"],
    )


def simulate(
    problem: PlanningProblem,
    plan: Plan,
    start_slot: int = 0,
    initial_soc_wh: Optional[np.ndarray] = None,
    initial_thermal: Optional[ThermalStart] = None,
    *,
    slot_soc_wh: Optional[np.ndarray] = None,
) -> SimulationResult:
    """Simulate a plan from ``start_slot`` on.

    Args:
        problem: The planning task.
        plan: The decisions; arrays cover the whole horizon, only slots from ``start_slot``
            on are used.
        start_slot: First simulated slot; slot 0 uses ``first_slot_fraction``.
        initial_soc_wh: State of charge of all storages (batteries then vehicles) at the
            start of ``start_slot`` [Wh]. Default: the initial state of the problem, which is
            only the state at slot 0.
        initial_thermal: State of the thermal devices at the start of ``start_slot``; needed
            for a later start when the problem has thermal devices.
        slot_soc_wh: Reference storage state of shape ``(storages, slots - start_slot)``.
            With it, each slot starts from the reference state and is evaluated independently.

    Returns:
        The time series from ``start_slot`` on, the totals and the final state.
    """
    slots = problem.slots
    if not 0 <= start_slot <= slots:
        raise ValueError(f"start_slot {start_slot} is outside of 0 ... {slots}.")
    if initial_soc_wh is None:
        if start_slot != 0:
            raise ValueError("A simulation from a later slot needs initial_soc_wh.")
        soc = initial_state(problem)
    else:
        soc = np.array(initial_soc_wh, dtype=float)
    batteries = len(problem.batteries)
    storages = batteries + len(problem.vehicles)
    if soc.shape != (storages,):
        raise ValueError(f"initial_soc_wh needs {storages} values, got shape {soc.shape}.")
    length = slots - start_slot
    if slot_soc_wh is not None and slot_soc_wh.shape != (storages, length):
        raise ValueError(f"slot_soc_wh needs shape {(storages, length)}, got {slot_soc_wh.shape}.")
    series = np.zeros((core.N_SERIES, length))
    device = np.zeros((storages, core.N_DEVICE, length))
    totals = np.zeros(core.N_TOTALS)
    plan_arrays = _plan_arrays(problem, plan)
    thermal: Optional[ThermalResult] = None
    load: Optional[np.ndarray] = None
    if problem.thermal:
        thermal = simulate_thermal(problem, plan, start_slot, initial_thermal)
        load = problem.load_wh + thermal_load_wh(problem, thermal)
    arguments = _kernel_arguments(problem, load)
    call: tuple[Any, ...] = (start_slot, soc, *arguments, *plan_arrays, series, device, totals)
    if slot_soc_wh is not None:
        call = (*call, np.ascontiguousarray(slot_soc_wh, dtype=float))
    core.run_plan(*call)
    slot_h = problem.slot_duration_h
    scale = np.ones(length)
    if start_slot == 0 and length > 0:
        scale[0] = problem.first_slot_fraction
    hours = slot_h * scale
    # Power at the storage side: raw energy of the slot over the simulated hours.
    net_battery_wh = (
        device[:batteries, core.DV_CHARGE_RAW] - device[:batteries, core.DV_DISCHARGE_RAW]
    )
    return SimulationResult(
        start_slot=start_slot,
        load_wh=series[core.SR_LOAD],
        grid_import_wh=series[core.SR_IMPORT],
        grid_export_wh=series[core.SR_FEED_IN],
        cost_eur=series[core.SR_COST],
        revenue_eur=series[core.SR_REVENUE],
        losses_wh=series[core.SR_LOSSES],
        appliance_wh=series[core.SR_APPLIANCE],
        import_price_per_wh=problem.import_price_per_wh[start_slot:],
        feed_in_tariff_per_wh=problem.feed_in_tariff_per_wh[start_slot:],
        import_excess_wh=series[core.SR_IMPORT_EXCESS],
        export_curtailed_wh=series[core.SR_EXPORT_CURTAILED],
        energy_balance_residual_wh=series[core.SR_BALANCE_RESIDUAL],
        vehicle_loss_wh=series[core.SR_VEHICLE_LOSS],
        soc_pct=device[:batteries, core.DV_SOC_PCT],
        vehicle_soc_pct=device[batteries:, core.DV_SOC_PCT],
        soc_wh=device[:, core.DV_SOC_WH],
        battery_power_w=net_battery_wh / hours,
        battery_discharge_ac_wh=device[:batteries, core.DV_DISCHARGE_AC],
        battery_pv_charge_wh=device[:batteries, core.DV_PV_CHARGE_RAW],
        battery_grid_charge_ac_wh=device[:batteries, core.DV_GRID_CHARGE_AC],
        vehicle_power_w=device[batteries:, core.DV_CHARGE_RAW] / hours,
        charge_raw_wh=device[:, core.DV_CHARGE_RAW],
        charge_stored_wh=device[:, core.DV_CHARGE_STORED],
        discharge_raw_wh=device[:, core.DV_DISCHARGE_RAW],
        discharge_dc_wh=device[:, core.DV_DISCHARGE_DC],
        final_soc_wh=soc,
        total_cost_eur=float(totals[core.TO_COST]),
        total_revenue_eur=float(totals[core.TO_REVENUE]),
        total_balance_eur=float(totals[core.TO_BALANCE]),
        total_losses_wh=float(totals[core.TO_LOSSES]),
        total_import_excess_wh=float(totals[core.TO_IMPORT_EXCESS]),
        total_export_curtailed_wh=float(totals[core.TO_EXPORT_CURTAILED]),
        thermal=thermal,
    )


@dataclass(frozen=True, eq=False)
class BatchResult:
    """Totals of many plans; every array has one entry per plan.

    Attributes:
        total_cost_eur: Cost per plan [EUR].
        total_revenue_eur: Revenue per plan [EUR].
        total_balance_eur: Cost minus revenue per plan [EUR].
        total_losses_wh: Losses per plan [Wh].
        total_import_excess_wh: Import above the connection limit per plan [Wh].
        total_export_curtailed_wh: Feed-in curtailed by the feed-in limit per plan [Wh].
    """

    total_cost_eur: np.ndarray
    total_revenue_eur: np.ndarray
    total_balance_eur: np.ndarray
    total_losses_wh: np.ndarray
    total_import_excess_wh: np.ndarray
    total_export_curtailed_wh: np.ndarray


def shared_thermal_load(problem: PlanningProblem, plans: list[Plan]) -> Optional[np.ndarray]:
    """Return the load of the house for a batch whose plans share their heat plans.

    The load is the base load without thermal devices and, with them, the base load plus their
    electricity. None if the plans differ in their heat plans (each then needs its own load).
    """
    if not problem.thermal or not plans:
        return problem.load_wh
    first = plans[0].thermal_heat_w
    if any(not np.array_equal(plan.thermal_heat_w, first) for plan in plans[1:]):
        return None
    result = simulate_thermal(problem, plans[0])
    return problem.load_wh + thermal_load_wh(problem, result)


def simulate_batch(problem: PlanningProblem, plans: list[Plan]) -> BatchResult:
    """Simulate many plans from slot 0 and return their totals.

    One compiled call; the totals equal those of ``simulate`` for each plan.
    """
    count = len(plans)
    batteries = len(problem.batteries)
    slots = problem.slots
    arrays = [_plan_arrays(problem, plan) for plan in plans]
    cycles = max((a[5].shape[1] for a in arrays), default=1)
    load = shared_thermal_load(problem, plans)
    if load is None:
        singles = [simulate(problem, plan) for plan in plans]
        return BatchResult(
            total_cost_eur=np.array([r.total_cost_eur for r in singles]),
            total_revenue_eur=np.array([r.total_revenue_eur for r in singles]),
            total_balance_eur=np.array([r.total_balance_eur for r in singles]),
            total_losses_wh=np.array([r.total_losses_wh for r in singles]),
            total_import_excess_wh=np.array([r.total_import_excess_wh for r in singles]),
            total_export_curtailed_wh=np.array([r.total_export_curtailed_wh for r in singles]),
        )

    def stack(position: int, rows: int) -> np.ndarray:
        if count == 0:
            return np.zeros((0, rows, slots))
        return np.ascontiguousarray(np.stack([a[position] for a in arrays]))

    starts = np.full((count, len(problem.appliances), max(cycles, 1)), -1, dtype=np.int64)
    for position, plan_arrays in enumerate(arrays):
        starts[position, :, : plan_arrays[5].shape[1]] = plan_arrays[5]
    totals = np.zeros((count, core.N_TOTALS))
    arguments = _kernel_arguments(problem, load)
    call: tuple[Any, ...] = (
        initial_state(problem),
        *arguments,
        stack(0, batteries),
        stack(1, batteries),
        stack(2, batteries),
        stack(3, batteries),
        stack(4, len(problem.vehicles)),
        starts,
        totals,
    )
    core.run_batch(*call)
    return BatchResult(
        total_cost_eur=totals[:, core.TO_COST].copy(),
        total_revenue_eur=totals[:, core.TO_REVENUE].copy(),
        total_balance_eur=totals[:, core.TO_BALANCE].copy(),
        total_losses_wh=totals[:, core.TO_LOSSES].copy(),
        total_import_excess_wh=totals[:, core.TO_IMPORT_EXCESS].copy(),
        total_export_curtailed_wh=totals[:, core.TO_EXPORT_CURTAILED].copy(),
    )
