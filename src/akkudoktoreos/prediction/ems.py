from datetime import datetime
from typing import Any, Dict, Optional, Union

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.config import EOSConfig
from akkudoktoreos.devices.battery import PVAkku
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Wechselrichter
from akkudoktoreos.utils.utils import NumpyEncoder


class EnergieManagementSystemParameters(BaseModel):
    pv_prognose_wh: list[float] = Field(
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
    )
    strompreis_euro_pro_wh: list[float] = Field(
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals."
    )
    einspeiseverguetung_euro_pro_wh: list[float] | float = Field(
        description="A float or array of floats representing the feed-in compensation in euros per watt-hour."
    )
    preis_euro_pro_wh_akku: float
    gesamtlast: list[float] = Field(
        description="An array of floats representing the total load (consumption) in watts for different time intervals."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        pv_prognose_length = len(self.pv_prognose_wh)
        if (
            pv_prognose_length != len(self.strompreis_euro_pro_wh)
            or pv_prognose_length != len(self.gesamtlast)
            or (
                isinstance(self.einspeiseverguetung_euro_pro_wh, list)
                and pv_prognose_length != len(self.einspeiseverguetung_euro_pro_wh)
            )
        ):
            raise ValueError("Input lists have different lengths")
        return self


class SimulationResult(BaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    Last_Wh_pro_Stunde: list[Optional[float]] = Field(description="TBD")
    EAuto_SoC_pro_Stunde: list[Optional[float]] = Field(
        description="The state of charge of the EV for each hour."
    )
    Einnahmen_Euro_pro_Stunde: list[Optional[float]] = Field(
        description="The revenue from grid feed-in or other sources in euros per hour."
    )
    Gesamt_Verluste: float = Field(
        description="The total losses in watt-hours over the entire period."
    )
    Gesamtbilanz_Euro: float = Field(
        description="The total balance of revenues minus costs in euros."
    )
    Gesamteinnahmen_Euro: float = Field(description="The total revenues in euros.")
    Gesamtkosten_Euro: float = Field(description="The total costs in euros.")
    Home_appliance_wh_per_hour: list[Optional[float]] = Field(
        description="The energy consumption of a household appliance in watt-hours per hour."
    )
    Kosten_Euro_pro_Stunde: list[Optional[float]] = Field(
        description="The costs in euros per hour."
    )
    Netzbezug_Wh_pro_Stunde: list[Optional[float]] = Field(
        description="The grid energy drawn in watt-hours per hour."
    )
    Netzeinspeisung_Wh_pro_Stunde: list[Optional[float]] = Field(
        description="The energy fed into the grid in watt-hours per hour."
    )
    Verluste_Pro_Stunde: list[Optional[float]] = Field(
        description="The losses in watt-hours per hour."
    )
    akku_soc_pro_stunde: list[Optional[float]] = Field(
        description="The state of charge of the battery (not the EV) in percentage per hour."
    )
    Electricity_price: list[Optional[float]] = Field(
        description="Used Electricity Price, including predictions"
    )

    @field_validator(
        "Last_Wh_pro_Stunde",
        "Netzeinspeisung_Wh_pro_Stunde",
        "akku_soc_pro_stunde",
        "Netzbezug_Wh_pro_Stunde",
        "Kosten_Euro_pro_Stunde",
        "Einnahmen_Euro_pro_Stunde",
        "EAuto_SoC_pro_Stunde",
        "Verluste_Pro_Stunde",
        "Home_appliance_wh_per_hour",
        "Electricity_price",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class EnergieManagementSystem:
    def __init__(
        self,
        config: EOSConfig,
        parameters: EnergieManagementSystemParameters,
        wechselrichter: Wechselrichter,
        eauto: Optional[PVAkku] = None,
        home_appliance: Optional[HomeAppliance] = None,
    ):
        self.akku = wechselrichter.akku
        self.gesamtlast = np.array(parameters.gesamtlast, float)
        self.pv_prognose_wh = np.array(parameters.pv_prognose_wh, float)
        self.strompreis_euro_pro_wh = np.array(parameters.strompreis_euro_pro_wh, float)
        self.einspeiseverguetung_euro_pro_wh_arr = (
            parameters.einspeiseverguetung_euro_pro_wh
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(len(self.gesamtlast), parameters.einspeiseverguetung_euro_pro_wh, float)
        )
        self.eauto = eauto
        self.home_appliance = home_appliance
        self.wechselrichter = wechselrichter
        self.ac_charge_hours = np.full(config.prediction_hours, 0)
        self.dc_charge_hours = np.full(config.prediction_hours, 1)
        self.ev_charge_hours = np.full(config.prediction_hours, 0)

    def set_akku_discharge_hours(self, ds: np.ndarray) -> None:
        self.akku.set_discharge_per_hour(ds)

    def set_akku_ac_charge_hours(self, ds: np.ndarray) -> None:
        self.ac_charge_hours = ds

    def set_akku_dc_charge_hours(self, ds: np.ndarray) -> None:
        self.dc_charge_hours = ds

    def set_ev_charge_hours(self, ds: np.ndarray) -> None:
        self.ev_charge_hours = ds

    def set_home_appliance_start(self, start_hour: int, global_start_hour: int = 0) -> None:
        assert self.home_appliance is not None
        self.home_appliance.set_starting_time(start_hour, global_start_hour=global_start_hour)

    def reset(self) -> None:
        if self.eauto:
            self.eauto.reset()
        self.akku.reset()

    def simuliere_ab_jetzt(self) -> dict[str, Any]:
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        return self.simuliere(start_stunde)

    def simuliere(self, start_hour: int) -> dict[str, Any]:
        """hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of  last hour (end state)
        """
        lastkurve_wh = self.gesamtlast
        assert (
            len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh)
        ), f"Array sizes do not match: Load Curve = {len(lastkurve_wh)}, PV Forecast = {len(self.pv_prognose_wh)}, Electricity Price = {len(self.strompreis_euro_pro_wh)}"

        # Optimized total hours calculation
        end_hour = len(lastkurve_wh)
        total_hours = end_hour - start_hour

        # Pre-allocate arrays for the results, optimized for speed
        loads_energy_per_hour = np.full((total_hours), np.nan)
        feedin_energy_per_hour = np.full((total_hours), np.nan)
        consumption_energy_per_hour = np.full((total_hours), np.nan)
        costs_per_hour = np.full((total_hours), np.nan)
        revenue_per_hour = np.full((total_hours), np.nan)
        soc_per_hour = np.full((total_hours), np.nan)  # Hour End State
        soc_ev_per_hour = np.full((total_hours), np.nan)
        losses_wh_per_hour = np.full((total_hours), np.nan)
        home_appliance_wh_per_hour = np.full((total_hours), np.nan)
        electricity_price_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        soc_per_hour[0] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            soc_ev_per_hour[0] = self.eauto.ladezustand_in_prozent()

        # All States
        for hour in range(start_hour, end_hour):
            hour_since_now = hour - start_hour

            # save begin states
            soc_per_hour[hour_since_now] = self.akku.ladezustand_in_prozent()
            if self.eauto:
                soc_ev_per_hour[hour_since_now] = self.eauto.ladezustand_in_prozent()

            # Accumulate loads and PV generation
            consumption = self.gesamtlast[hour]
            losses_wh_per_hour[hour_since_now] = 0.0
            if self.home_appliance:
                ha_load = self.home_appliance.get_load_for_hour(hour)
                consumption += ha_load
                home_appliance_wh_per_hour[hour_since_now] = ha_load

            # E-Auto handling
            if self.eauto and self.ev_charge_hours[hour] > 0:
                loaded_energy_ev, verluste_eauto = self.eauto.energie_laden(
                    None, hour, relative_power=self.ev_charge_hours[hour]
                )
                consumption += loaded_energy_ev
                losses_wh_per_hour[hour_since_now] += verluste_eauto

            # Process inverter logic
            energy_produced = self.pv_prognose_wh[hour]
            self.akku.set_charge_allowed_for_hour(self.dc_charge_hours[hour], hour)
            energy_feedin_grid_actual, energy_consumption_grid_actual, losses, eigenverbrauch = (
                self.wechselrichter.energie_verarbeiten(energy_produced, consumption, hour)
            )

            # AC PV Battery Charge
            if self.ac_charge_hours[hour] > 0.0:
                self.akku.set_charge_allowed_for_hour(1, hour)
                battery_charged_energy_actual, battery_losses_actual = self.akku.energie_laden(
                    None, hour, relative_power=self.ac_charge_hours[hour]
                )
                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.akku.ladezustand_in_prozent())
                consumption += battery_charged_energy_actual
                consumption += battery_losses_actual
                energy_consumption_grid_actual += battery_charged_energy_actual
                energy_consumption_grid_actual += battery_losses_actual
                losses_wh_per_hour[hour_since_now] += battery_losses_actual

            feedin_energy_per_hour[hour_since_now] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_since_now] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_since_now] += losses
            loads_energy_per_hour[hour_since_now] = consumption
            electricity_price_per_hour[hour_since_now] = self.strompreis_euro_pro_wh[hour]
            # Financial calculations
            costs_per_hour[hour_since_now] = (
                energy_consumption_grid_actual * self.strompreis_euro_pro_wh[hour]
            )
            revenue_per_hour[hour_since_now] = (
                energy_feedin_grid_actual * self.einspeiseverguetung_euro_pro_wh_arr[hour]
            )

        # Total cost and return
        gesamtkosten_euro = np.nansum(costs_per_hour) - np.nansum(revenue_per_hour)

        # Prepare output dictionary
        out: Dict[str, Union[np.ndarray, float]] = {
            "Last_Wh_pro_Stunde": loads_energy_per_hour,
            "Netzeinspeisung_Wh_pro_Stunde": feedin_energy_per_hour,
            "Netzbezug_Wh_pro_Stunde": consumption_energy_per_hour,
            "Kosten_Euro_pro_Stunde": costs_per_hour,
            "akku_soc_pro_stunde": soc_per_hour,
            "Einnahmen_Euro_pro_Stunde": revenue_per_hour,
            "Gesamtbilanz_Euro": gesamtkosten_euro,
            "EAuto_SoC_pro_Stunde": soc_ev_per_hour,
            "Gesamteinnahmen_Euro": np.nansum(revenue_per_hour),
            "Gesamtkosten_Euro": np.nansum(costs_per_hour),
            "Verluste_Pro_Stunde": losses_wh_per_hour,
            "Gesamt_Verluste": np.nansum(losses_wh_per_hour),
            "Home_appliance_wh_per_hour": home_appliance_wh_per_hour,
            "Electricity_price": electricity_price_per_hour,
        }
        return out
