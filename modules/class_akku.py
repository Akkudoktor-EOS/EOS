import numpy as np
class PVAkku:
    def __init__(self, kapazitaet_wh, hours, lade_effizienz=0.9, entlade_effizienz=0.9):
        # Kapazität des Akkus in Wh
        self.kapazitaet_wh = kapazitaet_wh
        # Initialer Ladezustand des Akkus in Wh
        self.soc_wh = 0
        self.hours = hours
        self.discharge_array = np.full(self.hours, 1)
        # Lade- und Entladeeffizienz
        self.lade_effizienz = lade_effizienz
        self.entlade_effizienz = entlade_effizienz        

    def reset(self):
        self.soc_wh = 0
        self.discharge_array = np.full(self.hours, 1)
        
    def set_discharge_per_hour(self, discharge_array):
        assert(len(discharge_array) == self.hours)
        self.discharge_array = discharge_array

    def ladezustand_in_prozent(self):
        return (self.soc_wh / self.kapazitaet_wh) * 100

    def energie_abgeben(self, wh, hour):
        if self.discharge_array[hour] == 0:
            return 0.0
        soc_tmp = self.soc_wh
        # Berechnung der tatsächlichen Entlademenge unter Berücksichtigung der Entladeeffizienz
        effektive_entlademenge = wh / self.entlade_effizienz
        # Aktualisierung des Ladezustands ohne negativ zu werden
        self.soc_wh = max(self.soc_wh - effektive_entlademenge, 0)
        return soc_tmp-self.soc_wh
        
        # if self.soc_wh >= wh:
            # self.soc_wh -= wh
            # return wh
        # else:
            # abgegebene_energie = self.soc_wh
            # self.soc_wh = 0
            # return abgegebene_energie

    def energie_laden(self, wh):
        # Berechnung der tatsächlichen Lademenge unter Berücksichtigung der Ladeeffizienz
        effektive_lademenge = wh * self.lade_effizienz
        # Aktualisierung des Ladezustands ohne die Kapazität zu überschreiten
        self.soc_wh = min(self.soc_wh + effektive_lademenge, self.kapazitaet_wh)




if __name__ == '__main__':
    # Beispiel zur Nutzung der Klasse
    akku = PVAkku(10000) # Ein Akku mit 10.000 Wh Kapazität
    print(f"Initialer Ladezustand: {akku.ladezustand_in_prozent()}%")

    akku.energie_laden(5000)
    print(f"Ladezustand nach Laden: {akku.ladezustand_in_prozent()}%, Aktueller Energieinhalt: {akku.aktueller_energieinhalt()} Wh")

    abgegebene_energie_wh = akku.energie_abgeben(3000)
    print(f"Abgegebene Energie: {abgegebene_energie_wh} Wh, Ladezustand danach: {akku.ladezustand_in_prozent()}%, Aktueller Energieinhalt: {akku.aktueller_energieinhalt()} Wh")

    akku.energie_laden(6000)
    print(f"Ladezustand nach weiterem Laden: {akku.ladezustand_in_prozent()}%, Aktueller Energieinhalt: {akku.aktueller_energieinhalt()} Wh")
