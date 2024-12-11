from typing import Optional

from pydantic import BaseModel, Field

from akkudoktoreos.devices.battery import PVAkku
from akkudoktoreos.devices.devicesabc import DeviceBase
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class WechselrichterParameters(BaseModel):
    max_leistung_wh: float = Field(default=10000, gt=0)


class Wechselrichter(DeviceBase):
    def __init__(
        self,
        parameters: Optional[WechselrichterParameters] = None,
        akku: Optional[PVAkku] = None,
        provider_id: Optional[str] = None,
    ):
        # Configuration initialisation
        self.provider_id = provider_id
        self.prefix = "<invalid>"
        if self.provider_id == "GenericInverter":
            self.prefix = "inverter"
        # Parameter initialisiation
        self.parameters = parameters
        if akku is None:
            # For the moment raise exception
            # TODO: Make akku configurable by config
            error_msg = "Battery for PV inverter is mandatory."
            logger.error(error_msg)
            raise NotImplementedError(error_msg)
        self.akku = akku  # Connection to a battery object

        self.initialised = False
        # Run setup if parameters are given, otherwise setup() has to be called later when the config is initialised.
        if self.parameters is not None:
            self.setup()

    def setup(self) -> None:
        if self.initialised:
            return
        if self.provider_id is not None:
            # Setup by configuration
            self.max_leistung_wh = getattr(self.config, f"{self.prefix}_power_max")
        elif self.parameters is not None:
            # Setup by parameters
            self.max_leistung_wh = (
                self.parameters.max_leistung_wh  # Maximum power that the inverter can handle
            )
        else:
            error_msg = "Parameters and provider ID missing. Can't instantiate."
            logger.error(error_msg)
            raise ValueError(error_msg)

    def energie_verarbeiten(
        self, erzeugung: float, verbrauch: float, hour: int
    ) -> tuple[float, float, float, float]:
        verluste = 0.0  # Losses during processing
        netzeinspeisung = 0.0  # Grid feed-in
        netzbezug = 0.0  # Grid draw
        eigenverbrauch = 0.0  # Self-consumption

        if erzeugung >= verbrauch:
            if verbrauch > self.max_leistung_wh:
                # If consumption exceeds maximum inverter power
                verluste += erzeugung - self.max_leistung_wh
                restleistung_nach_verbrauch = self.max_leistung_wh - verbrauch
                netzbezug = -restleistung_nach_verbrauch  # Negative indicates feeding into the grid
                eigenverbrauch = self.max_leistung_wh
            else:
                # Remaining power after consumption
                restleistung_nach_verbrauch = erzeugung - verbrauch

                # Load battery with excess energy
                geladene_energie, verluste_laden_akku = self.akku.energie_laden(
                    restleistung_nach_verbrauch, hour
                )
                rest_überschuss = restleistung_nach_verbrauch - (
                    geladene_energie + verluste_laden_akku
                )

                # Feed-in to the grid based on remaining capacity
                if rest_überschuss > self.max_leistung_wh - verbrauch:
                    netzeinspeisung = self.max_leistung_wh - verbrauch
                    verluste += rest_überschuss - netzeinspeisung
                else:
                    netzeinspeisung = rest_überschuss

                verluste += verluste_laden_akku
                eigenverbrauch = verbrauch  # Self-consumption is equal to the load

        else:
            benötigte_energie = verbrauch - erzeugung  # Energy needed from external sources
            max_akku_leistung = self.akku.max_ladeleistung_w  # Maximum battery discharge power

            # Calculate remaining AC power available
            rest_ac_leistung = max(self.max_leistung_wh - erzeugung, 0)

            # Discharge energy from the battery based on need
            if benötigte_energie < rest_ac_leistung:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, hour)
            else:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(rest_ac_leistung, hour)

            verluste += akku_entladeverluste  # Include losses from battery discharge
            netzbezug = benötigte_energie - aus_akku  # Energy drawn from the grid
            eigenverbrauch = erzeugung + aus_akku  # Total self-consumption

        return netzeinspeisung, netzbezug, verluste, eigenverbrauch
