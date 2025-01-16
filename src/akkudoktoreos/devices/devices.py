from typing import Any, ClassVar, Dict, Optional, Union

import numpy as np
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.devicesabc import DevicesBase
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Inverter
from akkudoktoreos.prediction.interpolator import SelfConsumptionPropabilityInterpolator
from akkudoktoreos.utils.datetimeutil import to_duration

logger = get_logger(__name__)


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    # Battery
    # -------
    battery_provider: Optional[str] = Field(
        default=None, description="Id of Battery simulation provider."
    )
    battery_capacity: Optional[int] = Field(default=None, description="Battery capacity [Wh].")
    battery_initial_soc: Optional[int] = Field(
        default=None, description="Battery initial state of charge [%]."
    )
    battery_soc_min: Optional[int] = Field(
        default=None, description="Battery minimum state of charge [%]."
    )
    battery_soc_max: Optional[int] = Field(
        default=None, description="Battery maximum state of charge [%]."
    )
    battery_charging_efficiency: Optional[float] = Field(
        default=None, description="Battery charging efficiency [%]."
    )
    battery_discharging_efficiency: Optional[float] = Field(
        default=None, description="Battery discharging efficiency [%]."
    )
    battery_max_charging_power: Optional[int] = Field(
        default=None, description="Battery maximum charge power [W]."
    )

    # Battery Electric Vehicle
    # ------------------------
    bev_provider: Optional[str] = Field(
        default=None, description="Id of Battery Electric Vehicle simulation provider."
    )
    bev_capacity: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle capacity [Wh]."
    )
    bev_initial_soc: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle initial state of charge [%]."
    )
    bev_soc_max: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle maximum state of charge [%]."
    )
    bev_charging_efficiency: Optional[float] = Field(
        default=None, description="Battery Electric Vehicle charging efficiency [%]."
    )
    bev_discharging_efficiency: Optional[float] = Field(
        default=None, description="Battery Electric Vehicle discharging efficiency [%]."
    )
    bev_max_charging_power: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle maximum charge power [W]."
    )

    # Home Appliance - Dish Washer
    # ----------------------------
    dishwasher_provider: Optional[str] = Field(
        default=None, description="Id of Dish Washer simulation provider."
    )
    dishwasher_consumption: Optional[int] = Field(
        default=None, description="Dish Washer energy consumption [Wh]."
    )
    dishwasher_duration: Optional[int] = Field(
        default=None, description="Dish Washer usage duration [h]."
    )

    # PV Inverter
    # -----------
    inverter_provider: Optional[str] = Field(
        default=None, description="Id of PV Inverter simulation provider."
    )
    inverter_power_max: Optional[float] = Field(
        default=None, description="Inverter maximum power [W]."
    )


