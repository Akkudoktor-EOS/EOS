import numpy as np
class PVAkku:
    def __init__(self, kapazitaet_wh=None, hours=None, lade_effizienz=0.9, entlade_effizienz=0.9,max_ladeleistung_w=None,start_soc_prozent=0):
        # Kapazität des Akkus in Wh
        self.kapazitaet_wh = kapazitaet_wh
        # Initialer Ladezustand des Akkus in Wh
        self.start_soc_prozent = start_soc_prozent
        self.soc_wh = (start_soc_prozent / 100) * kapazitaet_wh
        self.hours = hours
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        # Lade- und Entladeeffizienz
        self.lade_effizienz = lade_effizienz
        self.entlade_effizienz = entlade_effizienz        
        self.max_ladeleistung_w = max_ladeleistung_w if max_ladeleistung_w else self.kapazitaet_wh


    def reset(self):
        self.soc_wh = (self.start_soc_prozent / 100) * self.kapazitaet_wh
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        
    def set_discharge_per_hour(self, discharge_array):
        assert(len(discharge_array) == self.hours)
        self.discharge_array = discharge_array

    def set_charge_per_hour(self, charge_array):
        assert(len(charge_array) == self.hours)
        self.charge_array = charge_array

    def ladezustand_in_prozent(self):
        return (self.soc_wh / self.kapazitaet_wh) * 100

    def energie_abgeben(self, wh, hour):
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0  # Keine Energieabgabe und keine Verluste
        
        # Berechnung der maximal abgebenden Energiemenge unter Berücksichtigung der Entladeeffizienz
        max_abgebbar_wh = self.soc_wh * self.entlade_effizienz
        
        # Tatsächlich abgegebene Energie darf nicht mehr sein als angefragt und nicht mehr als maximal abgebbar
        tatsaechlich_abgegeben_wh = min(wh, max_abgebbar_wh)
        
        # Berechnung der tatsächlichen Entnahmemenge aus dem Akku (vor Effizienzverlust)
        tatsaechliche_entnahme_wh = tatsaechlich_abgegeben_wh / self.entlade_effizienz
        
        # Aktualisierung des Ladezustands unter Berücksichtigung der tatsächlichen Entnahmemenge
        self.soc_wh -= tatsaechliche_entnahme_wh
        
        # Berechnung der Verluste durch die Effizienz
        verluste_wh = tatsaechliche_entnahme_wh - tatsaechlich_abgegeben_wh
        
        # Rückgabe der tatsächlich abgegebenen Energiemenge und der Verluste
        return tatsaechlich_abgegeben_wh, verluste_wh

        # return soc_tmp-self.soc_wh

    def energie_laden(self, wh, hour):
        if hour is not None and self.charge_array[hour] == 0:
            return 0,0  # Ladevorgang in dieser Stunde nicht erlaubt

        # Wenn kein Wert für wh angegeben wurde, verwende die maximale Ladeleistung
        wh = wh if wh is not None else self.max_ladeleistung_w

        # Berechnung der tatsächlichen Lademenge unter Berücksichtigung der Ladeeffizienz
        effektive_lademenge = min(wh, self.max_ladeleistung_w) * self.lade_effizienz

        # Aktualisierung des Ladezustands ohne die Kapazität zu überschreiten
        geladene_menge = min(self.kapazitaet_wh - self.soc_wh, effektive_lademenge)
        self.soc_wh += geladene_menge
    
        verluste_wh = geladene_menge* (1.0-self.lade_effizienz)
        
        return geladene_menge, verluste_wh
        # effektive_lademenge = wh * self.lade_effizienz
        # self.soc_wh = min(self.soc_wh + effektive_lademenge, self.kapazitaet_wh)




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
