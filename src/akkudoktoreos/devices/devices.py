from typing import Optional

from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.devicesabc import DevicesBase
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Inverter
from akkudoktoreos.devices.settings import DevicesCommonSettings

logger = get_logger(__name__)


class Devices(SingletonMixin, DevicesBase):
    def __init__(self, settings: Optional[DevicesCommonSettings] = None):
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        if settings is None:
            settings = self.config.devices
            if settings is None:
                return

        # initialize devices
        if settings.batteries is not None:
            for battery_params in settings.batteries:
                self.add_device(Battery(battery_params))
        if settings.inverters is not None:
            for inverter_params in settings.inverters:
                self.add_device(Inverter(inverter_params))
        if settings.home_appliances is not None:
            for home_appliance_params in settings.home_appliances:
                self.add_device(HomeAppliance(home_appliance_params))

        self.post_setup()

    def post_setup(self) -> None:
        for device in self.devices.values():
            device.post_setup()


#    # Devices
#    # TODO: Make devices class a container of device simulation providers.
#    #       Device simulations to be used are then enabled in the configuration.
#    battery: ClassVar[Battery] = Battery(provider_id="GenericBattery")
#    ev: ClassVar[Battery] = Battery(provider_id="GenericBEV")
#    home_appliance: ClassVar[HomeAppliance] = HomeAppliance(provider_id="GenericDishWasher")
#    inverter: ClassVar[Inverter] = Inverter(
#        self_consumption_predictor=SelfConsumptionProbabilityInterpolator,
#        battery=battery,
#        provider_id="GenericInverter",
#    )
#
#    def update_data(self) -> None:
#        """Update device simulation data."""
#        # Assure devices are set up
#        self.battery.setup()
#        self.ev.setup()
#        self.home_appliance.setup()
#        self.inverter.setup()
#
#        # Pre-allocate arrays for the results, optimized for speed
#        self.last_wh_pro_stunde = np.full((self.total_hours), np.nan)
#        self.grid_export_wh_pro_stunde = np.full((self.total_hours), np.nan)
#        self.grid_import_wh_pro_stunde = np.full((self.total_hours), np.nan)
#        self.kosten_euro_pro_stunde = np.full((self.total_hours), np.nan)
#        self.einnahmen_euro_pro_stunde = np.full((self.total_hours), np.nan)
#        self.akku_soc_pro_stunde = np.full((self.total_hours), np.nan)
#        self.eauto_soc_pro_stunde = np.full((self.total_hours), np.nan)
#        self.verluste_wh_pro_stunde = np.full((self.total_hours), np.nan)
#        self.home_appliance_wh_per_hour = np.full((self.total_hours), np.nan)
#
#        # Set initial state
#        simulation_step = to_duration("1 hour")
#        if self.battery:
#            self.akku_soc_pro_stunde[0] = self.battery.current_soc_percentage()
#        if self.ev:
#            self.eauto_soc_pro_stunde[0] = self.ev.current_soc_percentage()
#
#        # Get predictions for full device simulation time range
#        # gesamtlast[stunde]
#        load_total_mean = self.prediction.key_to_array(
#            "load_total_mean",
#            start_datetime=self.start_datetime,
#            end_datetime=self.end_datetime,
#            interval=simulation_step,
#        )
#        # pv_prognose_wh[stunde]
#        pvforecast_ac_power = self.prediction.key_to_array(
#            "pvforecast_ac_power",
#            start_datetime=self.start_datetime,
#            end_datetime=self.end_datetime,
#            interval=simulation_step,
#        )
#        # strompreis_euro_pro_wh[stunde]
#        elecprice_marketprice_wh = self.prediction.key_to_array(
#            "elecprice_marketprice_wh",
#            start_datetime=self.start_datetime,
#            end_datetime=self.end_datetime,
#            interval=simulation_step,
#        )
#        # einspeiseverguetung_euro_pro_wh_arr[stunde]
#        # TODO: Create prediction for einspeiseverguetung_euro_pro_wh_arr
#        einspeiseverguetung_euro_pro_wh_arr = np.full((self.total_hours), 0.078)
#
#        for stunde_since_now in range(0, self.total_hours):
#            hour = self.start_datetime.hour + stunde_since_now
#
#            # Accumulate loads and PV generation
#            consumption = load_total_mean[stunde_since_now]
#            self.verluste_wh_pro_stunde[stunde_since_now] = 0.0
#
#            # Home appliances
#            if self.home_appliance:
#                ha_load = self.home_appliance.get_load_for_hour(hour)
#                consumption += ha_load
#                self.home_appliance_wh_per_hour[stunde_since_now] = ha_load
#
#            # E-Auto handling
#            if self.ev:
#                if self.ev_charge_hours[hour] > 0:
#                    geladene_menge_eauto, verluste_eauto = self.ev.charge_energy(
#                        None, hour, relative_power=self.ev_charge_hours[hour]
#                    )
#                    consumption += geladene_menge_eauto
#                    self.verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
#                self.eauto_soc_pro_stunde[stunde_since_now] = self.ev.current_soc_percentage()
#
#            # Process inverter logic
#            grid_export, grid_import, losses, self_consumption = (0.0, 0.0, 0.0, 0.0)
#            if self.battery:
#                self.battery.set_charge_allowed_for_hour(self.dc_charge_hours[hour], hour)
#            if self.inverter:
#                generation = pvforecast_ac_power[hour]
#                grid_export, grid_import, losses, self_consumption = self.inverter.process_energy(
#                    generation, consumption, hour
#                )
#
#            # AC PV Battery Charge
#            if self.battery and self.ac_charge_hours[hour] > 0.0:
#                self.battery.set_charge_allowed_for_hour(1, hour)
#                geladene_menge, verluste_wh = self.battery.charge_energy(
#                    None, hour, relative_power=self.ac_charge_hours[hour]
#                )
#                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.battery.current_soc_percentage())
#                consumption += geladene_menge
#                grid_import += geladene_menge
#                self.verluste_wh_pro_stunde[stunde_since_now] += verluste_wh
#
#            self.grid_export_wh_pro_stunde[stunde_since_now] = grid_export
#            self.grid_import_wh_pro_stunde[stunde_since_now] = grid_import
#            self.verluste_wh_pro_stunde[stunde_since_now] += losses
#            self.last_wh_pro_stunde[stunde_since_now] = consumption
#
#            # Financial calculations
#            self.kosten_euro_pro_stunde[stunde_since_now] = (
#                grid_import * self.strompreis_euro_pro_wh[hour]
#            )
#            self.einnahmen_euro_pro_stunde[stunde_since_now] = (
#                grid_export * self.einspeiseverguetung_euro_pro_wh_arr[hour]
#            )
#
#            # battery SOC tracking
#            if self.battery:
#                self.akku_soc_pro_stunde[stunde_since_now] = self.battery.current_soc_percentage()
#            else:
#                self.akku_soc_pro_stunde[stunde_since_now] = 0.0
#
#    def report_dict(self) -> Dict[str, Any]:
#        """Provides devices simulation output as a dictionary."""
#        out: Dict[str, Optional[Union[np.ndarray, float]]] = {
#            "Last_Wh_pro_Stunde": self.last_wh_pro_stunde,
#            "grid_export_Wh_pro_Stunde": self.grid_export_wh_pro_stunde,
#            "grid_import_Wh_pro_Stunde": self.grid_import_wh_pro_stunde,
#            "Kosten_Euro_pro_Stunde": self.kosten_euro_pro_stunde,
#            "akku_soc_pro_stunde": self.akku_soc_pro_stunde,
#            "Einnahmen_Euro_pro_Stunde": self.einnahmen_euro_pro_stunde,
#            "Gesamtbilanz_Euro": self.total_balance_euro,
#            "EAuto_SoC_pro_Stunde": self.eauto_soc_pro_stunde,
#            "Gesamteinnahmen_Euro": self.total_revenues_euro,
#            "Gesamtkosten_Euro": self.total_costs_euro,
#            "Verluste_Pro_Stunde": self.verluste_wh_pro_stunde,
#            "Gesamt_Verluste": self.total_losses_wh,
#            "Home_appliance_wh_per_hour": self.home_appliance_wh_per_hour,
#        }
#        return out


# Initialize the Devices  simulation, it is a singleton.
devices = Devices()


def get_devices() -> Devices:
    """Gets the EOS Devices simulation."""
    return devices
