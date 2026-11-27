"""The outcome of the request layer: everything an optimization run needs."""

from dataclasses import dataclass, field

from akkudoktoreos.devices.appliancescheduling import AppliancePlanInput
from akkudoktoreos.optimization.kernel.problem import PlanningProblem
from akkudoktoreos.optimization.objective import ObjectiveSettings
from akkudoktoreos.optimization.request.grid import Forecasts, TimeGrid
from akkudoktoreos.optimization.request.thermal import ThermalDeviceInfo
from akkudoktoreos.optimization.search.settings import SearchSettings, SearchSpace
from akkudoktoreos.optimization.solutionmodel import PlanningWarning


@dataclass(frozen=True, eq=False)
class PlanningTask:
    """A planning task with its objective, search settings and the names of its devices.

    Attributes:
        problem: The given data: grid, forecasts, devices, grid connection.
        objective: Weights and targets of the objective.
        search: Control levels and effort limits of the search.
        space: Start slots the appliances may use.
        grid: The time grid (start of slot 0, interval, horizon).
        forecasts: The forecast series as read (control horizon plus tail, slot 0 not reduced).
        battery_ids: Device ids of the batteries, in plan order.
        vehicle_ids: Device ids of the vehicles.
        inverter_ids: Device ids of the inverters.
        appliance_ids: Device ids of the appliances.
        appliance_pinned_end_slots: First slot after the run in progress of each appliance (0 none).
        thermal: The thermal devices with the comfort bands they were built from.
        warnings: Parts the request left out or adjusted (heat pumps that could not be set up,
            a vehicle deadline moved to the first slot), copied into the solution.
    """

    problem: PlanningProblem
    objective: ObjectiveSettings
    search: SearchSettings
    space: SearchSpace
    grid: TimeGrid
    forecasts: Forecasts
    battery_ids: tuple[str, ...] = ()
    vehicle_ids: tuple[str, ...] = ()
    inverter_ids: tuple[str, ...] = ()
    appliance_ids: tuple[str, ...] = ()
    appliance_pinned_end_slots: tuple[int, ...] = ()
    thermal: tuple[ThermalDeviceInfo, ...] = field(default_factory=tuple)
    appliance_inputs: tuple[AppliancePlanInput, ...] = ()
    warnings: tuple[PlanningWarning, ...] = ()
