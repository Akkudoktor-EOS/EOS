from datetime import datetime
from pprint import pprint
import numpy as np
import modules.class_akku as PVAkku

def replace_nan_with_none(data):
    if isinstance(data, dict):
        return {key: replace_nan_with_none(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_none(element) for element in data]
    elif isinstance(data, np.ndarray):
        # Konvertiere das numpy-Array zu einer Liste und rekursiv ersetzen
        return replace_nan_with_none(data.tolist())
    elif isinstance(data, (float, np.floating)) and np.isnan(data):
        # np.floating deckt auch numpy-NaNs ab
        return None
    else:
        return data




class EnergieManagementSystem:
    def __init__(self,  pv_prognose_wh=None, strompreis_euro_pro_wh=None, einspeiseverguetung_euro_pro_wh=None, eauto=None, gesamtlast=None, haushaltsgeraet=None, wechselrichter=None):
        self.akku = wechselrichter.akku
        #self.lastkurve_wh = lastkurve_wh
        self.gesamtlast = gesamtlast
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_euro_pro_wh = strompreis_euro_pro_wh  # Strompreis in Cent pro Wh
        self.einspeiseverguetung_euro_pro_wh = einspeiseverguetung_euro_pro_wh  # Einspeisevergütung in Cent pro Wh
        self.eauto = eauto
        self.haushaltsgeraet = haushaltsgeraet
        self.wechselrichter = wechselrichter
        
        
    
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
    
    
        lastkurve_wh = self.gesamtlast
        # Anzahl der Stunden berechnen
        assert len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh), f"Arraygrößen stimmen nicht überein: Lastkurve = {len(lastkurve_wh)}, PV-Prognose = {len(self.pv_prognose_wh)}, Strompreis = {len(self.strompreis_euro_pro_wh)}"

        ende = min( len(lastkurve_wh),len(self.pv_prognose_wh), len(self.strompreis_euro_pro_wh))
        
        
        total_hours = ende-start_stunde

        # Initialisierung der Arrays mit NaN-Werten
        last_wh_pro_stunde = np.full(total_hours, np.nan)
        netzeinspeisung_wh_pro_stunde = np.full(total_hours, np.nan)
        netzbezug_wh_pro_stunde = np.full(total_hours, np.nan)
        kosten_euro_pro_stunde = np.full(total_hours, np.nan)
        einnahmen_euro_pro_stunde = np.full(total_hours, np.nan)
        akku_soc_pro_stunde = np.full(total_hours, np.nan)
        eauto_soc_pro_stunde = np.full(total_hours, np.nan)
        verluste_wh_pro_stunde = np.full(total_hours, np.nan)
        haushaltsgeraet_wh_pro_stunde = np.full(total_hours, np.nan)

        # Setze den initialen Ladezustand für Akku und E-Auto
        akku_soc_pro_stunde[start_stunde] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            eauto_soc_pro_stunde[start_stunde] = self.eauto.ladezustand_in_prozent()


        for stunde in range(start_stunde + 1, ende):
            stunde_since_now = stunde-start_stunde
            #print(stunde_since_now) 
            # Anfangszustände
            akku_soc_start = self.akku.ladezustand_in_prozent()
            eauto_soc_start = self.eauto.ladezustand_in_prozent() if self.eauto else None

            # Verbrauch und zusätzliche Lasten bestimmen
            verbrauch = self.gesamtlast[stunde]
            haushalts_last = 0

            if self.haushaltsgeraet is not None:
                haushalts_last = self.haushaltsgeraet.get_last_fuer_stunde(stunde)
            verbrauch += haushalts_last

            haushaltsgeraet_wh_pro_stunde[stunde_since_now] = haushalts_last

            # PV-Erzeugung und Strompreis für die Stunde
            erzeugung = self.pv_prognose_wh[stunde]
            strompreis = self.strompreis_euro_pro_wh[stunde]

            # Verluste initialisieren
            verluste_wh_pro_stunde[stunde_since_now] = 0.0

            # E-Auto-Verbrauch bestimmen
            if self.eauto:
                geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(None, stunde)
                verbrauch += geladene_menge_eauto
                verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
                eauto_soc_pro_stunde[stunde_since_now] = self.eauto.ladezustand_in_prozent()

            # Wechselrichter-Logik
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)

            # Ergebnisse speichern
            netzeinspeisung_wh_pro_stunde[stunde_since_now] = netzeinspeisung
            netzbezug_wh_pro_stunde[stunde_since_now] = netzbezug
            verluste_wh_pro_stunde[stunde_since_now] += verluste
            last_wh_pro_stunde[stunde_since_now] = verbrauch
            # Finanzen berechnen
            kosten_euro_pro_stunde[stunde_since_now] = netzbezug * strompreis
            einnahmen_euro_pro_stunde[stunde_since_now] = netzeinspeisung * self.einspeiseverguetung_euro_pro_wh[stunde]

            # Letzter Akkuzustand speichern
            akku_soc_pro_stunde[stunde_since_now] = self.akku.ladezustand_in_prozent()

        # Gesamtkosten berechnen
        gesamtkosten_euro = np.nansum(kosten_euro_pro_stunde) - np.nansum(einnahmen_euro_pro_stunde)

        out = {
            'Last_Wh_pro_Stunde': last_wh_pro_stunde,
            'Netzeinspeisung_Wh_pro_Stunde': netzeinspeisung_wh_pro_stunde,
            'Netzbezug_Wh_pro_Stunde': netzbezug_wh_pro_stunde,
            'Kosten_Euro_pro_Stunde': kosten_euro_pro_stunde,
            'akku_soc_pro_stunde': akku_soc_pro_stunde,
            'Einnahmen_Euro_pro_Stunde': einnahmen_euro_pro_stunde,
            'Gesamtbilanz_Euro': gesamtkosten_euro,
            'E-Auto_SoC_pro_Stunde': eauto_soc_pro_stunde,
            'Gesamteinnahmen_Euro': np.nansum(einnahmen_euro_pro_stunde),
            'Gesamtkosten_Euro': np.nansum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde": verluste_wh_pro_stunde,
            "Gesamt_Verluste": np.nansum(verluste_wh_pro_stunde),
            "Haushaltsgeraet_wh_pro_stunde": haushaltsgeraet_wh_pro_stunde
        }

        out = replace_nan_with_none(out)
        return out

