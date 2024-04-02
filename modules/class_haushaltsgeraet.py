import numpy as np

class Haushaltsgeraet:
    def __init__(self, hours=None, verbrauch_kwh=None, dauer_h=None):
        self.hours = hours  # Gesamtzeitraum, für den die Planung erfolgt
        self.verbrauch_kwh = verbrauch_kwh  # Gesamtenergieverbrauch des Geräts in kWh
        self.dauer_h = dauer_h  # Dauer der Nutzung in Stunden
        self.lastkurve = np.zeros(self.hours)  # Initialisiere die Lastkurve mit Nullen

    def set_startzeitpunkt(self, start_hour,global_start_hour=0):
        """
        Setzt den Startzeitpunkt des Geräts und generiert eine entsprechende Lastkurve.
        :param start_hour: Die Stunde, zu der das Gerät starten soll.
        """
        self.reset()
        # Überprüfe, ob die Dauer der Nutzung innerhalb des verfügbaren Zeitraums liegt
        if start_hour + self.dauer_h > self.hours:
            raise ValueError("Die Nutzungsdauer überschreitet den verfügbaren Zeitraum.")
        if start_hour < global_start_hour:
            raise ValueError("Die Nutzungsdauer unterschreitet den verfügbaren Zeitraum.")
        
        # Berechne die Leistung pro Stunde basierend auf dem Gesamtverbrauch und der Dauer
        leistung_pro_stunde = (self.verbrauch_kwh / self.dauer_h) # Umwandlung in Wattstunde
        #print(start_hour," ",leistung_pro_stunde)
        # Setze die Leistung für die Dauer der Nutzung im Lastkurven-Array
        self.lastkurve[start_hour:start_hour + self.dauer_h] = leistung_pro_stunde

    def reset(self):
        """
        Setzt die Lastkurve zurück.
        """
        self.lastkurve = np.zeros(self.hours)

    def get_lastkurve(self):
        """
        Gibt die aktuelle Lastkurve zurück.
        """
        return self.lastkurve

    def get_last_fuer_stunde(self, hour):
        """
        Gibt die Last für eine spezifische Stunde zurück.
        :param hour: Die Stunde, für die die Last abgefragt wird.
        :return: Die Last in Watt für die angegebene Stunde.
        """
        if hour < 0 or hour >= self.hours:
            raise ValueError("Angegebene Stunde liegt außerhalb des verfügbaren Zeitraums.")
        
        return self.lastkurve[hour]

    def spaetestmoeglicher_startzeitpunkt(self):
        """
        Gibt den spätestmöglichen Startzeitpunkt zurück, an dem das Gerät noch vollständig laufen kann.
        """
        return self.hours - self.dauer_h


