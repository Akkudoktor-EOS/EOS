from typing import Any, ClassVar, Dict, Optional, Union

import numpy as np
from numpydantic import NDArray, Shape
from pendulum import DateTime
from pydantic import ConfigDict, Field, computed_field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.core.coreabc import ConfigMixin, PredictionMixin, SingletonMixin
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Inverter
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.logutil import get_logger
from akkudoktoreos.utils.utils import NumpyEncoder

logger = get_logger(__name__)


class EnergieManagementSystemParameters(PydanticBaseModel):
    pv_prognose_wh: list[float] = Field(
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
    )
    strompreis_euro_pro_wh: list[float] = Field(
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals."
    )
    einspeiseverguetung_euro_pro_wh: list[float] | float = Field(
        description="A float or array of floats representing the feed-in compensation in euros per watt-hour."
    )
    preis_euro_pro_wh_akku: float = Field(
        description="A float representing the cost of battery energy per watt-hour."
    )
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


class SimulationResult(PydanticBaseModel):
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
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class EnergieManagementSystem(SingletonMixin, ConfigMixin, PredictionMixin, PydanticBaseModel):
    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergieManagementSystem._start_datetime is None:
            EnergieManagementSystem.set_start_datetime()
        return EnergieManagementSystem._start_datetime

    @classmethod
    def set_start_datetime(cls, start_datetime: Optional[DateTime] = None) -> DateTime:
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._start_datetime = start_datetime.set(minute=0, second=0, microsecond=0)
        return cls._start_datetime

    # -------------------------
    # TODO: Take from prediction
    # -------------------------

    gesamtlast: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the total load (consumption) in watts for different time intervals.",
    )
    pv_prognose_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals.",
    )
    strompreis_euro_pro_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals.",
    )
    einspeiseverguetung_euro_pro_wh_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the feed-in compensation in euros per watt-hour.",
    )

    # -------------------------
    # TODO: Move to devices
    # -------------------------

    akku: Optional[Battery] = Field(default=None, description="TBD.")
    eauto: Optional[Battery] = Field(default=None, description="TBD.")
    home_appliance: Optional[HomeAppliance] = Field(default=None, description="TBD.")
    inverter: Optional[Inverter] = Field(default=None, description="TBD.")

    # -------------------------
    # TODO: Move to devices
    # -------------------------

    ac_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    dc_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    ev_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")

    def set_parameters(
        self,
        parameters: EnergieManagementSystemParameters,
        eauto: Optional[Battery] = None,
        home_appliance: Optional[HomeAppliance] = None,
        inverter: Optional[Inverter] = None,
    ) -> None:
        self.gesamtlast = np.array(parameters.gesamtlast, float)
        self.pv_prognose_wh = np.array(parameters.pv_prognose_wh, float)
        self.strompreis_euro_pro_wh = np.array(parameters.strompreis_euro_pro_wh, float)
        self.einspeiseverguetung_euro_pro_wh_arr = (
            parameters.einspeiseverguetung_euro_pro_wh
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(len(self.gesamtlast), parameters.einspeiseverguetung_euro_pro_wh, float)
        )
        if inverter is not None:
            self.akku = inverter.akku
        else:
            self.akku = None
        self.eauto = eauto
        self.home_appliance = home_appliance
        self.inverter = inverter
        self.ac_charge_hours = np.full(self.config.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.config.prediction_hours, 1.0)
        self.ev_charge_hours = np.full(self.config.prediction_hours, 0.0)

    def set_akku_discharge_hours(self, ds: np.ndarray) -> None:
        if self.akku is not None:
            self.akku.set_discharge_per_hour(ds)

    def set_akku_ac_charge_hours(self, ds: np.ndarray) -> None:
        self.ac_charge_hours = ds

    def set_akku_dc_charge_hours(self, ds: np.ndarray) -> None:
        self.dc_charge_hours = ds

    def set_ev_charge_hours(self, ds: np.ndarray) -> None:
        self.ev_charge_hours = ds

    def set_home_appliance_start(self, ds: int, global_start_hour: int = 0) -> None:
        if self.home_appliance is not None:
            self.home_appliance.set_starting_time(ds, global_start_hour=global_start_hour)

    def reset(self) -> None:
        if self.eauto:
            self.eauto.reset()
        if self.akku:
            self.akku.reset()

    def run(
        self,
        start_hour: Optional[int] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Run energy management.

        Sets `start_datetime` to current hour, updates the configuration and the prediction, and
        starts simulation at current hour.

        Args:
            start_hour (int, optional): Hour to take as start time for the energy management. Defaults
            to now.
            force_enable (bool, optional): If True, forces to update even if disabled. This
            is mostly relevant to prediction providers.
            force_update (bool, optional): If True, forces to update the data even if still cached.
        """
        self.set_start_hour(start_hour=start_hour)
        self.config.update()

        # Check for run definitions
        if self.start_datetime is None:
            error_msg = "Start datetime unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)
        if self.config.prediction_hours is None:
            error_msg = "Prediction hours unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)
        if self.config.optimisation_hours is None:
            error_msg = "Optimisation hours unknown."
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.prediction.update_data(force_enable=force_enable, force_update=force_update)
        # TODO: Create optimisation problem that calls into devices.update_data() for simulations.

    def set_start_hour(self, start_hour: Optional[int] = None) -> None:
        """Sets start datetime to given hour.

        Args:
            start_hour (int, optional): Hour to take as start time for the energy management. Defaults
            to now.
        """
        if start_hour is None:
            self.set_start_datetime()
        else:
            start_datetime = to_datetime().set(hour=start_hour, minute=0, second=0, microsecond=0)
            self.set_start_datetime(start_datetime)

    def simuliere_ab_jetzt(self) -> dict[str, Any]:
        jetzt = to_datetime().now()
        start_stunde = jetzt.hour
        return self.simuliere(start_stunde)

    def simuliere(self, start_stunde: int) -> dict[str, Any]:
        """hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of  last hour (end state)
        """
        # Check for simulation integrity
        if (
            self.gesamtlast is None
            or self.pv_prognose_wh is None
            or self.strompreis_euro_pro_wh is None
            or self.ev_charge_hours is None
            or self.ac_charge_hours is None
            or self.dc_charge_hours is None
            or self.einspeiseverguetung_euro_pro_wh_arr is None
        ):
            error_msg = (
                f"Mandatory data missing - "
                f"Load Curve: {self.gesamtlast}, "
                f"PV Forecast: {self.pv_prognose_wh}, "
                f"Electricity Price: {self.strompreis_euro_pro_wh}, "
                f"EV Charge Hours: {self.ev_charge_hours}, "
                f"AC Charge Hours: {self.ac_charge_hours}, "
                f"DC Charge Hours: {self.dc_charge_hours}, "
                f"Feed-in tariff: {self.einspeiseverguetung_euro_pro_wh_arr}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        lastkurve_wh = self.gesamtlast

        if not (len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh)):
            error_msg = f"Array sizes do not match: Load Curve = {len(lastkurve_wh)}, PV Forecast = {len(self.pv_prognose_wh)}, Electricity Price = {len(self.strompreis_euro_pro_wh)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Optimized total hours calculation
        ende = len(lastkurve_wh)
        total_hours = ende - start_stunde

        # Pre-allocate arrays for the results, optimized for speed
        last_wh_pro_stunde = np.full((total_hours), np.nan)
        netzeinspeisung_wh_pro_stunde = np.full((total_hours), np.nan)
        netzbezug_wh_pro_stunde = np.full((total_hours), np.nan)
        kosten_euro_pro_stunde = np.full((total_hours), np.nan)
        einnahmen_euro_pro_stunde = np.full((total_hours), np.nan)
        akku_soc_pro_stunde = np.full((total_hours), np.nan)
        eauto_soc_pro_stunde = np.full((total_hours), np.nan)
        verluste_wh_pro_stunde = np.full((total_hours), np.nan)
        home_appliance_wh_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        if self.akku:
            akku_soc_pro_stunde[0] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            eauto_soc_pro_stunde[0] = self.eauto.ladezustand_in_prozent()

        for stunde in range(start_stunde, ende):
            stunde_since_now = stunde - start_stunde

            # Accumulate loads and PV generation
            verbrauch = self.gesamtlast[stunde]
            verluste_wh_pro_stunde[stunde_since_now] = 0.0

            # Home appliances
            if self.home_appliance:
                ha_load = self.home_appliance.get_load_for_hour(stunde)
                verbrauch += ha_load
                home_appliance_wh_per_hour[stunde_since_now] = ha_load

            # E-Auto handling
            if self.eauto:
                if self.ev_charge_hours[stunde] > 0:
                    geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(
                        None, stunde, relative_power=self.ev_charge_hours[stunde]
                    )
                    verbrauch += geladene_menge_eauto
                    verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
                eauto_soc_pro_stunde[stunde_since_now] = self.eauto.ladezustand_in_prozent()

            # Process inverter logic
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = (0.0, 0.0, 0.0, 0.0)
            if self.akku:
                self.akku.set_charge_allowed_for_hour(self.dc_charge_hours[stunde], stunde)
            if self.inverter:
                erzeugung = self.pv_prognose_wh[stunde]
                netzeinspeisung, netzbezug, verluste, eigenverbrauch = self.inverter.process_energy(
                    erzeugung, verbrauch, stunde
                )

            # AC PV Battery Charge
            if self.akku and self.ac_charge_hours[stunde] > 0.0:
                self.akku.set_charge_allowed_for_hour(1, stunde)
                geladene_menge, verluste_wh = self.akku.energie_laden(
                    None, stunde, relative_power=self.ac_charge_hours[stunde]
                )
                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.akku.ladezustand_in_prozent())
                verbrauch += geladene_menge
                verbrauch += verluste_wh
                netzbezug += geladene_menge
                netzbezug += verluste_wh
                verluste_wh_pro_stunde[stunde_since_now] += verluste_wh

            netzeinspeisung_wh_pro_stunde[stunde_since_now] = netzeinspeisung
            netzbezug_wh_pro_stunde[stunde_since_now] = netzbezug
            verluste_wh_pro_stunde[stunde_since_now] += verluste
            last_wh_pro_stunde[stunde_since_now] = verbrauch

            # Financial calculations
            kosten_euro_pro_stunde[stunde_since_now] = (
                netzbezug * self.strompreis_euro_pro_wh[stunde]
            )
            einnahmen_euro_pro_stunde[stunde_since_now] = (
                netzeinspeisung * self.einspeiseverguetung_euro_pro_wh_arr[stunde]
            )

            # Akku SOC tracking
            if self.akku:
                akku_soc_pro_stunde[stunde_since_now] = self.akku.ladezustand_in_prozent()
            else:
                akku_soc_pro_stunde[stunde_since_now] = 0.0

        # Total cost and return
        gesamtkosten_euro = np.nansum(kosten_euro_pro_stunde) - np.nansum(einnahmen_euro_pro_stunde)

        # Prepare output dictionary
        out: Dict[str, Union[np.ndarray, float]] = {
            "Last_Wh_pro_Stunde": last_wh_pro_stunde,
            "Netzeinspeisung_Wh_pro_Stunde": netzeinspeisung_wh_pro_stunde,
            "Netzbezug_Wh_pro_Stunde": netzbezug_wh_pro_stunde,
            "Kosten_Euro_pro_Stunde": kosten_euro_pro_stunde,
            "akku_soc_pro_stunde": akku_soc_pro_stunde,
            "Einnahmen_Euro_pro_Stunde": einnahmen_euro_pro_stunde,
            "Gesamtbilanz_Euro": gesamtkosten_euro,
            "EAuto_SoC_pro_Stunde": eauto_soc_pro_stunde,
            "Gesamteinnahmen_Euro": np.nansum(einnahmen_euro_pro_stunde),
            "Gesamtkosten_Euro": np.nansum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde": verluste_wh_pro_stunde,
            "Gesamt_Verluste": np.nansum(verluste_wh_pro_stunde),
            "Home_appliance_wh_per_hour": home_appliance_wh_per_hour,
        }

        return out


# Initialize the Energy Management System, it is a singleton.
ems = EnergieManagementSystem()


def get_ems() -> EnergieManagementSystem:
    """Gets the EOS Energy Management System."""
    return ems
