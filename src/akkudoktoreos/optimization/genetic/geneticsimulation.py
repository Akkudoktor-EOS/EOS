"""Simulation of GENETIC: the energy flows of one genome over the slots of a run."""

from typing import Any, Optional

import numpy as np
from loguru import logger

from akkudoktoreos.optimization.genetic.devices.geneticbattery import GeneticBattery
from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticHomeAppliance,
)
from akkudoktoreos.optimization.genetic.devices.geneticinverter import GeneticInverter
from akkudoktoreos.optimization.genetic.geneticenergymanagement import (
    GeneticEnergyManagementParameters,
)


def _slot_values(values: Any) -> Any:
    """Return per-slot values as plain Python numbers for the simulation loop.

    Reading single elements of a NumPy array yields NumPy scalars, whose
    arithmetic is several times slower than that of Python numbers. The values
    are the same IEEE doubles (or integers), so the results do not change.
    Other element types are left as they are.
    """
    if isinstance(values, np.ndarray) and (values.dtype == np.float64 or values.dtype.kind in "iu"):
        return values.tolist()
    return values


class GeneticSimulation:
    """Device simulation for GENETIC optimization algorithm.

    ``prepare()`` sets the forecasts, prices and devices of a run; ``simulate()`` evaluates one
    candidate plan.
    """

    def __init__(self) -> None:
        """Create an empty simulation; ``prepare()`` fills it."""
        # Starting slot of the latest simulate() call.
        self.start_hour: int = 0
        self.optimization_hours: Optional[int] = 24
        self.prediction_hours: Optional[int] = 48
        # Per-slot forecasts and prices [Wh, currency/Wh].
        self.load_energy_array: Optional[np.ndarray] = None
        self.pv_prediction_wh: Optional[np.ndarray] = None
        self.elect_price_hourly: Optional[np.ndarray] = None
        self.elect_revenue_per_hour_arr: Optional[np.ndarray] = None
        # Use direct marketing behavior for feed-in/export decisions.
        self.direct_marketing_enabled: bool = False
        self.battery: Optional[GeneticBattery] = None
        self.ev: Optional[GeneticBattery] = None
        # Flexible consumers scheduled by the optimizer.
        self.home_appliances: list[GeneticHomeAppliance] = []
        self.inverter: Optional[GeneticInverter] = None
        # Per-slot actions of the candidate plan.
        self.ac_charge_hours: Optional[np.ndarray] = None
        self.dc_charge_hours: Optional[np.ndarray] = None
        self.bat_discharge_hours: Optional[np.ndarray] = None
        # Per-slot permission for battery discharge into the grid.
        self.bat_grid_export_hours: Optional[np.ndarray] = None
        self.ev_charge_hours: Optional[np.ndarray] = None
        self.ev_discharge_hours: Optional[np.ndarray] = None
        self.home_appliance_start_hour: Optional[int] = None

    @property
    def home_appliance(self) -> Optional[GeneticHomeAppliance]:
        """Deprecated singular device accessor."""
        return self.home_appliances[0] if self.home_appliances else None

    @home_appliance.setter
    def home_appliance(self, appliance: Optional[GeneticHomeAppliance]) -> None:
        self.home_appliances = [appliance] if appliance is not None else []

    def prepare(
        self,
        parameters: GeneticEnergyManagementParameters,
        optimization_hours: int,
        prediction_hours: int,
        ev: Optional[GeneticBattery] = None,
        home_appliance: Optional[GeneticHomeAppliance] = None,
        inverter: Optional[GeneticInverter] = None,
        direct_marketing_enabled: bool = False,
        home_appliances: Optional[list[GeneticHomeAppliance]] = None,
    ) -> None:
        """Prepare simulation runs.

        Populate internal arrays and device references used during simulation.
        """
        self.home_appliance_start_hour = None
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours
        self.direct_marketing_enabled = direct_marketing_enabled

        # Load arrays from provided EMS parameters
        self.load_energy_array = np.array(parameters.total_load, float)
        self.pv_prediction_wh = np.array(parameters.pv_forecast_wh, float)
        self.elect_price_hourly = np.array(parameters.electricity_price_per_wh, float)
        self.elect_revenue_per_hour_arr = (
            np.array(parameters.feed_in_tariff_per_wh, float)
            if isinstance(parameters.feed_in_tariff_per_wh, (list, tuple))
            else np.full(len(self.load_energy_array), parameters.feed_in_tariff_per_wh, float)
        )

        # Associate devices
        if inverter:
            self.battery = inverter.battery
        else:
            self.battery = None
        self.ev = ev
        if home_appliance is not None and home_appliances is not None:
            raise ValueError("Use home_appliance or home_appliances, not both.")
        self.home_appliances = home_appliances or (
            [home_appliance] if home_appliance is not None else []
        )
        self.inverter = inverter

        # Initialize per-hour action arrays for the prediction horizon
        self.ac_charge_hours = np.full(self.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_grid_export_hours = np.full(self.prediction_hours, 0.0)
        self.ev_charge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_discharge_hours = np.full(self.prediction_hours, 0.0)

    def reset(self) -> None:
        self.home_appliance_start_hour = None
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """Simulate energy usage and costs for the given start hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of last hour (end state)
        """
        # Preserve the singular hourly simulator API. Native schedules are built beforehand.
        if self.home_appliance is not None and self.home_appliance_start_hour is not None:
            self.home_appliance_start_hour = self.home_appliance.set_starting_time(
                self.home_appliance_start_hour, start_hour
            )
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

        end_hour = min(
            len(load_energy_array_fast), self.prediction_hours or len(load_energy_array_fast)
        )
        total_hours = end_hour - start_hour
        if total_hours < 0:
            raise ValueError("negative dimensions are not allowed")

        # Per-slot results are collected in plain lists (element writes into
        # NumPy arrays are slow) and turned into arrays after the loop.
        nan = float("nan")
        loads_energy_per_hour: Any = [nan] * total_hours
        feedin_energy_per_hour: Any = [nan] * total_hours
        consumption_energy_per_hour: Any = [nan] * total_hours
        costs_per_hour: Any = [nan] * total_hours
        revenue_per_hour: Any = [nan] * total_hours
        losses_wh_per_hour: Any = [nan] * total_hours
        electricity_price_per_hour: Any = [nan] * total_hours
        feed_in_tariff_per_hour: Any = [nan] * total_hours
        soc_per_hour: Any
        soc_ev_per_hour: Any
        home_appliance_wh_per_hour: Any

        # Set initial state
        if battery_fast:
            soc_per_hour = [nan] * total_hours

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

            # If AC charging is disabled via inverter, zero out AC charge hours.
            # In place, not by rebinding: the reported plan is read back from
            # this very array, so a rebind would leave AC charge values in the
            # solution that the simulation never executed - and a controller
            # acting on them would grid-charge the battery unplanned.
            if not ac_charging_possible:
                ac_charge_hours_fast[:] = 0.0

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
                    & (elect_revenue_per_hour_arr_fast[: len(bat_grid_export_hours_fast)] > 0.0)
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
            soc_ev_per_hour = [nan] * total_hours

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
            # The aggregate appliance load (sum over all devices). Each
            # appliance already carries its own resampled load curve, built
            # from the decoded start(s) before this call.
            home_appliance_wh_per_hour = [nan] * total_hours
            appliance_loads_fast = [
                appliance.get_load_for_hour for appliance in home_appliances_fast
            ]
        else:
            home_appliance_enabled = False
            # Default return if no home appliance is available
            home_appliance_wh_per_hour = np.full((total_hours), 0)
            appliance_loads_fast = []

        # The per-slot inputs as plain numbers. They are final here: the loop
        # itself changes only the charge arrays of the devices, and those after
        # the slot value was read.
        load_energy_array_fast = _slot_values(load_energy_array_fast)
        pv_prediction_wh_fast = _slot_values(pv_prediction_wh_fast)
        elect_price_hourly_fast = _slot_values(elect_price_hourly_fast)
        elect_revenue_per_hour_arr_fast = _slot_values(elect_revenue_per_hour_arr_fast)
        bat_grid_export_hours_fast = _slot_values(bat_grid_export_hours_fast)
        ac_charge_hours_fast = _slot_values(ac_charge_hours_fast)
        ev_charge_hours_fast = _slot_values(ev_charge_hours_fast)
        if battery_fast:
            battery_lcos_per_wh_factor = battery_fast.levelized_cost_of_storage_kwh

        # Aggregate appliance load per slot, summed once per candidate in the
        # order the loop used to add the appliances (0.0 + first + second ...).
        # Anything unusual (another appliance class, a curve that does not
        # cover the simulated slots) keeps the per-slot lookups and their
        # range check.
        appliance_load_fast: Optional[list[float]] = None
        if home_appliance_enabled and start_hour >= 0:
            appliance_load_fast = [0.0] * end_hour
            for appliance in home_appliances_fast:
                curve = getattr(appliance, "load_curve", None)
                if (
                    type(appliance).get_load_for_hour is not GeneticHomeAppliance.get_load_for_hour
                    or not isinstance(curve, np.ndarray)
                    or curve.dtype != np.float64
                    or curve.ndim != 1
                    or len(curve) < end_hour
                    or appliance.prediction_hours < end_hour
                ):
                    appliance_load_fast = None
                    break
                appliance_load_fast = [
                    total + load for total, load in zip(appliance_load_fast, curve.tolist())
                ]

        # State of charge in percent, read directly where the device uses the
        # plain Battery formula (two method calls per slot otherwise).
        battery_soc_direct = bool(
            battery_fast
            and type(battery_fast).current_soc_percentage is GeneticBattery.current_soc_percentage
        )
        ev_soc_direct = bool(
            ev_fast
            and type(ev_fast).current_soc_percentage is GeneticBattery.current_soc_percentage
        )
        battery_lcos_direct = False
        if battery_fast is not None and type(battery_fast) is GeneticBattery:
            battery_lcos_direct = True
            if battery_fast._curves is None:
                battery_discharged_raw_fast = battery_fast._discharged_raw_wh_per_slot
                battery_discharging_efficiency_fast = battery_fast.discharging_efficiency
            else:
                # With power dependent efficiencies the delivered energy is kept
                # per slot (see Battery.discharged_energy_wh()).
                battery_discharged_raw_fast = battery_fast._curves.discharged_dc_wh_per_slot
                battery_discharging_efficiency_fast = 1.0

        for hour in range(start_hour, end_hour):
            hour_idx = hour - start_hour

            # Accumulate loads and PV generation
            consumption = load_energy_array_fast[hour]
            slot_losses = 0.0

            # Home appliances (sum the per-slot load of all flexible consumers)
            if home_appliance_enabled:
                if appliance_load_fast is not None:
                    ha_load = appliance_load_fast[hour]
                else:
                    ha_load = 0.0
                    for appliance_load in appliance_loads_fast:
                        ha_load += appliance_load(hour)
                consumption += ha_load
                home_appliance_wh_per_hour[hour_idx] = ha_load

            # E-Auto handling
            if ev_fast:
                # save begin state
                if ev_soc_direct:
                    soc_ev_per_hour[hour_idx] = (ev_fast.soc_wh / ev_fast.capacity_wh) * 100
                else:
                    soc_ev_per_hour[hour_idx] = ev_fast.current_soc_percentage()
                ev_charge_factor = ev_charge_hours_fast[hour]
                if ev_charge_factor > 0:
                    stored_energy_ev, verluste_eauto = ev_fast.charge_energy(
                        wh=None, hour=hour, charge_factor=ev_charge_factor
                    )
                    # The inverter/grid must supply the EV charger's raw input,
                    # not only the energy stored after charging losses.
                    consumption += stored_energy_ev + verluste_eauto
                    slot_losses += verluste_eauto

            # Save battery SOC before inverter processing = true begin-of-interval state.
            # Must be recorded here (before DC charge/discharge) so the displayed SOC at
            # timestamp T reflects what the battery actually had at the START of interval T,
            # not the post-DC result. Consistent with the EV SOC convention above.
            if battery_fast:
                if battery_soc_direct:
                    soc_per_hour[hour_idx] = (battery_fast.soc_wh / battery_fast.capacity_wh) * 100
                else:
                    soc_per_hour[hour_idx] = battery_fast.current_soc_percentage()

            # Process inverter logic
            energy_feedin_grid_actual = energy_consumption_grid_actual = losses = eigenverbrauch = (
                0.0
            )

            # AC charge factor of this slot, capped by max_ac_charge_power_w
            ac_charge_factor = 0.0
            if battery_fast and ac_charging_possible:
                ac_charge_factor = ac_charge_hours_fast[hour]
                # No AC charging in this slot (the usual case): nothing to cap.
                if inverter_fast and ac_charge_factor != 0:
                    ac_charge_factor = inverter_fast.ac_charge_factor(ac_charge_factor)

            if inverter_fast:
                # Some inverters cap the total charge power with the AC setpoint;
                # that cap has to be in place before PV charges the battery.
                if ac_charge_factor > 0.0:
                    inverter_fast.begin_ac_charge_slot(hour, ac_charge_factor)
                energy_produced = pv_prediction_wh_fast[hour]
                hourly_feed_in_tariff = elect_revenue_per_hour_arr_fast[hour]
                # bat_grid_export_hours carries the export level per slot:
                # 0.0 = no export, otherwise the factor of the rated discharge
                # power the optimizer selected.
                battery_grid_export_factor = float(bat_grid_export_hours_fast[hour])
                battery_grid_export_allowed = (
                    direct_marketing_enabled_fast
                    and hourly_feed_in_tariff > 0.0
                    and battery_grid_export_factor > 0.0
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
                    battery_grid_export_allowed,
                    battery_grid_export_factor,
                )
            else:
                hourly_feed_in_tariff = elect_revenue_per_hour_arr_fast[hour]

            # AC PV Battery Charge
            if ac_charge_factor > 0.0 and battery_fast:
                if inverter_fast:
                    ac_energy, ac_charge_losses = inverter_fast.charge_battery_from_grid(
                        hour, ac_charge_factor
                    )
                else:
                    # Without an inverter the grid charges the battery losslessly
                    # (AC-to-DC efficiency 1.0).
                    stored, ac_charge_losses = battery_fast.charge_energy(
                        None, hour, charge_factor=ac_charge_factor
                    )
                    ac_energy = stored + ac_charge_losses
                consumption += ac_energy
                energy_consumption_grid_actual += ac_energy
                slot_losses += ac_charge_losses

            # Update hourly arrays
            if (
                direct_marketing_enabled_fast
                and hourly_feed_in_tariff < 0.0
                and energy_feedin_grid_actual > 0.0
            ):
                slot_losses += energy_feedin_grid_actual
                energy_feedin_grid_actual = 0.0

            feedin_energy_per_hour[hour_idx] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_idx] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_idx] = slot_losses + losses
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
            if battery_lcos_direct:
                # Battery.discharged_energy_wh(), without the call per slot.
                battery_lcos_cost = (
                    battery_discharged_raw_fast[hour]
                    * battery_discharging_efficiency_fast
                    * battery_lcos_per_wh_factor
                    / 1000.0
                )
            elif battery_fast:
                battery_lcos_cost = (
                    battery_fast.discharged_energy_wh(hour) * battery_lcos_per_wh_factor / 1000.0
                )
            costs_per_hour[hour_idx] = grid_cost + battery_lcos_cost
            revenue_per_hour[hour_idx] = energy_feedin_grid_actual * hourly_feed_in_tariff

        loads_energy_per_hour = np.array(loads_energy_per_hour, dtype=float)
        feedin_energy_per_hour = np.array(feedin_energy_per_hour, dtype=float)
        consumption_energy_per_hour = np.array(consumption_energy_per_hour, dtype=float)
        costs_per_hour = np.array(costs_per_hour, dtype=float)
        revenue_per_hour = np.array(revenue_per_hour, dtype=float)
        losses_wh_per_hour = np.array(losses_wh_per_hour, dtype=float)
        electricity_price_per_hour = np.array(electricity_price_per_hour, dtype=float)
        feed_in_tariff_per_hour = np.array(feed_in_tariff_per_hour, dtype=float)
        if battery_fast:
            soc_per_hour = np.array(soc_per_hour, dtype=float)
        if ev_fast:
            soc_ev_per_hour = np.array(soc_ev_per_hour, dtype=float)
        if home_appliance_enabled:
            home_appliance_wh_per_hour = np.array(home_appliance_wh_per_hour, dtype=float)

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
