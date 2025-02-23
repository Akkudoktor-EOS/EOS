from typing import Any, Optional

from pydantic import Field
from pyscipopt import Model, quicksum

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
)
from akkudoktoreos.core.pydantic import ParametersBaseModel
from akkudoktoreos.optimization.genetic import OptimizationParameters


class ExactSolutionResponse(ParametersBaseModel):
    """Response model for the exact optimization solution."""

    akku_charge: list[float] = Field(
        description="Array with target charging / Discharging values in wh."
    )
    eauto_charge: Optional[list[float]] = Field(
        default=None, description="Array containing electric vehicle charging values in wh."
    )


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
    ) -> ExactSolutionResponse:
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
            ExactSolutionResponse: Optimization results containing optimal charging schedules.

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
        time_steps = range(self.config.optimization_hours)  # Time steps

        # Extract parameters from input
        total_load = parameters.ems.gesamtlast  # Required total energy
        pv_forecast = parameters.ems.pv_prognose_wh  # Forecasted production

        # Battery parameters
        soc_min = {}  # Minimum state of charge
        soc_max = {}  # Maximum state of charge
        soc_init = {}  # Initial state of charge
        power_max = {}  # Maximum charging/discharging power
        capacity = {}  # Battery capacity
        eff_charge = {}  # Charging efficiency
        eff_discharge = {}  # Discharging efficiency
        battery_set = []  # Set of available batteries

        battery_types = ["pv_akku", "eauto"]  # Battery types
        for batt_type in battery_types:
            battery = getattr(parameters, batt_type, None)
            if battery is not None:
                # expects values to be between 0 and 100 to represent %
                soc_min[batt_type] = getattr(battery, "min_soc_percentage", 0)
                # expects values to be between 0 and 100 to represent %
                soc_max[batt_type] = getattr(battery, "max_soc_percentage", 100)
                # expects values to be between 0 and 100 to represent %
                soc_init[batt_type] = getattr(battery, "init_soc_percentage", 50)
                # expects values to be in w
                power_max[batt_type] = getattr(battery, "max_charge_power_w", 0)
                # expects values to be in wh
                capacity[batt_type] = getattr(battery, "capacity_wh", 0)
                # expects values to be in float in the range 0-1
                eff_charge[batt_type] = getattr(battery, "charging_efficiency", 1)
                # expects values to be in float in the range 0-1
                eff_discharge[batt_type] = getattr(battery, "discharging_efficiency", 1)

                battery_set.append(batt_type)

        if len(battery_set) == 0:
            print(
                "Please provide battery parameters for optimization.\nCurrently available battery types: pv_akku, eauto."
            )

        # Price parameters
        price_import = parameters.ems.strompreis_euro_pro_wh  # Price for buying from grid
        price_export = parameters.ems.einspeiseverguetung_euro_pro_wh  # Price for selling to grid
        price_storage = (
            parameters.ems.preis_euro_pro_wh_akku
        )  # Value of stored energy at end of horizon

        # Create variables
        charge = {}  # Charging power
        discharge = {}  # Discharging power
        soc = {}  # State of charge
        for batt_type in battery_set:
            for t in time_steps:
                charge[batt_type, t] = model.addVar(
                    name=f"charge_{batt_type}_{t}", vtype="C", lb=0, ub=power_max[batt_type]
                )
                discharge[batt_type, t] = model.addVar(
                    name=f"discharge_{batt_type}_{t}", vtype="C", lb=0, ub=power_max[batt_type]
                )
                soc[batt_type, t] = model.addVar(
                    name=f"soc_{batt_type}_{t}",
                    vtype="C",
                    lb=soc_min[batt_type],
                    ub=soc_max[batt_type],
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
                quicksum(
                    -charge[batt_type, t] + discharge[batt_type, t] for batt_type in battery_set
                )
                + pv_forecast[t]
                + grid_import[t]
                == grid_export[t] + total_load[t],
                name=f"grid_balance_{t}",
            )

        # Battery dynamics constraints
        for batt_type in battery_set:
            for t in time_steps:
                if t == time_steps[0]:
                    model.addCons(
                        soc_init[batt_type] * capacity[batt_type] / 100
                        + eff_charge[batt_type] * charge[batt_type, t]
                        - (1 / eff_discharge[batt_type]) * discharge[batt_type, t]
                        == soc[batt_type, t] * capacity[batt_type] / 100,
                        name=f"battery_dynamics_{batt_type}_{t}",
                    )
                else:
                    model.addCons(
                        soc[batt_type, t - 1] * capacity[batt_type] / 100
                        + eff_charge[batt_type] * charge[batt_type, t]
                        - (1 / eff_discharge[batt_type]) * discharge[batt_type, t]
                        == soc[batt_type, t] * capacity[batt_type] / 100,
                        name=f"battery_dynamics_{batt_type}_{t}",
                    )

        # Prevent simultaneous import and export when import price is less than or equal to export price
        for t in time_steps:
            if isinstance(price_export, float):
                enforce_flow = price_import[t] <= price_export
            else:
                enforce_flow = price_import[t] <= price_export[t]

            if enforce_flow:
                flow_direction = model.addVar(name=f"flow_direction_{t}", vtype="B", lb=0, ub=1)
                max_bezug = sum(
                    eff_charge[batt_type] * power_max[batt_type] for batt_type in battery_set
                ) + max(total_load)
                max_einspeise = sum(
                    eff_discharge[batt_type] * power_max[batt_type] for batt_type in battery_set
                ) + max(pv_forecast)
                big_m = max(max_bezug, max_einspeise)
                model.addCons(
                    grid_export[t] <= big_m * flow_direction, name=f"export_constraint_{t}"
                )
                model.addCons(
                    grid_import[t] <= big_m * (1 - flow_direction), name=f"import_constraint_{t}"
                )

        # Set objective
        objective = quicksum(
            -grid_import[t] * price_import[t] + grid_export[t] * price_export for t in time_steps
        ) + quicksum(
            soc[batt_type, time_steps[-1]] * price_storage * capacity[batt_type]
            for batt_type in battery_set
        )
        model.setObjective(objective, "maximize")
        model.optimize()

        # Solve the model
        if self.verbose:
            print("Number of variables:", len(model.getVars()))
            print("Number of constraints:", len(model.getConss()))
            print("Objective value:", model.getObjVal())

        if model.getStatus() != "optimal":
            raise ValueError("No optimal solution found")

        # Extract solution
        if "pv_akku" in battery_set:
            akku_charge = [
                model.getVal(charge["pv_akku", t]) - model.getVal(discharge["pv_akku", t])
                for t in time_steps
            ]
        else:
            akku_charge = []

        if "eauto" in battery_set:
            ev_charge = [model.getVal(charge["eauto", t]) for t in time_steps]
        else:
            ev_charge = None

        return ExactSolutionResponse(
            akku_charge=akku_charge,
            eauto_charge=ev_charge,
        )
