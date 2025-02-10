from akkudoktoreos.optimization.genetic import OptimizationParameters, OptimizeResponse
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings
from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
)
from pyscipopt import Model
from typing import Any, Dict, List, Optional, Tuple, Union
from pyscipopt import Model, quicksum
from typing import Dict, List, Optional
import numpy as np

class MILPOptimization(ConfigMixin, DevicesMixin, EnergyManagementSystemMixin):
    """
    This class implements an Mixed-Integer Optimization forumlation, that tries to minimize cost.
    """

    def __init__(
        self,
        verbose: bool = False,
    ):
        """Initialize the optimization problem with the required parameters."""
        self.opti_param: dict[str, Any] = {}
        self.possible_charge_values = self.config.optimization_ev_available_charge_rates_percent
        self.verbose = verbose

    def optimize_ems(
            self,
            parameters: OptimizationParameters,
            start_hour: Optional[int] = None,
    ) -> OptimizeResponse:
        """
        Implements an exact optimization problem to minimize energy costs using PySCIPOpt.
        """
        if start_hour is None:
            start_hour = self.ems.start_datetime.hour

        # Create optimization model
        model = Model("energy_management")

        # Define sets
        T = range(len(parameters.ems.pv_prognose_wh))  # Time steps
        I = ["pv", "eauto"]  # Battery types

        # Extract parameters from input
        g_t = parameters.ems.load_prognose_wh  # Required total energy
        f_t = parameters.ems.pv_prognose_wh  # Forecasted production

        # Battery parameters
        s_min = {
            "pv": parameters.pv_akku.min_soc if parameters.pv_akku else 0,
            "eauto": parameters.eauto.min_soc if parameters.eauto else 0
        }
        s_max = {
            "pv": parameters.pv_akku.max_soc if parameters.pv_akku else 0,
            "eauto": parameters.eauto.max_soc if parameters.eauto else 0
        }
        k_max = {
            "pv": parameters.pv_akku.max_power if parameters.pv_akku else 0,
            "eauto": parameters.eauto.max_power if parameters.eauto else 0
        }

        # Efficiency parameters
        eta_c = 0.95  # Charging efficiency
        eta_d = 0.95  # Discharging efficiency

        # Price parameters
        p_N = parameters.ems.price_buy  # Price for buying from grid
        p_E = parameters.ems.price_sell  # Price for selling to grid
        p_a = 0.1  # Value of stored energy at end of horizon

        # Create variables
        c = {}  # Charging power
        d = {}  # Discharging power
        s = {}  # State of charge
        for i in I:
            for t in T:
                c[i, t] = model.addVar(name=f"c_{i}_{t}", vtype="C", lb=0, ub=k_max[i])
                d[i, t] = model.addVar(name=f"d_{i}_{t}", vtype="C", lb=0, ub=k_max[i])
                s[i, t] = model.addVar(name=f"s_{i}_{t}", vtype="C", lb=s_min[i], ub=s_max[i])

        n = {}  # Grid import
        e = {}  # Grid export
        for t in T:
            n[t] = model.addVar(name=f"n_{t}", vtype="C", lb=0)
            e[t] = model.addVar(name=f"e_{t}", vtype="C", lb=0)

        # Add constraints

        # Grid balance constraint
        for t in T:
            model.addCons(
                quicksum(c[i, t] - d[i, t] for i in I) + f_t[t] + n[t] == e[t] + g_t[t],
                name=f"grid_balance_{t}"
            )

        # Battery dynamics constraints
        for i in I:
            for t in T[:-1]:
                model.addCons(
                    s[i, t + 1] == s[i, t] + eta_c * c[i, t] - (1 / eta_d) * d[i, t],
                    name=f"battery_dynamics_{i}_{t}"
                )

        # Set objective
        objective = quicksum(-n[t] * p_N + e[t] * p_E for t in T) + \
                    quicksum(s[i, T[-1]] * p_a for i in I)
        model.setObjective(objective, "maximize")

        # Solve the model
        model.optimize()

        if model.getStatus() != "optimal":
            raise ValueError("No optimal solution found")

        # Extract solution
        ac_charge = [model.getVal(c["eauto", t]) / k_max["eauto"] if k_max["eauto"] > 0 else 0 for t in T]
        dc_charge = [model.getVal(c["pv", t]) / k_max["pv"] if k_max["pv"] > 0 else 0 for t in T]
        discharge = [1 if model.getVal(d["pv", t]) > 0 or model.getVal(d["eauto", t]) > 0 else 0 for t in T]

        # Calculate simulation results
        grid_import = [model.getVal(n[t]) for t in T]
        grid_export = [model.getVal(e[t]) for t in T]
        battery_soc = {i: [model.getVal(s[i, t]) for t in T] for i in I}

        simulation_result = SimulationResult(
            Last_Wh_per_hour=[None] + grid_import[:-1],
            Netzeinspeisung_Wh_per_hour=[None] + grid_export[:-1],
            Netzbezug_Wh_per_hour=[None] + grid_import[:-1],
            # Add other required fields based on SimulationResult class definition
        )

        return OptimizeResponse(
            ac_charge=ac_charge,
            dc_charge=dc_charge,
            discharge_allowed=discharge,
            eautocharge_hours_float=None,  # Calculate if needed
            result=simulation_result,
            eauto_obj=self.ems.ev,
            start_solution=parameters.start_solution,
            washingstart=None  # Set if washing machine optimization is included
        )


    def define_model_parameters(self):
        """
        This function defines the model parameters for the optimization problem.
        """
        raise NotImplementedError()

    def define_model(self):
        self.define_model_parameters()
        model = Model()
        vars = self._define_variables()

        self._add_objective()
        self._add_constraints()


    def _define_variables(self):
        """
        This function defines the variables for the optimization problem.
        """
        raise NotImplementedError

    def _add_objective(self):
        """
        This function adds the objective to the optimization problem.
        """
        raise NotImplementedError()

    def _add_constraints(self):
        """
        This function adds the constraints to the optimization problem.
        """
        raise NotImplementedError()

