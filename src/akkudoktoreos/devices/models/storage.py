"""Energy storage model: house battery and electric vehicle.

A vehicle is a storage with an availability window (it can only charge while
plugged in) and without discharging. The physics is the one of the former
``Battery`` device: constant or power dependent efficiencies, charge and
discharge power limits that depend on the state of charge, a minimum and maximum
state of charge, and the fallback of a requested charge level to the next lower
level when the battery cannot take it.

All state lives in flat numpy arrays so the step functions compile with numba:

- ``fields``: ``(n_storages, N_FIELDS)`` scalar parameters, columns ``F_*``
- ``tables``: ``(n_storages, N_TABLES, 101)`` curves per power or SoC percent
- ``rates``: ``(n_storages, n_rates)`` charge levels, descending, padded with -1
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from akkudoktoreos.devices.models.jit import njit

# Resolution of the dense tables: one value per percent, index 0 ... 100.
TABLE_POINTS = 101

# Charge levels of a storage that has none configured.
DEFAULT_CHARGE_RATES: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)

# Columns of the ``fields`` array.
F_CAPACITY_WH = 0
F_CHARGING_EFFICIENCY = 1
F_DISCHARGING_EFFICIENCY = 2
F_MAX_CHARGE_W = 3
F_MAX_DISCHARGE_W = 4
F_INITIAL_SOC_PCT = 5
F_MIN_SOC_PCT = 6
F_MAX_SOC_PCT = 7
F_LCOS_PER_KWH = 8
F_IS_VEHICLE = 9
F_HAS_CHARGE_LIMIT = 10
F_HAS_DISCHARGE_LIMIT = 11
F_HAS_CHARGE_EFFICIENCY = 12
F_HAS_DISCHARGE_EFFICIENCY = 13
F_SOC_INDEX_PER_WH = 14
F_CHARGE_INDEX_PER_WH = 15
F_DISCHARGE_INDEX_PER_WH = 16
F_RATED_CHARGE_WH = 17
F_RATED_DISCHARGE_WH = 18
N_FIELDS = 19

# Slices of the ``tables`` array.
T_CHARGE_LIMIT_WH = 0  # raw charge energy of one slot per SoC percent, capped at rated power
T_DISCHARGE_LIMIT_WH = 1  # raw discharge energy of one slot per SoC percent
T_CHARGE_EFFICIENCY = 2  # per percent of rated charge power
T_DISCHARGE_EFFICIENCY = 3  # per percent of rated discharge power
N_TABLES = 4


def _dense_curve(curve: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Interpolate an efficiency curve at every power percent 0 ... 100."""
    fractions, efficiencies = zip(*curve)
    grid = np.linspace(0.0, 1.0, TABLE_POINTS)
    return np.asarray(np.interp(grid, fractions, efficiencies), dtype=float)


@dataclass(frozen=True)
class StorageParams:
    """Parameters of a house battery or an electric vehicle.

    Attributes:
        capacity_wh: Capacity [Wh]; 0 is a battery that stores nothing.
        initial_soc_pct: State of charge at the start of the horizon [%].
        min_soc_pct: Discharging stops at this state of charge [%]. Vehicles use 0.
        max_soc_pct: Charging stops at this state of charge [%].
        charging_efficiency: Constant charging efficiency, used without curve.
        discharging_efficiency: Constant discharging efficiency, used without curve.
        max_charge_power_w: Rated charge power [W].
        max_discharge_power_w: Rated discharge power [W], 0 for a vehicle.
        lcos_per_kwh: Levelized cost of storage per kWh the battery delivers [EUR/kWh].
        charge_rates: Charge levels as factor of rated power. A level the storage cannot
            take falls back to the next lower one.
        charge_power_limit_w_by_soc: 101 charge power limits [W], index = SoC percent.
        discharge_power_limit_w_by_soc: 101 discharge power limits [W], index = SoC percent.
        charging_efficiency_curve: ``(power_fraction, efficiency)`` points of the charge
            efficiency, replacing the constant.
        discharging_efficiency_curve: Same for discharging.
        is_vehicle: Electric vehicle: no discharging, charges up to full within a slot.
        available: Vehicle only: per slot whether it is plugged in (None: always).
    """

    capacity_wh: float
    initial_soc_pct: float = 0.0
    min_soc_pct: float = 0.0
    max_soc_pct: float = 100.0
    charging_efficiency: float = 0.88
    discharging_efficiency: float = 0.88
    max_charge_power_w: float = 5000.0
    max_discharge_power_w: float = 5000.0
    lcos_per_kwh: float = 0.0
    charge_rates: tuple[float, ...] = DEFAULT_CHARGE_RATES
    charge_power_limit_w_by_soc: Optional[tuple[float, ...]] = None
    discharge_power_limit_w_by_soc: Optional[tuple[float, ...]] = None
    charging_efficiency_curve: Optional[tuple[tuple[float, float], ...]] = None
    discharging_efficiency_curve: Optional[tuple[tuple[float, float], ...]] = None
    is_vehicle: bool = False
    available: Optional[tuple[bool, ...]] = field(default=None, compare=False)


