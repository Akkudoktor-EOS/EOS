"""Optimization result of GENETIC: the output of one run (ADR 0006, 0009, 0010).

Plain frozen dataclasses. Their values are normalised as the public response models validate them
(``geneticapi``): NumPy arrays become lists, NaN in NumPy arrays becomes ``None`` and values get the
field's number type, so that the conversion, the visualization and a response built from a result
see the same values as before the result became a dataclass (ADR 0008).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticterminalvalue import TerminalValueResult
from akkudoktoreos.utils.datetimeutil import DateTime
from akkudoktoreos.utils.utils import NumpyEncoder

# Keys of the simulation output (German and current) and the field names of the result.
SIMULATION_KEYS: Mapping[str, str] = {
    "Last_Wh_pro_Stunde": "load_wh_per_hour",
    "EAuto_SoC_pro_Stunde": "ev_soc_per_hour",
    "Einnahmen_Euro_pro_Stunde": "revenue_per_hour",
    "Gesamt_Verluste": "total_losses",
    "Gesamtbilanz_Euro": "total_balance",
    "Gesamteinnahmen_Euro": "total_revenue",
    "Gesamtkosten_Euro": "total_costs",
    "Home_appliance_wh_per_hour": "home_appliance_wh_per_hour",
    "Kosten_Euro_pro_Stunde": "costs_per_hour",
    "Netzbezug_Wh_pro_Stunde": "grid_consumption_wh_per_hour",
    "Netzeinspeisung_Wh_pro_Stunde": "grid_feed_in_wh_per_hour",
    "Verluste_Pro_Stunde": "losses_per_hour",
    "akku_soc_pro_stunde": "battery_soc_per_hour",
    "Electricity_price": "electricity_price",
    "Feed_in_tariff": "feed_in_tariff",
    "home_appliance_energy_wh": "home_appliance_energy_wh",
    "home_appliance_running": "home_appliance_running",
}


def _values(values: Any) -> list[Any]:
    """NumPy arrays as lists (NaN as ``None``), as ``NumpyEncoder.convert_numpy`` does."""
    return list(NumpyEncoder.convert_numpy(values)[0])


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Not a number: {value!r}")
    return float(value)


def _int(value: Any) -> int:
    number = float(value)
    if isinstance(value, bool) or not number.is_integer():
        raise ValueError(f"Not an integer: {value!r}")
    return int(number)


def float_list(values: Any) -> list[float]:
    """A list of floats, coerced as a ``list[float]`` model field does."""
    return [_float(value) for value in _values(values)]


def optional_float_list(values: Any) -> list[Optional[float]]:
    """A list of floats or ``None``, coerced as a ``list[Optional[float]]`` field does."""
    return [None if value is None else _float(value) for value in _values(values)]


def int_list(values: Any) -> list[int]:
    """A list of integers, coerced as a ``list[int]`` model field does."""
    return [_int(value) for value in _values(values)]


@dataclass(frozen=True, slots=True)
class GeneticSimulationOutput:
    """Values of the simulated best solution, per slot from the run start, and totals.

    Attributes:
        home_appliance_energy_wh: Energy of each home appliance per slot [Wh], by device id.
        feed_in_tariff: Feed-in tariff per slot.
        home_appliance_running: Active run occupancy of each home appliance per slot, including
            zero-power phases, by device id.
    """

    load_wh_per_hour: list[float]
    ev_soc_per_hour: list[float]
    revenue_per_hour: list[float]
    total_losses: float
    total_balance: float
    total_revenue: float
    total_costs: float
    home_appliance_wh_per_hour: list[Optional[float]]
    costs_per_hour: list[float]
    grid_consumption_wh_per_hour: list[float]
    grid_feed_in_wh_per_hour: list[float]
    losses_per_hour: list[float]
    battery_soc_per_hour: list[float]
    electricity_price: list[float]
    home_appliance_energy_wh: dict[str, list[float]] = field(default_factory=dict)
    feed_in_tariff: list[float] = field(default_factory=list)
    home_appliance_running: dict[str, list[bool]] = field(default_factory=dict)

    @classmethod
    def from_simulation(cls, output: Mapping[str, Any]) -> "GeneticSimulationOutput":
        """Build the output from the simulation's dictionary (German keys).

        Args:
            output: The dictionary ``GeneticSimulation.simulate()`` returns, with the home
                appliance energy and occupancy added.

        Returns:
            The normalised simulation output.
        """
        values = {
            SIMULATION_KEYS[key]: value for key, value in output.items() if key in SIMULATION_KEYS
        }
        totals = ("total_losses", "total_balance", "total_revenue", "total_costs")
        fields: dict[str, Any] = {}
        for key, value in values.items():
            if key in totals:
                fields[key] = _float(value)
            elif key == "home_appliance_wh_per_hour":
                fields[key] = optional_float_list(value)
            elif key == "home_appliance_energy_wh":
                fields[key] = {device_id: float_list(energy) for device_id, energy in value.items()}
            elif key == "home_appliance_running":
                fields[key] = {device_id: list(running) for device_id, running in value.items()}
            else:
                fields[key] = float_list(value)
        return cls(**fields)


@dataclass(frozen=True, slots=True)
class GeneticElectricVehicleState:
    """The electric vehicle at the end of the simulated best solution.

    Attributes:
        device_id: ID of the electric vehicle.
        hours: Number of slots of the simulation.
        charge_array: Charging factor per slot.
        discharge_array: 1 where discharging is allowed, per slot.
        discharging_efficiency: Discharging efficiency (0 to 1).
        capacity_wh: Battery capacity [Wh].
        charging_efficiency: Charging efficiency (0 to 1).
        max_charge_power_w: Maximum charging power [W].
        max_discharge_power_w: Maximum discharging power [W], if limited.
        soc_wh: State of charge at the end of the simulation [Wh].
        initial_soc_percentage: State of charge at the start [%].
    """

    device_id: str
    hours: int
    charge_array: list[float]
    discharge_array: list[int]
    discharging_efficiency: float
    capacity_wh: int
    charging_efficiency: float
    max_charge_power_w: int
    max_discharge_power_w: Optional[float]
    soc_wh: float
    initial_soc_percentage: int

    @classmethod
    def from_battery(cls, state: Mapping[str, Any]) -> "GeneticElectricVehicleState":
        """Build the state from ``GeneticBattery.to_dict()``.

        Args:
            state: The battery's dictionary.

        Returns:
            The normalised electric vehicle state.
        """
        max_discharge_power_w = state.get("max_discharge_power_w")
        return cls(
            device_id=str(state["device_id"]),
            hours=_int(state["hours"]),
            charge_array=float_list(state["charge_array"]),
            discharge_array=int_list(state["discharge_array"]),
            discharging_efficiency=_float(state["discharging_efficiency"]),
            capacity_wh=_int(state["capacity_wh"]),
            charging_efficiency=_float(state["charging_efficiency"]),
            max_charge_power_w=_int(state["max_charge_power_w"]),
            max_discharge_power_w=(
                None if max_discharge_power_w is None else _float(max_discharge_power_w)
            ),
            soc_wh=_float(state["soc_wh"]),
            initial_soc_percentage=_int(state["initial_soc_percentage"]),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneticResult:
    """Output of one GENETIC run.

    The control arrays and per-slot values start at the run start (``controls_start_at_now``).

    Attributes:
        request: The request the run optimized, after its adaptation to the configuration and
            the slot grid; the response echoes it as ``parameters``.
        ac_charge: AC charging factor per slot.
        dc_charge: DC charging factor per slot.
        discharge_allowed: 1 where discharging is allowed, per slot.
        ev_charge_hours_float: EV charging factor per slot, or ``None`` without an EV.
        result: Simulated values of the best solution.
        ev_obj: The electric vehicle at the end of the simulation, if any.
        start_hour: Hour of the day the run started at.
        start_solution: Best individual, the warm start of the next run.
        washingstart: Deprecated start hour of a single hourly home appliance, if any.
        extra_data: Extra data of the run.
        fitness_history: Fitness per generation.
        fixed_seed: Seed of the run.
        controls_start_at_now: The control arrays start at the run timestamp, not midnight.
        battery_grid_export_allowed: 1 where battery export to the grid is allowed, per slot.
        terminal_value: What the run credited for the energy left in the battery.
        battery_grid_export_factor: Battery grid export factor per slot.
        start_solution_datetime: Start of slot 0 of ``start_solution``.
        appliance_starts: Planned run starts of each home appliance, by device id.
        appliance_deadline_missed: Whether a home appliance misses its deadline, by device id.
        interval_seconds: Duration of one slot [s].
        first_slot_fraction: Fraction of the first slot that is still ahead at the run start.
    """

    request: GeneticOptimizationParameters
    ac_charge: list[float]
    dc_charge: list[float]
    discharge_allowed: list[int]
    ev_charge_hours_float: Optional[list[float]]
    result: GeneticSimulationOutput
    ev_obj: Optional[GeneticElectricVehicleState]
    start_hour: int = 0
    start_solution: Optional[list[float]] = None
    washingstart: Optional[int] = None
    extra_data: Optional[dict[str, Union[list[int], list[float]]]] = None
    fitness_history: Optional[dict[str, Any]] = None
    fixed_seed: Optional[int] = None
    controls_start_at_now: bool = False
    battery_grid_export_allowed: list[int] = field(default_factory=list)
    terminal_value: Optional[TerminalValueResult] = None
    battery_grid_export_factor: list[float] = field(default_factory=list)
    start_solution_datetime: Optional[DateTime] = None
    appliance_starts: dict[str, list[DateTime]] = field(default_factory=dict)
    appliance_deadline_missed: dict[str, bool] = field(default_factory=dict)
    interval_seconds: int = 3600
    first_slot_fraction: float = 1.0
