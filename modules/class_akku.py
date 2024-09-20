import numpy as np

class PVAkku:
    def __init__(self, kapazitaet_wh=None, hours=None, lade_effizienz=0.88, entlade_effizienz=0.88,
                 max_ladeleistung_w=None, start_soc_prozent=0, min_soc_prozent=0, max_soc_prozent=100):
        # Battery capacity in Wh
        self.kapazitaet_wh = kapazitaet_wh
        # Initial state of charge in Wh
        self.start_soc_prozent = start_soc_prozent
        self.soc_wh = (start_soc_prozent / 100) * kapazitaet_wh
        self.hours = hours
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        # Charge and discharge efficiency
        self.lade_effizienz = lade_effizienz
        self.entlade_effizienz = entlade_effizienz        
        self.max_ladeleistung_w = max_ladeleistung_w if max_ladeleistung_w else self.kapazitaet_wh
        self.min_soc_prozent = min_soc_prozent
        self.max_soc_prozent = max_soc_prozent

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
        
        # Calculate the maximum discharge amount considering discharge efficiency
        max_abgebbar_wh = self.soc_wh * self.entlade_effizienz
        
        # Consider the maximum discharge power of the battery
        max_abgebbar_wh = min(max_abgebbar_wh, self.max_ladeleistung_w)

        # The actually discharged energy cannot exceed requested energy or maximum discharge
        tatsaechlich_abgegeben_wh = min(wh, max_abgebbar_wh)
        
        # Calculate the actual amount withdrawn from the battery (before efficiency loss)
        tatsaechliche_entnahme_wh = tatsaechlich_abgegeben_wh / self.entlade_effizienz
        
        # Update the state of charge considering the actual withdrawal
        self.soc_wh -= tatsaechliche_entnahme_wh
        
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

        # Calculate the actual charging amount considering charging efficiency
        effektive_lademenge = min(wh, effektive_ladeleistung) 

        # Update the state of charge without exceeding capacity
        geladene_menge_ohne_verlust = min(self.kapazitaet_wh - self.soc_wh, effektive_lademenge)

        geladene_menge = geladene_menge_ohne_verlust * self.lade_effizienz

        self.soc_wh += geladene_menge
    
        verluste_wh = geladene_menge_ohne_verlust * (1.0 - self.lade_effizienz)

        return geladene_menge, verluste_wh

    def aktueller_energieinhalt(self):
        """
        This method returns the current remaining energy considering efficiency.
        It accounts for both charging and discharging efficiency.
        """
        # Calculate remaining energy considering discharge efficiency
        nutzbare_energie = self.soc_wh * self.entlade_effizienz
        return nutzbare_energie

    # def energie_laden(self, wh, hour):
    #     if hour is not None and self.charge_array[hour] == 0:
    #         return 0, 0  # Charging not allowed in this hour

    #     # If no value for wh is given, use the maximum charging power
    #     wh = wh if wh is not None else self.max_ladeleistung_w

    #     # Calculate the actual charging amount considering charging efficiency
    #     effective_charging_amount = min(wh, self.max_ladeleistung_w) 

    #     # Update the state of charge without exceeding capacity
    #     charged_amount_without_loss = min(self.kapazitaet_wh - self.soc_wh, effective_charging_amount)

    #     charged_amount = charged_amount_without_loss * self.lade_effizienz

    #     self.soc_wh += charged_amount
    
    #     losses_wh = charged_amount_without_loss * (1.0 - self.lade_effizienz)

    #     return charged_amount, losses_wh


if __name__ == '__main__':
    # Example of using the class
    akku = PVAkku(10000)  # A battery with 10,000 Wh capacity
    print(f"Initial state of charge: {akku.ladezustand_in_prozent()}%")

    akku.energie_laden(5000)
    print(f"State of charge after charging: {akku.ladezustand_in_prozent()}%, Current energy content: {akku.aktueller_energieinhalt()} Wh")

    abgegebene_energie_wh = akku.energie_abgeben(3000)
    print(f"Discharged energy: {abgegebene_energie_wh} Wh, State of charge afterwards: {akku.ladezustand_in_prozent()}%, Current energy content: {akku.aktueller_energieinhalt()} Wh")

    akku.energie_laden(6000)
    print(f"State of charge after further charging: {akku.ladezustand_in_prozent()}%, Current energy content: {akku.aktueller_energieinhalt()} Wh")