class Devices(SingletonMixin, DevicesBase):
    # Results of the devices simulation and
    # insights into various parameters over the entire forecast period.
    # -----------------------------------------------------------------
    last_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The load in watt-hours per hour."
    )
    eauto_soc_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The state of charge of the EV for each hour."
    )
    einnahmen_euro_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="The revenue from grid feed-in or other sources in euros per hour.",
    )
    home_appliance_wh_per_hour: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="The energy consumption of a household appliance in watt-hours per hour.",
    )
    kosten_euro_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The costs in euros per hour."
    )
    grid_import_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The grid energy drawn in watt-hours per hour."
    )
    grid_export_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The energy fed into the grid in watt-hours per hour."
    )
    verluste_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The losses in watt-hours per hour."
    )
    battery_soc_per_hour: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="The state of charge of the battery (not the EV) in percentage per hour.",
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_balance_euro(self) -> float:
        """The total balance of revenues minus costs in euros."""
        return self.total_revenues_euro - self.total_costs_euro

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_revenues_euro(self) -> float:
        """The total revenues in euros."""
        if self.einnahmen_euro_pro_stunde is None:
            return 0
        return np.nansum(self.einnahmen_euro_pro_stunde)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_costs_euro(self) -> float:
        """The total costs in euros."""
        if self.kosten_euro_pro_stunde is None:
            return 0
        return np.nansum(self.kosten_euro_pro_stunde)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_losses_wh(self) -> float:
        """The total losses in watt-hours over the entire period."""
        if self.verluste_wh_pro_stunde is None:
            return 0
        return np.nansum(self.verluste_wh_pro_stunde)

    # Devices
    # TODO: Make devices class a container of device simulation providers.
    #       Device simulations to be used are then enabled in the configuration.
    battery: ClassVar[Battery] = Battery(provider_id="GenericBattery")
    ev: ClassVar[Battery] = Battery(provider_id="GenericBEV")
    home_appliance: ClassVar[HomeAppliance] = HomeAppliance(provider_id="GenericDishWasher")
    inverter: ClassVar[Inverter] = Inverter(
        self_consumption_predictor=SelfConsumptionPropabilityInterpolator,
        battery=battery,
        provider_id="GenericInverter",
    )

    def update_data(self) -> None:
        """Update device simulation data."""
        # Assure devices are set up
        self.battery.setup()
        self.ev.setup()
        self.home_appliance.setup()
        self.inverter.setup()

        # Pre-allocate arrays for the results, optimized for speed
        self.last_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.grid_export_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.grid_import_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.kosten_euro_pro_stunde = np.full((self.total_hours), np.nan)
        self.einnahmen_euro_pro_stunde = np.full((self.total_hours), np.nan)
        self.battery_soc_per_hour = np.full((self.total_hours), np.nan)
        self.eauto_soc_pro_stunde = np.full((self.total_hours), np.nan)
        self.verluste_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.home_appliance_wh_per_hour = np.full((self.total_hours), np.nan)

        # Set initial state
        simulation_step = to_duration("1 hour")
        if self.battery:
            self.battery_soc_per_hour[0] = self.battery.current_soc_percentage()
        if self.ev:
            self.eauto_soc_pro_stunde[0] = self.ev.current_soc_percentage()

        # Get predictions for full device simulation time range
        # gesamtlast[stunde]
        load_total_mean = self.prediction.key_to_array(
            "load_total_mean",
            start_datetime=self.start_datetime,
            end_datetime=self.end_datetime,
            interval=simulation_step,
        )
        # pv_prognose_wh[stunde]
        pvforecast_ac_power = self.prediction.key_to_array(
            "pvforecast_ac_power",
            start_datetime=self.start_datetime,
            end_datetime=self.end_datetime,
            interval=simulation_step,
        )
        # strompreis_euro_pro_wh[stunde]
        elecprice_marketprice_wh = self.prediction.key_to_array(
            "elecprice_marketprice_wh",
            start_datetime=self.start_datetime,
            end_datetime=self.end_datetime,
            interval=simulation_step,
        )
        # einspeiseverguetung_euro_pro_wh_arr[stunde]
        # TODO: Create prediction for einspeiseverguetung_euro_pro_wh_arr
        einspeiseverguetung_euro_pro_wh_arr = np.full((self.total_hours), 0.078)

        for stunde_since_now in range(0, self.total_hours):
            hour = self.start_datetime.hour + stunde_since_now

            # Accumulate loads and PV generation
            consumption = load_total_mean[stunde_since_now]
            self.verluste_wh_pro_stunde[stunde_since_now] = 0.0

            # Home appliances
            if self.home_appliance:
                ha_load = self.home_appliance.get_load_for_hour(hour)
                consumption += ha_load
                self.home_appliance_wh_per_hour[stunde_since_now] = ha_load

            # E-Auto handling
            if self.ev:
                if self.ev_charge_hours[hour] > 0:
                    geladene_menge_eauto, verluste_eauto = self.ev.charge_energy(
                        None, hour, relative_power=self.ev_charge_hours[hour]
                    )
                    consumption += geladene_menge_eauto
                    self.verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
                self.eauto_soc_pro_stunde[stunde_since_now] = self.ev.current_soc_percentage()

            # Process inverter logic
            grid_export, grid_import, losses, self_consumption = (0.0, 0.0, 0.0, 0.0)
            if self.battery:
                self.battery.set_charge_allowed_for_hour(self.dc_charge_hours[hour], hour)
            if self.inverter:
                generation = pvforecast_ac_power[hour]
                grid_export, grid_import, losses, self_consumption = self.inverter.process_energy(
                    generation, consumption, hour
                )

            # AC PV Battery Charge
            if self.battery and self.ac_charge_hours[hour] > 0.0:
                self.battery.set_charge_allowed_for_hour(1, hour)
                geladene_menge, verluste_wh = self.battery.charge_energy(
                    None, hour, relative_power=self.ac_charge_hours[hour]
                )
                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.battery.current_soc_percentage())
                consumption += geladene_menge
                grid_import += geladene_menge
                self.verluste_wh_pro_stunde[stunde_since_now] += verluste_wh

            self.grid_export_wh_pro_stunde[stunde_since_now] = grid_export
            self.grid_import_wh_pro_stunde[stunde_since_now] = grid_import
            self.verluste_wh_pro_stunde[stunde_since_now] += losses
            self.last_wh_pro_stunde[stunde_since_now] = consumption

            # Financial calculations
            self.kosten_euro_pro_stunde[stunde_since_now] = (
                grid_import * self.strompreis_euro_pro_wh[hour]
            )
            self.einnahmen_euro_pro_stunde[stunde_since_now] = (
                grid_export * self.einspeiseverguetung_euro_pro_wh_arr[hour]
            )

            # battery SOC tracking
            if self.battery:
                self.battery_soc_per_hour[stunde_since_now] = self.battery.current_soc_percentage()
            else:
                self.battery_soc_per_hour[stunde_since_now] = 0.0

    def report_dict(self) -> Dict[str, Any]:
        """Provides devices simulation output as a dictionary."""
        out: Dict[str, Optional[Union[np.ndarray, float]]] = {
            "Last_Wh_pro_Stunde": self.last_wh_pro_stunde,
            "grid_export_Wh_pro_Stunde": self.grid_export_wh_pro_stunde,
            "grid_import_Wh_pro_Stunde": self.grid_import_wh_pro_stunde,
            "Kosten_Euro_pro_Stunde": self.kosten_euro_pro_stunde,
            "battery_soc_per_hour": self.battery_soc_per_hour,
            "Einnahmen_Euro_pro_Stunde": self.einnahmen_euro_pro_stunde,
            "Gesamtbilanz_Euro": self.total_balance_euro,
            "EAuto_SoC_pro_Stunde": self.eauto_soc_pro_stunde,
            "Gesamteinnahmen_Euro": self.total_revenues_euro,
            "Gesamtkosten_Euro": self.total_costs_euro,
            "Verluste_Pro_Stunde": self.verluste_wh_pro_stunde,
            "Gesamt_Verluste": self.total_losses_wh,
            "Home_appliance_wh_per_hour": self.home_appliance_wh_per_hour,
        }
        return out


# Initialize the Devices  simulation, it is a singleton.
devices = Devices()


def get_devices() -> Devices:
    """Gets the EOS Devices simulation."""
    return devices
