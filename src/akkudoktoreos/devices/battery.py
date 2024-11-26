from typing import Any, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from akkudoktoreos.utils.utils import NumpyEncoder


def max_ladeleistung_w_field(default: Optional[float] = None) -> Optional[float]:
    return Field(
        default=default,
        gt=0,
        description="An integer representing the charging power of the battery in watts.",
    )


def start_soc_prozent_field(description: str) -> int:
    return Field(default=0, ge=0, le=100, description=description)


class BaseAkkuParameters(BaseModel):
    kapazitaet_wh: int = Field(
        gt=0, description="An integer representing the capacity of the battery in watt-hours."
    )
    lade_effizienz: float = Field(
        default=0.88,
        gt=0,
        le=1,
        description="A float representing the charging efficiency of the battery.",
    )
    entlade_effizienz: float = Field(default=0.88, gt=0, le=1)
    max_ladeleistung_w: Optional[float] = max_ladeleistung_w_field()
    start_soc_prozent: int = start_soc_prozent_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_prozent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="An integer representing the minimum state of charge (SOC) of the battery in percentage.",
    )
    max_soc_prozent: int = Field(default=100, ge=0, le=100)


class PVAkkuParameters(BaseAkkuParameters):
    max_ladeleistung_w: Optional[float] = max_ladeleistung_w_field(5000)


class EAutoParameters(BaseAkkuParameters):
    entlade_effizienz: float = 1.0
    start_soc_prozent: int = start_soc_prozent_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class EAutoResult(BaseModel):
    """This object contains information related to the electric vehicle and its charging and discharging behavior."""

    charge_array: list[float] = Field(
        description="Indicates for each hour whether the EV is charging (`0` for no charging, `1` for charging)."
    )
    discharge_array: list[int] = Field(
        description="Indicates for each hour whether the EV is discharging (`0` for no discharging, `1` for discharging)."
    )
    entlade_effizienz: float = Field(description="The discharge efficiency as a float.")
    hours: int = Field(description="Amount of hours the simulation is done for.")
    kapazitaet_wh: int = Field(description="The capacity of the EV’s battery in watt-hours.")
    lade_effizienz: float = Field(description="The charging efficiency as a float.")
    max_ladeleistung_w: int = Field(description="The maximum charging power of the EV in watts.")
    soc_wh: float = Field(
        description="The state of charge of the battery in watt-hours at the start of the simulation."
    )
    start_soc_prozent: int = Field(
        description="The state of charge of the battery in percentage at the start of the simulation."
    )

    @field_validator(
        "discharge_array",
        "charge_array",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class PVAkku:
    def __init__(self, parameters: BaseAkkuParameters, hours: int = 24):
        # Battery capacity in Wh
        self.kapazitaet_wh = parameters.kapazitaet_wh
        # Initial state of charge in Wh
        self.start_soc_prozent = parameters.start_soc_prozent
        self.soc_wh = (parameters.start_soc_prozent / 100) * parameters.kapazitaet_wh
        self.hours = hours
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        # Charge and discharge efficiency
        self.lade_effizienz = parameters.lade_effizienz
        self.entlade_effizienz = parameters.entlade_effizienz
        self.max_ladeleistung_w = (
            parameters.max_ladeleistung_w if parameters.max_ladeleistung_w else self.kapazitaet_wh
        )
        # Only assign for storage battery
        self.min_soc_prozent = (
            parameters.min_soc_prozent if isinstance(parameters, PVAkkuParameters) else 0
        )
        self.max_soc_prozent = parameters.max_soc_prozent
        # Calculate min and max SoC in Wh
        self.min_soc_wh = (self.min_soc_prozent / 100) * self.kapazitaet_wh
        self.max_soc_wh = (self.max_soc_prozent / 100) * self.kapazitaet_wh

    def to_dict(self) -> dict[str, Any]:
        return {
            "kapazitaet_wh": self.kapazitaet_wh,
            "start_soc_prozent": self.start_soc_prozent,
            "soc_wh": self.soc_wh,
            "hours": self.hours,
            "discharge_array": self.discharge_array,
            "charge_array": self.charge_array,
            "lade_effizienz": self.lade_effizienz,
            "entlade_effizienz": self.entlade_effizienz,
            "max_ladeleistung_w": self.max_ladeleistung_w,
        }

    def reset(self) -> None:
        self.soc_wh = (self.start_soc_prozent / 100) * self.kapazitaet_wh
        # Ensure soc_wh is within min and max limits
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)

        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)

    def set_discharge_per_hour(self, discharge_array: np.ndarray) -> None:
        assert len(discharge_array) == self.hours
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array: np.ndarray) -> None:
        assert len(charge_array) == self.hours
        self.charge_array = np.array(charge_array)

    def set_charge_allowed_for_hour(self, charge: float, hour: int) -> None:
        assert hour < self.hours
        self.charge_array[hour] = charge

    def ladezustand_in_prozent(self) -> float:
        return (self.soc_wh / self.kapazitaet_wh) * 100

    def energie_abgeben(self, wh: float, hour: int) -> tuple[float, float]:
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0  # No energy discharge and no losses

        # Calculate the maximum energy that can be discharged considering min_soc and efficiency
        max_possible_discharge_wh = (self.soc_wh - self.min_soc_wh) * self.entlade_effizienz
        max_possible_discharge_wh = max(max_possible_discharge_wh, 0.0)  # Ensure non-negative

        # Consider the maximum discharge power of the battery
        max_abgebbar_wh = min(max_possible_discharge_wh, self.max_ladeleistung_w)

        # The actually discharged energy cannot exceed requested energy or maximum discharge
        tatsaechlich_abgegeben_wh = min(wh, max_abgebbar_wh)

        # Calculate the actual amount withdrawn from the battery (before efficiency loss)
        if self.entlade_effizienz > 0:
            tatsaechliche_entnahme_wh = tatsaechlich_abgegeben_wh / self.entlade_effizienz
        else:
            tatsaechliche_entnahme_wh = 0.0

        # Update the state of charge considering the actual withdrawal
        self.soc_wh -= tatsaechliche_entnahme_wh
        # Ensure soc_wh does not go below min_soc_wh
        self.soc_wh = max(self.soc_wh, self.min_soc_wh)

        # Calculate losses due to efficiency
        verluste_wh = tatsaechliche_entnahme_wh - tatsaechlich_abgegeben_wh

        # Return the actually discharged energy and the losses
        return tatsaechlich_abgegeben_wh, verluste_wh

    def energie_laden(
        self, wh: Optional[float], hour: int, relative_power: float = 0.0
    ) -> tuple[float, float]:
        if hour is not None and self.charge_array[hour] == 0:
            return 0.0, 0.0  # Charging not allowed in this hour
        if relative_power > 0.0:
            wh = self.max_ladeleistung_w * relative_power
        # If no value for wh is given, use the maximum charging power
        wh = wh if wh is not None else self.max_ladeleistung_w

        # Calculate the maximum energy that can be charged considering max_soc and efficiency
        if self.lade_effizienz > 0:
            max_possible_charge_wh = (self.max_soc_wh - self.soc_wh) / self.lade_effizienz
        else:
            max_possible_charge_wh = 0.0
        max_possible_charge_wh = max(max_possible_charge_wh, 0.0)  # Ensure non-negative

        # The actually charged energy cannot exceed requested energy, charging power, or maximum possible charge
        effektive_lademenge = min(wh, max_possible_charge_wh)

        # Energy actually stored in the battery
        geladene_menge = effektive_lademenge * self.lade_effizienz

        # Update soc_wh
        self.soc_wh += geladene_menge
        # Ensure soc_wh does not exceed max_soc_wh
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)

        # Calculate losses
        verluste_wh = effektive_lademenge - geladene_menge
        return geladene_menge, verluste_wh

    def aktueller_energieinhalt(self) -> float:
        """This method returns the current remaining energy considering efficiency.

        It accounts for both charging and discharging efficiency.
        """
        # Calculate remaining energy considering discharge efficiency
        nutzbare_energie = (self.soc_wh - self.min_soc_wh) * self.entlade_effizienz
        return max(nutzbare_energie, 0.0)


