class EnergieManagementSystem:
    def __init__(self, akku, lastkurve_wh, pv_prognose_wh):
        self.akku = akku
        self.lastkurve_wh = lastkurve_wh
        self.pv_prognose_wh = pv_prognose_wh

    def simuliere(self):
        eigenverbrauch_wh = 0
        netzeinspeisung_wh = 0
        netzbezug_wh = 0

        for stunde in range(len(self.lastkurve_wh)):
            verbrauch = self.lastkurve_wh[stunde]
            erzeugung = self.pv_prognose_wh[stunde]

            if erzeugung > verbrauch:
                überschuss = erzeugung - verbrauch
                eigenverbrauch_wh += verbrauch
                geladene_energie = min(überschuss, self.akku.kapazitaet_wh - self.akku.soc_wh)
                self.akku.energie_laden(geladene_energie)
                netzeinspeisung_wh += überschuss - geladene_energie
            else:
                eigenverbrauch_wh += erzeugung
                benötigte_energie = verbrauch - erzeugung
                aus_akku = self.akku.energie_abgeben(benötigte_energie)
                netzbezug_wh += benötigte_energie - aus_akku

        return {
            'Eigenverbrauch_Wh': eigenverbrauch_wh,
            'Netzeinspeisung_Wh': netzeinspeisung_wh,
            'Netzbezug_Wh': netzbezug_wh
        }
