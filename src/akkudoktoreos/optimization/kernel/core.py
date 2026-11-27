"""Compiled simulation of a plan: one pass over the slots of the horizon.

The order within a slot is fixed:

1. Fixed loads: base load, appliances, vehicle charging (a vehicle's charge energy including
   its losses is load).
2. Direct consumption from PV (probability table).
3. Batteries cover the remaining load in their configured order, each up to its discharge
   ceiling, its inverter and its state of charge.
4. PV surplus charges the batteries that may charge from PV, in order.
5. What is left is fed in up to the inverter capacity; the rest is curtailed (a loss).
6. Direct marketing: batteries with an export level discharge into the grid.
7. Grid charging of the batteries; its AC energy is load and grid import.
8. Direct marketing and a negative tariff: nothing is fed in, the energy is a loss.
9. A feed-in limit curtails the feed-in (a loss); a grid import limit only reports its excess.

Several inverters pool their capacity: direct consumption is bounded by the sum of the
inverter limits and the capacity left afterwards is shared by the inverters in proportion
to their limits. Each battery discharges and exports through its own inverter.
"""

import math
from typing import Optional

import numpy as np

from akkudoktoreos.devices.models import inverter as inv
from akkudoktoreos.devices.models import storage as sto
from akkudoktoreos.devices.models.appliance import appliance_load_wh
from akkudoktoreos.devices.models.jit import njit
from akkudoktoreos.optimization.kernel.selfconsumption import expected_direct_power_w

# Entries of the ``constants`` array.
C_SLOT_H = 0
C_FIRST_SLOT_FRACTION = 1
C_DIRECT_MARKETING = 2
C_IMPORT_LIMIT_W = 3
C_EXPORT_LIMIT_W = 4

# Rows of the ``series`` result array (one value per simulated slot).
SR_LOAD = 0
SR_FEED_IN = 1
SR_IMPORT = 2
SR_COST = 3
SR_REVENUE = 4
SR_LOSSES = 5
SR_APPLIANCE = 6
SR_IMPORT_EXCESS = 7
SR_EXPORT_CURTAILED = 8
SR_BALANCE_RESIDUAL = 9
SR_VEHICLE_LOSS = 10
N_SERIES = 11

# Rows of the ``device`` result array (per storage and slot).
DV_SOC_PCT = 0  # at the start of the slot
DV_SOC_WH = 1  # at the start of the slot
DV_CHARGE_RAW = 2  # energy taken into the storage, before the charging efficiency
DV_CHARGE_STORED = 3
DV_DISCHARGE_RAW = 4  # energy taken out of the storage
DV_DISCHARGE_DC = 5  # energy delivered at the DC side
DV_DISCHARGE_AC = 6  # energy delivered at the AC side (batteries)
DV_PV_CHARGE_RAW = 7  # raw charge energy from PV surplus
DV_GRID_CHARGE_AC = 8  # AC energy drawn from the grid for charging
N_DEVICE = 9

# Entries of the ``totals`` result array.
TO_COST = 0
TO_REVENUE = 1
TO_BALANCE = 2
TO_LOSSES = 3
TO_IMPORT_EXCESS = 4
TO_EXPORT_CURTAILED = 5
N_TOTALS = 6