if __name__ == "__main__":
    # Test battery discharge below min_soc
    print("Test: Discharge below min_soc")
    akku = PVAkku(
        PVAkkuParameters(
            kapazitaet_wh=10000,
            start_soc_prozent=50,
            min_soc_prozent=20,
            max_soc_prozent=80,
        ),
        hours=1,
    )
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    # Try to discharge 5000 Wh
    abgegeben_wh, verlust_wh = akku.energie_abgeben(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.ladezustand_in_prozent()}%")
    print(f"Expected min SoC: {akku.min_soc_prozent}%")

    # Test battery charge above max_soc
    print("\nTest: Charge above max_soc")
    akku = PVAkku(
        PVAkkuParameters(
            kapazitaet_wh=10000,
            start_soc_prozent=50,
            min_soc_prozent=20,
            max_soc_prozent=80,
        ),
        hours=1,
    )
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    # Try to charge 5000 Wh
    geladen_wh, verlust_wh = akku.energie_laden(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.ladezustand_in_prozent()}%")
    print(f"Expected max SoC: {akku.max_soc_prozent}%")

    # Test charging when battery is at max_soc
    print("\nTest: Charging when at max_soc")
    akku = PVAkku(
        PVAkkuParameters(
            kapazitaet_wh=10000,
            start_soc_prozent=80,
            min_soc_prozent=20,
            max_soc_prozent=80,
        ),
        hours=1,
    )
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    geladen_wh, verlust_wh = akku.energie_laden(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.ladezustand_in_prozent()}%")

    # Test discharging when battery is at min_soc
    print("\nTest: Discharging when at min_soc")
    akku = PVAkku(
        PVAkkuParameters(
            kapazitaet_wh=10000,
            start_soc_prozent=20,
            min_soc_prozent=20,
            max_soc_prozent=80,
        ),
        hours=1,
    )
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    abgegeben_wh, verlust_wh = akku.energie_abgeben(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.ladezustand_in_prozent()}%")
