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
        self.possible_charge_values = (
            self.config.optimization_ev_available_charge_rates_percent
        )
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
                - Load profiles (gesamtlast)
                - PV generation forecast (pv_prognose_wh)
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
        set_t = range(self.config.prediction_hours)  # Time steps

        # Extract parameters from input
        g_t = parameters.ems.gesamtlast  # Required total energy
        f_t = parameters.ems.pv_prognose_wh  # Forecasted production

        # Battery parameters
        s_min = {}
        s_max = {}
        s_init = {}
        k_max = {}
        cap = {}
        eta_c = {}
        eta_d = {}
        set_i = []
        battery_cand = ["pv_akku", "eauto"]  # Battery types
        for i in battery_cand:
            battery = getattr(parameters, i, None)
            if battery is not None:
                s_min[i] = getattr(battery, "min_soc_percentage", 0)
                s_max[i] = getattr(battery, "max_soc_percentage", 100)
                s_init[i] = getattr(battery, "init_soc_percentage", 50)
                k_max[i] = getattr(battery, "max_charge_power_w", None)
                cap[i] = getattr(battery, "capacity_wh", None)
                # Charging efficiency
                eta_c[i] = getattr(battery, "charging_efficiency", 1)
                # Discharging efficiency
                eta_d[i] = getattr(battery, "discharging_efficiency", 1)
                set_i.append(i)

        # Price parameters
        p_N = parameters.ems.strompreis_euro_pro_wh  # Price for buying from grid
        p_E = (
            parameters.ems.einspeiseverguetung_euro_pro_wh
        )  # Price for selling to grid
        p_a = (
            parameters.ems.preis_euro_pro_wh_akku
        )  # Value of stored energy at end of horizon cents per wh

        # Create variables
        c = {}  # Charging power
        d = {}  # Discharging power
        s = {}  # State of charge
        for i in set_i:
            for t in set_t:
                c[i, t] = model.addVar(name=f"c_{i}_{t}", vtype="C", lb=0, ub=k_max[i])
                d[i, t] = model.addVar(name=f"d_{i}_{t}", vtype="C", lb=0, ub=k_max[i])
                s[i, t] = model.addVar(
                    name=f"s_{i}_{t}", vtype="C", lb=s_min[i], ub=s_max[i]
                )

        n = {}  # Grid import
        e = {}  # Grid export
        for t in set_t:
            n[t] = model.addVar(name=f"n_{t}", vtype="C", lb=0)
            e[t] = model.addVar(name=f"e_{t}", vtype="C", lb=0)

        # Add constraints
        # Grid balance constraint
        for t in set_t:
            model.addCons(
                quicksum(-c[i, t] + d[i, t] for i in set_i) + f_t[t] + n[t]
                == e[t] + g_t[t],
                name=f"grid_balance_{t}",
            )

        # Battery dynamics constraints
        for i in set_i:
            for t in set_t[:-1]:
                if t == set_t[0]:
                    model.addCons(
                        s[i, t]
                        == s_init[i]
                        + eta_c[i] * c[i, t] / cap[i]
                        - (1 / eta_d[i]) * d[i, t] / cap[i],
                        name=f"battery_dynamics_{i}_{t}",
                    )
                else:
                    model.addCons(
                        s[i, t]
                        == s[i, t - 1]
                        + eta_c[i] * c[i, t] / cap[i]
                        - (1 / eta_d[i]) * d[i, t] / cap[i],
                        name=f"battery_dynamics_{i}_{t}",
                    )

        # We need to forbid the combination of einspese and netzbezugn, in times where ein is better than
        for t in set_t:
            if p_N[t] <= p_E:
                f = model.addVar(
                    name=f"enforce_onedirectional_powerflow{t}", vtype="B", lb=0, ub=1
                )
                big_m = sum(eta_c[i] * k_max[i] for i in set_i) + max(g_t)
                model.addCons(e[t] <= big_m * f, name=f"set_indicator_if_einpeisung{t}")
                model.addCons(
                    n[t] <= big_m * (1 - f), name=f"no_netzbezug_if_einpeisung{t}"
                )

        # Set objective
        objective = quicksum(-n[t] * p_N[t] + e[t] * p_E for t in set_t) + quicksum(
            s[i, set_t[-1]] * p_a for i in set_i
        )
        model.setObjective(objective, "maximize")

        # Solve the model
        # Print the number of variables and constraints
        if self.verbose:
            print("Number of variables:", len(model.getVars()))
            print("Number of constraints:", len(model.getConss()))

        model.optimize()

        if model.getStatus() != "optimal":
            raise ValueError("No optimal solution found")

        # Extract solution
        ac_charge = [model.getVal(c["pv_akku", t]) / k_max["eauto"] for t in set_t]
        dc_charge = [model.getVal(c["pv_akku", t]) / k_max["pv_akku"] for t in set_t]

        if "eauto" in set_i:
            eauto_charge = [model.getVal(c["eauto", t]) / k_max["eauto"] for t in set_t]
        else:
            eauto_charge = None

        return ExactSolutioResponse(
            ac_charge=ac_charge,
            dc_charge=dc_charge,
            eauto_charge=eauto_charge,
        )
