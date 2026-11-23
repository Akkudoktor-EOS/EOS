"""Optimization result of GENETIC0: the output of one run (ADR 0006, 0009, 0010).

Plain frozen dataclasses. Their values are normalised as the public response models validate them
(``genetic0api``): NumPy arrays become lists, NaN becomes ``None`` and values get the field's
number type, so that a response built from a result is the same as before the result became a
dataclass (ADR 0008).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Union

from akkudoktoreos.utils.utils import NumpyEncoder

# German keys of the simulation output and the field names of the result.
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
class Genetic0SimulationOutput:
    """Hourly and total values of the simulated best solution, from the start hour."""

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

    @classmethod
    def from_simulation(cls, output: Mapping[str, Any]) -> "Genetic0SimulationOutput":
        """Build the output from the simulation's dictionary (German keys).

        Args:
            output: The dictionary ``Genetic0Simulation.simulate()`` returns.

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
            else:
                fields[key] = float_list(value)
        return cls(**fields)


@dataclass(frozen=True, slots=True)
class Genetic0ElectricVehicleState:
    """The electric vehicle at the end of the simulated best solution."""

    device_id: str
    hours: int
    charge_array: list[float]
    discharge_array: list[int]
    discharging_efficiency: float
    capacity_wh: int
    charging_efficiency: float
    max_charge_power_w: int
    soc_wh: float
    initial_soc_percentage: int

    @classmethod
    def from_battery(cls, state: Mapping[str, Any]) -> "Genetic0ElectricVehicleState":
        """Build the state from ``Genetic0Battery.to_dict()``.

        Args:
            state: The battery's dictionary.

        Returns:
            The normalised electric vehicle state.
        """
        return cls(
            device_id=str(state["device_id"]),
            hours=_int(state["hours"]),
            charge_array=float_list(state["charge_array"]),
            discharge_array=int_list(state["discharge_array"]),
            discharging_efficiency=_float(state["discharging_efficiency"]),
            capacity_wh=_int(state["capacity_wh"]),
            charging_efficiency=_float(state["charging_efficiency"]),
            max_charge_power_w=_int(state["max_charge_power_w"]),
            soc_wh=_float(state["soc_wh"]),
            initial_soc_percentage=_int(state["initial_soc_percentage"]),
        )


@dataclass(frozen=True, slots=True)
class Genetic0Result:
    """Output of one GENETIC0 run.

    Attributes:
        ac_charge: AC charging factor per hour of the arrays.
        dc_charge: DC charging factor per hour of the arrays.
        discharge_allowed: 1 where discharging is allowed, per hour of the arrays.
        ev_charge_hours_float: EV charging factor per hour, or ``None`` without an EV.
        result: Simulated values of the best solution.
        ev_obj: The electric vehicle at the end of the simulation, if any.
        start_hour: Hour of the day the run started at.
        start_solution: Best individual, the warm start of the next run.
        washingstart: Start hour of the home appliance, if any.
        extra_data: Extra data of the run.
        fitness_history: Fitness per generation.
        fixed_seed: Seed of the run.
    """

    ac_charge: list[float]
    dc_charge: list[float]
    discharge_allowed: list[int]
    ev_charge_hours_float: Optional[list[float]]
    result: Genetic0SimulationOutput
    ev_obj: Optional[Genetic0ElectricVehicleState]
    start_hour: int
    start_solution: Optional[list[float]]
    washingstart: Optional[int]
    extra_data: Optional[dict[str, Union[list[int], list[float]]]]
    fitness_history: Optional[dict[str, Union[list[int], list[float]]]]
    fixed_seed: Optional[int]
