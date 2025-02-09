from akkudoktoreos.optimization.genetic import OptimizationParameters, OptimizeResponse
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings
from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
)
from pyscipopt import Model
from typing import Any, Dict, List, Optional, Tuple, Union

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
            paramaters: OptimizationParameters,
            start_hour: Optional[int] = None,
    ) -> OptimizeResponse:
        """
        This function implements an exact optimization problem that tries to minimize the cost.
        """
        if start_hour is None:
            start_hour = self.ems.start_datetime.hour

        # define sets
        time_steps = None
        batteries = None

        # define parameters
        self.define_model_parameters()

        # define optimization model
        self.define_model()

        # solve optimization model

        # solution needs to be always feasible

        # transform solution to target output format
        ac_charge = None
        dc_charge = None
        discharge = None
        eautocharge_hours_float = None
        SimulationResult = None
        start_solution = None
        washingstart_int = None
        return OptimizeResponse(
            **{
                "ac_charge": ac_charge,
                "dc_charge": dc_charge,
                "discharge_allowed": discharge,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": SimulationResult(**o),
                "eauto_obj": self.ems.ev,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
            }
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

