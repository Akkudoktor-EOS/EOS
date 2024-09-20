from datetime import datetime
from pprint import pprint
import numpy as np

def replace_nan_with_none(data):
    if isinstance(data, dict):
        return {key: replace_nan_with_none(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_none(element) for element in data]
    elif isinstance(data, float) and np.isnan(data):
        return None
    else:
        return data

class EnergieManagementSystem:
    def __init__(self, pv_prognose_wh=None, strompreis_euro_pro_wh=None, einspeiseverguetung_euro_pro_wh=None, eauto=None, gesamtlast=None, haushaltsgeraet=None, wechselrichter=None):
        self.akku = wechselrichter.akku
        # self.lastkurve_wh = lastkurve_wh
        self.gesamtlast = gesamtlast
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_euro_pro_wh = strompreis_euro_pro_wh  # Electricity price in cents per Wh
        self.einspeiseverguetung_euro_pro_wh = einspeiseverguetung_euro_pro_wh  # Feed-in tariff in cents per Wh
        self.eauto = eauto  # Electric vehicle
        self.haushaltsgeraet = haushaltsgeraet  # Household appliance
        self.wechselrichter = wechselrichter  # Inverter
        
    def set_akku_discharge_hours(self, ds):
        """Set the discharge hours for the battery."""
        self.akku.set_discharge_per_hour(ds)
    
    def set_eauto_charge_hours(self, ds):
        """Set the charge hours for the electric vehicle."""
        self.eauto.set_charge_per_hour(ds)

    def set_haushaltsgeraet_start(self, ds, global_start_hour=0):
        """Set the start time for the household appliance."""
        self.haushaltsgeraet.set_startzeitpunkt(ds, global_start_hour=global_start_hour)
        
    def reset(self):
        """Reset the electric vehicle and battery to their initial states."""
        self.eauto.reset()
        self.akku.reset()

    def simuliere_ab_jetzt(self):
        """Simulate from the current time onward."""
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        # Calculate the number of hours until the same time the next day
        stunden_bis_ende_tag = 24 - start_stunde
        # Add these hours to the next day
        gesamt_stunden = stunden_bis_ende_tag + 24

        # Start the simulation from the current hour and run for the calculated duration
        return self.simuliere(start_stunde)

    def simuliere(self, start_stunde):
        """Run the simulation from the given start hour."""
        last_wh_pro_stunde = []
        netzeinspeisung_wh_pro_stunde = []
        netzbezug_wh_pro_stunde = []
        kosten_euro_pro_stunde = []
        einnahmen_euro_pro_stunde = []
        akku_soc_pro_stunde = []
        eauto_soc_pro_stunde = []
        verluste_wh_pro_stunde = []
        haushaltsgeraet_wh_pro_stunde = []
        lastkurve_wh = self.gesamtlast
        
        assert len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh), f"Array sizes do not match: Load curve = {len(lastkurve_wh)}, PV forecast = {len(self.pv_prognose_wh)}, Electricity price = {len(self.strompreis_euro_pro_wh)}"

        ende = min(len(lastkurve_wh), len(self.pv_prognose_wh), len(self.strompreis_euro_pro_wh))

        # Set final states to NaN to skip them for the hour
        last_wh_pro_stunde.append(np.nan)
        netzeinspeisung_wh_pro_stunde.append(np.nan)
        netzbezug_wh_pro_stunde.append(np.nan) 
        kosten_euro_pro_stunde.append(np.nan) 
        akku_soc_pro_stunde.append(self.akku.ladezustand_in_prozent()) 
        einnahmen_euro_pro_stunde.append(np.nan) 
        eauto_soc_pro_stunde.append(self.eauto.ladezustand_in_prozent())
        verluste_wh_pro_stunde.append(np.nan)
        haushaltsgeraet_wh_pro_stunde.append(np.nan)
        
        # Calculate the end based on the length of the load curve
        for stunde in range(start_stunde + 1, ende):
            # Initial state at the beginning of the hour
            akku_soc_start = self.akku.ladezustand_in_prozent()  # Initial state of battery SoC
            if self.eauto:
                eauto_soc_start = self.eauto.ladezustand_in_prozent()  # Initial state of electric vehicle SoC

            # Ensure indices are correct
            verbrauch = lastkurve_wh[stunde]  # Consumption for the hour
            if self.haushaltsgeraet is not None:
                verbrauch += self.haushaltsgeraet.get_last_fuer_stunde(stunde)
                haushaltsgeraet_wh_pro_stunde.append(self.haushaltsgeraet.get_last_fuer_stunde(stunde))
            else: 
                haushaltsgeraet_wh_pro_stunde.append(0)
                
            erzeugung = self.pv_prognose_wh[stunde]  # PV generation forecast
            strompreis = self.strompreis_euro_pro_wh[stunde] if stunde < len(self.strompreis_euro_pro_wh) else self.strompreis_euro_pro_wh[-1]
            
            verluste_wh_pro_stunde.append(0.0)

            # Logic for electric vehicle charging or discharging
            if self.eauto:  # If an electric vehicle is present
                geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(None, stunde)
                verbrauch += geladene_menge_eauto
                verluste_wh_pro_stunde[-1] += verluste_eauto
                eauto_soc = self.eauto.ladezustand_in_prozent()

            stündlicher_netzbezug_wh = 0
            stündliche_kosten_euro = 0
            stündliche_einnahmen_euro = 0

            # How much can the inverter process?
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)
            
            # Store results
            netzeinspeisung_wh_pro_stunde.append(netzeinspeisung)
            stündliche_einnahmen_euro = netzeinspeisung * self.einspeiseverguetung_euro_pro_wh[stunde] 
            stündliche_kosten_euro = netzbezug * strompreis 
            netzbezug_wh_pro_stunde.append(netzbezug)
            verluste_wh_pro_stunde[-1] += verluste
            last_wh_pro_stunde.append(eigenverbrauch + netzbezug)

            if self.eauto:
                eauto_soc_pro_stunde.append(eauto_soc)
            
            akku_soc_pro_stunde.append(self.akku.ladezustand_in_prozent())
         
            kosten_euro_pro_stunde.append(stündliche_kosten_euro)
            einnahmen_euro_pro_stunde.append(stündliche_einnahmen_euro)

        gesamtkosten_euro = np.nansum(kosten_euro_pro_stunde) - np.nansum(einnahmen_euro_pro_stunde)
        expected_length = ende - start_stunde
        array_names = ['Eigenverbrauch_Wh_pro_Stunde', 'Netzeinspeisung_Wh_pro_Stunde', 'Netzbezug_Wh_pro_Stunde', 'Kosten_Euro_pro_Stunde', 'akku_soc_pro_stunde', 'Einnahmen_Euro_pro_Stunde', 'E-Auto_SoC_pro_Stunde', "Verluste_Pro_Stunde"]
        all_arrays = [last_wh_pro_stunde, netzeinspeisung_wh_pro_stunde, netzbezug_wh_pro_stunde, kosten_euro_pro_stunde, akku_soc_pro_stunde, einnahmen_euro_pro_stunde, eauto_soc_pro_stunde, verluste_wh_pro_stunde]

        inconsistent_arrays = [name for name, arr in zip(array_names, all_arrays) if len(arr) != expected_length]
        
        if inconsistent_arrays:
            raise ValueError(f"Inconsistent lengths in the arrays: {', '.join(inconsistent_arrays)}. Expected length: {expected_length}, found: {[len(all_arrays[array_names.index(name)]) for name in inconsistent_arrays]}")

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
