from pydantic import BaseModel, Field

from akkudoktoreos.devices.battery import PVAkku


class WechselrichterParameters(BaseModel):
    max_leistung_wh: float = Field(default=10000, gt=0)


class Wechselrichter:
    def __init__(self, parameters: WechselrichterParameters, akku: PVAkku):
        self.max_leistung_wh = (
            parameters.max_leistung_wh  # Maximum power that the inverter can handle
        )
        self.akku = akku  # Connection to a battery object

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
                restleistung_nach_verbrauch = (erzeugung - verbrauch) * 0.95  # EVQ
                # Remaining load Self Consumption not perfect
                restlast_evq = (erzeugung - verbrauch) * (1.0 - 0.95)

                if restlast_evq > 0:
                    # Akku muss den Restverbrauch decken
                    aus_akku, akku_entladeverluste = self.akku.energie_abgeben(restlast_evq, hour)
                    restlast_evq -= aus_akku  # Restverbrauch nach Akkuentladung
                    verluste += akku_entladeverluste

                    # Wenn der Akku den Restverbrauch nicht vollständig decken kann, wird der Rest ins Netz gezogen
                    if restlast_evq > 0:
                        netzbezug += restlast_evq
                        restlast_evq = 0

                if restleistung_nach_verbrauch > 0:
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
                eigenverbrauch = verbrauch + aus_akku  # Self-consumption is equal to the load

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
