from typing import Any, ClassVar, Dict, Optional, Union

import numpy as np
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.devices.battery import PVAkku
from akkudoktoreos.devices.devicesabc import DevicesBase
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Wechselrichter
from akkudoktoreos.utils.datetimeutil import to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    # Battery
    # -------
    battery_provider: Optional[str] = Field(
        default=None, description="Id of Battery simulation provider."
    )
    battery_capacity: Optional[int] = Field(default=None, description="Battery capacity [Wh].")
    battery_soc_start: Optional[int] = Field(
        default=None, description="Battery initial state of charge [%]."
    )
    battery_soc_min: Optional[int] = Field(
        default=None, description="Battery minimum state of charge [%]."
    )
    battery_soc_max: Optional[int] = Field(
        default=None, description="Battery maximum state of charge [%]."
    )
    battery_charge_efficiency: Optional[float] = Field(
        default=None, description="Battery charging efficiency [%]."
    )
    battery_discharge_efficiency: Optional[float] = Field(
        default=None, description="Battery discharging efficiency [%]."
    )
    battery_charge_power_max: Optional[int] = Field(
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
    bev_soc_start: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle initial state of charge [%]."
    )
    bev_soc_max: Optional[int] = Field(
        default=None, description="Battery Electric Vehicle maximum state of charge [%]."
    )
    bev_charge_efficiency: Optional[float] = Field(
        default=None, description="Battery Electric Vehicle charging efficiency [%]."
    )
    bev_discharge_efficiency: Optional[float] = Field(
        default=None, description="Battery Electric Vehicle discharging efficiency [%]."
    )
    bev_charge_power_max: Optional[int] = Field(
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
    netzbezug_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The grid energy drawn in watt-hours per hour."
    )
    netzeinspeisung_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The energy fed into the grid in watt-hours per hour."
    )
    verluste_wh_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, description="The losses in watt-hours per hour."
    )
    akku_soc_pro_stunde: Optional[NDArray[Shape["*"], float]] = Field(
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
    akku: ClassVar[PVAkku] = PVAkku(provider_id="GenericBattery")
    eauto: ClassVar[PVAkku] = PVAkku(provider_id="GenericBEV")
    home_appliance: ClassVar[HomeAppliance] = HomeAppliance(provider_id="GenericDishWasher")
    wechselrichter: ClassVar[Wechselrichter] = Wechselrichter(
        akku=akku, provider_id="GenericInverter"
    )

    def update_data(self) -> None:
        """Update device simulation data."""
        # Assure devices are set up
        self.akku.setup()
        self.eauto.setup()
        self.home_appliance.setup()
        self.wechselrichter.setup()

        # Pre-allocate arrays for the results, optimized for speed
        self.last_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.netzeinspeisung_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.netzbezug_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.kosten_euro_pro_stunde = np.full((self.total_hours), np.nan)
        self.einnahmen_euro_pro_stunde = np.full((self.total_hours), np.nan)
        self.akku_soc_pro_stunde = np.full((self.total_hours), np.nan)
        self.eauto_soc_pro_stunde = np.full((self.total_hours), np.nan)
        self.verluste_wh_pro_stunde = np.full((self.total_hours), np.nan)
        self.home_appliance_wh_per_hour = np.full((self.total_hours), np.nan)

        # Set initial state
        simulation_step = to_duration("1 hour")
        if self.akku:
            self.akku_soc_pro_stunde[0] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            self.eauto_soc_pro_stunde[0] = self.eauto.ladezustand_in_prozent()

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
        elecprice_marketprice = self.prediction.key_to_array(
            "elecprice_marketprice",
            start_datetime=self.start_datetime,
            end_datetime=self.end_datetime,
            interval=simulation_step,
        )
        # einspeiseverguetung_euro_pro_wh_arr[stunde]
        # TODO: Create prediction for einspeiseverguetung_euro_pro_wh_arr
        einspeiseverguetung_euro_pro_wh_arr = np.full((self.total_hours), 0.078)

        for stunde_since_now in range(0, self.total_hours):
            stunde = self.start_datetime.hour + stunde_since_now

            # Accumulate loads and PV generation
            verbrauch = load_total_mean[stunde_since_now]
            self.verluste_wh_pro_stunde[stunde_since_now] = 0.0

            # Home appliances
            if self.home_appliance:
                ha_load = self.home_appliance.get_load_for_hour(stunde)
                verbrauch += ha_load
                self.home_appliance_wh_per_hour[stunde_since_now] = ha_load

            # E-Auto handling
            if self.eauto:
                if self.ev_charge_hours[stunde] > 0:
                    geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(
                        None, stunde, relative_power=self.ev_charge_hours[stunde]
                    )
                    verbrauch += geladene_menge_eauto
                    self.verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
                self.eauto_soc_pro_stunde[stunde_since_now] = self.eauto.ladezustand_in_prozent()

            # Process inverter logic
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = (0.0, 0.0, 0.0, 0.0)
            if self.akku:
                self.akku.set_charge_allowed_for_hour(self.dc_charge_hours[stunde], stunde)
            if self.wechselrichter:
                erzeugung = pvforecast_ac_power[stunde]
                netzeinspeisung, netzbezug, verluste, eigenverbrauch = (
                    self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)
                )

            # AC PV Battery Charge
            if self.akku and self.ac_charge_hours[stunde] > 0.0:
                self.akku.set_charge_allowed_for_hour(1, stunde)
                geladene_menge, verluste_wh = self.akku.energie_laden(
                    None, stunde, relative_power=self.ac_charge_hours[stunde]
                )
                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.akku.ladezustand_in_prozent())
                verbrauch += geladene_menge
                netzbezug += geladene_menge
                self.verluste_wh_pro_stunde[stunde_since_now] += verluste_wh

            self.netzeinspeisung_wh_pro_stunde[stunde_since_now] = netzeinspeisung
            self.netzbezug_wh_pro_stunde[stunde_since_now] = netzbezug
            self.verluste_wh_pro_stunde[stunde_since_now] += verluste
            self.last_wh_pro_stunde[stunde_since_now] = verbrauch

            # Financial calculations
            self.kosten_euro_pro_stunde[stunde_since_now] = (
                netzbezug * self.strompreis_euro_pro_wh[stunde]
            )
            self.einnahmen_euro_pro_stunde[stunde_since_now] = (
                netzeinspeisung * self.einspeiseverguetung_euro_pro_wh_arr[stunde]
            )

            # Akku SOC tracking
            if self.akku:
                self.akku_soc_pro_stunde[stunde_since_now] = self.akku.ladezustand_in_prozent()
            else:
                self.akku_soc_pro_stunde[stunde_since_now] = 0.0

    def report_dict(self) -> Dict[str, Any]:
        """Provides devices simulation output as a dictionary."""
        out: Dict[str, Optional[Union[np.ndarray, float]]] = {
            "Last_Wh_pro_Stunde": self.last_wh_pro_stunde,
            "Netzeinspeisung_Wh_pro_Stunde": self.netzeinspeisung_wh_pro_stunde,
            "Netzbezug_Wh_pro_Stunde": self.netzbezug_wh_pro_stunde,
            "Kosten_Euro_pro_Stunde": self.kosten_euro_pro_stunde,
            "akku_soc_pro_stunde": self.akku_soc_pro_stunde,
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
