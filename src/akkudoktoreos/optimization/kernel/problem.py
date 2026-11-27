"""Algorithm neutral description of a planning task and of a plan for it.

``PlanningProblem`` holds everything that is given: the time grid, the forecasts, the
devices with their initial state and the grid connection. ``Plan`` holds what an algorithm
decides: one value per device and slot for each control. Neither knows how a plan is found.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from akkudoktoreos.devices.models.appliance import ApplianceParams, pack_appliances
from akkudoktoreos.devices.models.inverter import InverterParams, pack_inverters
from akkudoktoreos.devices.models.storage import StorageParams, pack_storages
from akkudoktoreos.devices.models.thermal import ThermalParams
from akkudoktoreos.optimization.kernel.selfconsumption import load_table


@dataclass(frozen=True)
class GridConnection:
    """Limits of the grid connection; None means unlimited.

    The feed-in limit is physical (the inverter curtails, a loss); the import limit is only
    reported as excess energy.

    Attributes:
        import_limit_w: Maximum power drawn from the grid [W].
        export_limit_w: Maximum power fed into the grid [W].
    """

    import_limit_w: Optional[float] = None
    export_limit_w: Optional[float] = None


def _series(values: Any, name: str) -> np.ndarray:
    array = np.ascontiguousarray(values, dtype=float)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional.")
    return array


@dataclass(frozen=True, eq=False)
class PlanningProblem:
    """The given data of a planning task.

    All series have one value per slot. Energies are per slot [Wh], prices per Wh. The
    forecasts of slot 0 are already reduced to the part of the slot still ahead when
    ``first_slot_fraction`` is below 1; the fraction itself scales the power limits of slot 0.

    Attributes:
        slot_duration_h: Duration of a slot [h].
        pv_wh: PV generation per slot [Wh].
        load_wh: Base load per slot [Wh] (without appliances, vehicles and grid charging).
        import_price_per_wh: Price of grid import per slot [EUR/Wh].
        feed_in_tariff_per_wh: Remuneration of grid export per slot [EUR/Wh].
        batteries: House batteries. Plan arrays follow this order.
        vehicles: Electric vehicles. Plan arrays follow this order.
        inverters: Inverters; each refers to its battery by index (or has none).
        appliances: Household appliances.
        appliance_cycles: Cycles per appliance (width of the plan's start array), default 1.
        thermal: Thermal devices (heat pumps with a room model). Plan arrays follow this order.
            Their electricity is added to the base load; every device covers the whole horizon.
        grid: Grid connection limits.
        first_slot_fraction: Part of slot 0 still ahead, in (0, 1].
        tail_pv_wh: PV generation after the horizon, per slot [Wh] (lookahead of the terminal
            value); empty without lookahead. The four tail series have equal length.
        tail_load_wh: Base load after the horizon [Wh].
        tail_import_price_per_wh: Import price after the horizon [EUR/Wh].
        tail_feed_in_tariff_per_wh: Feed-in tariff after the horizon [EUR/Wh].
        direct_marketing: Feed-in under direct marketing: negative tariff exports nothing,
            batteries may export to the grid at a positive tariff.
    """

    slot_duration_h: float
    pv_wh: np.ndarray
    load_wh: np.ndarray
    import_price_per_wh: np.ndarray
    feed_in_tariff_per_wh: np.ndarray
    batteries: tuple[StorageParams, ...] = ()
    vehicles: tuple[StorageParams, ...] = ()
    inverters: tuple[InverterParams, ...] = ()
    appliances: tuple[ApplianceParams, ...] = ()
    appliance_cycles: tuple[int, ...] = ()
    thermal: tuple[ThermalParams, ...] = ()
    grid: GridConnection = field(default_factory=GridConnection)
    first_slot_fraction: float = 1.0
    direct_marketing: bool = False
    tail_pv_wh: np.ndarray = field(default_factory=lambda: np.zeros(0))
    tail_load_wh: np.ndarray = field(default_factory=lambda: np.zeros(0))
    tail_import_price_per_wh: np.ndarray = field(default_factory=lambda: np.zeros(0))
    tail_feed_in_tariff_per_wh: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def __post_init__(self) -> None:
        """Validate the data and prepare the arrays of the compiled kernel."""
        object.__setattr__(self, "pv_wh", _series(self.pv_wh, "pv_wh"))
        object.__setattr__(self, "load_wh", _series(self.load_wh, "load_wh"))
        object.__setattr__(
            self, "import_price_per_wh", _series(self.import_price_per_wh, "import_price_per_wh")
        )
        object.__setattr__(
            self,
            "feed_in_tariff_per_wh",
            _series(self.feed_in_tariff_per_wh, "feed_in_tariff_per_wh"),
        )
        slots = len(self.pv_wh)
        for name in ("load_wh", "import_price_per_wh", "feed_in_tariff_per_wh"):
            if len(getattr(self, name)) != slots:
                raise ValueError(f"{name} must have {slots} values like pv_wh.")
        for name in (
            "tail_pv_wh",
            "tail_load_wh",
            "tail_import_price_per_wh",
            "tail_feed_in_tariff_per_wh",
        ):
            object.__setattr__(self, name, _series(getattr(self, name), name))
        if (
            len(
                {
                    len(getattr(self, n))
                    for n in (
                        "tail_pv_wh",
                        "tail_load_wh",
                        "tail_import_price_per_wh",
                        "tail_feed_in_tariff_per_wh",
                    )
                }
            )
            != 1
        ):
            raise ValueError("The four tail series must have equal length.")
        if not self.slot_duration_h > 0.0:
            raise ValueError("slot_duration_h must be positive.")
        if not 0.0 < self.first_slot_fraction <= 1.0:
            raise ValueError(
                f"first_slot_fraction must be in (0, 1], got {self.first_slot_fraction}."
            )
        if not self.inverters:
            raise ValueError("At least one inverter is required.")
        attached = [i.battery_index for i in self.inverters if i.battery_index is not None]
        if sorted(attached) != list(range(len(self.batteries))):
            raise ValueError("Each battery needs exactly one inverter that refers to its index.")
        if any(not vehicle.is_vehicle for vehicle in self.vehicles):
            raise ValueError("Vehicles must be storages with is_vehicle=True.")
        cycles = self.appliance_cycles or (1,) * len(self.appliances)
        if len(cycles) != len(self.appliances):
            raise ValueError("appliance_cycles needs one entry per appliance.")
        object.__setattr__(self, "appliance_cycles", tuple(cycles))
        for unit in self.thermal:
            if unit.slots != slots:
                raise ValueError(
                    f"A thermal device covers {unit.slots} slots, the problem has {slots}."
                )
        object.__setattr__(self, "_kernel_cache", None)

    @property
    def slots(self) -> int:
        """Number of slots of the horizon."""
        return len(self.pv_wh)

    def kernel_arrays(self) -> dict[str, Any]:
        """Return the flat arrays of the compiled kernel (built once)."""
        cache: Optional[dict[str, Any]] = getattr(self, "_kernel_cache")
        if cache is not None:
            return cache
        storages = self.batteries + self.vehicles
        fields, tables, rates, available = pack_storages(storages, self.slot_duration_h, self.slots)
        inverter_fields, curve_x, curve_y = pack_inverters(self.inverters)
        run_energy, run_slots, pinned = pack_appliances(self.appliances, self.slots)
        axis, levels, values = load_table()
        nan = float("nan")
        constants = np.array(
            [
                self.slot_duration_h,
                self.first_slot_fraction,
                1.0 if self.direct_marketing else 0.0,
                nan if self.grid.import_limit_w is None else self.grid.import_limit_w,
                nan if self.grid.export_limit_w is None else self.grid.export_limit_w,
            ]
        )
        initial_soc = fields[:, 5] / 100 * fields[:, 0]
        max_soc = fields[:, 7] / 100 * fields[:, 0]
        cache = {
            "constants": constants,
            "storage_fields": fields,
            "storage_tables": tables,
            "storage_rates": rates,
            "storage_available": np.ascontiguousarray(available),
            "inverter_fields": inverter_fields,
            "curve_x": curve_x,
            "curve_y": curve_y,
            "run_energy": run_energy,
            "run_slots": run_slots,
            "pinned": pinned,
            "load_axis": axis,
            "load_levels": levels,
            "load_values": values,
            "initial_soc_wh": np.minimum(initial_soc, max_soc),
        }
        object.__setattr__(self, "_kernel_cache", cache)
        return cache


@dataclass
class Plan:
    """The decisions of a plan: one value per device and slot.

    Attributes:
        battery_grid_charge: Grid charge level per battery, factor of its rated charge
            power (0 = no grid charging), shape ``(batteries, slots)``. A level the inverter
            cannot execute is reduced to its ``max_ac_charge_power_w``.
        battery_discharge_limit: Discharge ceiling per battery as factor of its rated
            discharge power, shape ``(batteries, slots)``. The battery covers at most
            this share of the maximum power of the remaining load, never more than the load;
            0 forbids discharging into the house.
        battery_pv_charge: 1 where the battery may charge from PV surplus, else 0.
        battery_export: Grid export level per battery, factor of its rated discharge power
            (direct marketing only; 0 = no export), shape ``(batteries, slots)``.
        vehicle_charge: Charge level per vehicle, factor of its rated charge power,
            shape ``(vehicles, slots)``.
        appliance_starts: Start slot of each appliance cycle (-1 = not run),
            shape ``(appliances, cycles)``.
        thermal_heat_w: Heat power per thermal device and slot [W], shape ``(devices, slots)``;
            0 or a value of the operating table of the slot. Absent (empty) without thermal
            devices.
    """

    battery_grid_charge: np.ndarray
    battery_discharge_limit: np.ndarray
    battery_pv_charge: np.ndarray
    battery_export: np.ndarray
    vehicle_charge: np.ndarray
    appliance_starts: np.ndarray
    thermal_heat_w: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))

    def copy(self) -> "Plan":
        """Return a plan with copies of all arrays."""
        return Plan(
            battery_grid_charge=self.battery_grid_charge.copy(),
            battery_discharge_limit=self.battery_discharge_limit.copy(),
            battery_pv_charge=self.battery_pv_charge.copy(),
            battery_export=self.battery_export.copy(),
            vehicle_charge=self.vehicle_charge.copy(),
            appliance_starts=self.appliance_starts.copy(),
            thermal_heat_w=self.thermal_heat_w.copy(),
        )

    @classmethod
    def idle(cls, problem: PlanningProblem) -> "Plan":
        """Return the plan that lets batteries discharge and charge from PV, nothing else."""
        batteries = len(problem.batteries)
        slots = problem.slots
        cycles = max(problem.appliance_cycles, default=1)
        return cls(
            battery_grid_charge=np.zeros((batteries, slots)),
            battery_discharge_limit=np.ones((batteries, slots)),
            battery_pv_charge=np.ones((batteries, slots)),
            battery_export=np.zeros((batteries, slots)),
            vehicle_charge=np.zeros((len(problem.vehicles), slots)),
            appliance_starts=np.full((len(problem.appliances), cycles), -1, dtype=np.int64),
            thermal_heat_w=np.zeros((len(problem.thermal), slots)),
        )

    def validate(self, problem: PlanningProblem) -> None:
        """Raise ``ValueError`` if the plan does not match the problem."""
        batteries = len(problem.batteries)
        slots = problem.slots
        for name, rows in (
            ("battery_grid_charge", batteries),
            ("battery_discharge_limit", batteries),
            ("battery_pv_charge", batteries),
            ("battery_export", batteries),
            ("vehicle_charge", len(problem.vehicles)),
        ):
            shape = np.shape(getattr(self, name))
            if shape != (rows, slots):
                raise ValueError(f"{name} has shape {shape}, expected {(rows, slots)}.")
        starts = np.shape(self.appliance_starts)
        if len(starts) != 2 or starts[0] != len(problem.appliances):
            raise ValueError("appliance_starts needs one row per appliance.")
        heat = np.shape(self.thermal_heat_w)
        if problem.thermal:
            if heat != (len(problem.thermal), slots):
                raise ValueError(
                    f"thermal_heat_w has shape {heat}, expected {(len(problem.thermal), slots)}."
                )
        elif np.size(self.thermal_heat_w):
            raise ValueError("thermal_heat_w must be empty without thermal devices.")
