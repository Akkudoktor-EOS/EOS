"""Genetic algorithm."""

import random
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from deap import algorithms, base, creator, tools
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import ConfigDict, Field

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.devicesabc import ConsumerScheduleMode
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.homeappliance import HomeAppliance
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import (
    GeneticSimulationResult,
    GeneticSolution,
)
from akkudoktoreos.optimization.optimizationabc import OptimizationBase


@dataclass
class ApplianceGeneSlot:
    """One appliance start gene in the genome.

    The gene value is an **index into ``allowed_start_slots``**, not an absolute
    slot. This guarantees every gene value maps to a genuinely valid start and
    keeps all allowed starts equally reachable by mutation/crossover.
    """

    gene_index: int
    appliance_index: int
    device_id: str
    run_index: int
    # Local calendar date of the run for DAILY appliances; None for ONCE.
    run_date: Optional[Any]
    allowed_start_slots: list[int]


@dataclass
class ApplianceGeneLayout:
    """Ordered descriptor of the appliance part of the genome.

    Every genome-building step (create/split/merge/mutate/decode) consumes only
    this descriptor, so the appliance gene block can vary in length with the
    number of devices and DAILY run days without any hard-coded gene positions.
    """

    genes: list[ApplianceGeneSlot] = field(default_factory=list)

    @property
    def n_genes(self) -> int:
        """Number of appliance start genes."""
        return len(self.genes)

    def signature(self) -> tuple:
        """Stable identity of the layout for start-solution compatibility.

        Two layouts with the same length can still describe different schedules;
        the signature captures device, run date and the allowed-start list so a
        cached start solution built for a different layout is not silently
        reused.
        """
        return tuple(
            (gene.device_id, str(gene.run_date), tuple(gene.allowed_start_slots))
            for gene in self.genes
        )


@dataclass(frozen=True)
class FitnessCacheEntry:
    """One canonical, successful fitness evaluation within an optimization run."""

    genome: tuple[int, ...]
    fitness: tuple[float]
    extra_data: tuple[float, float, float]


class GeneticSimulation(PydanticBaseModel):
    """Device simulation for GENETIC optimization algorithm."""

    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )

    start_hour: int = Field(
        default=0,
        ge=0,
        le=23,
        json_schema_extra={"description": "Starting hour on day for optimizations."},
    )

    optimization_hours: Optional[int] = Field(
        default=24,
        ge=0,
        json_schema_extra={"description": "Number of hours into the future for optimizations."},
    )

    prediction_hours: Optional[int] = Field(
        default=48,
        ge=0,
        json_schema_extra={"description": "Number of hours into the future for predictions"},
    )

    load_energy_array: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the total load (consumption) in watts for different time intervals."
        },
    )
    pv_prediction_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
        },
    )
    elect_price_hourly: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the electricity price in euros per watt-hour for different time intervals."
        },
    )
    elect_revenue_per_hour_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the feed-in compensation in euros per watt-hour."
        },
    )
    direct_marketing_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "description": "Use direct marketing behavior for feed-in/export decisions."
        },
    )
    battery: Optional[Battery] = Field(default=None, json_schema_extra={"description": "TBD."})
    ev: Optional[Battery] = Field(default=None, json_schema_extra={"description": "TBD."})
    home_appliances: list[HomeAppliance] = Field(
        default_factory=list,
        json_schema_extra={"description": "Flexible consumers scheduled by the optimizer."},
    )
    inverter: Optional[Inverter] = Field(default=None, json_schema_extra={"description": "TBD."})

    ac_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    dc_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    bat_discharge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    bat_grid_export_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={"description": "Hourly permission for battery discharge into the grid."},
    )
    ev_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    ev_discharge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    def prepare(
        self,
        parameters: GeneticEnergyManagementParameters,
        optimization_hours: int,
        prediction_hours: int,
        ev: Optional[Battery] = None,
        home_appliances: Optional[list[HomeAppliance]] = None,
        inverter: Optional[Inverter] = None,
        direct_marketing_enabled: bool = False,
    ) -> None:
        """Prepare simulation runs.

        Populate internal arrays and device references used during simulation.
        """
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours
        self.direct_marketing_enabled = direct_marketing_enabled

        # Load arrays from provided EMS parameters
        self.load_energy_array = np.array(parameters.gesamtlast, float)
        self.pv_prediction_wh = np.array(parameters.pv_prognose_wh, float)
        self.elect_price_hourly = np.array(parameters.strompreis_euro_pro_wh, float)
        self.elect_revenue_per_hour_arr = (
            np.array(parameters.einspeiseverguetung_euro_pro_wh, float)
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(
                len(self.load_energy_array), parameters.einspeiseverguetung_euro_pro_wh, float
            )
        )

        # Associate devices
        if inverter:
            self.battery = inverter.battery
        else:
            self.battery = None
        self.ev = ev
        self.home_appliances = home_appliances or []
        self.inverter = inverter

        # Initialize per-hour action arrays for the prediction horizon
        self.ac_charge_hours = np.full(self.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_grid_export_hours = np.full(self.prediction_hours, 0.0)
        self.ev_charge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_discharge_hours = np.full(self.prediction_hours, 0.0)

    def reset(self) -> None:
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """Simulate energy usage and costs for the given start hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of last hour (end state)
        """
        # Remember start hour
        self.start_hour = start_hour

        # Provide fast (3x..5x) local read access (vs. self.xxx) for repetitive read access
        load_energy_array_fast = self.load_energy_array
        ev_charge_hours_fast = self.ev_charge_hours
        ev_discharge_hours_fast = self.ev_discharge_hours
        ac_charge_hours_fast = self.ac_charge_hours
        dc_charge_hours_fast = self.dc_charge_hours
        bat_discharge_hours_fast = self.bat_discharge_hours
        bat_grid_export_hours_fast = self.bat_grid_export_hours
        elect_price_hourly_fast = self.elect_price_hourly
        elect_revenue_per_hour_arr_fast = self.elect_revenue_per_hour_arr
        pv_prediction_wh_fast = self.pv_prediction_wh
        battery_fast = self.battery
        ev_fast = self.ev
        home_appliances_fast = self.home_appliances
        inverter_fast = self.inverter
        direct_marketing_enabled_fast = self.direct_marketing_enabled

        # Check for simulation integrity (in a way that mypy understands)
        if (
            load_energy_array_fast is None
            or pv_prediction_wh_fast is None
            or elect_price_hourly_fast is None
            or ev_charge_hours_fast is None
            or ac_charge_hours_fast is None
            or dc_charge_hours_fast is None
            or elect_revenue_per_hour_arr_fast is None
            or bat_discharge_hours_fast is None
            or bat_grid_export_hours_fast is None
            or ev_discharge_hours_fast is None
        ):
            missing = []
            if load_energy_array_fast is None:
                missing.append("Load Energy Array")
            if pv_prediction_wh_fast is None:
                missing.append("PV Prediction Wh")
            if elect_price_hourly_fast is None:
                missing.append("Electricity Price Hourly")
            if ev_charge_hours_fast is None:
                missing.append("EV Charge Hours")
            if ac_charge_hours_fast is None:
                missing.append("AC Charge Hours")
            if dc_charge_hours_fast is None:
                missing.append("DC Charge Hours")
            if elect_revenue_per_hour_arr_fast is None:
                missing.append("Electricity Revenue Per Hour")
            if bat_discharge_hours_fast is None:
                missing.append("Battery Discharge Hours")
            if bat_grid_export_hours_fast is None:
                missing.append("Battery Grid Export Hours")
            if ev_discharge_hours_fast is None:
                missing.append("EV Discharge Hours")
            msg = ", ".join(missing)
            logger.error("Mandatory data missing - %s", msg)
            raise ValueError(f"Mandatory data missing: {msg}")

        if not (
            len(load_energy_array_fast)
            == len(pv_prediction_wh_fast)
            == len(elect_price_hourly_fast)
        ):
            error_msg = f"Array sizes do not match: Load Curve = {len(load_energy_array_fast)}, PV Forecast = {len(pv_prediction_wh_fast)}, Electricity Price = {len(elect_price_hourly_fast)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        end_hour = len(load_energy_array_fast)
        total_hours = end_hour - start_hour

        # Pre-allocate arrays for the results, optimized for speed
        loads_energy_per_hour = np.full((total_hours), np.nan)
        feedin_energy_per_hour = np.full((total_hours), np.nan)
        consumption_energy_per_hour = np.full((total_hours), np.nan)
        costs_per_hour = np.full((total_hours), np.nan)
        revenue_per_hour = np.full((total_hours), np.nan)
        losses_wh_per_hour = np.full((total_hours), np.nan)
        electricity_price_per_hour = np.full((total_hours), np.nan)
        feed_in_tariff_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        if battery_fast:
            # Pre-allocate arrays for the results, optimized for speed
            soc_per_hour = np.full((total_hours), np.nan)

            soc_per_hour[0] = battery_fast.current_soc_percentage()

            # Determine AC charging availability from inverter parameters
            if inverter_fast:
                ac_to_dc_eff_fast = inverter_fast.ac_to_dc_efficiency
                dc_to_ac_eff_fast = inverter_fast.dc_to_ac_efficiency
                max_ac_charge_w_fast = inverter_fast.max_ac_charge_power_w
            else:
                ac_to_dc_eff_fast = 1.0
                dc_to_ac_eff_fast = 1.0
                max_ac_charge_w_fast = None

            ac_charging_possible = ac_to_dc_eff_fast > 0 and (
                max_ac_charge_w_fast is None or max_ac_charge_w_fast > 0
            )

            # If AC charging is disabled via inverter, zero out AC charge hours
            if not ac_charging_possible:
                ac_charge_hours_fast = np.zeros_like(ac_charge_hours_fast)

            # Fill the charge array of the battery
            dc_charge_hours_fast[0:start_hour] = 0
            dc_charge_hours_fast[end_hour:] = 0
            ac_charge_hours_fast[0:start_hour] = 0
            ac_charge_hours_fast[end_hour:] = 0
            battery_fast.charge_array = np.where(
                ac_charge_hours_fast != 0, ac_charge_hours_fast, dc_charge_hours_fast
            )
            # Fill the discharge array of the battery
            bat_discharge_hours_fast[0:start_hour] = 0
            bat_discharge_hours_fast[end_hour:] = 0
            bat_grid_export_hours_fast[0:start_hour] = 0
            bat_grid_export_hours_fast[end_hour:] = 0
            battery_fast.discharge_array = np.where(
                (bat_discharge_hours_fast > 0)
                | (
                    direct_marketing_enabled_fast
                    & (bat_grid_export_hours_fast > 0)
                    & (elect_revenue_per_hour_arr_fast > 0.0)
                ),
                1,
                0,
            )
        else:
            # Default return if no battery is available
            soc_per_hour = np.full((total_hours), 0)
            ac_to_dc_eff_fast = 1.0
            dc_to_ac_eff_fast = 1.0
            max_ac_charge_w_fast = None
            ac_charging_possible = False

        if ev_fast:
            # Pre-allocate arrays for the results, optimized for speed
            soc_ev_per_hour = np.full((total_hours), np.nan)

            soc_ev_per_hour[0] = ev_fast.current_soc_percentage()
            # Fill the charge array of the ev
            ev_charge_hours_fast[0:start_hour] = 0
            ev_charge_hours_fast[end_hour:] = 0
            ev_fast.charge_array = ev_charge_hours_fast
            # Fill the discharge array of the ev
            ev_discharge_hours_fast[0:start_hour] = 0
            ev_discharge_hours_fast[end_hour:] = 0
            ev_fast.discharge_array = ev_discharge_hours_fast
        else:
            # Default return if no electric vehicle is available
            soc_ev_per_hour = np.full((total_hours), 0)

        if home_appliances_fast:
            home_appliance_enabled = True
            # Pre-allocate the aggregate appliance load array (sum over all
            # devices). Each appliance already carries its own resampled load
            # curve, built from the decoded start(s) before this call.
            home_appliance_wh_per_hour = np.full((total_hours), np.nan)
        else:
            home_appliance_enabled = False
            # Default return if no home appliance is available
            home_appliance_wh_per_hour = np.full((total_hours), 0)

        for hour in range(start_hour, end_hour):
            hour_idx = hour - start_hour

            # Accumulate loads and PV generation
            consumption = load_energy_array_fast[hour]
            losses_wh_per_hour[hour_idx] = 0.0

            # Home appliances (sum the per-slot load of all flexible consumers)
            if home_appliance_enabled:
                ha_load = 0.0
                for appliance in home_appliances_fast:
                    ha_load += appliance.get_load_for_hour(hour)
                consumption += ha_load
                home_appliance_wh_per_hour[hour_idx] = ha_load

            # E-Auto handling
            if ev_fast:
                soc_ev_per_hour[hour_idx] = ev_fast.current_soc_percentage()  # save begin state
                if ev_charge_hours_fast[hour] > 0:
                    loaded_energy_ev, verluste_eauto = ev_fast.charge_energy(
                        wh=None, hour=hour, charge_factor=ev_charge_hours_fast[hour]
                    )
                    consumption += loaded_energy_ev
                    losses_wh_per_hour[hour_idx] += verluste_eauto

            # Save battery SOC before inverter processing = true begin-of-interval state.
            # Must be recorded here (before DC charge/discharge) so the displayed SOC at
            # timestamp T reflects what the battery actually had at the START of interval T,
            # not the post-DC result. Consistent with the EV SOC convention above.
            if battery_fast:
                soc_per_hour[hour_idx] = battery_fast.current_soc_percentage()

            # Process inverter logic
            energy_feedin_grid_actual = energy_consumption_grid_actual = losses = eigenverbrauch = (
                0.0
            )

            if inverter_fast:
                energy_produced = pv_prediction_wh_fast[hour]
                hourly_feed_in_tariff = elect_revenue_per_hour_arr_fast[hour]
                battery_grid_export_allowed = (
                    direct_marketing_enabled_fast
                    and hourly_feed_in_tariff > 0.0
                    and bat_grid_export_hours_fast[hour] > 0
                )
                (
                    energy_feedin_grid_actual,
                    energy_consumption_grid_actual,
                    losses,
                    eigenverbrauch,
                ) = inverter_fast.process_energy(
                    energy_produced,
                    consumption,
                    hour,
                    allow_battery_grid_export=battery_grid_export_allowed,
                )
            else:
                hourly_feed_in_tariff = elect_revenue_per_hour_arr_fast[hour]

            # AC PV Battery Charge
            if battery_fast:
                hour_ac_charge = ac_charge_hours_fast[hour]
                if hour_ac_charge > 0.0 and ac_charging_possible:
                    # Cap charge factor by max_ac_charge_power_w if set
                    effective_charge_factor = hour_ac_charge
                    if max_ac_charge_w_fast is not None and battery_fast.max_charge_power_w > 0:
                        # DC power = max_charge_power_w * factor
                        # AC power = DC power / ac_to_dc_eff
                        # AC power must be <= max_ac_charge_power_w
                        max_dc_factor = (
                            max_ac_charge_w_fast * ac_to_dc_eff_fast
                        ) / battery_fast.max_charge_power_w
                        effective_charge_factor = min(effective_charge_factor, max_dc_factor)

                    if effective_charge_factor > 0:
                        battery_charged_energy_actual, battery_losses_actual = (
                            battery_fast.charge_energy(
                                None, hour, charge_factor=effective_charge_factor
                            )
                        )

                        # DC energy entering the battery (before battery internal efficiency)
                        dc_energy = battery_charged_energy_actual + battery_losses_actual
                        # AC energy consumed from grid (accounts for AC→DC conversion loss)
                        ac_energy = dc_energy / ac_to_dc_eff_fast
                        # Inverter AC→DC conversion losses
                        inverter_charge_losses = ac_energy - dc_energy

                        consumption += ac_energy
                        energy_consumption_grid_actual += ac_energy
                        losses_wh_per_hour[hour_idx] += (
                            battery_losses_actual + inverter_charge_losses
                        )

            # Update hourly arrays
            if (
                direct_marketing_enabled_fast
                and hourly_feed_in_tariff < 0.0
                and energy_feedin_grid_actual > 0.0
            ):
                losses_wh_per_hour[hour_idx] += energy_feedin_grid_actual
                energy_feedin_grid_actual = 0.0

            feedin_energy_per_hour[hour_idx] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_idx] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_idx] += losses
            loads_energy_per_hour[hour_idx] = consumption
            hourly_electricity_price = elect_price_hourly_fast[hour]
            electricity_price_per_hour[hour_idx] = hourly_electricity_price
            feed_in_tariff_per_hour[hour_idx] = hourly_feed_in_tariff

            # Financial calculations
            grid_cost = energy_consumption_grid_actual * hourly_electricity_price
            # LCOS is charged exactly once on battery-delivered DC energy. It is
            # not charged on input energy, internal discharge losses, or the
            # downstream DC-to-AC inverter loss.
            battery_lcos_cost = 0.0
            if battery_fast:
                battery_lcos_cost = (
                    battery_fast.discharged_energy_wh(hour)
                    * battery_fast.levelized_cost_of_storage_kwh
                    / 1000.0
                )
            costs_per_hour[hour_idx] = grid_cost + battery_lcos_cost
            revenue_per_hour[hour_idx] = energy_feedin_grid_actual * hourly_feed_in_tariff

        total_cost = np.nansum(costs_per_hour)
        total_losses = np.nansum(losses_wh_per_hour)
        total_revenue = np.nansum(revenue_per_hour)

        # Prepare output dictionary
        return {
            "Last_Wh_pro_Stunde": loads_energy_per_hour,
            "Netzeinspeisung_Wh_pro_Stunde": feedin_energy_per_hour,
            "Netzbezug_Wh_pro_Stunde": consumption_energy_per_hour,
            "Kosten_Euro_pro_Stunde": costs_per_hour,
            "akku_soc_pro_stunde": soc_per_hour,
            "Einnahmen_Euro_pro_Stunde": revenue_per_hour,
            "Gesamtbilanz_Euro": total_cost - total_revenue,  # Fitness score ("FitnessMin")
            "EAuto_SoC_pro_Stunde": soc_ev_per_hour,
            "Gesamteinnahmen_Euro": total_revenue,
            "Gesamtkosten_Euro": total_cost,
            "Verluste_Pro_Stunde": losses_wh_per_hour,
            "Gesamt_Verluste": total_losses,
            "Home_appliance_wh_per_hour": home_appliance_wh_per_hour,
            "Electricity_price": electricity_price_per_hour,
            "Feed_in_tariff": feed_in_tariff_per_hour,
        }


