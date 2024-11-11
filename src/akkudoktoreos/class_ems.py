from datetime import datetime
from typing import Dict, List, Optional, Union

import numpy as np

from akkudoktoreos.battery import Battery


def replace_nan_with_none(
    data: Union[np.ndarray, dict, list, float],
) -> Union[List, dict, float, None]:
    if data is None:
        return None
    if isinstance(data, np.ndarray):
        # Use numpy vectorized approach
        return np.where(np.isnan(data), None, data).tolist()
    elif isinstance(data, dict):
        return {key: replace_nan_with_none(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_none(element) for element in data]
    elif isinstance(data, (float, np.floating)) and np.isnan(data):
        return None
    else:
        return data
from akkudoktoreos.config import EOSConfig


class EnergieManagementSystem:
    def __init__(
        self,
        config: EOSConfig,
        pv_prognose_wh: Optional[np.ndarray] = None,
        strompreis_euro_pro_wh: Optional[np.ndarray] = None,
        einspeiseverguetung_euro_pro_wh: Optional[np.ndarray] = None,
        eauto: Optional[Battery] = None,
        gesamtlast: Optional[np.ndarray] = None,
        haushaltsgeraet: Optional[object] = None,
        wechselrichter: Optional[object] = None,
    ):
        self.akku: Battery = wechselrichter.akku
        self.gesamtlast = gesamtlast
        self.pv_prognose_wh = pv_prognose_wh
        self.strompreis_euro_pro_wh = strompreis_euro_pro_wh
        self.einspeiseverguetung_euro_pro_wh = einspeiseverguetung_euro_pro_wh
        self.eauto = eauto
        self.haushaltsgeraet = haushaltsgeraet
        self.wechselrichter = wechselrichter
        self.ac_charge_hours = np.full(config.prediction_hours, 0)
        self.dc_charge_hours = np.full(config.prediction_hours, 1)
        self.ev_charge_hours = np.full(config.prediction_hours, 0)

    def set_akku_discharge_hours(self, ds: List[int]) -> None:
        self.akku.set_discharge_per_hour(ds)

    def set_akku_ac_charge_hours(self, ds: np.ndarray) -> None:
        self.ac_charge_hours = ds

    def set_akku_dc_charge_hours(self, ds: np.ndarray) -> None:
        self.dc_charge_hours = ds

    def set_ev_charge_hours(self, ds: List[int]) -> None:
        self.ev_charge_hours = ds

    def set_haushaltsgeraet_start(self, ds: List[int], global_start_hour: int = 0) -> None:
        self.haushaltsgeraet.set_startzeitpunkt(ds, global_start_hour=global_start_hour)

    def reset(self) -> None:
        self.eauto.reset()
        self.akku.reset()

    def simuliere_ab_jetzt(self) -> dict:
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        return self.simuliere(start_stunde)

    def simuliere(self, start_stunde: int) -> dict:
        """hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of  last hour (end state)
        """
        lastkurve_wh = self.gesamtlast
        assert (
            len(lastkurve_wh) == len(self.pv_prognose_wh) == len(self.strompreis_euro_pro_wh)
        ), f"Array sizes do not match: Load Curve = {len(lastkurve_wh)}, PV Forecast = {len(self.pv_prognose_wh)}, Electricity Price = {len(self.strompreis_euro_pro_wh)}"

        # Optimized total hours calculation
        ende = len(lastkurve_wh)
        total_hours = ende - start_stunde

        # Pre-allocate arrays for the results, optimized for speed
        last_wh_pro_stunde = np.full((total_hours), np.nan)
        netzeinspeisung_wh_pro_stunde = np.full((total_hours), np.nan)
        netzbezug_wh_pro_stunde = np.full((total_hours), np.nan)
        kosten_euro_pro_stunde = np.full((total_hours), np.nan)
        einnahmen_euro_pro_stunde = np.full((total_hours), np.nan)
        akku_soc_pro_stunde = np.full((total_hours), np.nan)
        eauto_soc_pro_stunde = np.full((total_hours), np.nan)
        verluste_wh_pro_stunde = np.full((total_hours), np.nan)
        haushaltsgeraet_wh_pro_stunde = np.full((total_hours), np.nan)

        # Set initial state
        akku_soc_pro_stunde[0] = self.akku.charge_state_percent()
        if self.eauto:
            eauto_soc_pro_stunde[0] = self.eauto.charge_state_percent()

        for stunde in range(start_stunde, ende):
            stunde_since_now = stunde - start_stunde

            # Accumulate loads and PV generation
            verbrauch = self.gesamtlast[stunde]
            verluste_wh_pro_stunde[stunde_since_now] = 0.0
            if self.haushaltsgeraet:
                ha_load = self.haushaltsgeraet.get_last_fuer_stunde(stunde)
                verbrauch += ha_load
                haushaltsgeraet_wh_pro_stunde[stunde_since_now] = ha_load

            # E-Auto handling
            if self.eauto and self.ev_charge_hours[stunde] > 0:
                geladene_menge_eauto, verluste_eauto = self.eauto.energie_laden(
                    None, stunde, relative_power=self.ev_charge_hours[stunde]
                )
                verbrauch += geladene_menge_eauto
                verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto

            if self.eauto:
                eauto_soc_pro_stunde[stunde_since_now] = self.eauto.ladezustand_in_prozent()
            # Process inverter logic
            erzeugung = self.pv_prognose_wh[stunde]
            self.akku.set_charge_allowed_for_hour(self.dc_charge_hours[stunde], stunde)
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = (
                self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)
            )

            # AC PV Battery Charge
            if self.ac_charge_hours[stunde] > 0.0:
                self.akku.set_charge_allowed_for_hour(1, stunde)
                geladene_menge, verluste_wh = self.akku.energie_laden(
                    None, stunde, relative_power=self.ac_charge_hours[stunde]
                )
                # print(stunde, " ", geladene_menge, " ",self.ac_charge_hours[stunde]," ",self.akku.ladezustand_in_prozent())
                verbrauch += geladene_menge
                netzbezug += geladene_menge
                verluste_wh_pro_stunde[stunde_since_now] += verluste_wh

            netzeinspeisung_wh_pro_stunde[stunde_since_now] = netzeinspeisung
            netzbezug_wh_pro_stunde[stunde_since_now] = netzbezug
            verluste_wh_pro_stunde[stunde_since_now] += verluste
            last_wh_pro_stunde[stunde_since_now] = verbrauch

            # Financial calculations
            kosten_euro_pro_stunde[stunde_since_now] = (
                netzbezug * self.strompreis_euro_pro_wh[stunde]
            )
            einnahmen_euro_pro_stunde[stunde_since_now] = (
                netzeinspeisung * self.einspeiseverguetung_euro_pro_wh[stunde]
            )

            # Akku SOC tracking
            akku_soc_pro_stunde[stunde_since_now] = self.akku.charge_state_percent()

        # Total cost and return
        gesamtkosten_euro = np.nansum(kosten_euro_pro_stunde) - np.nansum(einnahmen_euro_pro_stunde)

        # Prepare output dictionary
        out: Dict[str, Union[np.ndarray, float]] = {
            "Last_Wh_pro_Stunde": last_wh_pro_stunde,
            "Netzeinspeisung_Wh_pro_Stunde": netzeinspeisung_wh_pro_stunde,
            "Netzbezug_Wh_pro_Stunde": netzbezug_wh_pro_stunde,
            "Kosten_Euro_pro_Stunde": kosten_euro_pro_stunde,
            "akku_soc_pro_stunde": akku_soc_pro_stunde,
            "Einnahmen_Euro_pro_Stunde": einnahmen_euro_pro_stunde,
            "Gesamtbilanz_Euro": gesamtkosten_euro,
            "E-Auto_SoC_pro_Stunde": eauto_soc_pro_stunde,
            "Gesamteinnahmen_Euro": np.nansum(einnahmen_euro_pro_stunde),
            "Gesamtkosten_Euro": np.nansum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde": verluste_wh_pro_stunde,
            "Gesamt_Verluste": np.nansum(verluste_wh_pro_stunde),
            "Haushaltsgeraet_wh_pro_stunde": haushaltsgeraet_wh_pro_stunde,
        }

        return out
