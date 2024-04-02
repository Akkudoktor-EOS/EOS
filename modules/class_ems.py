from datetime import datetime
from pprint import pprint


class EnergieManagementSystem:
    def __init__(self, akku=None,  pv_prognose_wh=None, strompreis_euro_pro_wh=None, einspeiseverguetung_euro_pro_wh=None, eauto=None, gesamtlast=None, haushaltsgeraet=None):
        self.akku = akku
        #self.lastkurve_wh = lastkurve_wh
        self.gesamtlast = gesamtlast
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_euro_pro_wh = strompreis_euro_pro_wh  # Strompreis in Cent pro Wh
        self.einspeiseverguetung_euro_pro_wh = einspeiseverguetung_euro_pro_wh  # Einspeisevergütung in Cent pro Wh
        self.eauto = eauto
        self.haushaltsgeraet = haushaltsgeraet
        
    
    def set_akku_discharge_hours(self, ds):
        self.akku.set_discharge_per_hour(ds)
    
    def set_eauto_charge_hours(self, ds):
        self.eauto.set_charge_per_hour(ds)

    def set_haushaltsgeraet_start(self, ds, global_start_hour=0):
        self.haushaltsgeraet.set_startzeitpunkt(ds,global_start_hour=global_start_hour)
        
    def reset(self):
        self.eauto.reset()
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
        eauto_soc_pro_stunde = []
        verluste_wh_pro_stunde = []
        haushaltsgeraet_wh_pro_stunde = []
        lastkurve_wh = self.gesamtlast.gesamtlast_berechnen()
        
        
        assert len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh), f"Arraygrößen stimmen nicht überein: Lastkurve = {len(lastkurve_wh)}, PV-Prognose = {len(self.pv_prognose_wh)}, Strompreis = {len(self.strompreis_euro_pro_wh)}"

        ende = min( len(lastkurve_wh),len(self.pv_prognose_wh), len(self.strompreis_euro_pro_wh))

        # Berechnet das Ende basierend auf der Länge der Lastkurve
        for stunde in range(start_stunde, ende):
            
            # Anpassung, um sicherzustellen, dass Indizes korrekt sind
            verbrauch = lastkurve_wh[stunde] 
            if self.haushaltsgeraet != None:
                verbrauch = verbrauch + self.haushaltsgeraet.get_last_fuer_stunde(stunde)
                haushaltsgeraet_wh_pro_stunde.append(self.haushaltsgeraet.get_last_fuer_stunde(stunde))
            else: 
                haushaltsgeraet_wh_pro_stunde.append(0)
            erzeugung = self.pv_prognose_wh[stunde]
            strompreis = self.strompreis_euro_pro_wh[stunde] if stunde < len(self.strompreis_euro_pro_wh) else self.strompreis_euro_pro_wh[-1]
            verluste_wh_pro_stunde.append(0.0)
            

            # Logik für die E-Auto-Ladung bzw. Entladung
            if self.eauto:  # Falls ein E-Auto vorhanden ist
                geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(None,stunde)
                verbrauch = verbrauch + geladene_menge_eauto
                verluste_wh_pro_stunde[-1] += verluste_eauto
                eauto_soc = self.eauto.ladezustand_in_prozent()


            
            stündlicher_netzbezug_wh = 0
            stündliche_kosten_euro = 0
            stündliche_einnahmen_euro = 0
            

            if erzeugung > verbrauch:
                überschuss = erzeugung - verbrauch
                #geladene_energie = min(überschuss, self.akku.kapazitaet_wh - self.akku.soc_wh)
                geladene_energie, verluste_laden_akku = self.akku.energie_laden(überschuss, stunde)
                verluste_wh_pro_stunde[-1] += verluste_laden_akku
                #print("verluste_laden_akku:",verluste_laden_akku)
                netzeinspeisung_wh_pro_stunde.append(überschuss - geladene_energie-verluste_laden_akku)
                eigenverbrauch_wh_pro_stunde.append(verbrauch)
                stündliche_einnahmen_euro = (überschuss - geladene_energie-verluste_laden_akku) * self.einspeiseverguetung_euro_pro_wh[stunde] 
                #print(überschuss," ", geladene_energie," ",verluste_laden_akku)
                netzbezug_wh_pro_stunde.append(0.0)
            else:
                netzeinspeisung_wh_pro_stunde.append(0.0)
                benötigte_energie = verbrauch - erzeugung
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, stunde)
                verluste_wh_pro_stunde[-1] += akku_entladeverluste
                #print("akku_entladeverluste:",akku_entladeverluste)
                
                stündlicher_netzbezug_wh = benötigte_energie - aus_akku
                netzbezug_wh_pro_stunde.append(stündlicher_netzbezug_wh)
                eigenverbrauch_wh_pro_stunde.append(erzeugung+aus_akku)
                stündliche_kosten_euro = stündlicher_netzbezug_wh * strompreis 
            
            if self.eauto:
                eauto_soc_pro_stunde.append(eauto_soc)
            
            akku_soc_pro_stunde.append(self.akku.ladezustand_in_prozent())
            kosten_euro_pro_stunde.append(stündliche_kosten_euro)
            einnahmen_euro_pro_stunde.append(stündliche_einnahmen_euro)


        gesamtkosten_euro = sum(kosten_euro_pro_stunde) - sum(einnahmen_euro_pro_stunde)
        expected_length = ende - start_stunde
        array_names = ['Eigenverbrauch_Wh_pro_Stunde', 'Netzeinspeisung_Wh_pro_Stunde', 'Netzbezug_Wh_pro_Stunde', 'Kosten_Euro_pro_Stunde', 'akku_soc_pro_stunde', 'Einnahmen_Euro_pro_Stunde','E-Auto_SoC_pro_Stunde', "Verluste_Pro_Stunde"]
        all_arrays = [eigenverbrauch_wh_pro_stunde, netzeinspeisung_wh_pro_stunde, netzbezug_wh_pro_stunde, kosten_euro_pro_stunde, akku_soc_pro_stunde, einnahmen_euro_pro_stunde,eauto_soc_pro_stunde,verluste_wh_pro_stunde]

        inconsistent_arrays = [name for name, arr in zip(array_names, all_arrays) if len(arr) != expected_length]
        #print(inconsistent_arrays)
        
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
            'E-Auto_SoC_pro_Stunde':eauto_soc_pro_stunde,
            'Gesamteinnahmen_Euro': sum(einnahmen_euro_pro_stunde),
            'Gesamtkosten_Euro': sum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde":verluste_wh_pro_stunde,
            "Gesamt_Verluste":sum(verluste_wh_pro_stunde),
            "Haushaltsgeraet_wh_pro_stunde":haushaltsgeraet_wh_pro_stunde
        }
        
        return out
