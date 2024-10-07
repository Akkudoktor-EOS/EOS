from datetime import datetime
from typing import Dict, List, Optional, Union

from modules.battery import Battery

import numpy as np


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


class EnergieManagementSystem:
    def __init__(
        self,
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

    def set_akku_discharge_hours(self, ds: List[int]) -> None:
        self.akku.set_discharge_per_hour(ds)

    def set_eauto_charge_hours(self, ds: List[int]) -> None:
        self.eauto.set_charge_per_hour(ds)

    def set_haushaltsgeraet_start(
        self, ds: List[int], global_start_hour: int = 0
    ) -> None:
        self.haushaltsgeraet.set_startzeitpunkt(ds, global_start_hour=global_start_hour)

    def reset(self) -> None:
        self.eauto.reset()
        self.akku.reset()

    def simuliere_ab_jetzt(self) -> dict:
        jetzt = datetime.now()
        start_stunde = jetzt.hour
        return self.simuliere(start_stunde)

    def simuliere(self, start_stunde: int) -> dict:
        # Ensure arrays have the same length
        lastkurve_wh = self.gesamtlast
        assert (
            len(lastkurve_wh)
            == len(self.pv_prognose_wh)
            == len(self.strompreis_euro_pro_wh)
        ), f"Array sizes do not match: Load Curve = {len(lastkurve_wh)}, PV Forecast = {len(self.pv_prognose_wh)}, Electricity Price = {len(self.strompreis_euro_pro_wh)}"

        # Optimized total hours calculation
        ende = len(lastkurve_wh)
        total_hours = ende - start_stunde

        # Pre-allocate arrays for the results, optimized for speed
        last_wh_pro_stunde = np.zeros(total_hours)
        netzeinspeisung_wh_pro_stunde = np.zeros(total_hours)
        netzbezug_wh_pro_stunde = np.zeros(total_hours)
        kosten_euro_pro_stunde = np.zeros(total_hours)
        einnahmen_euro_pro_stunde = np.zeros(total_hours)
        akku_soc_pro_stunde = np.zeros(total_hours)
        eauto_soc_pro_stunde = np.zeros(total_hours)
        verluste_wh_pro_stunde = np.zeros(total_hours)
        haushaltsgeraet_wh_pro_stunde = np.zeros(total_hours)

        # Set initial state
        akku_soc_pro_stunde[0] = self.akku.charge_state_percent()
        if self.eauto:
            eauto_soc_pro_stunde[0] = self.eauto.charge_state_percent()

        for stunde in range(start_stunde + 1, ende):
            stunde_since_now = stunde - start_stunde

            # Accumulate loads and PV generation
            verbrauch = self.gesamtlast[stunde]

            if self.haushaltsgeraet:
                ha_load = self.haushaltsgeraet.get_last_fuer_stunde(stunde)
                verbrauch += ha_load
                haushaltsgeraet_wh_pro_stunde[stunde_since_now] = ha_load

            # E-Auto handling
            if self.eauto:
                geladene_menge_eauto, verluste_eauto = self.eauto.charge(
                    None, stunde
                )
                verbrauch += geladene_menge_eauto
                verluste_wh_pro_stunde[stunde_since_now] += verluste_eauto
                eauto_soc_pro_stunde[stunde_since_now] = (
                    self.eauto.charge_state_percent()
                )

            # Process inverter logic
            erzeugung = self.pv_prognose_wh[stunde]
            netzeinspeisung, netzbezug, verluste, eigenverbrauch = (
                self.wechselrichter.energie_verarbeiten(erzeugung, verbrauch, stunde)
            )
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
        gesamtkosten_euro = np.sum(kosten_euro_pro_stunde) - np.sum(
            einnahmen_euro_pro_stunde
        )

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
            "Gesamteinnahmen_Euro": np.sum(einnahmen_euro_pro_stunde),
            "Gesamtkosten_Euro": np.sum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde": verluste_wh_pro_stunde,
            "Gesamt_Verluste": np.sum(verluste_wh_pro_stunde),
            "Haushaltsgeraet_wh_pro_stunde": haushaltsgeraet_wh_pro_stunde,
        }

        return replace_nan_with_none(out)
