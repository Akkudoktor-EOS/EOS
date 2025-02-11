from akkudoktoreos.optimization.genetic import OptimizationParameters
from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
)

from typing import Any, Optional
from pyscipopt import Model, quicksum

from akkudoktoreos.core.pydantic import ParametersBaseModel
from pydantic import Field, field_validator
from akkudoktoreos.devices.battery import (
    Battery,
    ElectricVehicleResult,
)
from akkudoktoreos.utils.utils import NumpyEncoder


class ExactSolutioResponse(ParametersBaseModel):
    """Response model for the exact optimization solution.

    This class represents the output of the MILP optimization problem, containing
    the optimal charging schedules for different components of the energy system.

    Attributes:
        ac_charge (List[float]): Array containing AC charging values in watt-hours for each timestep.
        dc_charge (List[float]): Array containing DC charging values in watt-hours for each timestep.
        eauto_charge (Optional[List[float]]): Array containing electric vehicle charging values in watt-hours
            for each timestep. Only present if an electric vehicle is part of the system.
    """

    ac_charge: list[float] = Field(description="Array with AC charging values in wh.")
    dc_charge: list[float] = Field(description="Array with DC charging values in wh.")
    eauto_charge: Optional[list[float]] = Field(description="TBD")

    @field_validator(
        "ac_charge",
        "dc_charge",
        "discharge_allowed",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]

    @field_validator(
        "eauto_obj",
        mode="before",
    )
    def convert_eauto(cls, field: Any) -> Any:
        if isinstance(field, Battery):
            return ElectricVehicleResult(**field.to_dict())
        return field