class GeneticOptimization(OptimizationBase):
    """GENETIC algorithm to solve energy optimization."""

    WARM_START_COPIES = 10
    WARM_START_MUTATIONS = 50
    EDUCATED_GUESS_TARGET = 100
    MIN_RANDOM_POPULATION_FRACTION = 0.25
    SURVIVOR_COUNT = 150
    OFFSPRING_COUNT = 150
    EDUCATED_GUESS_EXPORT_QUANTILES = (0.60, 0.75, 0.90)

    # Slot-math helpers — single source of truth for the optimization grid.
    # At the default optimization interval of 3600 s, slot_duration_h is 1.0 and
    # total_slots equals prediction.hours, so the established hourly behaviour is
    # preserved. At 900 s (15 min) slot_duration_h is 0.25 and there are 4x as
    # many slots.
    @property
    def slot_duration_h(self) -> float:
        """Length of one optimization slot in hours (1.0 hourly, 0.25 at 15 min)."""
        interval = self.config.optimization.interval or 3600
        return interval / 3600

    @property
    def slots_per_hour(self) -> int:
        """Number of optimization slots per hour (1 hourly, 4 at 15 min)."""
        interval = self.config.optimization.interval or 3600
        return 3600 // interval

    @property
    def total_slots(self) -> int:
        """Total number of optimization slots = prediction.hours * slots_per_hour."""
        # Read prediction.hours directly to avoid recursing through total_slots.
        return int(self.config.prediction.hours * self.slots_per_hour)

    def _start_day_slot(self) -> int:
        """Slot index of ems.start_datetime counted from the start day's midnight.

        simulate()/evaluate() use the simulation start position as a slot index
        into the prediction/charge arrays. Those arrays begin at the midnight of
        ``ems.start_datetime`` (geneticparams sets ``start_datetime.set(hour=0)``),
        so the index is computed from the same datetime — no timezone conversion —
        keeping it consistent with how the arrays are built. At interval=3600 s
        slots_per_hour == 1 and minute // 60 == 0, so this reduces to
        ``start_datetime.hour`` (the previous hourly behaviour).
        """
        sd = self.ems.start_datetime
        sph = self.slots_per_hour
        slot_minutes = max(1, 60 // sph)
        return sd.hour * sph + sd.minute // slot_minutes

    def __init__(
        self,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        if self.config.optimization.interval not in (900, 3600):
            logger.warning(
                "Genetic optimization interval {} seconds is unsupported; using 3600 seconds.",
                self.config.optimization.interval,
            )
            self.config.optimization.interval = 3600
        self.opti_param: dict[str, Any] = {}
        # Number of slots at the tail of the optimization window where EV
        # charging is fixed to 0. Slot-counted so 15-min runs reserve the right
        # tail length (at interval=3600 s this equals prediction.hours - horizon).
        self.fixed_eauto_hours = max(
            self.total_slots
            - (
                self._start_day_slot()
                + self.config.optimization.horizon_hours * self.slots_per_hour
            ),
            0,
        )
        self.ev_possible_charge_values: list[float] = [1.0]
        # Separate charge-level list for battery AC charging (independent of EV rates).
        # Populated from parameters.pv_akku.charge_rates in optimierung_ems.
        self.bat_possible_charge_values: list[float] = [1.0]
        self.verbose = verbose
        self.fix_seed = fixed_seed
        self.optimize_ev = True
        self.optimize_dc_charge = False
        self.optimize_battery_grid_export = False
        self.fitness_history: dict[str, Any] = {}

        # Set a fixed seed for random operations if provided or in debug mode
        if self.fix_seed is not None:
            random.seed(self.fix_seed)
        elif logger.level == "DEBUG":
            self.fix_seed = random.randint(1, 100000000000)  # noqa: S311
            random.seed(self.fix_seed)

        # Per-run cache for the AC-charge break-even penalty (see evaluate()).
        self._ac_break_even_best_prices: Optional[list[float]] = None

        # Fitness memoization is activated only around optimize(). The cache is
        # never shared across runs because forecasts, prices and device state may
        # have changed even when the genome is identical.
        self._fitness_cache_enabled = False
        self._fitness_cache: dict[tuple[int, ...], FitnessCacheEntry] = {}
        self._fitness_cache_hits = 0
        self._fitness_cache_misses = 0

        # Appliance genome layout, built once per optimization run in
        # optimierung_ems(). Empty by default so setup_deap_environment() can be
        # exercised standalone (e.g. in tests) without appliances.
        self.appliance_layout: ApplianceGeneLayout = ApplianceGeneLayout([])
        # Local datetime of slot index 0 (midnight of the start day), needed to
        # turn decoded start slots into absolute local timestamps.
        self._slot0_datetime: Optional[Any] = None

        # Create Simulation
        self.simulation = GeneticSimulation()

    def _direct_marketing_enabled(self) -> bool:
        """Return whether direct marketing mode is enabled in configuration."""
        try:
            return bool(self.config.feedintariff.direct_marketing_enabled)
        except Exception:
            return False

    def _appliance_horizon_end_slot(self) -> int:
        """Exclusive upper slot bound for appliance runs (end of horizon).

        A run must complete within the optimization horizon. The horizon starts
        at the current slot and lasts ``horizon_hours``; the bound is capped to
        the total slot grid.
        """
        start_slot = self._start_day_slot()
        horizon_slots = self.config.optimization.horizon_hours * self.slots_per_hour
        return min(self.total_slots, start_slot + horizon_slots)

    def _build_appliance_layout(
        self, appliances: list[HomeAppliance], slot0_datetime: Any
    ) -> ApplianceGeneLayout:
        """Compute the appliance genome layout from the configured consumers.

        For each appliance the allowed start slots are computed once. ONCE
        appliances get a single gene; DAILY appliances get one gene per local
        calendar day that still has at least one complete allowed run.

        Raises:
            ValueError: If a ONCE appliance has no valid start within the horizon.
        """
        start_slot = self._start_day_slot()
        horizon_end_slot = self._appliance_horizon_end_slot()
        genes: list[ApplianceGeneSlot] = []
        gene_index = 0
        for appliance_index, appliance in enumerate(appliances):
            allowed = appliance.allowed_start_slots(
                slot0_datetime=slot0_datetime,
                earliest_slot=start_slot,
                horizon_end_slot=horizon_end_slot,
            )
            if appliance.schedule_mode == ConsumerScheduleMode.ONCE:
                if not allowed:
                    raise ValueError(
                        f"Home appliance '{appliance.device_id}' (ONCE) has no valid "
                        f"start slot within the optimization horizon and its time windows."
                    )
                genes.append(
                    ApplianceGeneSlot(
                        gene_index=gene_index,
                        appliance_index=appliance_index,
                        device_id=appliance.device_id,
                        run_index=0,
                        run_date=None,
                        allowed_start_slots=allowed,
                    )
                )
                gene_index += 1
            else:  # DAILY
                by_date: "OrderedDict[Any, list[int]]" = OrderedDict()
                for slot in allowed:
                    run_date = slot0_datetime.add(
                        seconds=slot * appliance.slot_interval_seconds
                    ).date()
                    by_date.setdefault(run_date, []).append(slot)
                if not by_date:
                    logger.warning(
                        "Home appliance '{}' (DAILY) has no valid start slot within the "
                        "horizon; no runs are scheduled.",
                        appliance.device_id,
                    )
                for run_index, (run_date, slots) in enumerate(by_date.items()):
                    genes.append(
                        ApplianceGeneSlot(
                            gene_index=gene_index,
                            appliance_index=appliance_index,
                            device_id=appliance.device_id,
                            run_index=run_index,
                            run_date=run_date,
                            allowed_start_slots=slots,
                        )
                    )
                    gene_index += 1
        return ApplianceGeneLayout(genes)

    def _decode_appliance_starts(
        self, appliance_gene_values: list[int]
    ) -> dict[int, list[int]]:
        """Map appliance gene values to absolute start slots per appliance.

        Each gene value is an index into its gene's ``allowed_start_slots``; it is
        clamped defensively so crossover artefacts can never index out of range.
        """
        starts_per_appliance: dict[int, list[int]] = defaultdict(list)
        for position, gene in enumerate(self.appliance_layout.genes):
            allowed = gene.allowed_start_slots
            if not allowed:
                continue
            value = int(appliance_gene_values[position])
            value = min(max(value, 0), len(allowed) - 1)
            starts_per_appliance[gene.appliance_index].append(allowed[value])
        return starts_per_appliance

    def _apply_appliance_starts(self, appliance_gene_values: list[int]) -> None:
        """Build every appliance's load curve from the decoded starts."""
        if not self.simulation.home_appliances:
            return
        starts_per_appliance = self._decode_appliance_starts(appliance_gene_values)
        for appliance_index, appliance in enumerate(self.simulation.home_appliances):
            appliance.build_load_curve(starts_per_appliance.get(appliance_index, []))

    def _start_solution_matches_layout(self, start_solution: list[float]) -> bool:
        """Check that a start solution's appliance tail fits the current layout.

        A length match alone is insufficient (two different layouts can share a
        length), so every appliance gene value must be a valid index into its
        gene's ``allowed_start_slots``.
        """
        n_genes = self.appliance_layout.n_genes
        if n_genes == 0:
            return True
        if len(start_solution) < n_genes:
            return False
        tail = start_solution[-n_genes:]
        for value, gene in zip(tail, self.appliance_layout.genes):
            if not gene.allowed_start_slots:
                return False
            if not (0 <= int(value) < len(gene.allowed_start_slots)):
                return False
        return True

    def _ac_break_even_prices(
        self,
        prices_arr: Any,
        load_arr: Any,
        free_ac_wh: float,
    ) -> list[float]:
        """Best still-uncovered future price per potential AC-charge slot.

        The AC-charge break-even penalty needs, for every potential charge slot,
        the highest future price whose load is not already covered by the energy
        that is in the battery at simulation start. Prices, loads and the free
        battery energy are constant within one optimization run, so this table
        is computed once per run and looked up in every fitness evaluation.
        (Previously the future list was rebuilt and sorted per slot per
        individual, which dominated the fitness runtime.) The loops replicate
        the former inline computation exactly, keeping results bit-identical.
        """
        n = len(prices_arr)
        best_prices = [0.0] * n
        for hour in range(n):
            # Build list of (price, load_wh) for all future hours in the horizon
            future = [(float(prices_arr[h]), float(load_arr[h])) for h in range(hour + 1, n)]
            # Sort descending by price so we "use" the most expensive hours first
            future.sort(key=lambda x: -x[0])

            # Consume free PV energy against the highest-price future hours.
            # The first uncovered (partially or fully) hour defines the best
            # price still available for the new AC charge.
            remaining_free = free_ac_wh
            best_uncovered_price = 0.0
            for fp, fl in future:
                if remaining_free >= fl:
                    # Entire expensive hour is already covered by free PV energy
                    remaining_free -= fl
                else:
                    # First hour not (fully) covered: this is where new charge goes
                    best_uncovered_price = fp
                    break
            best_prices[hour] = best_uncovered_price
        return best_prices

    def _parameters_for_config(
        self, parameters: GeneticOptimizationParameters
    ) -> GeneticOptimizationParameters:
        """Apply configuration-derived parameter overrides before optimization."""
        if not self._direct_marketing_enabled():
            return parameters

        feed_in_tariff = parameters.ems.einspeiseverguetung_euro_pro_wh
        if (
            isinstance(feed_in_tariff, list)
            and len(feed_in_tariff) == len(parameters.ems.strompreis_euro_pro_wh)
            and len(set(feed_in_tariff)) > 1
        ):
            return parameters

        ems_parameters = parameters.ems.model_copy(
            update={"einspeiseverguetung_euro_pro_wh": list(parameters.ems.strompreis_euro_pro_wh)},
            deep=True,
        )
        return parameters.model_copy(update={"ems": ems_parameters}, deep=True)

    def _parameters_for_slot_grid(
        self, parameters: GeneticOptimizationParameters
    ) -> GeneticOptimizationParameters:
        """Normalize hourly or native-slot EMS input onto the optimization grid.

        API clients historically provide one value per prediction hour. At a
        sub-hourly interval, energy quantities are distributed across the slots
        while price quantities are held constant. Inputs already matching the
        native slot grid are preserved exactly. Any other length is ambiguous and
        rejected instead of silently shortening the simulation horizon.
        """

        def normalize(values: list[float], name: str, *, energy: bool) -> list[float]:
            value_count = len(values)
            if value_count == self.total_slots:
                return list(values)
            if value_count != self.config.prediction.hours:
                raise ValueError(
                    f"{name} has {value_count} values; expected either "
                    f"{self.config.prediction.hours} hourly values or "
                    f"{self.total_slots} optimization-slot values."
                )

            normalized = np.repeat(np.asarray(values, dtype=float), self.slots_per_hour)
            if energy:
                normalized /= self.slots_per_hour
            return normalized.tolist()

        ems = parameters.ems
        feed_in_tariff = ems.einspeiseverguetung_euro_pro_wh
        if isinstance(feed_in_tariff, list):
            normalized_feed_in_tariff: list[float] | float = normalize(
                feed_in_tariff,
                "einspeiseverguetung_euro_pro_wh",
                energy=False,
            )
        else:
            normalized_feed_in_tariff = [float(feed_in_tariff)] * self.total_slots

        normalized_ems = ems.model_copy(
            update={
                "pv_prognose_wh": normalize(ems.pv_prognose_wh, "pv_prognose_wh", energy=True),
                "gesamtlast": normalize(ems.gesamtlast, "gesamtlast", energy=True),
                "strompreis_euro_pro_wh": normalize(
                    ems.strompreis_euro_pro_wh,
                    "strompreis_euro_pro_wh",
                    energy=False,
                ),
                "einspeiseverguetung_euro_pro_wh": normalized_feed_in_tariff,
            },
            deep=True,
        )
        temperature_forecast = parameters.temperature_forecast
        if temperature_forecast is not None:
            if len(temperature_forecast) == self.config.prediction.hours:
                temperature_forecast = [
                    value for value in temperature_forecast for _ in range(self.slots_per_hour)
                ]
            elif len(temperature_forecast) != self.total_slots:
                raise ValueError(
                    f"temperature_forecast has {len(temperature_forecast)} values; expected "
                    f"either {self.config.prediction.hours} hourly values or "
                    f"{self.total_slots} optimization-slot values."
                )
        return parameters.model_copy(
            update={"ems": normalized_ems, "temperature_forecast": temperature_forecast},
            deep=True,
        )

    def _start_solution_for_slot_grid(self, start_solution: list[float]) -> list[float]:
        """Expand a legacy hourly genome to the configured slot grid when possible.

        Only the battery and EV parts are grid-expanded. The appliance start
        genes are indices into interval-dependent allowed-start lists, so they
        are copied verbatim and validated later against the current layout
        (incompatible tails cause the whole start solution to be discarded).
        """
        n_appliance_genes = self.appliance_layout.n_genes
        expected_length = self.total_slots * (2 if self.optimize_ev else 1) + n_appliance_genes
        hourly_length = (
            self.config.prediction.hours * (2 if self.optimize_ev else 1) + n_appliance_genes
        )

        if len(start_solution) == expected_length or self.slots_per_hour == 1:
            return list(start_solution)
        if len(start_solution) != hourly_length:
            return list(start_solution)

        battery_end = self.config.prediction.hours
        migrated = np.repeat(start_solution[:battery_end], self.slots_per_hour).tolist()
        if self.optimize_ev:
            ev_end = battery_end + self.config.prediction.hours
            migrated.extend(
                np.repeat(start_solution[battery_end:ev_end], self.slots_per_hour).tolist()
            )
        if n_appliance_genes > 0:
            migrated.extend(list(start_solution[-n_appliance_genes:]))
        logger.info(
            "Expanded hourly start_solution from {} to {} slot values.",
            hourly_length,
            expected_length,
        )
        return migrated

    def decode_charge_discharge(
        self, discharge_hours_bin: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Decode the input array into charge, self-consumption discharge and export arrays."""
        discharge_hours_bin_np = np.array(discharge_hours_bin)
        # Battery AC charge uses its own charge-level list (bat_possible_charge_values).
        len_bat = len(self.bat_possible_charge_values)

        # Categorization (using battery charge levels):
        # Idle:       0 .. len_bat-1
        # Discharge:  len_bat .. 2*len_bat - 1
        # AC Charge:  2*len_bat .. 3*len_bat - 1  (maps to bat_possible_charge_values)
        # DC optional: 3*len_bat (not allowed), 3*len_bat + 1 (allowed)
        # Grid export: next state, if direct marketing/export optimization is enabled

        # Idle states
        idle_mask = (discharge_hours_bin_np >= 0) & (discharge_hours_bin_np < len_bat)

        # Discharge states
        discharge_mask = (discharge_hours_bin_np >= len_bat) & (
            discharge_hours_bin_np < 2 * len_bat
        )

        # AC states
        ac_mask = (discharge_hours_bin_np >= 2 * len_bat) & (discharge_hours_bin_np < 3 * len_bat)
        ac_indices = (discharge_hours_bin_np[ac_mask] - 2 * len_bat).astype(int)

        # DC states (if enabled)
        if self.optimize_dc_charge:
            dc_not_allowed_state = 3 * len_bat
            dc_allowed_state = 3 * len_bat + 1
            dc_charge = np.where(discharge_hours_bin_np == dc_allowed_state, 1, 0)
        else:
            dc_charge = np.ones_like(discharge_hours_bin_np, dtype=float)

        # Generate the result arrays
        discharge = np.zeros_like(discharge_hours_bin_np, dtype=int)
        discharge[discharge_mask] = 1  # Set Discharge states to 1

        ac_charge = np.zeros_like(discharge_hours_bin_np, dtype=float)
        ac_charge[ac_mask] = [self.bat_possible_charge_values[i] for i in ac_indices]

        battery_grid_export = np.zeros_like(discharge_hours_bin_np, dtype=int)
        if self.optimize_battery_grid_export:
            grid_export_state = 3 * len_bat + (2 if self.optimize_dc_charge else 0)
            battery_grid_export = np.where(discharge_hours_bin_np == grid_export_state, 1, 0)

        # Idle is just 0, already default.

        return ac_charge, dc_charge, discharge, battery_grid_export

    def mutate(self, individual: list[int]) -> tuple[list[int]]:
        """Custom mutation function for the individual."""
        # Calculate the number of states using battery charge levels
        len_bat = len(self.bat_possible_charge_values)
        if self.optimize_dc_charge:
            total_states = 3 * len_bat + 2
        else:
            total_states = 3 * len_bat
        if self.optimize_battery_grid_export:
            total_states += 1

        # 1. Mutating the charge_discharge part
        charge_discharge_part = individual[: self.total_slots]
        (charge_discharge_mutated,) = self.toolbox.mutate_charge_discharge(charge_discharge_part)

        # Instead of a fixed clamping to 0..8 or 0..6 dynamically:
        charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, total_states - 1)
        individual[: self.total_slots] = charge_discharge_mutated

        # 2. Mutating the EV charge part, if active
        if self.optimize_ev:
            ev_charge_part = individual[self.total_slots : self.total_slots * 2]
            (ev_charge_part_mutated,) = self.toolbox.mutate_ev_charge_index(ev_charge_part)
            ev_charge_part_mutated[self.total_slots - self.fixed_eauto_hours :] = [
                0
            ] * self.fixed_eauto_hours
            individual[self.total_slots : self.total_slots * 2] = ev_charge_part_mutated

        # 3. Mutating the appliance start genes. Each gene is an index into its
        #    own allowed_start_slots list, so the redraw stays within valid range.
        n_appliance_genes = self.appliance_layout.n_genes
        if n_appliance_genes > 0:
            base = len(individual) - n_appliance_genes
            appliance_mutation_probability = 0.2
            for position, gene in enumerate(self.appliance_layout.genes):
                if random.random() < appliance_mutation_probability:  # noqa: S311
                    upper = len(gene.allowed_start_slots) - 1
                    individual[base + position] = random.randint(0, upper)  # noqa: S311

        return (individual,)

    # Method to create an individual based on the conditions
    def create_individual(self) -> list[int]:
        # Start with discharge states for the individual
        individual_components = [
            self.toolbox.attr_discharge_state() for _ in range(self.total_slots)
        ]

        # Add EV charge index values if optimize_ev is True
        if self.optimize_ev:
            individual_components += [
                self.toolbox.attr_ev_charge_index() for _ in range(self.total_slots)
            ]

        # Add one appliance start gene per scheduled run (index into that run's
        # allowed_start_slots). No draws happen when there are no appliances, so
        # the battery/EV-only genome is unchanged.
        for gene in self.appliance_layout.genes:
            individual_components.append(random.randint(0, len(gene.allowed_start_slots) - 1))  # noqa: S311

        return creator.Individual(individual_components)

    def merge_individual(
        self,
        discharge_hours_bin: np.ndarray,
        eautocharge_hours_index: Optional[np.ndarray],
        appliance_gene_values: Optional[list[int]],
    ) -> list[int]:
        """Merge the individual components back into a single solution list.

        Parameters:
            discharge_hours_bin (np.ndarray): Binary discharge hours.
            eautocharge_hours_index (Optional[np.ndarray]): EV charge hours as integers, or None.
            appliance_gene_values (Optional[list[int]]): One index per appliance
                start gene (into the gene's allowed_start_slots), or None.

        Returns:
            list[int]: The merged individual solution as a list of integers.
        """
        # Start with the discharge hours
        individual = discharge_hours_bin.tolist()

        # Add EV charge hours if applicable
        if self.optimize_ev and eautocharge_hours_index is not None:
            individual.extend(eautocharge_hours_index.tolist())
        elif self.optimize_ev:
            # optimize_ev active but no EV data present: pad with zeros
            individual.extend([0] * self.total_slots)

        # Add appliance start genes (one index per scheduled run).
        n_appliance_genes = self.appliance_layout.n_genes
        if n_appliance_genes > 0:
            if appliance_gene_values is not None:
                individual.extend(int(value) for value in appliance_gene_values)
            else:
                individual.extend([0] * n_appliance_genes)

        return individual

    def split_individual(
        self, individual: list[int]
    ) -> tuple[np.ndarray, Optional[np.ndarray], list[int]]:
        """Split the individual solution into its components.

        Components:
        1. Discharge hours (binary as int NumPy array),
        2. Electric vehicle charge hours (float as int NumPy array, if applicable),
        3. Appliance start genes (list of indices, one per scheduled run).
        """
        # Discharge hours as a NumPy array of ints
        discharge_hours_bin = np.array(individual[: self.total_slots], dtype=int)

        # EV charge hours as a NumPy array of ints (if optimize_ev is True)
        eautocharge_hours_index = (
            # append ev charging states to individual
            np.array(
                individual[self.total_slots : self.total_slots * 2],
                dtype=int,
            )
            if self.optimize_ev
            else None
        )

        # Appliance start genes are the trailing entries of the genome.
        n_appliance_genes = self.appliance_layout.n_genes
        if n_appliance_genes > 0:
            appliance_gene_values = [int(value) for value in individual[-n_appliance_genes:]]
        else:
            appliance_gene_values = []

        return discharge_hours_bin, eautocharge_hours_index, appliance_gene_values

    def _repair_ev_charge_at_full_soc(
        self,
        individual: list[int],
        simulation_result: dict[str, Any],
    ) -> bool:
        """Remove EV charging genes in slots that begin at full SoC.

        The repair is deliberately separated from fitness calculation. Callers
        must re-simulate after a change so the individual's genome, simulation
        state and assigned fitness always describe the same schedule.
        """
        if not self.optimize_ev or not self.ev_possible_charge_values:
            return False

        zero_charge_index = min(
            range(len(self.ev_possible_charge_values)),
            key=lambda index: abs(self.ev_possible_charge_values[index]),
        )
        if abs(self.ev_possible_charge_values[zero_charge_index]) > 1e-12:
            return False

        _, ev_charge_indices, _ = self.split_individual(individual)
        if ev_charge_indices is None:
            return False

        ev_soc = np.asarray(simulation_result.get("EAuto_SoC_pro_Stunde", []), dtype=float)
        start_slot = self._start_day_slot()
        result_slots = min(ev_soc.size, self.total_slots - start_slot)
        if result_slots <= 0:
            return False

        changed = False
        for offset in range(result_slots):
            slot = start_slot + offset
            charge_index = int(ev_charge_indices[slot])
            if (
                ev_soc[offset] >= 100.0 - 1e-9
                and self.ev_possible_charge_values[charge_index] > 0.0
            ):
                ev_charge_indices[slot] = zero_charge_index
                changed = True

        if changed:
            battery_genes, _, appliance_genes = self.split_individual(individual)
            individual[:] = self.merge_individual(
                battery_genes,
                ev_charge_indices,
                appliance_genes,
            )
        return changed

    def _heuristic_ev_schedule(self, *, prefer_pv: bool) -> list[int]:
        """Build a low-cost EV schedule that reaches the configured minimum SoC."""
        if not self.optimize_ev or not self.ev_possible_charge_values:
            return []

        zero_index = min(
            range(len(self.ev_possible_charge_values)),
            key=lambda index: abs(self.ev_possible_charge_values[index]),
        )
        schedule = [zero_index] * self.total_slots
        ev = self.simulation.ev
        if ev is None:
            return schedule

        required_stored_wh = max(
            ev.min_soc_wh
            - ev.capacity_wh * ev.initial_soc_percentage / 100.0,
            0.0,
        )
        if required_stored_wh <= 0.0:
            return schedule

        start_slot = self._start_day_slot()
        end_slot = max(start_slot, self.total_slots - self.fixed_eauto_hours)
        prices = np.asarray(self.simulation.elect_price_hourly, dtype=float)
        feed_in = np.asarray(self.simulation.elect_revenue_per_hour_arr, dtype=float)
        pv = np.asarray(self.simulation.pv_prediction_wh, dtype=float)
        load = np.asarray(self.simulation.load_energy_array, dtype=float)

        def marginal_cost(slot: int) -> tuple[float, float]:
            surplus = pv[slot] - load[slot]
            if prefer_pv and surplus > 0.0:
                return (float(feed_in[slot]), -float(surplus))
            return (float(prices[slot]), -float(surplus))

        candidates = sorted(range(start_slot, end_slot), key=marginal_cost)
        positive_rates = sorted(
            (
                (rate, index)
                for index, rate in enumerate(self.ev_possible_charge_values)
                if rate > 0.0
            ),
            key=lambda item: item[0],
        )
        if not positive_rates:
            return schedule

        max_stored_wh = (
            ev.max_charge_power_w
            * self.slot_duration_h
            * ev.charging_efficiency
        )
        remaining_wh = required_stored_wh
        for slot in candidates:
            required_rate = remaining_wh / max(max_stored_wh, 1e-9)
            rate, rate_index = next(
                (item for item in positive_rates if item[0] >= required_rate),
                positive_rates[-1],
            )
            schedule[slot] = rate_index
            remaining_wh -= max_stored_wh * rate
            if remaining_wh <= 1e-9:
                break
        return schedule

    def _heuristic_appliance_genes(self) -> list[int]:
        """Choose low-opportunity-cost starts for flexible appliances."""
        if self.appliance_layout.n_genes == 0:
            return []
        prices = np.asarray(self.simulation.elect_price_hourly, dtype=float)
        feed_in = np.asarray(self.simulation.elect_revenue_per_hour_arr, dtype=float)
        pv = np.asarray(self.simulation.pv_prediction_wh, dtype=float)
        load = np.asarray(self.simulation.load_energy_array, dtype=float)
        genes: list[int] = []
        for gene in self.appliance_layout.genes:
            def opportunity_cost(position: int) -> float:
                slot = gene.allowed_start_slots[position]
                return float(feed_in[slot] if pv[slot] > load[slot] else prices[slot])

            genes.append(min(range(len(gene.allowed_start_slots)), key=opportunity_cost))
        return genes

    def _educated_guess_individuals(
        self,
        target_count: int = EDUCATED_GUESS_TARGET,
    ) -> list[list[int]]:
        """Create a randomized family of domain-informed initial candidates."""
        if target_count <= 0:
            return []

        slots = self.total_slots
        start_slot = self._start_day_slot()
        len_bat = len(self.bat_possible_charge_values)
        idle_state = 0
        discharge_state = len_bat
        ac_charge_state = 3 * len_bat - 1
        dc_allowed_state = 3 * len_bat + 1
        export_state = 3 * len_bat + (2 if self.optimize_dc_charge else 0)

        prices = np.asarray(self.simulation.elect_price_hourly, dtype=float)
        feed_in = np.asarray(self.simulation.elect_revenue_per_hour_arr, dtype=float)
        pv = np.asarray(self.simulation.pv_prediction_wh, dtype=float)
        load = np.asarray(self.simulation.load_energy_array, dtype=float)
        future = slice(start_slot, slots)
        future_prices = prices[future]
        future_feed_in = feed_in[future]
        high_import_price = float(np.quantile(future_prices, 0.70))
        low_import_price = float(np.quantile(future_prices, 0.25))
        feed_spread = float(np.ptp(future_feed_in)) if future_feed_in.size else 0.0

        ev_price = self._heuristic_ev_schedule(prefer_pv=False)
        ev_pv = self._heuristic_ev_schedule(prefer_pv=True)
        appliance_genes = self._heuristic_appliance_genes()

        def compose(battery_genes: list[int], ev_genes: list[int]) -> list[int]:
            individual = list(battery_genes)
            if self.optimize_ev:
                individual.extend(ev_genes)
            individual.extend(appliance_genes)
            return individual

        unique: dict[tuple[int, ...], list[int]] = {}

        def add_guess(battery_genes: list[int], ev_genes: list[int]) -> None:
            guess = compose(battery_genes, ev_genes)
            unique.setdefault(tuple(guess), guess)

        def policy_guess(
            *,
            import_quantile: float,
            export_quantile: Optional[float],
            pv_surplus_ratio: float,
            allow_ac_arbitrage: bool,
        ) -> list[int]:
            import_threshold = float(np.quantile(future_prices, import_quantile))
            export_threshold = (
                float(np.quantile(future_feed_in, export_quantile))
                if export_quantile is not None and future_feed_in.size
                else float("inf")
            )
            low_price_threshold = float(
                np.quantile(future_prices, max(0.05, 1.0 - import_quantile))
            )
            battery_genes = [idle_state] * slots
            for slot in range(start_slot, slots):
                high_feed_in = (
                    export_quantile is not None
                    and self.optimize_battery_grid_export
                    and feed_spread > 1e-12
                    and feed_in[slot] > 0.0
                    and feed_in[slot] >= export_threshold
                )
                pv_surplus = pv[slot] > load[slot] * pv_surplus_ratio
                if high_feed_in:
                    battery_genes[slot] = export_state
                elif self.optimize_dc_charge and pv_surplus:
                    battery_genes[slot] = dc_allowed_state
                elif allow_ac_arbitrage and prices[slot] <= low_price_threshold:
                    battery_genes[slot] = ac_charge_state
                elif prices[slot] >= import_threshold and load[slot] > pv[slot]:
                    battery_genes[slot] = discharge_state
            return battery_genes

        # Baseline and self-consumption candidates are useful even without
        # direct marketing and anchor the population with feasible schedules.
        add_guess([idle_state] * slots, ev_price)
        add_guess(
            policy_guess(
                import_quantile=0.70,
                export_quantile=None,
                pv_surplus_ratio=1.0,
                allow_ac_arbitrage=False,
            ),
            ev_pv,
        )

        # Direct marketing candidates export only in the relatively expensive
        # feed-in slots. At low tariffs PV is preferentially stored instead.
        if self.optimize_battery_grid_export and future_feed_in.size:
            for quantile in self.EDUCATED_GUESS_EXPORT_QUANTILES:
                add_guess(
                    policy_guess(
                        import_quantile=0.70,
                        export_quantile=quantile,
                        pv_surplus_ratio=1.0,
                        allow_ac_arbitrage=False,
                    ),
                    ev_pv,
                )

        inverter = self.simulation.inverter
        ac_arbitrage_possible = inverter is not None and (
            inverter.max_ac_charge_power_w is None or inverter.max_ac_charge_power_w > 0
        )
        if ac_arbitrage_possible:
            price_arbitrage = [idle_state] * slots
            for slot in range(start_slot, slots):
                if prices[slot] <= low_import_price:
                    price_arbitrage[slot] = ac_charge_state
                elif prices[slot] >= high_import_price:
                    price_arbitrage[slot] = discharge_state
            add_guess(price_arbitrage, ev_price)

        # Randomize policy thresholds rather than merely cloning a handful of
        # templates. Every candidate remains policy-safe: a flat/low-information
        # feed-in series never acquires export actions through blind mutation.
        attempts = max(target_count * 20, 100)
        for _ in range(attempts):
            export_quantile = (
                random.uniform(0.50, 0.98)  # noqa: S311
                if self.optimize_battery_grid_export and feed_spread > 1e-12
                else None
            )
            randomized = policy_guess(
                import_quantile=random.uniform(0.55, 0.95),  # noqa: S311
                export_quantile=export_quantile,
                pv_surplus_ratio=random.uniform(0.80, 1.20),  # noqa: S311
                allow_ac_arbitrage=ac_arbitrage_possible and random.random() < 0.35,  # noqa: S311
            )

            # Add small policy-safe local variations. These provide diversity
            # even when price quantiles collapse to only a few distinct slot
            # masks. Export is only ever removed here, never introduced into a
            # slot that the tariff policy did not mark as attractive.
            future_slots = list(range(start_slot, slots))
            perturbations = random.randint(1, max(2, len(future_slots) // 12))  # noqa: S311
            for slot in random.sample(future_slots, min(perturbations, len(future_slots))):  # noqa: S311
                if randomized[slot] != idle_state:
                    randomized[slot] = idle_state
                elif self.optimize_dc_charge and pv[slot] > load[slot]:
                    randomized[slot] = dc_allowed_state
                elif load[slot] > pv[slot] and prices[slot] >= high_import_price:
                    randomized[slot] = discharge_state

            add_guess(
                randomized,
                ev_pv if random.random() < 0.5 else ev_price,  # noqa: S311
            )
            if len(unique) >= target_count:
                break

        return list(unique.values())[:target_count]

    def _mutated_warm_start_neighbors(
        self,
        start_solution: list[float],
        count: int,
    ) -> list[list[int]]:
        """Create unique local variants while preserving already elapsed slots."""
        original = [int(value) for value in start_solution]
        start_slot = self._start_day_slot()
        seen = {tuple(original)}
        neighbors: list[list[int]] = []
        for _ in range(max(count * 10, 1)):
            neighbor = creator.Individual(original)
            self.mutate(neighbor)
            neighbor[:start_slot] = original[:start_slot]
            if self.optimize_ev:
                ev_start = self.total_slots
                neighbor[ev_start : ev_start + start_slot] = original[
                    ev_start : ev_start + start_slot
                ]
            key = tuple(int(value) for value in neighbor)
            if key in seen:
                continue
            seen.add(key)
            neighbors.append(list(key))
            if len(neighbors) >= count:
                break
        return neighbors

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
        # Battery state space uses bat_possible_charge_values; EV index space uses ev_possible_charge_values.
        len_bat = len(self.bat_possible_charge_values)
        len_ev = len(self.ev_possible_charge_values)

        # Total battery/discharge states:
        # Idle:      len_bat states
        # Discharge: len_bat states
        # AC-Charge: len_bat states  (maps to bat_possible_charge_values)
        # With DC: + 2 additional states
        # With battery grid export: + 1 additional state
        if self.optimize_dc_charge:
            total_states = 3 * len_bat + 2
        else:
            total_states = 3 * len_bat
        if self.optimize_battery_grid_export:
            total_states += 1

        # State space: 0 .. (total_states - 1)
        self.toolbox.register("attr_discharge_state", random.randint, 0, total_states - 1)

        # EV attributes (separate index space)
        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len_ev - 1,
            )

        self.toolbox.register("individual", self.create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)

        # Mutation operator for battery charge/discharge states
        # Keep the expected number of mutated genes per hour stable when the
        # interval becomes finer (0.2 hourly -> 0.05 on a quarter-hour grid).
        mutation_probability = 0.2 / self.slots_per_hour
        self.toolbox.register(
            "mutate_charge_discharge",
            tools.mutUniformInt,
            low=0,
            up=total_states - 1,
            indpb=mutation_probability,
        )

        # Mutation operator for EV states (separate index space)
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len_ev - 1,
            indpb=mutation_probability,
        )

        # Custom mutate function remains unchanged
        self.toolbox.register("mutate", self.mutate)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual: list[int]) -> dict[str, Any]:
        """Simulates the energy management system (EMS) using the provided individual solution.

        This is an internal function.
        """
        self.simulation.reset()
        discharge_hours_bin, eautocharge_hours_index, appliance_gene_values = self.split_individual(
            individual
        )

        # Decode the appliance start genes and (re)build each appliance's load
        # curve for this candidate solution.
        self._apply_appliance_starts(appliance_gene_values)

        ac_charge_hours, dc_charge_hours, discharge, battery_grid_export = (
            self.decode_charge_discharge(discharge_hours_bin)
        )

        self.simulation.bat_discharge_hours = discharge
        self.simulation.bat_grid_export_hours = battery_grid_export
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            self.simulation.dc_charge_hours = dc_charge_hours
        else:
            self.simulation.dc_charge_hours = np.full(self.total_slots, 1)
        self.simulation.ac_charge_hours = ac_charge_hours

        if eautocharge_hours_index is not None:
            eautocharge_hours_float = np.array(
                [self.ev_possible_charge_values[i] for i in eautocharge_hours_index],
                float,
            )
            # discharge is set to 0 by default
            self.simulation.ev_charge_hours = eautocharge_hours_float
        else:
            # discharge is set to 0 by default
            self.simulation.ev_charge_hours = np.full(self.total_slots, 0)

        # Do the simulation and return result. simulate()'s argument is a slot
        # index into the prediction/charge arrays, not an hour-of-day, so pass
        # the start_day_slot to keep sub-hourly runs aligned.
        return self.simulation.simulate(self._start_day_slot())

    def evaluate(
        self,
        individual: list[int],
        parameters: GeneticOptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> tuple[float]:
        """Evaluate an individual, using run-local canonical memoization when active."""
        if not self._fitness_cache_enabled:
            return self._evaluate_uncached(individual, parameters, start_hour, worst_case)

        original_key = tuple(int(value) for value in individual)
        cached = self._fitness_cache.get(original_key)
        if cached is not None:
            individual[:] = cached.genome
            individual.extra_data = cached.extra_data  # type: ignore[attr-defined]
            self._fitness_cache_hits += 1
            return cached.fitness

        self._fitness_cache_misses += 1
        fitness = self._evaluate_uncached(individual, parameters, start_hour, worst_case)
        extra_data = getattr(individual, "extra_data", None)
        if extra_data is None:
            # Failed evaluations use the sentinel fitness and are intentionally
            # not cached: an unexpected transient failure must never become a
            # persistent result for the remainder of the run.
            return fitness

        canonical_key = tuple(int(value) for value in individual)
        extra_value1, extra_value2, extra_value3 = extra_data
        entry = FitnessCacheEntry(
            genome=canonical_key,
            fitness=fitness,
            extra_data=(
                float(extra_value1),
                float(extra_value2),
                float(extra_value3),
            ),
        )
        self._fitness_cache[original_key] = entry
        self._fitness_cache[canonical_key] = entry
        return fitness

    def _evaluate_uncached(
        self,
        individual: list[int],
        parameters: GeneticOptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> tuple[float]:
        """Evaluate the fitness score of a single individual in the DEAP genetic algorithm.

        This method runs a simulation based on the provided individual genome and
        optimization parameters. The resulting performance is converted into a
        fitness score compatible with DEAP (i.e., returned as a 1-tuple).

        Args:
            individual (list[int]):
                The genome representing one candidate solution.
            parameters (GeneticOptimizationParameters):
                Optimization parameters that influence simulation behavior,
                constraints, and scoring logic.
            start_hour (int):
                The simulation start hour (0–23 or domain-specific).
                Used to initialize time-based scheduling or constraints.
            worst_case (bool):
                If True, evaluates the solution under worst-case assumptions
                (e.g., pessimistic forecasts or boundary conditions).
                If False, uses nominal assumptions.

        Returns:
            tuple[float]:
                A single-element tuple containing the computed fitness score.
                Lower score is better: "FitnessMin".

        Raises:
            ValueError: If input arguments are invalid or the individual structure
                is not compatible with the simulation.
            RuntimeError: If the simulation fails or cannot produce results.

        Notes:
            The resulting score should match DEAP's expected format: a tuple, even
            if only a single scalar fitness value is returned.
        """
        try:
            simulation_result = self.evaluate_inner(individual)
            if self._repair_ev_charge_at_full_soc(individual, simulation_result):
                simulation_result = self.evaluate_inner(individual)
        except Exception:
            # Return bad fitness score ("FitnessMin") in case of an exception
            if hasattr(individual, "extra_data"):
                del individual.extra_data  # type: ignore[attr-defined]
            return (100000.0,)

        gesamtbilanz = simulation_result["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        # New check: Activate discharge when battery SoC is 0
        # battery_soc_per_hour = np.array(
        #     o.get("akku_soc_pro_stunde", [])
        # )  # Example key for battery SoC

        # if battery_soc_per_hour is not None:
        #     if battery_soc_per_hour is None or discharge_hours_bin is None:
        #         raise ValueError("battery_soc_per_hour or discharge_hours_bin is None")
        #     min_length = min(battery_soc_per_hour.size, discharge_hours_bin.size)
        #     battery_soc_per_hour_tail = battery_soc_per_hour[-min_length:]
        #     discharge_hours_bin_tail = discharge_hours_bin[-min_length:]
        #     len_ac = len(self.config.optimization.ev_available_charge_rates_percent)

        #     # # Find hours where battery SoC is 0
        #     # zero_soc_mask = battery_soc_per_hour_tail == 0
        #     # discharge_hours_bin_tail[zero_soc_mask] = (
        #     #     len_ac + 2
        #     # )  # Activate discharge for these hours

        #     # When Battery SoC then set the Discharge randomly to 0 or 1. otherwise it's very
        #     # unlikely to get a state where a battery can store energy for a longer time
        #     # Find hours where battery SoC is 0
        #     zero_soc_mask = battery_soc_per_hour_tail == 0
        #     # discharge_hours_bin_tail[zero_soc_mask] = (
        #     # len_ac + 2
        #     # )  # Activate discharge for these hours
        #     set_to_len_ac_plus_2 = np.random.rand() < 0.5  # True mit 50% Wahrscheinlichkeit

        #     # Werte setzen basierend auf der zufälligen Entscheidung
        #     value_to_set = len_ac + 2 if set_to_len_ac_plus_2 else 0
        #     discharge_hours_bin_tail[zero_soc_mask] = value_to_set

        #     # Merge the updated discharge_hours_bin back into the individual
        #     adjusted_individual = self.merge_individual(
        #         discharge_hours_bin, eautocharge_hours_index, washingstart_int
        #     )
        #     individual[:] = adjusted_individual

        # More metrics
        individual.extra_data = (  # type: ignore[attr-defined]
            simulation_result["Gesamtbilanz_Euro"],
            simulation_result["Gesamt_Verluste"],
            parameters.eauto.min_soc_percentage - self.simulation.ev.current_soc_percentage()
            if parameters.eauto and self.simulation.ev
            else 0,
        )

        # Adjust total balance with battery value and penalties for unmet SOC
        if self.simulation.battery:
            battery_energy_content = self.simulation.battery.current_energy_content()
            # Apply DC→AC inverter efficiency to residual battery value
            # (stored DC energy must pass through inverter to be usable as AC)
            if self.simulation.inverter:
                battery_energy_content *= self.simulation.inverter.dc_to_ac_efficiency
            restwert_akku = battery_energy_content * parameters.ems.preis_euro_pro_wh_akku
            gesamtbilanz += -restwert_akku

        # --- AC charging break-even penalty ---
        # Penalise AC charging decisions that cannot be economically justified given the
        # round-trip losses (AC→DC charge conversion, battery internal, DC→AC discharge
        # conversion) and the best available future electricity prices.
        #
        # Key insight: energy already stored in the battery (from PV, zero grid cost) covers
        # the most expensive future hours first.  AC charging from the grid only makes sense
        # for the hours that remain uncovered, and only when the discharge price exceeds
        # P_charge / η_round_trip.
        #
        # This penalty does not double-count the simulation result – it amplifies the "bad
        # decision" signal so that the genetic algorithm converges faster away from
        # unprofitable charging regions.
        if (
            self.simulation.battery
            and self.simulation.inverter
            and self.simulation.ac_charge_hours is not None
            and self.simulation.elect_price_hourly is not None
            and self.simulation.load_energy_array is not None
        ):
            inv = self.simulation.inverter
            bat = self.simulation.battery

            # Full round-trip efficiency: 1 Wh drawn from grid → η Wh delivered to AC load
            round_trip_eff = (
                inv.ac_to_dc_efficiency
                * bat.charging_efficiency
                * bat.discharging_efficiency
                * inv.dc_to_ac_efficiency
            )

            # Configurable penalty multiplier (default 1 = economic loss in €)
            try:
                ac_penalty_factor = float(
                    self.config.optimization.genetic.penalties["ac_charge_break_even"]
                )
            except Exception:
                ac_penalty_factor = 1.0

            # A factor of 0 multiplies every penalty term to zero - skip the
            # whole computation in that case.
            if round_trip_eff > 0 and ac_penalty_factor != 0.0:
                ac_charge_arr = self.simulation.ac_charge_hours
                prices_arr = self.simulation.elect_price_hourly
                load_arr = self.simulation.load_energy_array
                n = len(prices_arr)

                # Usable AC energy already in battery from prior PV charging (zero grid cost).
                # This covers the most expensive future hours first, pushing AC charging demand
                # to cheaper hours where the break-even hurdle may not be met.
                initial_soc_wh = (bat.initial_soc_percentage / 100.0) * bat.capacity_wh
                free_ac_wh = (
                    max(0.0, initial_soc_wh - bat.min_soc_wh)
                    * bat.discharging_efficiency
                    * inv.dc_to_ac_efficiency
                )

                # Prices/loads/free energy are constant within one optimization
                # run - compute the break-even lookup once, reuse it for every
                # individual (cache is reset per run in optimierung_ems()).
                best_prices = getattr(self, "_ac_break_even_best_prices", None)
                if best_prices is None:
                    best_prices = self._ac_break_even_prices(prices_arr, load_arr, free_ac_wh)
                    self._ac_break_even_best_prices = best_prices

                for hour in range(start_hour, min(len(ac_charge_arr), n)):
                    ac_factor = ac_charge_arr[hour]
                    if ac_factor <= 0.0:
                        continue

                    charge_price = prices_arr[hour]
                    if charge_price <= 0:
                        continue

                    # Price that a future AC discharge hour must reach to break
                    # even. LCOS is defined per DC Wh delivered by the battery;
                    # dividing it by DC-to-AC efficiency converts it to the
                    # corresponding cost per useful/exported AC Wh.
                    lcos_per_wh_dc = getattr(bat, "levelized_cost_of_storage_kwh", 0.0) / 1000.0
                    break_even_price = (
                        charge_price / round_trip_eff + lcos_per_wh_dc / inv.dc_to_ac_efficiency
                    )

                    best_uncovered_price = best_prices[hour]

                    if best_uncovered_price < break_even_price:
                        # AC charging at this hour is economically unjustified.
                        # Penalty = excess cost per Wh × DC energy requested this slot.
                        # max_charge_power_w is a power [W]; the energy movable in
                        # one slot is power × slot_duration_h (¼ at 15 min).
                        dc_wh = bat.max_charge_power_w * self.slot_duration_h * ac_factor
                        ac_wh = dc_wh / max(inv.ac_to_dc_efficiency, 1e-9)
                        excess_cost_per_wh = break_even_price - best_uncovered_price
                        gesamtbilanz += ac_wh * excess_cost_per_wh * ac_penalty_factor

        if self.optimize_ev and parameters.eauto and self.simulation.ev:
            try:
                penalty = self.config.optimization.genetic.penalties["ev_soc_miss"]
            except:
                # Use default
                penalty = 10
                logger.error(
                    "Penalty function parameter `ev_soc_miss` not configured, using {}.", penalty
                )
            ev_soc_percentage = self.simulation.ev.current_soc_percentage()
            if ev_soc_percentage < parameters.eauto.min_soc_percentage:
                gesamtbilanz += (
                    abs(parameters.eauto.min_soc_percentage - ev_soc_percentage) * penalty
                )

        return (gesamtbilanz,)

    def optimize(
        self,
        start_solution: Optional[list[float]] = None,
        ngen: int = 200,
    ) -> tuple[Any, dict[str, list[Any]]]:
        """Run the optimization process using a genetic algorithm.

        @TODO: optimize() ngen default (200) is different from optimierung_ems() ngen default (400).
        """
        # Re-seed at the actual optimization boundary. Setup and validation may
        # consume random values elsewhere in a long-running process; a fixed seed
        # must nevertheless produce the same population and result.
        if self.fix_seed is not None:
            random.seed(self.fix_seed)

        # Set the number of inviduals in a generation
        try:
            individuals = self.config.optimization.genetic.individuals
            if individuals is None:
                raise
        except:
            individuals = 300
            logger.error("Individuals not configured. Using {}.", individuals)

        hof = tools.HallOfFame(1)
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("min", np.min)
        stats.register("avg", np.mean)
        stats.register("max", np.max)

        logger.debug("Start optimize: {}", start_solution)

        # Validate the warm start before assigning the fixed population budget.
        valid_start_solution: Optional[list[float]] = None
        if start_solution is not None:
            n_appliance_genes = self.appliance_layout.n_genes
            expected_length = (
                self.total_slots * (2 if self.optimize_ev else 1) + n_appliance_genes
            )
            start_solution = self._start_solution_for_slot_grid(start_solution)

            if len(start_solution) != expected_length:
                logger.warning(
                    "Ignoring start_solution with incompatible length {} (expected {}).",
                    len(start_solution),
                    expected_length,
                )
            elif not self._start_solution_matches_layout(start_solution):
                logger.warning(
                    "Ignoring start_solution: appliance genes do not match the current "
                    "appliance layout."
                )
            else:
                valid_start_solution = start_solution

        # Keep the configured initial population size fixed. With the default
        # 300 individuals this yields 10 exact warm starts, 50 local variants,
        # 100 educated guesses and 140 fully random candidates.
        minimum_random = max(
            int(individuals * self.MIN_RANDOM_POPULATION_FRACTION + 0.999999),
            individuals
            - (
                self.WARM_START_COPIES
                + self.WARM_START_MUTATIONS
                + self.EDUCATED_GUESS_TARGET
            ),
        )
        seed_budget = max(individuals - minimum_random, 0)
        seeded: list[list[float]] = []

        exact_warm_count = 0
        warm_neighbors: list[list[int]] = []
        if valid_start_solution is not None and seed_budget > 0:
            exact_warm_count = min(self.WARM_START_COPIES, seed_budget)
            seeded.extend([valid_start_solution] * exact_warm_count)
            remaining_seed_budget = seed_budget - len(seeded)
            warm_neighbors = self._mutated_warm_start_neighbors(
                valid_start_solution,
                min(self.WARM_START_MUTATIONS, remaining_seed_budget),
            )
            seeded.extend(warm_neighbors)

        remaining_seed_budget = seed_budget - len(seeded)
        educated_guesses = self._educated_guess_individuals(
            min(self.EDUCATED_GUESS_TARGET, remaining_seed_budget)
        )
        seeded.extend(educated_guesses)

        random_count = max(individuals - len(seeded), 0)
        population = [creator.Individual(seed) for seed in seeded]
        population.extend(self.toolbox.population(n=random_count))
        logger.info(
            "Initial population {}: {} exact warm starts, {} warm mutations, "
            "{} educated guesses, {} random candidates.",
            len(population),
            exact_warm_count,
            len(warm_neighbors),
            len(educated_guesses),
            random_count,
        )

        # The memoization scope is exactly one optimizer invocation. Always turn
        # it off again, including when DEAP raises, so no later caller can reuse
        # results under changed forecasts or device state.
        self._fitness_cache.clear()
        self._fitness_cache_hits = 0
        self._fitness_cache_misses = 0
        self._fitness_cache_enabled = True
        try:
            pop, log = algorithms.eaMuPlusLambda(
                population,
                self.toolbox,
                mu=self.SURVIVOR_COUNT,
                lambda_=self.OFFSPRING_COUNT,
                cxpb=0.6,
                mutpb=0.4,
                ngen=ngen,
                stats=stats,
                halloffame=hof,
                verbose=self.verbose,
            )
        finally:
            self._fitness_cache_enabled = False

        cache_lookups = self._fitness_cache_hits + self._fitness_cache_misses
        cache_hit_rate = (
            self._fitness_cache_hits / cache_lookups if cache_lookups > 0 else 0.0
        )
        logger.info(
            "Fitness cache: {} hits, {} misses, {:.1%} hit rate, {} keys.",
            self._fitness_cache_hits,
            self._fitness_cache_misses,
            cache_hit_rate,
            len(self._fitness_cache),
        )

        # Store fitness history
        self.fitness_history = {
            "gen": log.select("gen"),  # Generation numbers (X-axis)
            "avg": log.select("avg"),  # Average fitness for each generation (Y-axis)
            "max": log.select("max"),  # Maximum fitness for each generation (Y-axis)
            "min": log.select("min"),  # Minimum fitness for each generation (Y-axis)
            "fitness_cache": {
                "hits": self._fitness_cache_hits,
                "misses": self._fitness_cache_misses,
                "hit_rate": cache_hit_rate,
                "keys": len(self._fitness_cache),
            },
        }

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
        parameters: GeneticOptimizationParameters,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        ngen: Optional[int] = None,
    ) -> GeneticSolution:
        """Perform EMS (Energy Management System) optimization and visualize results."""
        direct_marketing_enabled = self._direct_marketing_enabled()
        parameters = self._parameters_for_config(parameters)
        parameters = self._parameters_for_slot_grid(parameters)
        # Home-appliance scheduling now supports sub-hourly intervals via the
        # energy-preserving per-slot run profile.
        home_appliance_params = parameters.resolved_home_appliances()
        self.optimize_dc_charge = direct_marketing_enabled
        self.optimize_battery_grid_export = direct_marketing_enabled

        if start_hour is None:
            start_hour = self.ems.start_datetime.hour
        # Start hour has to be in sync with energy management
        if start_hour != self.ems.start_datetime.hour:
            raise ValueError(
                f"Start hour not synced. EMS {self.ems.start_datetime.hour} vs. GENETIC {start_hour}."
            )
        # start_hour stays the hour-of-day for the appliance-start gene bounds
        # (0..23). Everything that indexes the slot arrays (the simulate offset
        # and evaluate's break-even loop) uses the slot index instead.
        start_slot = self._start_day_slot()

        # Set the number of generations
        generations = ngen
        if generations is None:
            try:
                generations = self.config.optimization.genetic.generations
            except:
                generations = 400
                logger.error("Generations not configured. Using {}.", generations)

        self.simulation.reset()
        # Prices/loads/initial SoC may differ from the previous run - the
        # break-even lookup must be rebuilt lazily on first evaluation.
        self._ac_break_even_best_prices = None

        # Initialize PV and EV batteries. slot_duration_h lets the Battery scale
        # its power caps (max_charge_power_w) to a per-slot energy cap.
        akku: Optional[Battery] = None
        if parameters.pv_akku:
            akku = Battery(
                parameters.pv_akku,
                prediction_hours=self.total_slots,
                slot_duration_h=self.slot_duration_h,
            )
            akku.set_charge_per_hour(np.full(self.total_slots, 0))

        eauto: Optional[Battery] = None
        if parameters.eauto:
            eauto = Battery(
                parameters.eauto,
                prediction_hours=self.total_slots,
                slot_duration_h=self.slot_duration_h,
            )
            eauto.set_charge_per_hour(np.full(self.total_slots, 1))
            self.optimize_ev = (
                parameters.eauto.min_soc_percentage > parameters.eauto.initial_soc_percentage
            )
            # electrical vehicle charge rates
            if parameters.eauto.charge_rates is not None:
                self.ev_possible_charge_values = parameters.eauto.charge_rates
            elif (
                self.config.devices.electric_vehicles
                and self.config.devices.electric_vehicles[0]
                and self.config.devices.electric_vehicles[0].charge_rates is not None
            ):
                self.ev_possible_charge_values = self.config.devices.electric_vehicles[
                    0
                ].charge_rates
            else:
                warning_msg = "No charge rates provided for electric vehicle - using default."
                logger.warning(warning_msg)
                self.ev_possible_charge_values = [
                    0.0,
                    0.1,
                    0.2,
                    0.3,
                    0.4,
                    0.5,
                    0.6,
                    0.7,
                    0.8,
                    0.9,
                    1.0,
                ]
        else:
            self.optimize_ev = False

        # Battery AC charge rates — use the battery's configured charge_rates so the
        # optimizer can select partial AC charge power (e.g. 10 %, 50 %, 100 %) instead
        # of always forcing full power.  Falls back to [1.0] when not configured.
        if parameters.pv_akku and parameters.pv_akku.charge_rates:
            self.bat_possible_charge_values = [
                r for r in parameters.pv_akku.charge_rates if r > 0.0
            ] or [1.0]
        elif (
            self.config.devices.batteries
            and self.config.devices.batteries[0]
            and self.config.devices.batteries[0].charge_rates
        ):
            self.bat_possible_charge_values = [
                r for r in self.config.devices.batteries[0].charge_rates if r > 0.0
            ] or [1.0]
        else:
            self.bat_possible_charge_values = [1.0]
        logger.debug("Battery AC charge levels: {}", self.bat_possible_charge_values)

        # Initialize the flexible consumers (home appliances) and their genome
        # layout. slot0_datetime (midnight of the start day) turns decoded start
        # slots into absolute local timestamps and drives DAILY day grouping.
        self._slot0_datetime = self.ems.start_datetime.set(
            hour=0, minute=0, second=0, microsecond=0
        )
        home_appliances = [
            HomeAppliance(
                parameters=appliance_params,
                optimization_hours=self.config.optimization.horizon_hours,
                prediction_hours=self.total_slots,
                slot_duration_h=self.slot_duration_h,
            )
            for appliance_params in home_appliance_params
        ]
        self.appliance_layout = self._build_appliance_layout(
            home_appliances, self._slot0_datetime
        )

        # Initialize the inverter and energy management system. slot_duration_h
        # lets the Inverter scale max_power_wh to a per-slot energy cap.
        inverter: Optional[Inverter] = None
        if parameters.inverter:
            inverter = Inverter(
                parameters.inverter,
                battery=akku,
                slot_duration_h=self.slot_duration_h,
            )

        # Prepare device simulation
        self.simulation.prepare(
            parameters=parameters.ems,
            optimization_hours=self.config.optimization.horizon_hours,
            prediction_hours=self.total_slots,
            inverter=inverter,  # battery is part of inverter
            ev=eauto,
            home_appliances=home_appliances,
            direct_marketing_enabled=direct_marketing_enabled,
        )

        # Setup the DEAP environment and optimization process. The appliance
        # genome layout (built above) drives the appliance gene block; evaluate
        # gets the slot index (its break-even loop walks the slot arrays from "now").
        self.setup_deap_environment(
            {"home_appliance": self.appliance_layout.n_genes}, start_hour
        )
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, parameters, start_slot, worst_case),
        )

        start_time = time.time()
        start_solution, extra_data = self.optimize(parameters.start_solution, ngen=generations)
        elapsed_time = time.time() - start_time
        logger.debug(f"Time evaluate inner: {elapsed_time:.4f} sec.")

        # Perform final evaluation on the best solution
        simulation_result = self.evaluate_inner(start_solution)

        # Prepare results
        discharge_hours_bin, eautocharge_hours_index, appliance_gene_values = (
            self.split_individual(start_solution)
        )

        # Materialize the per-device appliance results only for the final best
        # solution. Each appliance's load curve (already built by the final
        # evaluate_inner above) starts at slot 0; slice it to the simulation
        # window so it aligns with the other per-slot result arrays.
        starts_per_appliance = self._decode_appliance_starts(appliance_gene_values)
        home_appliance_energy_wh: dict[str, list[float]] = {}
        appliance_starts: dict[str, list[Any]] = {}
        timezone = self.config.general.timezone
        for appliance_index, appliance in enumerate(self.simulation.home_appliances):
            device_id = appliance.device_id
            home_appliance_energy_wh[device_id] = appliance.get_load_curve()[start_slot:].tolist()
            starts = sorted(starts_per_appliance.get(appliance_index, []))
            appliance_starts[device_id] = [
                self._slot0_datetime.add(
                    seconds=start * appliance.slot_interval_seconds
                ).in_timezone(timezone)
                for start in starts
            ]
        simulation_result["home_appliance_energy_wh"] = home_appliance_energy_wh

        # Deprecated single-device hourly start (kept for backward compatibility).
        # Only meaningful for the legacy case: exactly one appliance on the hourly
        # grid. Otherwise None; use appliance_starts instead.
        washingstart_int: Optional[int] = None
        if self.slots_per_hour == 1 and len(self.simulation.home_appliances) == 1:
            single_starts = starts_per_appliance.get(0, [])
            if single_starts:
                washingstart_int = int(min(single_starts))

        eautocharge_hours_float = None
        if eautocharge_hours_index is not None and self.simulation.ev is not None:
            eautocharge_hours_float = self.simulation.ev.charge_array.tolist()

        # Simulation may have changed something, use simulation values
        ac_charge_hours = self.simulation.ac_charge_hours
        if ac_charge_hours is None:
            ac_charge_hours = []
        else:
            ac_charge_hours = ac_charge_hours.tolist()
        dc_charge_hours = self.simulation.dc_charge_hours
        if dc_charge_hours is None:
            dc_charge_hours = []
        else:
            dc_charge_hours = dc_charge_hours.tolist()
        discharge = self.simulation.bat_discharge_hours
        if discharge is None:
            discharge = []
        else:
            discharge = discharge.tolist()
        battery_grid_export = self.simulation.bat_grid_export_hours
        if not direct_marketing_enabled or battery_grid_export is None:
            battery_grid_export = []
        else:
            battery_grid_export = battery_grid_export.tolist()

        # Visualize the results in PDF. Skippable via config — matplotlib PDF
        # generation costs several seconds per run, which headless setups
        # (API/Node-RED polling) never look at.
        if getattr(self.config.optimization, "visualize_pdf", True):
            try:
                from akkudoktoreos.utils.visualize import prepare_visualize

                visualize = {
                    "ac_charge": ac_charge_hours,
                    "dc_charge": dc_charge_hours,
                    "discharge_allowed": discharge,
                    "battery_grid_export_allowed": battery_grid_export,
                    "eautocharge_hours_float": eautocharge_hours_float,
                    "result": simulation_result,
                    "eauto_obj": self.simulation.ev.to_dict() if self.simulation.ev else None,
                    "start_solution": start_solution,
                    "spuelstart": washingstart_int,
                    "extra_data": extra_data,
                    "fitness_history": self.fitness_history,
                    "fixed_seed": self.fix_seed,
                }

                prepare_visualize(parameters, visualize, start_hour=start_slot)

            except Exception as ex:
                error_msg = f"Visualization failed: {ex}"
                logger.error(error_msg)

        return GeneticSolution(
            **{
                "ac_charge": ac_charge_hours,
                "dc_charge": dc_charge_hours,
                "discharge_allowed": discharge,
                "battery_grid_export_allowed": battery_grid_export,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": GeneticSimulationResult(**simulation_result),
                "eauto_obj": self.simulation.ev,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
                "appliance_starts": appliance_starts,
            }
        )
