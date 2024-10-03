class Wechselrichter:
    def __init__(self, max_leistung_wh, akku):
        self.max_leistung_wh = (
            max_leistung_wh  # Maximum power that the inverter can handle
        )
        self.akku = akku  # Connection to a battery object

    def energie_verarbeiten(self, erzeugung, verbrauch, hour):
        verluste = 0  # Losses during processing
        netzeinspeisung = 0  # Grid feed-in
        netzbezug = 0.0  # Grid draw
        eigenverbrauch = 0.0  # Self-consumption

        if erzeugung >= verbrauch:
            if verbrauch > self.max_leistung_wh:
                # If consumption exceeds maximum inverter power
                verluste += erzeugung - self.max_leistung_wh
                restleistung_nach_verbrauch = self.max_leistung_wh - verbrauch
                netzbezug = (
                    -restleistung_nach_verbrauch
                )  # Negative indicates feeding into the grid
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
            benötigte_energie = (
                verbrauch - erzeugung
            )  # Energy needed from external sources
            max_akku_leistung = (
                self.akku.max_ladeleistung_w
            )  # Maximum battery discharge power

            # Calculate remaining AC power available
            rest_ac_leistung = max(self.max_leistung_wh - erzeugung, 0)

            # Discharge energy from the battery based on need
            if benötigte_energie < rest_ac_leistung:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(
                    benötigte_energie, hour
                )
            else:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(
                    rest_ac_leistung, hour
                )

            verluste += akku_entladeverluste  # Include losses from battery discharge
            netzbezug = benötigte_energie - aus_akku  # Energy drawn from the grid
            eigenverbrauch = erzeugung + aus_akku  # Total self-consumption

        return netzeinspeisung, netzbezug, verluste, eigenverbrauch
