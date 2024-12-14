import random
from typing import Any, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
)
from akkudoktoreos.core.ems import EnergieManagementSystemParameters, SimulationResult
from akkudoktoreos.devices.battery import (
    EAutoParameters,
    EAutoResult,
    PVAkku,
    PVAkkuParameters,
)
from akkudoktoreos.devices.generic import HomeAppliance, HomeApplianceParameters
from akkudoktoreos.devices.inverter import Wechselrichter, WechselrichterParameters
from akkudoktoreos.utils.utils import NumpyEncoder
from akkudoktoreos.visualize import visualisiere_ergebnisse


class OptimizationParameters(BaseModel):
    ems: EnergieManagementSystemParameters
    pv_akku: PVAkkuParameters
    wechselrichter: WechselrichterParameters = WechselrichterParameters()
    eauto: Optional[EAutoParameters]
    dishwasher: Optional[HomeApplianceParameters] = None
    temperature_forecast: Optional[list[float]] = Field(
        default=None,
        description="An array of floats representing the temperature forecast in degrees Celsius for different time intervals.",
    )
    start_solution: Optional[list[float]] = Field(
        default=None, description="Can be `null` or contain a previous solution (if available)."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        arr_length = len(self.ems.pv_prognose_wh)
        if self.temperature_forecast is not None and arr_length != len(self.temperature_forecast):
            raise ValueError("Input lists have different lenghts")
        return self

    @field_validator("start_solution")
    def validate_start_solution(
        cls, start_solution: Optional[list[float]]
    ) -> Optional[list[float]]:
        if start_solution is not None and len(start_solution) < 2:
            raise ValueError("Requires at least two values.")
        return start_solution


class OptimizeResponse(BaseModel):
    """**Note**: The first value of "Last_Wh_pro_Stunde", "Netzeinspeisung_Wh_pro_Stunde" and "Netzbezug_Wh_pro_Stunde", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

    ac_charge: list[float] = Field(
        description="Array with AC charging values as relative power (0-1), other values set to 0."
    )
    dc_charge: list[float] = Field(
        description="Array with DC charging values as relative power (0-1), other values set to 0."
    )
    discharge_allowed: list[int] = Field(
        description="Array with discharge values (1 for discharge, 0 otherwise)."
    )
    eautocharge_hours_float: Optional[list[float]] = Field(description="TBD")
    result: SimulationResult
    eauto_obj: Optional[EAutoResult]
    start_solution: Optional[list[float]] = Field(
        default=None,
        description="An array of binary values (0 or 1) representing a possible starting solution for the simulation.",
    )
    washingstart: Optional[int] = Field(
        default=None,
        description="Can be `null` or contain an object representing the start of washing (if applicable).",
    )

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
        if isinstance(field, PVAkku):
            return EAutoResult(**field.to_dict())
        return field


class optimization_problem(ConfigMixin, DevicesMixin, EnergyManagementSystemMixin):
    def __init__(
        self,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self.opti_param: dict[str, Any] = {}
        self.fixed_eauto_hours = self.config.prediction_hours - self.config.optimization_hours
        self.possible_charge_values = self.config.optimization_ev_available_charge_rates_percent
        self.verbose = verbose
        self.fix_seed = fixed_seed
        self.optimize_ev = True
        self.optimize_dc_charge = False

        # Set a fixed seed for random operations if provided
        if fixed_seed is not None:
            random.seed(fixed_seed)

    def decode_charge_discharge(
        self, discharge_hours_bin: list[float]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decode the input array `discharge_hours_bin` into three separate arrays for AC charging, DC charging, and discharge.

        The function maps AC and DC charging values to relative power levels (0 to 1), while the discharge remains binary (0 or 1).

        Parameters:
        - discharge_hours_bin (np.ndarray): Input array with integer values representing the different states.
        The states are:
        0: No action ("idle")
        1: Discharge ("discharge")
        2-6: AC charging with different power levels ("ac_charge")
        7-8: DC charging Dissallowed/allowed ("dc_charge")

        Returns:
        - ac_charge (np.ndarray): Array with AC charging values as relative power (0-1), other values set to 0.
        - dc_charge (np.ndarray): Array with DC charging values as relative power (0-1), other values set to 0.
        - discharge (np.ndarray): Array with discharge values (1 for discharge, 0 otherwise).
        """
        # Convert the input list to a NumPy array, if it's not already
        discharge_hours_bin_np = np.array(discharge_hours_bin)

        # Create ac_charge array: Only consider values between 2 and 6 (AC charging power levels), set the rest to 0
        ac_charge = np.where(
            (discharge_hours_bin_np >= 2) & (discharge_hours_bin_np <= 6),
            discharge_hours_bin_np - 1,
            0,
        )
        ac_charge = ac_charge / 5.0  # Normalize AC charge to range between 0 and 1

        # Create dc_charge array: 7 = Not allowed (mapped to 0), 8 = Allowed (mapped to 1)
        # Create dc_charge array: Only if DC charge optimization is enabled
        if self.optimize_dc_charge:
            dc_charge = np.where(discharge_hours_bin_np == 8, 1, 0)
        else:
            dc_charge = np.ones_like(
                discharge_hours_bin_np
            )  # Set DC charge to 0 if optimization is disabled

        # Create discharge array: Only consider value 1 (Discharge), set the rest to 0 (binary output)
        discharge = np.where(discharge_hours_bin_np == 1, 1, 0)

        return ac_charge, dc_charge, discharge

    # Custom mutation function that applies type-specific mutations
    def mutate(self, individual: list[int]) -> tuple[list[int]]:
        """Custom mutation function for the individual.

        This function mutates different parts of the individual:
        - Mutates the discharge and charge states (AC, DC, idle) using the split_charge_discharge method.
        - Mutates the EV charging schedule if EV optimization is enabled.
        - Mutates appliance start times if household appliances are part of the optimization.

        Parameters:
        - individual (list): The individual being mutated, which includes different optimization parameters.

        Returns:
        - (tuple): The mutated individual as a tuple (required by DEAP).
        """
        # Step 1: Mutate the charge/discharge states (idle, discharge, AC charge, DC charge)
        # Extract the relevant part of the individual for prediction hours, which represents the charge/discharge behavior.
        charge_discharge_part = individual[: self.config.prediction_hours]

        # Apply the mutation to the charge/discharge part
        (charge_discharge_mutated,) = self.toolbox.mutate_charge_discharge(charge_discharge_part)

        # Ensure that no invalid states are introduced during mutation (valid values: 0-8)
        if self.optimize_dc_charge:
            charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, 8)
        else:
            charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, 6)

        # Use split_charge_discharge to split the mutated array into AC charge, DC charge, and discharge components
        # ac_charge, dc_charge, discharge = self.split_charge_discharge(charge_discharge_mutated)

        # Optionally: You can process the split arrays further if needed, for example,
        # applying additional constraints or penalties, or keeping track of charging limits.

        # Reassign the mutated values back to the individual
        individual[: self.config.prediction_hours] = charge_discharge_mutated

        # Step 2: Mutate EV charging schedule if enabled
        if self.optimize_ev:
            # Extract the relevant part for EV charging schedule
            ev_charge_part = individual[
                self.config.prediction_hours : self.config.prediction_hours * 2
            ]

            # Apply mutation on the EV charging schedule
            (ev_charge_part_mutated,) = self.toolbox.mutate_ev_charge_index(ev_charge_part)

            # Ensure the EV does not charge during fixed hours (set those hours to 0)
            ev_charge_part_mutated[self.config.prediction_hours - self.fixed_eauto_hours :] = [
                0
            ] * self.fixed_eauto_hours

            # Reassign the mutated EV charging part back to the individual
            individual[self.config.prediction_hours : self.config.prediction_hours * 2] = (
                ev_charge_part_mutated
            )

        # Step 3: Mutate appliance start times if household appliances are part of the optimization
        if self.opti_param["home_appliance"] > 0:
            # Extract the appliance part (typically a single value for the start hour)
            appliance_part = [individual[-1]]

            # Apply mutation on the appliance start hour
            (appliance_part_mutated,) = self.toolbox.mutate_hour(appliance_part)

            # Reassign the mutated appliance part back to the individual
            individual[-1] = appliance_part_mutated[0]

        return (individual,)

    # Method to create an individual based on the conditions
    def create_individual(self) -> list[int]:
        # Start with discharge states for the individual
        individual_components = [
            self.toolbox.attr_discharge_state() for _ in range(self.config.prediction_hours)
        ]

        # Add EV charge index values if optimize_ev is True
        if self.optimize_ev:
            individual_components += [
                self.toolbox.attr_ev_charge_index() for _ in range(self.config.prediction_hours)
            ]

        # Add the start time of the household appliance if it's being optimized
        if self.opti_param["home_appliance"] > 0:
            individual_components += [self.toolbox.attr_int()]

        return creator.Individual(individual_components)

    def split_individual(
        self, individual: list[float]
    ) -> tuple[list[float], Optional[list[float]], Optional[int]]:
        """Split the individual solution into its components.

        Components:
        1. Discharge hours (binary),
        2. Electric vehicle charge hours (float),
        3. Dishwasher start time (integer if applicable).
        """
        discharge_hours_bin = individual[: self.config.prediction_hours]
        eautocharge_hours_index = (
            individual[self.config.prediction_hours : self.config.prediction_hours * 2]
            if self.optimize_ev
            else None
        )

        washingstart_int = (
            int(individual[-1])
            if self.opti_param and self.opti_param.get("home_appliance", 0) > 0
            else None
        )
        return discharge_hours_bin, eautocharge_hours_index, washingstart_int

    def setup_deap_environment(self, opti_param: dict[str, Any], start_hour: int) -> None:
        """Set up the DEAP environment with fitness and individual creation rules."""
        self.opti_param = opti_param

        # Remove existing FitnessMin and Individual classes from creator if present
        for attr in ["FitnessMin", "Individual"]:
            if attr in creator.__dict__:
                del creator.__dict__[attr]

        # Create new FitnessMin and Individual classes
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        # Initialize toolbox with attributes and operations
        self.toolbox = base.Toolbox()
        if self.optimize_dc_charge:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 8)
        else:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 6)

        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len(self.config.optimization_ev_available_charge_rates_percent) - 1,
            )
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        # Register individual creation function
        self.toolbox.register("individual", self.create_individual)

        # Register population, mating, mutation, and selection functions
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        # self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        # Register separate mutation functions for each type of value:
        # - Discharge state mutation (-5, 0, 1)
        if self.optimize_dc_charge:
            self.toolbox.register(
                "mutate_charge_discharge", tools.mutUniformInt, low=0, up=8, indpb=0.2
            )
        else:
            self.toolbox.register(
                "mutate_charge_discharge", tools.mutUniformInt, low=0, up=6, indpb=0.2
            )
        # - Float mutation for EV charging values
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len(self.config.optimization_ev_available_charge_rates_percent) - 1,
            indpb=0.2,
        )
        # - Start hour mutation for household devices
        self.toolbox.register("mutate_hour", tools.mutUniformInt, low=start_hour, up=23, indpb=0.2)

        # Register custom mutation function
        self.toolbox.register("mutate", self.mutate)

        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual: list[float]) -> dict[str, Any]:
        """Simulates the energy management system (EMS) using the provided individual solution.

        This is an internal function.
        """
        self.ems.reset()
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            individual
        )
        if self.opti_param.get("home_appliance", 0) > 0:
            self.ems.set_home_appliance_start(
                washingstart_int, global_start_hour=self.ems.start_datetime.hour
            )

        ac, dc, discharge = self.decode_charge_discharge(discharge_hours_bin)

        self.ems.set_akku_discharge_hours(discharge)
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            self.ems.set_akku_dc_charge_hours(dc)
        self.ems.set_akku_ac_charge_hours(ac)

        if eautocharge_hours_index is not None:
            eautocharge_hours_float = np.array(
                [
                    self.config.optimization_ev_available_charge_rates_percent[i]
                    for i in eautocharge_hours_index
                ],
                float,
            )
            self.ems.set_ev_charge_hours(eautocharge_hours_float)
        else:
            self.ems.set_ev_charge_hours(np.full(self.config.prediction_hours, 0.0))
        return self.ems.simuliere(self.ems.start_datetime.hour)

    def evaluate(
        self,
        individual: list[float],
        parameters: OptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> Tuple[float]:
        """Evaluate the fitness of an individual solution based on the simulation results."""
        try:
            o = self.evaluate_inner(individual)
        except Exception as e:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        discharge_hours_bin, eautocharge_hours_index, _ = self.split_individual(individual)

        # Small Penalty for not discharging
        gesamtbilanz += sum(
            0.01 for i in range(self.config.prediction_hours) if discharge_hours_bin[i] == 0.0
        )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        # if parameters.eauto_min_soc_prozent - ems.eauto.ladezustand_in_prozent() <= 0.0 and  self.optimize_ev:
        #     gesamtbilanz += sum(
        #         self.config.optimization_penalty for ladeleistung in eautocharge_hours_float if ladeleistung != 0.0
        #     )

        individual.extra_data = (  # type: ignore[attr-defined]
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameters.eauto.min_soc_prozent - self.ems.eauto.ladezustand_in_prozent()
            if parameters.eauto and self.ems.eauto
            else 0,
        )

        # Adjust total balance with battery value and penalties for unmet SOC

        restwert_akku = (
            self.ems.akku.aktueller_energieinhalt() * parameters.ems.preis_euro_pro_wh_akku
        )
        # print(ems.akku.aktueller_energieinhalt()," * ", parameters.ems.preis_euro_pro_wh_akku , " ", restwert_akku, " ", gesamtbilanz)
        gesamtbilanz += -restwert_akku
        # print(gesamtbilanz)
        if self.optimize_ev:
            gesamtbilanz += max(
                0,
                (
                    parameters.eauto.min_soc_prozent - self.ems.eauto.ladezustand_in_prozent()
                    if parameters.eauto and self.ems.eauto
                    else 0
                )
                * self.config.optimization_penalty,
            )

        return (gesamtbilanz,)

    def optimize(
        self, start_solution: Optional[list[float]] = None, ngen: int = 400
    ) -> Tuple[Any, dict[str, list[Any]]]:
        """Run the optimization process using a genetic algorithm."""
        population = self.toolbox.population(n=300)
        hof = tools.HallOfFame(1)
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("min", np.min)

        if self.verbose:
            print("Start optimize:", start_solution)

        # Insert the start solution into the population if provided
        if start_solution is not None:
            for _ in range(3):
                population.insert(0, creator.Individual(start_solution))

        # Run the evolutionary algorithm
        algorithms.eaMuPlusLambda(
            population,
            self.toolbox,
            mu=100,
            lambda_=150,
            cxpb=0.6,
            mutpb=0.4,
            ngen=ngen,
            stats=stats,
            halloffame=hof,
            verbose=self.verbose,
        )

        member: dict[str, list[float]] = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, "extra_data"):
                extra_value1, extra_value2, extra_value3 = ind.extra_data
                member["bilanz"].append(extra_value1)
                member["verluste"].append(extra_value2)
                member["nebenbedingung"].append(extra_value3)

        return hof[0], member

    def optimierung_ems(
        self,
        parameters: OptimizationParameters,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        ngen: int = 600,
    ) -> OptimizeResponse:
        """Perform EMS (Energy Management System) optimization and visualize results."""
        if start_hour is None:
            start_hour = self.ems.start_datetime.hour

        einspeiseverguetung_euro_pro_wh = np.full(
            self.config.prediction_hours, parameters.ems.einspeiseverguetung_euro_pro_wh
        )

        # Initialize PV and EV batteries
        akku = PVAkku(
            parameters.pv_akku,
            hours=self.config.prediction_hours,
        )
        akku.set_charge_per_hour(np.full(self.config.prediction_hours, 1))

        eauto: Optional[PVAkku] = None
        if parameters.eauto:
            eauto = PVAkku(
                parameters.eauto,
                hours=self.config.prediction_hours,
            )
            eauto.set_charge_per_hour(np.full(self.config.prediction_hours, 1))
            self.optimize_ev = (
                parameters.eauto.min_soc_prozent - parameters.eauto.start_soc_prozent >= 0
            )
        else:
            self.optimize_ev = False

        # Initialize household appliance if applicable
        dishwasher = (
            HomeAppliance(
                parameters=parameters.dishwasher,
                hours=self.config.prediction_hours,
            )
            if parameters.dishwasher is not None
            else None
        )

        # Initialize the inverter and energy management system
        wr = Wechselrichter(parameters.wechselrichter, akku)
        self.ems.set_parameters(
            parameters.ems,
            wechselrichter=wr,
            eauto=eauto,
            home_appliance=dishwasher,
        )
        self.ems.set_start_hour(start_hour)

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment({"home_appliance": 1 if dishwasher else 0}, start_hour)
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, parameters, start_hour, worst_case),
        )
        start_solution, extra_data = self.optimize(parameters.start_solution, ngen=ngen)

        # Perform final evaluation on the best solution
        o = self.evaluate_inner(start_solution)
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            start_solution
        )
        eautocharge_hours_float = (
            [
                self.config.optimization_ev_available_charge_rates_percent[i]
                for i in eautocharge_hours_index
            ]
            if eautocharge_hours_index is not None
            else None
        )

        ac_charge, dc_charge, discharge = self.decode_charge_discharge(discharge_hours_bin)
        # Visualize the results
        visualisiere_ergebnisse(
            parameters.ems.gesamtlast,
            parameters.ems.pv_prognose_wh,
            parameters.ems.strompreis_euro_pro_wh,
            o,
            ac_charge,
            dc_charge,
            discharge,
            parameters.temperature_forecast,
            start_hour,
            einspeiseverguetung_euro_pro_wh,
            extra_data=extra_data,
        )

        return OptimizeResponse(
            **{
                "ac_charge": ac_charge,
                "dc_charge": dc_charge,
                "discharge_allowed": discharge,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": SimulationResult(**o),
                "eauto_obj": self.ems.eauto,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
            }
        )