@njit
def discharge_to_ac(
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    soc: np.ndarray,
    discharged_raw: np.ndarray,
    discharged_dc: np.ndarray,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    inverter: int,
    battery: int,
    requested_ac_wh: float,
    limit_factor: float,
    slot_h: float,
    scale: float,
) -> tuple[float, float]:
    """Discharge a battery through its inverter to deliver ``requested_ac_wh`` at the AC side.

    Returns:
        ``(delivered_ac_wh, losses_wh)`` including the discharge and the inverter losses.
    """
    if requested_ac_wh <= 0.0 or limit_factor <= 0.0:
        return 0.0, 0.0
    points = int(i_fields[inverter, inv.F_DC_TO_AC_POINTS])
    if points == 0:
        efficiency = i_fields[inverter, inv.F_DC_TO_AC_EFFICIENCY]
    else:
        efficiency = inv.dc_to_ac_efficiency_at(
            i_fields, curve_x, curve_y, inverter, requested_ac_wh, slot_h, scale
        )
    dc_request = requested_ac_wh / efficiency
    delivered_dc, discharge_losses = sto.discharge(
        s_fields,
        s_tables,
        soc,
        discharged_raw,
        discharged_dc,
        battery,
        dc_request,
        slot_h,
        scale,
        limit_factor,
    )
    # A limited discharge is converted with the efficiency at the AC energy actually delivered.
    if points > 0 and delivered_dc < dc_request * (1.0 - 1e-12):
        delivered_ac = inv.solve_ac_for_dc_energy(
            curve_x,
            curve_y,
            inverter,
            points,
            delivered_dc,
            i_fields[inverter, inv.F_MAX_POWER_W] * slot_h * scale,
            requested_ac_wh,
        )
    else:
        delivered_ac = delivered_dc * efficiency
    return delivered_ac, discharge_losses + (delivered_dc - delivered_ac)