class MILPOptimization(ConfigMixin, DevicesMixin, EnergyManagementSystemMixin):
    """Mixed-Integer Linear Programming Optimization for Energy Management Systems.

    This class implements a Mixed-Integer Linear Programming (MILP) formulation that
    minimizes energy costs while satisfying system constraints. It considers multiple
    energy sources and storage devices, including PV systems, batteries, and electric vehicles.

    The optimization problem is solved using the SCIP solver through the PySCIPOpt interface.

    Attributes:
        opti_param (Dict[str, Any]): Dictionary storing optimization parameters.
        possible_charge_values (List[float]): List of available charge rates as percentages.
        verbose (bool): Flag to control logging verbosity.
    """

    def __init__(
        self,
        verbose: bool = False,
    ):
        """Initialize the MILP optimization problem.

        Args:
            verbose (bool, optional): Enable verbose output. Defaults to False.
        """
        self.opti_param: dict[str, Any] = {}
        self.verbose = verbose

    def optimize_ems(
            self,
            parameters: OptimizationParameters,
    ) -> ExactSolutioResponse:
        """Solve the energy management system optimization problem using MILP.

        This method formulates and solves a MILP problem to minimize energy costs while satisfying
        system constraints. The optimization considers:
        - Grid power exchange (import/export)
        - Battery storage systems
        - PV generation
        - Electric vehicle charging
        - Time-varying electricity prices

        Args:
            parameters (OptimizationParameters): Input parameters containing:
                - Load profiles (total_load)
                - PV generation forecast (pv_forecast_wh)
                - Battery parameters (capacity, efficiency, power limits)
                - Price data (grid import/export prices)
                - Initial conditions

        Returns:
            ExactSolutioResponse: Optimization results containing optimal charging schedules.

        Raises:
            ValueError: If no optimal solution is found.

        Note:
            The optimization problem includes the following key components:

            Variables:
                - c[i,t]: Charging power for storage device i at time t
                - d[i,t]: Discharging power for storage device i at time t
                - s[i,t]: State of charge for storage device i at time t
                - n[t]: Grid import power at time t
                - e[t]: Grid export power at time t

            Constraints:
                1. Power balance at each timestep
                2. Battery dynamics (state of charge evolution)
                3. Operating limits (power, energy capacity)
                4. Grid power flow directionality

            Objective:
                Maximize: sum(-n[t]*p_N[t] + e[t]*p_E[t]) + sum(s[i,T]*p_a)
                where:
                - p_N: Grid import price
                - p_E: Grid export price
                - p_a: Final state of charge value
                - T: Final timestep
        """
        # Create optimization model
        model = Model("energy_management")

        # Define sets
        time_steps = range(self.config.prediction_hours)  # Time steps

        # Extract parameters from input
        total_load = parameters.ems.total_load  # Required total energy
        pv_forecast = parameters.ems.pv_forecast_wh  # Forecasted production

        # Battery parameters
        soc_min = {}  # Minimum state of charge
        soc_max = {}  # Maximum state of charge
        soc_init = {}  # Initial state of charge
        power_max = {}  # Maximum charging/discharging power
        capacity = {}  # Battery capacity
        eff_charge = {}  # Charging efficiency
        eff_discharge = {}  # Discharging efficiency
        battery_set = []  # Set of available batteries

        battery_types = ["pv_battery", "ev"]  # Battery types
        for batt_type in battery_types:
            battery = getattr(parameters, batt_type, None)
            if battery is not None:
                soc_min[batt_type] = getattr(battery, "min_soc_percentage", 0)
                soc_max[batt_type] = getattr(battery, "max_soc_percentage", 100)
                soc_init[batt_type] = getattr(battery, "init_soc_percentage", 50)
                power_max[batt_type] = getattr(battery, "max_charge_power_w", None)
                capacity[batt_type] = getattr(battery, "capacity_wh", None)
                eff_charge[batt_type] = getattr(battery, "charging_efficiency", 1)
                eff_discharge[batt_type] = getattr(battery, "discharging_efficiency", 1)
                battery_set.append(batt_type)

        # Price parameters
        price_import = parameters.ems.grid_price_eur_per_wh  # Price for buying from grid
        price_export = parameters.ems.feed_in_price_eur_per_wh  # Price for selling to grid
        price_storage = parameters.ems.storage_value_eur_per_wh  # Value of stored energy at end of horizon

        # Create variables
        charge = {}  # Charging power
        discharge = {}  # Discharging power
        soc = {}  # State of charge
        for batt_type in battery_set:
            for t in time_steps:
                charge[batt_type, t] = model.addVar(
                    name=f"charge_{batt_type}_{t}",
                    vtype="C",
                    lb=0,
                    ub=power_max[batt_type]
                )
                discharge[batt_type, t] = model.addVar(
                    name=f"discharge_{batt_type}_{t}",
                    vtype="C",
                    lb=0,
                    ub=power_max[batt_type]
                )
                soc[batt_type, t] = model.addVar(
                    name=f"soc_{batt_type}_{t}",
                    vtype="C",
                    lb=soc_min[batt_type],
                    ub=soc_max[batt_type]
                )

        grid_import = {}  # Grid import power
        grid_export = {}  # Grid export power
        for t in time_steps:
            grid_import[t] = model.addVar(name=f"grid_import_{t}", vtype="C", lb=0)
            grid_export[t] = model.addVar(name=f"grid_export_{t}", vtype="C", lb=0)

        # Add constraints
        # Grid balance constraint
        for t in time_steps:
            model.addCons(
                quicksum(-charge[batt_type, t] + discharge[batt_type, t] for batt_type in battery_set)
                + pv_forecast[t] + grid_import[t] == grid_export[t] + total_load[t],
                name=f"grid_balance_{t}",
            )

        # Battery dynamics constraints
        for batt_type in battery_set:
            for t in time_steps[:-1]:
                if t == time_steps[0]:
                    model.addCons(
                        soc[batt_type, t] == soc_init[batt_type]
                        + eff_charge[batt_type] * charge[batt_type, t] / capacity[batt_type]
                        - (1 / eff_discharge[batt_type]) * discharge[batt_type, t] / capacity[batt_type],
                        name=f"battery_dynamics_{batt_type}_{t}",
                    )
                else:
                    model.addCons(
                        soc[batt_type, t] == soc[batt_type, t - 1]
                        + eff_charge[batt_type] * charge[batt_type, t] / capacity[batt_type]
                        - (1 / eff_discharge[batt_type]) * discharge[batt_type, t] / capacity[batt_type],
                        name=f"battery_dynamics_{batt_type}_{t}",
                    )

        # Prevent simultaneous import and export when import price is less than or equal to export price
        for t in time_steps:
            if price_import[t] <= price_export:
                flow_direction = model.addVar(
                    name=f"flow_direction_{t}",
                    vtype="B",
                    lb=0,
                    ub=1
                )
                big_m = sum(eff_charge[batt_type] * power_max[batt_type] for batt_type in battery_set) + max(total_load)
                model.addCons(
                    grid_export[t] <= big_m * flow_direction,
                    name=f"export_constraint_{t}"
                )
                model.addCons(
                    grid_import[t] <= big_m * (1 - flow_direction),
                    name=f"import_constraint_{t}"
                )

        # Set objective
        objective = quicksum(-grid_import[t] * price_import[t] + grid_export[t] * price_export for t in time_steps) + \
                    quicksum(soc[batt_type, time_steps[-1]] * price_storage for batt_type in battery_set)
        model.setObjective(objective, "maximize")

        # Solve the model
        if self.verbose:
            print("Number of variables:", len(model.getVars()))
            print("Number of constraints:", len(model.getConss()))

        model.optimize()

        if model.getStatus() != "optimal":
            raise ValueError("No optimal solution found")

        # Extract solution
        ac_charge = [model.getVal(charge["pv_battery", t]) / power_max["ev"] for t in time_steps]
        dc_charge = [model.getVal(charge["pv_battery", t]) / power_max["pv_battery"] for t in time_steps]

        if "ev" in battery_set:
            ev_charge = [model.getVal(charge["ev", t]) / power_max["ev"] for t in time_steps]
        else:
            ev_charge = None

        return ExactSolutioResponse(
            ac_charge=ac_charge,
            dc_charge=dc_charge,
            eauto_charge=ev_charge,
        )