def pack_storages(
    storages: tuple[StorageParams, ...], slot_duration_h: float, slots: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Flatten storage parameters into the arrays the step functions use.

    Args:
        storages: Storages in plan order (batteries first, then vehicles).
        slot_duration_h: Slot duration [h].
        slots: Number of slots of the horizon.

    Returns:
        ``(fields, tables, rates, available)``; ``available`` is ``(n, slots)`` with 1.0
        where the storage may charge.
    """
    count = len(storages)
    fields = np.zeros((count, N_FIELDS))
    tables = np.zeros((count, N_TABLES, TABLE_POINTS))
    width = max((len(s.charge_rates) for s in storages), default=1)
    rates = np.full((count, max(width, 1)), -1.0)
    available = np.ones((count, slots))
    for index, storage in enumerate(storages):
        row = fields[index]
        row[F_CAPACITY_WH] = storage.capacity_wh
        row[F_CHARGING_EFFICIENCY] = storage.charging_efficiency
        row[F_DISCHARGING_EFFICIENCY] = storage.discharging_efficiency
        row[F_MAX_CHARGE_W] = storage.max_charge_power_w
        row[F_MAX_DISCHARGE_W] = storage.max_discharge_power_w
        row[F_INITIAL_SOC_PCT] = storage.initial_soc_pct
        row[F_MIN_SOC_PCT] = storage.min_soc_pct
        row[F_MAX_SOC_PCT] = storage.max_soc_pct
        row[F_LCOS_PER_KWH] = storage.lcos_per_kwh
        row[F_IS_VEHICLE] = 1.0 if storage.is_vehicle else 0.0
        rated_charge_wh = storage.max_charge_power_w * slot_duration_h
        rated_discharge_wh = storage.max_discharge_power_w * slot_duration_h
        row[F_RATED_CHARGE_WH] = rated_charge_wh
        row[F_RATED_DISCHARGE_WH] = rated_discharge_wh
        # A rated power or capacity of 0 maps every energy to the 0 % entry.
        row[F_SOC_INDEX_PER_WH] = 100.0 / storage.capacity_wh if storage.capacity_wh > 0 else 0.0
        row[F_CHARGE_INDEX_PER_WH] = 100.0 / rated_charge_wh if rated_charge_wh > 0 else 0.0
        row[F_DISCHARGE_INDEX_PER_WH] = (
            100.0 / rated_discharge_wh if rated_discharge_wh > 0 else 0.0
        )
        if storage.charge_power_limit_w_by_soc is not None:
            row[F_HAS_CHARGE_LIMIT] = 1.0
            tables[index, T_CHARGE_LIMIT_WH] = [
                min(limit_w * slot_duration_h, rated_charge_wh)
                for limit_w in storage.charge_power_limit_w_by_soc
            ]
        if storage.discharge_power_limit_w_by_soc is not None:
            row[F_HAS_DISCHARGE_LIMIT] = 1.0
            tables[index, T_DISCHARGE_LIMIT_WH] = [
                min(limit_w * slot_duration_h, rated_discharge_wh)
                for limit_w in storage.discharge_power_limit_w_by_soc
            ]
        if storage.charging_efficiency_curve is not None:
            row[F_HAS_CHARGE_EFFICIENCY] = 1.0
            tables[index, T_CHARGE_EFFICIENCY] = _dense_curve(storage.charging_efficiency_curve)
        if storage.discharging_efficiency_curve is not None:
            row[F_HAS_DISCHARGE_EFFICIENCY] = 1.0
            tables[index, T_DISCHARGE_EFFICIENCY] = _dense_curve(
                storage.discharging_efficiency_curve
            )
        levels = sorted((float(rate) for rate in set(storage.charge_rates)), reverse=True)
        rates[index, : len(levels)] = levels
        if storage.available is not None:
            window = np.asarray(storage.available, dtype=float)
            available[index, :] = 0.0
            available[index, : min(slots, len(window))] = window[:slots]
    return fields, tables, rates, available


@njit
def table_entry(tables: np.ndarray, storage: int, table: int, index: int) -> float:
    """Return a table entry with the index clamped to ``0 ... 100``."""
    if index <= 0:
        return float(tables[storage, table, 0])
    if index >= TABLE_POINTS:
        return float(tables[storage, table, TABLE_POINTS - 1])
    return float(tables[storage, table, index])


@njit
def discharge_slot_cap_wh(
    fields: np.ndarray,
    tables: np.ndarray,
    soc_wh: float,
    storage: int,
    slot_duration_h: float,
    scale: float,
) -> float:
    """Return the raw discharge energy of a slot [Wh] at the current state of charge."""
    if fields[storage, F_HAS_DISCHARGE_LIMIT] == 0.0:
        return fields[storage, F_MAX_DISCHARGE_W] * slot_duration_h * scale
    index = int(soc_wh * fields[storage, F_SOC_INDEX_PER_WH] + 0.5)
    return table_entry(tables, storage, T_DISCHARGE_LIMIT_WH, index) * scale


@njit
def discharging_efficiency_at(
    fields: np.ndarray, tables: np.ndarray, storage: int, slot_raw_wh: float, scale: float
) -> float:
    """Return the discharging efficiency at the slot power of ``slot_raw_wh``."""
    if fields[storage, F_HAS_DISCHARGE_EFFICIENCY] == 0.0:
        return fields[storage, F_DISCHARGING_EFFICIENCY]
    index = int(slot_raw_wh / scale * fields[storage, F_DISCHARGE_INDEX_PER_WH] + 0.5)
    return table_entry(tables, storage, T_DISCHARGE_EFFICIENCY, index)


@njit
def rated_discharge_energy_dc_wh(
    fields: np.ndarray, tables: np.ndarray, storage: int, scale: float
) -> float:
    """Return the DC energy a full-power discharge delivers in one slot."""
    rated_slot_wh = fields[storage, F_RATED_DISCHARGE_WH] * scale
    return rated_slot_wh * discharging_efficiency_at(fields, tables, storage, rated_slot_wh, scale)


@njit
def remaining_discharge_energy_dc_wh(
    fields: np.ndarray,
    tables: np.ndarray,
    soc_wh: float,
    discharged_raw_wh: float,
    storage: int,
    slot_duration_h: float,
    scale: float,
) -> float:
    """Return the DC energy still deliverable within the slot [Wh]."""
    cap_wh = discharge_slot_cap_wh(fields, tables, soc_wh, storage, slot_duration_h, scale)
    power_remaining = max(cap_wh - discharged_raw_wh, 0.0)
    min_soc_wh = fields[storage, F_MIN_SOC_PCT] / 100.0 * fields[storage, F_CAPACITY_WH]
    soc_available = max(soc_wh - min_soc_wh, 0.0)
    raw_remaining = min(power_remaining, soc_available)
    return raw_remaining * discharging_efficiency_at(
        fields, tables, storage, discharged_raw_wh + raw_remaining, scale
    )


@njit
def discharge(
    fields: np.ndarray,
    tables: np.ndarray,
    soc: np.ndarray,
    discharged_raw: np.ndarray,
    discharged_dc: np.ndarray,
    storage: int,
    request_dc_wh: float,
    slot_duration_h: float,
    scale: float,
    limit_factor: float,
) -> tuple[float, float]:
    """Discharge a storage to deliver ``request_dc_wh`` (DC side).

    The delivery is limited by the request, by the energy above the minimum SoC, and by
    the discharge power of the slot: the rated power or the SoC dependent limit, and at
    most ``limit_factor`` times the rated power (the plan's discharge ceiling). Power
    already used in this slot counts. The state of charge and the slot counters are updated.

    Args:
        fields: Scalar parameters per storage.
        tables: Dense curves per storage.
        soc: State of charge per storage [Wh], updated.
        discharged_raw: Energy taken from the storage in this slot per storage [Wh], updated.
        discharged_dc: Energy delivered in this slot per storage [Wh], updated.
        storage: Index of the storage.
        request_dc_wh: Requested DC energy [Wh].
        slot_duration_h: Slot duration [h].
        scale: Simulated part of the slot (``first_slot_fraction`` in slot 0, else 1).
        limit_factor: Ceiling on the discharge power as factor of the rated power.

    Returns:
        ``(delivered_dc_wh, losses_wh)``.
    """
    min_soc_wh = fields[storage, F_MIN_SOC_PCT] / 100.0 * fields[storage, F_CAPACITY_WH]
    raw_available_wh = max(soc[storage] - min_soc_wh, 0.0)
    if raw_available_wh == 0.0:
        # A measured SoC below the reserve must not be raised by discharging.
        return 0.0, 0.0
    already_wh = discharged_raw[storage]
    efficiency = fields[storage, F_DISCHARGING_EFFICIENCY]
    slot_cap_wh = discharge_slot_cap_wh(
        fields, tables, soc[storage], storage, slot_duration_h, scale
    )
    ceiling_wh = limit_factor * fields[storage, F_RATED_DISCHARGE_WH] * scale
    if ceiling_wh < slot_cap_wh:
        slot_cap_wh = ceiling_wh
    raw_withdrawal_wh = slot_cap_wh - already_wh
    if raw_withdrawal_wh < 0.0:
        raw_withdrawal_wh = 0.0
    if raw_available_wh < raw_withdrawal_wh:
        raw_withdrawal_wh = raw_available_wh
    if fields[storage, F_HAS_DISCHARGE_EFFICIENCY] != 0.0:
        # Efficiency at the slot power: what was discharged already plus this request
        # (estimated with the constant efficiency), within the possible withdrawal.
        raw_estimate_wh = request_dc_wh / efficiency
        if raw_estimate_wh > raw_withdrawal_wh:
            raw_estimate_wh = raw_withdrawal_wh
        index = int(
            (already_wh + raw_estimate_wh) / scale * fields[storage, F_DISCHARGE_INDEX_PER_WH] + 0.5
        )
        if index <= TABLE_POINTS - 1:
            efficiency = tables[storage, T_DISCHARGE_EFFICIENCY, index]
        else:
            efficiency = tables[storage, T_DISCHARGE_EFFICIENCY, TABLE_POINTS - 1]
    max_deliverable_wh = raw_withdrawal_wh * efficiency
    delivered_wh = min(request_dc_wh, max_deliverable_wh)
    raw_used_wh = delivered_wh / efficiency
    new_soc = soc[storage] - raw_used_wh
    soc[storage] = max(new_soc, min_soc_wh)
    discharged_raw[storage] = already_wh + raw_used_wh
    discharged_dc[storage] += delivered_wh
    return delivered_wh, raw_used_wh - delivered_wh


@njit
def charge(
    fields: np.ndarray,
    tables: np.ndarray,
    rates: np.ndarray,
    soc: np.ndarray,
    charged_raw: np.ndarray,
    charged_stored: np.ndarray,
    charge_cap_raw: np.ndarray,
    storage: int,
    request_wh: float,
    factor: float,
    by_factor: bool,
    slot_duration_h: float,
    scale: float,
) -> tuple[float, float]:
    """Charge a storage, either with a raw energy or at a charge level.

    ``by_factor=False``: ``request_wh`` is the raw energy offered (PV surplus); the storage
    takes what fits. ``by_factor=True``: the request is ``factor`` of the rated charge power.
    A battery that cannot take it falls back to the next lower charge level of its list and,
    if none fits, takes nothing; a vehicle takes the energy up to full.

    The caps of the slot are the rated power (or the SoC dependent limit), the slot cap
    ``charge_cap_raw`` and what was charged in this slot already.

    Returns:
        ``(stored_wh, losses_wh)``; stored energy and the losses of the efficiency.
    """
    soc_wh = soc[storage]
    already_wh = charged_raw[storage]
    max_per_slot_wh = max(
        min(fields[storage, F_MAX_CHARGE_W] * slot_duration_h * scale, charge_cap_raw[storage])
        - already_wh,
        0.0,
    )
    efficiency = fields[storage, F_CHARGING_EFFICIENCY]
    has_soc_limit = fields[storage, F_HAS_CHARGE_LIMIT] != 0.0
    soc_limit_raw_wh = 0.0
    if has_soc_limit:
        index = int(soc_wh * fields[storage, F_SOC_INDEX_PER_WH] + 0.5)
        soc_limit_raw_wh = (
            table_entry(tables, storage, T_CHARGE_LIMIT_WH, index) * scale - already_wh
        )
        if soc_limit_raw_wh < 0.0:
            soc_limit_raw_wh = 0.0
    if fields[storage, F_HAS_CHARGE_EFFICIENCY] != 0.0:
        # Efficiency at the slot power: what was charged already plus the intended request.
        intended_wh = max_per_slot_wh * factor if by_factor else request_wh
        if intended_wh > max_per_slot_wh:
            intended_wh = max_per_slot_wh
        if has_soc_limit and soc_limit_raw_wh < intended_wh:
            intended_wh = soc_limit_raw_wh
        if intended_wh < 0.0:
            intended_wh = 0.0
        index = int(
            (already_wh + intended_wh) / scale * fields[storage, F_CHARGE_INDEX_PER_WH] + 0.5
        )
        if index <= TABLE_POINTS - 1:
            efficiency = tables[storage, T_CHARGE_EFFICIENCY, index]
        else:
            efficiency = tables[storage, T_CHARGE_EFFICIENCY, TABLE_POINTS - 1]

    max_soc_wh = fields[storage, F_MAX_SOC_PCT] / 100.0 * fields[storage, F_CAPACITY_WH]
    raw_charge_wh = max(max_soc_wh - soc_wh, 0.0) / efficiency
    if not by_factor:
        raw_request_wh = request_wh
    else:
        raw_request_wh = max_per_slot_wh * factor
        if fields[storage, F_IS_VEHICLE] != 0.0:
            # The vehicle ends charging when full, even within a slot.
            if raw_charge_wh <= 0.0:
                return 0.0, 0.0
        elif raw_request_wh > raw_charge_wh:
            # Fall back to the levels below the requested one, highest first.
            requested = factor
            for position in range(rates.shape[1]):
                level = rates[storage, position]
                if level < 0.0:
                    break
                if level >= requested:
                    continue
                raw_request_wh = max_per_slot_wh * level
                if raw_request_wh <= raw_charge_wh:
                    break
            if raw_request_wh > raw_charge_wh:
                return 0.0, 0.0

    max_raw_wh = min(raw_charge_wh, max_per_slot_wh)
    if has_soc_limit and soc_limit_raw_wh < max_raw_wh:
        max_raw_wh = soc_limit_raw_wh
    raw_input_wh = raw_request_wh if raw_request_wh < max_raw_wh else max_raw_wh
    stored_wh = raw_input_wh * efficiency
    soc[storage] = soc_wh + stored_wh
    charged_raw[storage] = already_wh + raw_input_wh
    charged_stored[storage] += stored_wh
    return stored_wh, raw_input_wh - stored_wh
