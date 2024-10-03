import numpy as np

class PVAkku:
    def __init__(self, kapazitaet_wh=None, hours=None, lade_effizienz=0.88, entlade_effizienz=0.88,
                 max_ladeleistung_w=None, start_soc_prozent=0, min_soc_prozent=0, max_soc_prozent=100):
        # Battery capacity in Wh
        self.kapazitaet_wh = kapazitaet_wh
        # Initial state of charge in Wh
        self.start_soc_prozent = start_soc_prozent
        self.soc_wh = (start_soc_prozent / 100) * kapazitaet_wh
        self.hours = hours if hours is not None else 24  # Default to 24 hours if not specified
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        # Charge and discharge efficiency
        self.lade_effizienz = lade_effizienz
        self.entlade_effizienz = entlade_effizienz        
        self.max_ladeleistung_w = max_ladeleistung_w if max_ladeleistung_w else self.kapazitaet_wh
        self.min_soc_prozent = min_soc_prozent
        self.max_soc_prozent = max_soc_prozent
        # Calculate min and max SoC in Wh
        self.min_soc_wh = (self.min_soc_prozent / 100) * self.kapazitaet_wh
        self.max_soc_wh = (self.max_soc_prozent / 100) * self.kapazitaet_wh


    def to_dict(self):
        return {
            "kapazitaet_wh": self.kapazitaet_wh,
            "start_soc_prozent": self.start_soc_prozent,
            "soc_wh": self.soc_wh,
            "hours": self.hours,
            "discharge_array": self.discharge_array.tolist(),  # Convert np.array to list
            "charge_array": self.charge_array.tolist(),
            "lade_effizienz": self.lade_effizienz,
            "entlade_effizienz": self.entlade_effizienz,
            "max_ladeleistung_w": self.max_ladeleistung_w
        }

    @classmethod
    def from_dict(cls, data):
        # Create a new object with basic data
        obj = cls(
            kapazitaet_wh=data["kapazitaet_wh"],
            hours=data["hours"],
            lade_effizienz=data["lade_effizienz"],
            entlade_effizienz=data["entlade_effizienz"],
            max_ladeleistung_w=data["max_ladeleistung_w"],
            start_soc_prozent=data["start_soc_prozent"]
        )
        # Set arrays
        obj.discharge_array = np.array(data["discharge_array"])
        obj.charge_array = np.array(data["charge_array"])
        obj.soc_wh = data["soc_wh"]  # Set current state of charge, which may differ from start_soc_prozent
        
        return obj

    def reset(self):
        self.soc_wh = (self.start_soc_prozent / 100) * self.kapazitaet_wh
        # Ensure soc_wh is within min and max limits
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)
        
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)

    def set_discharge_per_hour(self, discharge_array):
        assert len(discharge_array) == self.hours
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array):
        assert len(charge_array) == self.hours
        self.charge_array = np.array(charge_array)

    def ladezustand_in_prozent(self):
        return (self.soc_wh / self.kapazitaet_wh) * 100

    def energie_abgeben(self, wh, hour):
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


    def energie_laden(self, wh, hour):
        if hour is not None and self.charge_array[hour] == 0:
            return 0, 0  # Charging not allowed in this hour

        # If no value for wh is given, use the maximum charging power
        wh = wh if wh is not None else self.max_ladeleistung_w

        # Relative to the maximum charging power (between 0 and 1)
        relative_ladeleistung = self.charge_array[hour]
        effektive_ladeleistung = relative_ladeleistung * self.max_ladeleistung_w

        # Calculate the maximum energy that can be charged considering max_soc and efficiency
        if self.lade_effizienz > 0:
            max_possible_charge_wh = (self.max_soc_wh - self.soc_wh) / self.lade_effizienz
        else:
            max_possible_charge_wh = 0.0
        max_possible_charge_wh = max(max_possible_charge_wh, 0.0)  # Ensure non-negative

        # The actually charged energy cannot exceed requested energy, charging power, or maximum possible charge
        effektive_lademenge = min(wh, effektive_ladeleistung, max_possible_charge_wh)

        # Energy actually stored in the battery
        geladene_menge = effektive_lademenge * self.lade_effizienz

        # Update soc_wh
        self.soc_wh += geladene_menge
        # Ensure soc_wh does not exceed max_soc_wh
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)

        # Calculate losses
        verluste_wh = effektive_lademenge - geladene_menge

        return geladene_menge, verluste_wh


    def aktueller_energieinhalt(self):
        """
        This method returns the current remaining energy considering efficiency.
        It accounts for both charging and discharging efficiency.
        """
        # Calculate remaining energy considering discharge efficiency
        nutzbare_energie = (self.soc_wh - self.min_soc_wh) * self.entlade_effizienz
        return max(nutzbare_energie, 0.0)



if __name__ == '__main__':
    # Test battery discharge below min_soc
    print("Test: Discharge below min_soc")
    akku = PVAkku(kapazitaet_wh=10000, hours=1, start_soc_prozent=50, min_soc_prozent=20, max_soc_prozent=80)
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    # Try to discharge 5000 Wh
    abgegeben_wh, verlust_wh = akku.energie_abgeben(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.ladezustand_in_prozent()}%")
    print(f"Expected min SoC: {akku.min_soc_prozent}%")

    # Test battery charge above max_soc
    print("\nTest: Charge above max_soc")
    akku = PVAkku(kapazitaet_wh=10000, hours=1, start_soc_prozent=50, min_soc_prozent=20, max_soc_prozent=80)
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    # Try to charge 5000 Wh
    geladen_wh, verlust_wh = akku.energie_laden(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.ladezustand_in_prozent()}%")
    print(f"Expected max SoC: {akku.max_soc_prozent}%")

    # Test charging when battery is at max_soc
    print("\nTest: Charging when at max_soc")
    akku = PVAkku(kapazitaet_wh=10000, hours=1, start_soc_prozent=80, min_soc_prozent=20, max_soc_prozent=80)
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    geladen_wh, verlust_wh = akku.energie_laden(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.ladezustand_in_prozent()}%")

    # Test discharging when battery is at min_soc
    print("\nTest: Discharging when at min_soc")
    akku = PVAkku(kapazitaet_wh=10000, hours=1, start_soc_prozent=20, min_soc_prozent=20, max_soc_prozent=80)
    akku.reset()
    print(f"Initial SoC: {akku.ladezustand_in_prozent()}%")

    abgegeben_wh, verlust_wh = akku.energie_abgeben(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.ladezustand_in_prozent()}%")