@njit
def run_plan(
    start_slot: int,
    soc: np.ndarray,
    constants: np.ndarray,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    s_available: np.ndarray,
    batteries: int,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    run_energy: np.ndarray,
    run_slots: np.ndarray,
    pinned: np.ndarray,
    plan_grid_charge: np.ndarray,
    plan_discharge_limit: np.ndarray,
    plan_pv_charge: np.ndarray,
    plan_export: np.ndarray,
    plan_vehicle_charge: np.ndarray,
    plan_starts: np.ndarray,
    series: np.ndarray,
    device: np.ndarray,
    totals: np.ndarray,
    slot_soc_wh: Optional[np.ndarray] = None,
) -> None:
    """Simulate the slots ``start_slot ... slots - 1`` of a plan.

    Args:
        start_slot: First simulated slot.
        soc: State of charge per storage at the start of ``start_slot`` [Wh]; updated to the
            state after the last slot.
        constants: Slot duration, first slot fraction, direct marketing flag, grid limits.
        pv: PV energy per slot [Wh].
        load: Base load per slot [Wh].
        price: Import price per slot [EUR/Wh].
        tariff: Feed-in tariff per slot [EUR/Wh].
        load_axis: Mean load axis of the direct consumption table.
        load_levels: Minute load levels of the table.
        load_values: Probabilities of the table.
        s_fields: Storage scalars (batteries first, then vehicles).
        s_tables: Storage curves.
        s_rates: Charge levels per storage.
        s_available: Per storage and slot 1.0 where charging is possible.
        batteries: Number of batteries; the other storages are vehicles.
        i_fields: Inverter scalars.
        curve_x: Inverter curve fractions.
        curve_y: Inverter curve efficiencies.
        run_energy: Appliance cycle energy per slot.
        run_slots: Appliance cycle length.
        pinned: Appliance fixed load.
        plan_grid_charge: Battery grid charge levels.
        plan_discharge_limit: Battery discharge ceilings.
        plan_pv_charge: Battery PV charge permissions.
        plan_export: Battery grid export levels.
        plan_vehicle_charge: Vehicle charge levels.
        plan_starts: Appliance start slots.
        series: Result ``(N_SERIES, slots - start_slot)``.
        device: Result ``(storages, N_DEVICE, slots - start_slot)``.
        totals: Result ``(N_TOTALS,)``.
        slot_soc_wh: Optional reference state per storage at the start of every simulated slot.
    """
    slots = pv.shape[0]
    storages = s_fields.shape[0]
    inverters = i_fields.shape[0]
    slot_h = constants[C_SLOT_H]
    first_slot_fraction = constants[C_FIRST_SLOT_FRACTION]
    direct_marketing = constants[C_DIRECT_MARKETING] != 0.0
    import_limit_w = constants[C_IMPORT_LIMIT_W]
    export_limit_w = constants[C_EXPORT_LIMIT_W]

    # Appliance load per slot, summed over the appliances in order.
    appliances = run_slots.shape[0]
    appliance_total = np.zeros(slots)
    appliance_curve = np.zeros(slots)
    for appliance in range(appliances):
        appliance_load_wh(
            run_energy, run_slots, pinned, plan_starts[appliance], appliance, slots, appliance_curve
        )
        for slot in range(slots):
            appliance_total[slot] = appliance_total[slot] + appliance_curve[slot]

    discharged_raw = np.zeros(storages)
    discharged_dc = np.zeros(storages)
    charged_raw = np.zeros(storages)
    charged_stored = np.zeros(storages)
    charge_cap = np.zeros(storages)
    pv_charged_raw = np.zeros(storages)
    grid_charged_ac = np.zeros(storages)
    grid_factor = np.zeros(inverters)
    spare = np.zeros(inverters)
    discharged_ac = np.zeros(storages)
    soc_before = np.zeros(storages)

    total_cost = 0.0
    total_revenue = 0.0
    total_losses = 0.0
    total_import_excess = 0.0
    total_export_curtailed = 0.0

    for slot in range(start_slot, slots):
        index = slot - start_slot
        scale = first_slot_fraction if slot == 0 else 1.0
        for storage in range(storages):
            if slot_soc_wh is not None:
                soc[storage] = slot_soc_wh[storage, index]
            discharged_raw[storage] = 0.0
            discharged_dc[storage] = 0.0
            charged_raw[storage] = 0.0
            charged_stored[storage] = 0.0
            charge_cap[storage] = math.inf
            pv_charged_raw[storage] = 0.0
            grid_charged_ac[storage] = 0.0
            discharged_ac[storage] = 0.0
            soc_before[storage] = soc[storage]
            capacity = s_fields[storage, sto.F_CAPACITY_WH]
            device[storage, DV_SOC_WH, index] = soc[storage]
            device[storage, DV_SOC_PCT, index] = (
                soc[storage] / capacity * 100 if capacity > 0.0 else 0.0
            )

        consumption = load[slot]
        slot_losses = 0.0
        vehicle_loss = 0.0
        vehicle_input = 0.0
        if appliances > 0:
            consumption += appliance_total[slot]
        base_and_appliance = consumption
        for vehicle in range(storages - batteries):
            storage = batteries + vehicle
            factor = plan_vehicle_charge[vehicle, slot]
            if s_available[storage, slot] == 0.0:
                factor = 0.0
            if factor > 0.0:
                stored, losses = sto.charge(
                    s_fields,
                    s_tables,
                    s_rates,
                    soc,
                    charged_raw,
                    charged_stored,
                    charge_cap,
                    storage,
                    0.0,
                    factor,
                    True,
                    slot_h,
                    scale,
                )
                # The charger takes the raw input, not only the energy stored.
                consumption += stored + losses
                vehicle_input += stored + losses
                slot_losses += losses
                vehicle_loss += losses

        # AC charge level of each inverter's battery, capped by its AC charge limit.
        for inverter in range(inverters):
            grid_factor[inverter] = 0.0
            battery = int(i_fields[inverter, inv.F_BATTERY])
            if battery < 0:
                continue
            factor = plan_grid_charge[battery, slot]
            if factor != 0.0:
                factor = inv.ac_charge_factor(
                    i_fields, s_fields[battery, sto.F_MAX_CHARGE_W], inverter, factor
                )
            grid_factor[inverter] = factor
            if factor > 0.0 and i_fields[inverter, inv.F_AC_LIMITS_TOTAL_CHARGE] != 0.0:
                # The setpoint caps the total charge power, PV included.
                cap = s_fields[battery, sto.F_MAX_CHARGE_W] * slot_h * scale * factor
                cap = max(cap, 0.0)
                if cap < charge_cap[battery]:
                    charge_cap[battery] = cap

        # --- Inverter: direct consumption, discharge, PV charging, feed-in ---
        generation = max(pv[slot], 0.0)
        demand = max(consumption, 0.0)
        capacity_total = 0.0
        for inverter in range(inverters):
            capacity_total += i_fields[inverter, inv.F_MAX_POWER_W] * slot_h * scale
        slot_hours = slot_h * scale
        direct_pv = 0.0
        if generation > 0.0 and demand > 0.0:
            expected_w = expected_direct_power_w(
                demand / slot_hours, generation / slot_hours, load_axis, load_levels, load_values
            )
            direct_pv = expected_w * slot_hours
        direct_pv = max(direct_pv, 0.0)
        direct_pv = min(direct_pv, generation)
        direct_pv = min(direct_pv, demand)
        direct_pv = min(direct_pv, capacity_total)
        remaining_load = max(demand - direct_pv, 0.0)
        pv_surplus = max(generation - direct_pv, 0.0)
        capacity_left = max(capacity_total - direct_pv, 0.0)
        for inverter in range(inverters):
            if inverters == 1:
                spare[inverter] = capacity_left
            else:
                share = i_fields[inverter, inv.F_MAX_POWER_W] * slot_h * scale / capacity_total
                spare[inverter] = capacity_left * share

        losses_inverter = 0.0
        feed_in = 0.0
        # A feed-in limit is physical (NaN: none, every comparison with it is false).
        export_cap = export_limit_w * slot_h * scale
        # Batteries cover the remaining load.
        for inverter in range(inverters):
            battery = int(i_fields[inverter, inv.F_BATTERY])
            if battery < 0 or remaining_load <= 0.0 or spare[inverter] <= 0.0:
                continue
            requested = spare[inverter] if spare[inverter] < remaining_load else remaining_load
            delivered, losses = discharge_to_ac(
                s_fields,
                s_tables,
                soc,
                discharged_raw,
                discharged_dc,
                i_fields,
                curve_x,
                curve_y,
                inverter,
                battery,
                requested,
                plan_discharge_limit[battery, slot],
                slot_h,
                scale,
            )
            discharged_ac[battery] += delivered
            remaining_load = max(remaining_load - delivered, 0.0)
            spare[inverter] = max(spare[inverter] - delivered, 0.0)
            losses_inverter += losses
        grid_import = remaining_load

        if pv_surplus != 0.0:
            remaining_surplus = pv_surplus
            for inverter in range(inverters):
                battery = int(i_fields[inverter, inv.F_BATTERY])
                if battery < 0 or remaining_surplus <= 0.0 or plan_pv_charge[battery, slot] == 0.0:
                    continue
                stored, losses = sto.charge(
                    s_fields,
                    s_tables,
                    s_rates,
                    soc,
                    charged_raw,
                    charged_stored,
                    charge_cap,
                    battery,
                    remaining_surplus,
                    0.0,
                    False,
                    slot_h,
                    scale,
                )
                remaining_surplus = remaining_surplus - stored - losses
                if remaining_surplus < 0.0:
                    remaining_surplus = 0.0
                losses_inverter += losses
                pv_charged_raw[battery] += stored + losses
            spare_total = 0.0
            for inverter in range(inverters):
                spare_total += spare[inverter]
            pv_feed_in = spare_total if spare_total < remaining_surplus else remaining_surplus
            feed_in += pv_feed_in
            for inverter in range(inverters):
                if inverters == 1:
                    spare[inverter] = max(spare[inverter] - pv_feed_in, 0.0)
                elif spare_total > 0.0:
                    spare[inverter] = max(
                        spare[inverter] - pv_feed_in * spare[inverter] / spare_total, 0.0
                    )
            # PV that neither charges nor passes the inverter is curtailed (a loss).
            curtailed = max(remaining_surplus - pv_feed_in, 0.0)
            losses_inverter += curtailed

        # Batteries export to the grid (direct marketing, positive tariff).
        for inverter in range(inverters):
            battery = int(i_fields[inverter, inv.F_BATTERY])
            if battery < 0 or not direct_marketing or tariff[slot] <= 0.0:
                continue
            export_factor = plan_export[battery, slot]
            if export_factor <= 0.0 or spare[inverter] <= 0.0:
                continue
            export_factor = min(export_factor, 1.0)
            remaining_dc = sto.remaining_discharge_energy_dc_wh(
                s_fields, s_tables, soc[battery], discharged_raw[battery], battery, slot_h, scale
            )
            remaining_ac = remaining_dc * inv.dc_to_ac_efficiency_at(
                i_fields, curve_x, curve_y, inverter, remaining_dc, slot_h, scale
            )
            rated_dc = sto.rated_discharge_energy_dc_wh(s_fields, s_tables, battery, scale)
            rated_dc = rated_dc * export_factor
            rated_ac = rated_dc * inv.dc_to_ac_efficiency_at(
                i_fields, curve_x, curve_y, inverter, rated_dc, slot_h, scale
            )
            export_capacity = spare[inverter]
            if remaining_ac < export_capacity:
                export_capacity = remaining_ac
            if rated_ac < export_capacity:
                export_capacity = rated_ac
            # The inverter does not draw battery energy for what the limit would curtail.
            if export_cap - feed_in < export_capacity:
                export_capacity = export_cap - feed_in
            delivered, losses = discharge_to_ac(
                s_fields,
                s_tables,
                soc,
                discharged_raw,
                discharged_dc,
                i_fields,
                curve_x,
                curve_y,
                inverter,
                battery,
                export_capacity,
                1.0,
                slot_h,
                scale,
            )
            discharged_ac[battery] += delivered
            spare[inverter] = max(spare[inverter] - delivered, 0.0)
            feed_in += delivered
            losses_inverter += losses

        # Grid charging: its AC energy is load and import.
        for inverter in range(inverters):
            factor = grid_factor[inverter]
            if factor <= 0.0:
                continue
            battery = int(i_fields[inverter, inv.F_BATTERY])
            if i_fields[inverter, inv.F_AC_LIMITS_TOTAL_CHARGE] != 0.0:
                stored, battery_losses = sto.charge(
                    s_fields,
                    s_tables,
                    s_rates,
                    soc,
                    charged_raw,
                    charged_stored,
                    charge_cap,
                    battery,
                    s_fields[battery, sto.F_MAX_CHARGE_W] * slot_h * scale * factor,
                    0.0,
                    False,
                    slot_h,
                    scale,
                )
            else:
                stored, battery_losses = sto.charge(
                    s_fields,
                    s_tables,
                    s_rates,
                    soc,
                    charged_raw,
                    charged_stored,
                    charge_cap,
                    battery,
                    0.0,
                    factor,
                    True,
                    slot_h,
                    scale,
                )
            dc_energy = stored + battery_losses
            points = int(i_fields[inverter, inv.F_AC_TO_DC_POINTS])
            if points > 0:
                ac_energy = inv.solve_ac_for_charge_dc_energy(
                    curve_x,
                    curve_y,
                    inverter,
                    points,
                    dc_energy,
                    i_fields[inverter, inv.F_MAX_POWER_W] * slot_h * scale,
                )
            else:
                ac_energy = dc_energy / i_fields[inverter, inv.F_AC_TO_DC_EFFICIENCY]
            consumption += ac_energy
            grid_import += ac_energy
            slot_losses += battery_losses + (ac_energy - dc_energy)
            grid_charged_ac[battery] += ac_energy

        # Direct marketing: a negative tariff feeds nothing in.
        if direct_marketing and tariff[slot] < 0.0 and feed_in > 0.0:
            slot_losses += feed_in
            feed_in = 0.0

        # The inverter curtails PV that exceeds the feed-in limit (a loss).
        export_curtailed = 0.0
        if feed_in > export_cap:
            export_curtailed = feed_in - export_cap
            feed_in = export_cap
            slot_losses += export_curtailed

        slot_total_losses = slot_losses + losses_inverter
        grid_cost = grid_import * price[slot]
        lcos_cost = 0.0
        for battery in range(batteries):
            lcos_cost += discharged_dc[battery] * s_fields[battery, sto.F_LCOS_PER_KWH] / 1000.0
        cost = grid_cost + lcos_cost
        revenue = feed_in * tariff[slot]

        # Energy balance of the slot: sources minus sinks, zero up to rounding.
        sources = generation + grid_import
        sinks = base_and_appliance + vehicle_input + feed_in + (slot_total_losses - vehicle_loss)
        for battery in range(batteries):
            sources += soc_before[battery] - soc[battery]

        series[SR_LOAD, index] = consumption
        series[SR_FEED_IN, index] = feed_in
        series[SR_IMPORT, index] = grid_import
        series[SR_COST, index] = cost
        series[SR_REVENUE, index] = revenue
        series[SR_LOSSES, index] = slot_total_losses
        series[SR_APPLIANCE, index] = appliance_total[slot] if appliances > 0 else 0.0
        import_excess = 0.0
        if not math.isnan(import_limit_w):
            import_excess = max(grid_import - import_limit_w * slot_h * scale, 0.0)
        series[SR_IMPORT_EXCESS, index] = import_excess
        series[SR_EXPORT_CURTAILED, index] = export_curtailed
        series[SR_BALANCE_RESIDUAL, index] = sources - sinks
        series[SR_VEHICLE_LOSS, index] = vehicle_loss
        for storage in range(storages):
            device[storage, DV_CHARGE_RAW, index] = charged_raw[storage]
            device[storage, DV_CHARGE_STORED, index] = charged_stored[storage]
            device[storage, DV_DISCHARGE_RAW, index] = discharged_raw[storage]
            device[storage, DV_DISCHARGE_DC, index] = discharged_dc[storage]
            device[storage, DV_DISCHARGE_AC, index] = discharged_ac[storage]
            device[storage, DV_PV_CHARGE_RAW, index] = pv_charged_raw[storage]
            device[storage, DV_GRID_CHARGE_AC, index] = grid_charged_ac[storage]

        total_cost += cost
        total_revenue += revenue
        total_losses += slot_total_losses
        total_import_excess += import_excess
        total_export_curtailed += export_curtailed

    totals[TO_COST] = total_cost
    totals[TO_REVENUE] = total_revenue
    totals[TO_BALANCE] = total_cost - total_revenue
    totals[TO_LOSSES] = total_losses
    totals[TO_IMPORT_EXCESS] = total_import_excess
    totals[TO_EXPORT_CURTAILED] = total_export_curtailed


