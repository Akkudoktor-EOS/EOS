import random
from typing import Any, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self
from pathlib import Path

from akkudoktoreos.config import AppConfig
from akkudoktoreos.devices.battery import (
    EAutoParameters,
    EAutoResult,
    PVAkku,
    PVAkkuParameters,
)
from akkudoktoreos.prediction.self_consumption_probability import (
    self_consumption_probability_interpolator,
)
from akkudoktoreos.devices.generic import HomeAppliance, HomeApplianceParameters
from akkudoktoreos.devices.inverter import Wechselrichter, WechselrichterParameters
from akkudoktoreos.prediction.ems import (
    EnergieManagementSystem,
    EnergieManagementSystemParameters,
    SimulationResult,
)
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
            raise ValueError("Input lists have different lengths")
        return self

    @field_validator("start_solution")
    def validate_start_solution(
        cls, start_solution: Optional[list[float]]
    ) -> Optional[list[float]]:
        if start_solution is not None and len(start_solution) < 2:
            raise ValueError("Requires at least two values.")
        return start_solution


class OptimizeResponse(BaseModel):
    """**Note**: The first value of "Last_Wh_per_hour", "Netzeinspeisung_Wh_per_hour", and "Netzbezug_Wh_per_hour", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

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


class optimization_problem:
    def __init__(
        self,
        config: AppConfig,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self._config = config
        self.prediction_hours = config.eos.prediction_hours
        self.strafe = config.eos.penalty
        self.opti_param: dict[str, Any] = {}
        self.fixed_eauto_hours = config.eos.prediction_hours - config.eos.optimization_hours
        self.possible_charge_values = config.eos.available_charging_rates_in_percentage
        self.verbose = verbose
        self.fix_seed = fixed_seed
        self.optimize_ev = True
        self.optimize_dc_charge = False

        # Set a fixed seed for random operations if provided
        if fixed_seed is not None:
            random.seed(fixed_seed)

    def decode_charge_discharge(
        self, discharge_hours_bin: list[int]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decode the input array into ac_charge, dc_charge, and discharge arrays."""
        discharge_hours_bin_np = np.array(discharge_hours_bin)
        len_ac = len(self._config.eos.available_charging_rates_in_percentage)

        # Categorization:
        # Idle:       0 .. len_ac-1
        # Discharge:  len_ac .. 2*len_ac - 1
        # AC Charge:  2*len_ac .. 3*len_ac - 1
        # DC optional: 3*len_ac (not allowed), 3*len_ac + 1 (allowed)

        # Idle has no charge, Discharge has binary 1, AC Charge has corresponding values
        # Idle states
        idle_mask = (discharge_hours_bin_np >= 0) & (discharge_hours_bin_np < len_ac)

        # Discharge states
        discharge_mask = (discharge_hours_bin_np >= len_ac) & (discharge_hours_bin_np < 2 * len_ac)

        # AC states
        ac_mask = (discharge_hours_bin_np >= 2 * len_ac) & (discharge_hours_bin_np < 3 * len_ac)
        ac_indices = (discharge_hours_bin_np[ac_mask] - 2 * len_ac).astype(int)

        # DC states (if enabled)
        if self.optimize_dc_charge:
            dc_not_allowed_state = 3 * len_ac
            dc_allowed_state = 3 * len_ac + 1
            dc_charge = np.where(discharge_hours_bin_np == dc_allowed_state, 1, 0)
        else:
            dc_charge = np.ones_like(discharge_hours_bin_np, dtype=float)

        # Generate the result arrays
        discharge = np.zeros_like(discharge_hours_bin_np, dtype=int)
        discharge[discharge_mask] = 1  # Set Discharge states to 1

        ac_charge = np.zeros_like(discharge_hours_bin_np, dtype=float)
        ac_charge[ac_mask] = [
            self._config.eos.available_charging_rates_in_percentage[i] for i in ac_indices
        ]

        # Idle is just 0, already default.

        return ac_charge, dc_charge, discharge

    def mutate(self, individual: list[int]) -> tuple[list[int]]:
        """Custom mutation function for the individual."""
        # Calculate the number of states
        len_ac = len(self._config.eos.available_charging_rates_in_percentage)
        if self.optimize_dc_charge:
            total_states = 3 * len_ac + 2
        else:
            total_states = 3 * len_ac

        # 1. Mutating the charge_discharge part
        charge_discharge_part = individual[: self.prediction_hours]
        (charge_discharge_mutated,) = self.toolbox.mutate_charge_discharge(charge_discharge_part)

        # Instead of a fixed clamping to 0..8 or 0..6 dynamically:
        charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, total_states - 1)
        individual[: self.prediction_hours] = charge_discharge_mutated

        # 2. Mutating the EV charge part, if active
        if self.optimize_ev:
            ev_charge_part = individual[self.prediction_hours : self.prediction_hours * 2]
            (ev_charge_part_mutated,) = self.toolbox.mutate_ev_charge_index(ev_charge_part)
            ev_charge_part_mutated[self.prediction_hours - self.fixed_eauto_hours :] = [
                0
            ] * self.fixed_eauto_hours
            individual[self.prediction_hours : self.prediction_hours * 2] = ev_charge_part_mutated

        # 3. Mutating the appliance start time, if applicable
        if self.opti_param["home_appliance"] > 0:
            appliance_part = [individual[-1]]
            (appliance_part_mutated,) = self.toolbox.mutate_hour(appliance_part)
            individual[-1] = appliance_part_mutated[0]

        return (individual,)

    # Method to create an individual based on the conditions
    def create_individual(self) -> list[int]:
        # Start with discharge states for the individual
        individual_components = [
            self.toolbox.attr_discharge_state() for _ in range(self.prediction_hours)
        ]

        # Add EV charge index values if optimize_ev is True
        if self.optimize_ev:
            individual_components += [
                self.toolbox.attr_ev_charge_index() for _ in range(self.prediction_hours)
            ]

        # Add the start time of the household appliance if it's being optimized
        if self.opti_param["home_appliance"] > 0:
            individual_components += [self.toolbox.attr_int()]

        return creator.Individual(individual_components)

    def split_individual(
        self, individual: list[int]
    ) -> tuple[list[int], Optional[list[int]], Optional[int]]:
        """Split the individual solution into its components.

        Components:
        1. Discharge hours (binary),
        2. Electric vehicle charge hours (float),
        3. Dishwasher start time (integer if applicable).
        """
        discharge_hours_bin = individual[: self.prediction_hours]
        eautocharge_hours_index = (
            individual[self.prediction_hours : self.prediction_hours * 2]
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

        # Remove existing definitions if any
        for attr in ["FitnessMin", "Individual"]:
            if attr in creator.__dict__:
                del creator.__dict__[attr]

        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        self.toolbox = base.Toolbox()
        len_ac = len(self._config.eos.available_charging_rates_in_percentage)

        # Total number of states without DC:
        # Idle: len_ac states
        # Discharge: len_ac states
        # AC-Charge: len_ac states
        # Total without DC: 3 * len_ac

        # With DC: + 2 states
        if self.optimize_dc_charge:
            total_states = 3 * len_ac + 2
        else:
            total_states = 3 * len_ac

        # State space: 0 .. (total_states - 1)
        self.toolbox.register("attr_discharge_state", random.randint, 0, total_states - 1)

        # EV attributes
        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len_ac - 1,
            )

        # Household appliance start time
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        self.toolbox.register("individual", self.create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)

        # Mutation operator for charge/discharge states
        self.toolbox.register(
            "mutate_charge_discharge", tools.mutUniformInt, low=0, up=total_states - 1, indpb=0.2
        )

        # Mutation operator for EV states
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len_ac - 1,
            indpb=0.2,
        )

        # Mutation for household appliance
        self.toolbox.register("mutate_hour", tools.mutUniformInt, low=start_hour, up=23, indpb=0.2)

        # Custom mutate function remains unchanged
        self.toolbox.register("mutate", self.mutate)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(
        self, individual: list[int], ems: EnergieManagementSystem, start_hour: int
    ) -> dict[str, Any]:
        """Simulates the energy management system (EMS) using the provided individual solution.

        This is an internal function.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            individual
        )
        if washingstart_int is not None:
            ems.set_home_appliance_start(washingstart_int, global_start_hour=start_hour)

        ac, dc, discharge = self.decode_charge_discharge(discharge_hours_bin)

        ems.set_akku_discharge_hours(discharge)
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            ems.set_akku_dc_charge_hours(dc)
        ems.set_akku_ac_charge_hours(ac)

        if eautocharge_hours_index is not None:
            eautocharge_hours_float = [
                self._config.eos.available_charging_rates_in_percentage[i]
                for i in eautocharge_hours_index
            ]
            ems.set_ev_charge_hours(np.array(eautocharge_hours_float))
        else:
            ems.set_ev_charge_hours(np.full(self.prediction_hours, 0))
        return ems.simuliere(start_hour)

    def evaluate(
        self,
        individual: list[int],
        ems: EnergieManagementSystem,
        parameters: OptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> Tuple[float]:
        """Evaluate the fitness of an individual solution based on the simulation results."""
        # try:
        o = self.evaluate_inner(individual, ems, start_hour)
        # except Exception as e:
        #    return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        discharge_hours_bin, eautocharge_hours_index, _ = self.split_individual(individual)

        # Small Penalty for not discharging
        # gesamtbilanz += sum(
        #    0.01 for i in range(start_hour, self.prediction_hours) if discharge_hours_bin[i] == 0.0
        # )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        # if parameters.eauto_min_soc_prozent - ems.eauto.ladezustand_in_prozent() <= 0.0 and  self.optimize_ev:
        #     gesamtbilanz += sum(
        #         self.strafe for ladeleistung in eautocharge_hours_index if ladeleistung != 0.0
        #     )

        individual.extra_data = (  # type: ignore[attr-defined]
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent()
            if parameters.eauto and ems.eauto
            else 0,
        )

        # Adjust total balance with battery value and penalties for unmet SOC

        restwert_akku = ems.akku.aktueller_energieinhalt() * parameters.ems.preis_euro_pro_wh_akku
        # print(ems.akku.aktueller_energieinhalt()," * ", parameters.ems.preis_euro_pro_wh_akku , " ", restwert_akku, " ", gesamtbilanz)
        gesamtbilanz += -restwert_akku
        # print(gesamtbilanz)
        if self.optimize_ev:
            gesamtbilanz += max(
                0,
                (
                    parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent()
                    if parameters.eauto and ems.eauto
                    else 0
                )
                * self.strafe,
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
        start_hour: int,
        worst_case: bool = False,
        ngen: int = 200,
    ) -> OptimizeResponse:
        """Perform EMS (Energy Management System) optimization and visualize results."""
        einspeiseverguetung_euro_pro_wh = np.full(
            self.prediction_hours, parameters.ems.einspeiseverguetung_euro_pro_wh
        )

        # 1h Load to Sub 1h Load Distribution -> SelfConsumptionRate
        sc = self_consumption_probability_interpolator(
            Path(__file__).parent.resolve() / ".." / "data" / "regular_grid_interpolator.pkl"
        )

        # Initialize PV and EV batteries
        akku = PVAkku(
            parameters.pv_akku,
            hours=self.prediction_hours,
        )
        akku.set_charge_per_hour(np.full(self.prediction_hours, 1))

        eauto: Optional[PVAkku] = None
        if parameters.eauto:
            eauto = PVAkku(
                parameters.eauto,
                hours=self.prediction_hours,
            )
            eauto.set_charge_per_hour(np.full(self.prediction_hours, 1))
            self.optimize_ev = (
                parameters.eauto.min_soc_prozent - parameters.eauto.start_soc_prozent >= 0
            )
        else:
            self.optimize_ev = False

        # Initialize household appliance if applicable
        dishwasher = (
            HomeAppliance(
                parameters=parameters.dishwasher,
                hours=self.prediction_hours,
            )
            if parameters.dishwasher is not None
            else None
        )

        # Initialize the inverter and energy management system
        wr = Wechselrichter(
            parameters.wechselrichter,
            akku,
            self_consumption_predictor=sc,
        )
        ems = EnergieManagementSystem(
            self._config.eos,
            parameters.ems,
            wechselrichter=wr,
            eauto=eauto,
            home_appliance=dishwasher,
        )

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment({"home_appliance": 1 if dishwasher else 0}, start_hour)
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, ems, parameters, start_hour, worst_case),
        )
        start_solution, extra_data = self.optimize(parameters.start_solution, ngen=ngen)

        # Perform final evaluation on the best solution
        o = self.evaluate_inner(start_solution, ems, start_hour)
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            start_solution
        )
        eautocharge_hours_float = (
            [
                self._config.eos.available_charging_rates_in_percentage[i]
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
            config=self._config,
            extra_data=extra_data,
        )

        return OptimizeResponse(
            **{
                "ac_charge": ac_charge,
                "dc_charge": dc_charge,
                "discharge_allowed": discharge,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": SimulationResult(**o),
                "eauto_obj": ems.eauto,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
            }
        )
