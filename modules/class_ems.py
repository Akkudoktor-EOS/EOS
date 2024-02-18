# class EnergieManagementSystem:
    # def __init__(self, akku, lastkurve_wh, pv_prognose_wh):
        # self.akku = akku
        # self.lastkurve_wh = lastkurve_wh
        # self.pv_prognose_wh = pv_prognose_wh

    # def simuliere(self):
        # eigenverbrauch_wh = 0
        # netzeinspeisung_wh = 0
        # netzbezug_wh = 0

        # for stunde in range(len(self.lastkurve_wh)):
            # verbrauch = self.lastkurve_wh[stunde]
            # erzeugung = self.pv_prognose_wh[stunde]

            # if erzeugung > verbrauch:
                # überschuss = erzeugung - verbrauch
                # eigenverbrauch_wh += verbrauch
                # geladene_energie = min(überschuss, self.akku.kapazitaet_wh - self.akku.soc_wh)
                # self.akku.energie_laden(geladene_energie)
                # netzeinspeisung_wh += überschuss - geladene_energie
            # else:
                # eigenverbrauch_wh += erzeugung
                # benötigte_energie = verbrauch - erzeugung
                # aus_akku = self.akku.energie_abgeben(benötigte_energie)
                # netzbezug_wh += benötigte_energie - aus_akku

        # return {
            # 'Eigenverbrauch_Wh': eigenverbrauch_wh,
            # 'Netzeinspeisung_Wh': netzeinspeisung_wh,
            # 'Netzbezug_Wh': netzbezug_wh
        # }


class EnergieManagementSystem:
    def __init__(self, akku, lastkurve_wh, pv_prognose_wh, strompreis_cent_pro_wh, einspeiseverguetung_cent_pro_wh):
        self.akku = akku
        self.lastkurve_wh = lastkurve_wh
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_cent_pro_wh = strompreis_cent_pro_wh  # Strompreis in Cent pro Wh
        self.einspeiseverguetung_cent_pro_wh = einspeiseverguetung_cent_pro_wh  # Einspeisevergütung in Cent pro Wh
    def set_akku_discharge_hours(self, ds):
        self.akku.set_discharge_per_hour(ds)
        
    def reset(self):
        self.akku.reset()
        
    def simuliere(self):
        eigenverbrauch_wh_pro_stunde = []
        netzeinspeisung_wh_pro_stunde = []
        netzbezug_wh_pro_stunde = []
        kosten_euro_pro_stunde = []
        einnahmen_euro_pro_stunde = []
        akku_soc_pro_stunde = []

        for stunde in range(len(self.lastkurve_wh)):
            verbrauch = self.lastkurve_wh[stunde]
            erzeugung = self.pv_prognose_wh[stunde]
            strompreis = self.strompreis_cent_pro_wh[stunde]

            stündlicher_netzbezug_wh = 0
            stündliche_kosten_euro = 0
            stündliche_einnahmen_euro = 0

            if erzeugung > verbrauch:
                überschuss = erzeugung - verbrauch
                geladene_energie = min(überschuss, self.akku.kapazitaet_wh - self.akku.soc_wh)
                self.akku.energie_laden(geladene_energie)
                netzeinspeisung_wh_pro_stunde.append(überschuss - geladene_energie)
                eigenverbrauch_wh_pro_stunde.append(verbrauch)
                stündliche_einnahmen_euro = (überschuss - geladene_energie) * self.einspeiseverguetung_cent_pro_wh[stunde] 
                netzbezug_wh_pro_stunde.append(0.0)
            else:
                netzeinspeisung_wh_pro_stunde.append(0.0)
                benötigte_energie = verbrauch - erzeugung
                aus_akku = self.akku.energie_abgeben(benötigte_energie, stunde)
                stündlicher_netzbezug_wh = benötigte_energie - aus_akku
                netzbezug_wh_pro_stunde.append(stündlicher_netzbezug_wh)
                eigenverbrauch_wh_pro_stunde.append(erzeugung)
                stündliche_kosten_euro = stündlicher_netzbezug_wh * strompreis 
            akku_soc_pro_stunde.append(self.akku.ladezustand_in_prozent())
            kosten_euro_pro_stunde.append(stündliche_kosten_euro)
            einnahmen_euro_pro_stunde.append(stündliche_einnahmen_euro)

        # Berechnung der Gesamtbilanzen
        gesamtkosten_euro = sum(kosten_euro_pro_stunde) - sum(einnahmen_euro_pro_stunde)

        return {
            'Eigenverbrauch_Wh_pro_Stunde': eigenverbrauch_wh_pro_stunde,
            'Netzeinspeisung_Wh_pro_Stunde': netzeinspeisung_wh_pro_stunde,
            'Netzbezug_Wh_pro_Stunde': netzbezug_wh_pro_stunde,
            'Kosten_Euro_pro_Stunde': kosten_euro_pro_stunde,
            'akku_soc_pro_stunde': akku_soc_pro_stunde,
            'Einnahmen_Euro_pro_Stunde': einnahmen_euro_pro_stunde,
            'Gesamtbilanz_Euro': gesamtkosten_euro,
            'Gesamteinnahmen_Euro': sum(einnahmen_euro_pro_stunde),
            'Gesamtkosten_Euro': sum(kosten_euro_pro_stunde)
            
        }

    # def simuliere(self):
        # eigenverbrauch_wh = 0
        # netzeinspeisung_wh = 0
        # netzbezug_wh = 0
        # kosten_euro = 0
        # einnahmen_euro = 0

        # for stunde in range(len(self.lastkurve_wh)):
            # verbrauch = self.lastkurve_wh[stunde]
            # erzeugung = self.pv_prognose_wh[stunde]
            # strompreis = self.strompreis_cent_pro_wh[stunde]

            # if erzeugung > verbrauch:
                # überschuss = erzeugung - verbrauch
                # eigenverbrauch_wh += verbrauch
                # geladene_energie = min(überschuss, self.akku.kapazitaet_wh - self.akku.soc_wh)
                # self.akku.energie_laden(geladene_energie)
                # netzeinspeisung_wh += überschuss - geladene_energie
                # einnahmen_euro += (überschuss - geladene_energie) * self.einspeiseverguetung_cent_pro_wh[stunde] / 100
            # else:
                # eigenverbrauch_wh += erzeugung
                # benötigte_energie = verbrauch - erzeugung
                # aus_akku = self.akku.energie_abgeben(benötigte_energie)
                # netzbezug_wh += benötigte_energie - aus_akku
                # print(strompreis)
                
                # kosten_euro += (benötigte_energie - aus_akku) * strompreis / 100

        # gesamtkosten_euro = kosten_euro - einnahmen_euro

        # return {
            # 'Eigenverbrauch_Wh': eigenverbrauch_wh,
            # 'Netzeinspeisung_Wh': netzeinspeisung_wh,
            # 'Netzbezug_Wh': netzbezug_wh,
            # 'Kosten_Euro': kosten_euro,
            # 'Einnahmen_Euro': einnahmen_euro,
            # 'Gesamtkosten_Euro': gesamtkosten_euro
        # }