@njit
def run_batch(
    soc_start: np.ndarray,
    constants: np.ndarray,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    s_available: np.ndarray,
    batteries: int,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    run_energy: np.ndarray,
    run_slots: np.ndarray,
    pinned: np.ndarray,
    plans_grid_charge: np.ndarray,
    plans_discharge_limit: np.ndarray,
    plans_pv_charge: np.ndarray,
    plans_export: np.ndarray,
    plans_vehicle_charge: np.ndarray,
    plans_starts: np.ndarray,
    totals: np.ndarray,
) -> None:
    """Simulate many plans from slot 0 and write only their totals ``(plans, N_TOTALS)``."""
    plans = plans_grid_charge.shape[0]
    slots = pv.shape[0]
    storages = s_fields.shape[0]
    series = np.empty((N_SERIES, slots))
    device = np.empty((storages, N_DEVICE, slots))
    soc = np.empty(storages)
    for plan in range(plans):
        for storage in range(storages):
            soc[storage] = soc_start[storage]
        run_plan(
            0,
            soc,
            constants,
            pv,
            load,
            price,
            tariff,
            load_axis,
            load_levels,
            load_values,
            s_fields,
            s_tables,
            s_rates,
            s_available,
            batteries,
            i_fields,
            curve_x,
            curve_y,
            run_energy,
            run_slots,
            pinned,
            plans_grid_charge[plan],
            plans_discharge_limit[plan],
            plans_pv_charge[plan],
            plans_export[plan],
            plans_vehicle_charge[plan],
            plans_starts[plan],
            series,
            device,
            totals[plan],
        )
