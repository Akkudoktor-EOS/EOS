from datetime import datetime
import numpy as np
import modules.class_akku as PVAkku
from typing import Dict, List, Optional, Union


def replace_nan_with_none(data):
    if data is None:
        return None
    if isinstance(data, dict):
        return {key: replace_nan_with_none(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_none(element) for element in data]
    elif isinstance(data, np.ndarray):
        return replace_nan_with_none(data.tolist())
    elif isinstance(data, (float, np.floating)) and np.isnan(data):
        return None
    else:
        return data


class EnergieManagementSystem:
    def __init__(
        self,
        pv_prognose_wh: Optional[np.ndarray] = None,
        strompreis_euro_pro_wh: Optional[np.ndarray] = None,
        einspeiseverguetung_euro_pro_wh: Optional[np.ndarray] = None,
        eauto = None,
        gesamtlast: Optional[np.ndarray] = None,
        haushaltsgeraet = None,
        wechselrichter = None
    ) -> None:
        self.akku = wechselrichter.akku if wechselrichter else None
        self.gesamtlast = gesamtlast
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_euro_pro_wh = strompreis_euro_pro_wh
        self.einspeiseverguetung_euro_pro_wh = einspeiseverguetung_euro_pro_wh
        self.eauto = eauto
        self.haushaltsgeraet = haushaltsgeraet
        self.wechselrichter = wechselrichter

        self.validate_parameters()

    def validate_parameters(self) -> None:
        """Ensure that all the required parameters are properly initialized and arrays are valid."""
        if not self.akku:
            raise ValueError("Wechselrichter or Akku object is not set.")
        if not self.pv_prognose_wh or not self.strompreis_euro_pro_wh or not self.gesamtlast:
            raise ValueError("Missing or invalid input data: PV Prognose, Strompreis, or Gesamtlast.")
        if len(self.pv_prognose_wh) != len(self.strompreis_euro_pro_wh) or len(self.gesamtlast) != len(self.pv_prognose_wh):
            raise ValueError("Input arrays must have the same length.")

    def set_akku_discharge_hours(self, ds: np.ndarray) -> None:
        try:
            self.akku.set_discharge_per_hour(ds)
        except AttributeError:
            raise ValueError("Akku object is not set or invalid.")

    def set_eauto_charge_hours(self, ds: np.ndarray) -> None:
        if self.eauto:
            try:
                self.eauto.set_charge_per_hour(ds)
            except AttributeError:
                raise ValueError("Eauto object is not set or invalid.")

    def set_haushaltsgeraet_start(self, ds: np.ndarray, global_start_hour: int = 0) -> None:
        if self.haushaltsgeraet:
            try:
                self.haushaltsgeraet.set_startzeitpunkt(ds, global_start_hour=global_start_hour)
            except AttributeError:
                raise ValueError("Haushaltsgerät object is not set or invalid.")

    def reset(self) -> None:
        if self.eauto:
            self.eauto.reset()
        if self.akku:
            self.akku.reset()

    def simuliere_ab_jetzt(self) -> Dict:
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        return self.simuliere(start_stunde)

    def simuliere(self, start_stunde: int) -> Dict:
        # Parameter validation
        self.validate_parameters()

        lastkurve_wh = self.gesamtlast
        ende = min(len(lastkurve_wh), len(self.pv_prognose_wh), len(self.strompreis_euro_pro_wh))
        total_hours = ende - start_stunde

        # Initialize arrays
        last_wh_pro_stunde = np.full(total_hours, np.nan)
        netzeinspeisung_wh_pro_stunde = np.full(total_hours, np.nan)
        netzbezug_wh_pro_stunde = np.full(total_hours, np.nan)
        kosten_euro_pro_stunde = np.full(total_hours, np.nan)
        einnahmen_euro_pro_stunde = np.full(total_hours, np.nan)
        akku_soc_pro_stunde = np.full(total_hours, np.nan)
        eauto_soc_pro_stunde = np.full(total_hours, np.nan) if self.eauto else None
        verluste_wh_pro_stunde = np.full(total_hours, np.nan)
        haushaltsgeraet_wh_pro_stunde = np.full(total_hours, np.nan)

        # Cache initial states
        akku_soc_pro_stunde[start_stunde] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            eauto_soc_pro_stunde[start_stunde] = self.eauto.ladezustand_in_prozent()

        # Main simulation loop
        for stunde in range(start_stunde + 1, ende):
            stunde_since_now = stunde - start_stunde

            # Calculate consumption and household load
            verbrauch, haushalts_last = self.calculate_consumption(stunde)
            haushaltsgeraet_wh_pro_stunde[stunde_since_now] = haushalts_last

            # PV generation and electricity price
            erzeugung = self.pv_prognose_wh[stunde]
            strompreis = self.strompreis_euro_pro_wh[stunde]

            # E-Auto charging
            if self.eauto:
                verbrauch, verluste = self.charge_eauto(stunde, verbrauch)
                verluste_wh_pro_stunde[stunde_since_now] += verluste
                eauto_soc_pro_stunde[stunde_since_now] = self.eauto.ladezustand_in_prozent()

            # Process energy via inverter
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = self.process_inverter(erzeugung, verbrauch, stunde)
            netzeinspeisung_wh_pro_stunde[stunde_since_now] = netzeinspeisung
            netzbezug_wh_pro_stunde[stunde_since_now] = netzbezug
            verluste_wh_pro_stunde[stunde_since_now] += verluste
            last_wh_pro_stunde[stunde_since_now] = verbrauch

            # Calculate costs
            kosten_euro_pro_stunde[stunde_since_now], einnahmen_euro_pro_stunde[stunde_since_now] = self.calculate_costs(netzbezug, netzeinspeisung, strompreis, stunde)

            # Update Akku SoC
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

        return replace_nan_with_none(out)

    def calculate_consumption(self, stunde: int) -> Union[int, float]:
        """Calculate consumption and household load for a given hour."""
        verbrauch = self.gesamtlast[stunde]
        haushalts_last = self.haushaltsgeraet.get_last_fuer_stunde(stunde) if self.haushaltsgeraet else 0
        verbrauch += haushalts_last
        return verbrauch, haushalts_last

    def charge_eauto(self, stunde: int, verbrauch: float) -> Union[float, float]:
        """Handle E-Auto charging and energy losses."""
        geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(None, stunde)
        verbrauch += geladene_menge_eauto
        return verbrauch, verluste_eauto

    def process_inverter(self, erzeugung: float, verbrauch: float, stunde: int) -> Union[float, float, float, float]:
        """Process energy via inverter and return key metrics."""
        return self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)

    def calculate_costs(self, netzbezug: float, netzeinspeisung: float, strompreis: float, stunde: int) -> Union[float, float]:
        """Calculate the cost and earnings for a given hour."""
        kosten = netzbezug * strompreis
        einnahmen = netzeinspeisung * self.einspeiseverguetung_euro_pro_wh[stunde]
        return kosten, einnahmen
