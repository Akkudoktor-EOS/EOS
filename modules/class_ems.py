from datetime import datetime
from pprint import pprint
from modules.class_generic_load import *


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

    def simuliere_ab_jetzt(self):
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        # Berechne die Anzahl der Stunden bis zum gleichen Zeitpunkt am nächsten Tag
        stunden_bis_ende_tag = 24 - start_stunde
        # Füge diese Stunden zum nächsten Tag hinzu
        gesamt_stunden = stunden_bis_ende_tag + 24

        # Beginne die Simulation ab der aktuellen Stunde und führe sie für die berechnete Dauer aus
        return self.simuliere(start_stunde)


    def simuliere(self, start_stunde):
        eigenverbrauch_wh_pro_stunde = []
        netzeinspeisung_wh_pro_stunde = []
        netzbezug_wh_pro_stunde = []
        kosten_euro_pro_stunde = []
        einnahmen_euro_pro_stunde = []
        akku_soc_pro_stunde = []

        #print(gesamtlast_pro_stunde)
        #sys.exit()
        
        ende = min( len(self.lastkurve_wh),len(self.pv_prognose_wh), len(self.strompreis_cent_pro_wh))
        #print(ende)
        # Berechnet das Ende basierend auf der Länge der Lastkurve
        for stunde in range(start_stunde, ende):
            
            # Anpassung, um sicherzustellen, dass Indizes korrekt sind
            verbrauch = self.lastkurve_wh[stunde]
            erzeugung = self.pv_prognose_wh[stunde]
            strompreis = self.strompreis_cent_pro_wh[stunde] if stunde < len(self.strompreis_cent_pro_wh) else self.strompreis_cent_pro_wh[-1]
            #print(verbrauch," ",erzeugung," ",strompreis)
            
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
        expected_length = ende - start_stunde
        array_names = ['Eigenverbrauch_Wh_pro_Stunde', 'Netzeinspeisung_Wh_pro_Stunde', 'Netzbezug_Wh_pro_Stunde', 'Kosten_Euro_pro_Stunde', 'akku_soc_pro_stunde', 'Einnahmen_Euro_pro_Stunde']
        all_arrays = [eigenverbrauch_wh_pro_stunde, netzeinspeisung_wh_pro_stunde, netzbezug_wh_pro_stunde, kosten_euro_pro_stunde, akku_soc_pro_stunde, einnahmen_euro_pro_stunde]

        inconsistent_arrays = [name for name, arr in zip(array_names, all_arrays) if len(arr) != expected_length]

        if inconsistent_arrays:
            raise ValueError(f"Inkonsistente Längen bei den Arrays: {', '.join(inconsistent_arrays)}. Erwartete Länge: {expected_length}, gefunden: {[len(all_arrays[array_names.index(name)]) for name in inconsistent_arrays]}")

        out = {
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
        
        return out
