from datetime import datetime
from typing import Dict, List, Optional, Union

import numpy as np
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self

from akkudoktoreos.class_akku import PVAkku
from akkudoktoreos.class_haushaltsgeraet import Haushaltsgeraet
from akkudoktoreos.class_inverter import Wechselrichter
from akkudoktoreos.config import EOSConfig


class EnergieManagementSystemParameters(BaseModel):
    pv_prognose_wh: list[float] = Field(
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
    )
    strompreis_euro_pro_wh: list[float] = Field(
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals."
    )
    einspeiseverguetung_euro_pro_wh: list[float] | float = Field(
        description="A float or array of floats representing the feed-in compensation in euros per watt-hour."
    )
    preis_euro_pro_wh_akku: float
    gesamtlast: list[float] = Field(
        description="An array of floats representing the total load (consumption) in watts for different time intervals."
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        pv_prognose_length = len(self.pv_prognose_wh)
        if (
            pv_prognose_length != len(self.strompreis_euro_pro_wh)
            or pv_prognose_length != len(self.gesamtlast)
            or (
                isinstance(self.einspeiseverguetung_euro_pro_wh, list)
                and pv_prognose_length != len(self.einspeiseverguetung_euro_pro_wh)
            )
        ):
            raise ValueError("Input lists have different lengths")
        return self


class EnergieManagementSystem:
    def __init__(
        self,
        config: EOSConfig,
        parameters: EnergieManagementSystemParameters,
        eauto: Optional[PVAkku] = None,
        haushaltsgeraet: Optional[Haushaltsgeraet] = None,
        wechselrichter: Optional[Wechselrichter] = None,
    ):
        self.akku = wechselrichter.akku
        self.gesamtlast = np.array(parameters.gesamtlast, float)
        self.pv_prognose_wh = np.array(parameters.pv_prognose_wh, float)
        self.strompreis_euro_pro_wh = np.array(parameters.strompreis_euro_pro_wh, float)
        self.einspeiseverguetung_euro_pro_wh_arr = (
            parameters.einspeiseverguetung_euro_pro_wh
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(len(self.gesamtlast), parameters.einspeiseverguetung_euro_pro_wh, float)
        )
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
        akku_soc_pro_stunde[0] = self.akku.ladezustand_in_prozent()
        if self.eauto:
            eauto_soc_pro_stunde[0] = self.eauto.ladezustand_in_prozent()

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
                netzeinspeisung * self.einspeiseverguetung_euro_pro_wh_arr[stunde]
            )

            # Akku SOC tracking
            akku_soc_pro_stunde[stunde_since_now] = self.akku.ladezustand_in_prozent()

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
            "EAuto_SoC_pro_Stunde": eauto_soc_pro_stunde,
            "Gesamteinnahmen_Euro": np.nansum(einnahmen_euro_pro_stunde),
            "Gesamtkosten_Euro": np.nansum(kosten_euro_pro_stunde),
            "Verluste_Pro_Stunde": verluste_wh_pro_stunde,
            "Gesamt_Verluste": np.nansum(verluste_wh_pro_stunde),
            "Haushaltsgeraet_wh_pro_stunde": haushaltsgeraet_wh_pro_stunde,
        }

        return out
