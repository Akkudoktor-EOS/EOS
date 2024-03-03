import json
from datetime import datetime, timedelta, timezone
import numpy as np
from pprint import pprint

# Lade die .npz-Datei beim Start der Anwendung

class LoadForecast:
    def __init__(self, filepath=None, year_energy=None):
        self.filepath = filepath
        self.data = None
        self.data_year_energy = None
        self.year_energy = year_energy
        self.load_data()

    def get_daily_stats(self, date_str):
        """
        Gibt den 24-Stunden-Verlauf mit Erwartungswert und Standardabweichung für ein gegebenes Datum zurück.
        
        :param data: NumPy Array mit Shape (365, 2, 24), repräsentiert Daten für ein Jahr
        :param date_str: Datum als String im Format "YYYY-MM-DD"
        :return: Ein Array mit Shape (2, 24), enthält Erwartungswerte und Standardabweichungen
        """
        # Umwandlung des Datums-Strings in ein datetime-Objekt
        date = datetime.strptime(date_str, "%Y-%m-%d")
        
        # Berechnung des Tages des Jahres (1 bis 365)
        day_of_year = date.timetuple().tm_yday
        
        # Extraktion des 24-Stunden-Verlaufs für das gegebene Datum
        daily_stats = self.data_year_energy[day_of_year - 1]  # -1, da die Indizierung bei 0 beginnt
        return daily_stats

    def get_hourly_stats(self, date_str, hour):
        """
        Gibt Erwartungswert und Standardabweichung für eine spezifische Stunde eines gegebenen Datums zurück.
        
        :param data: NumPy Array mit Shape (365, 2, 24), repräsentiert Daten für ein Jahr
        :param date_str: Datum als String im Format "YYYY-MM-DD"
        :param hour: Spezifische Stunde (0 bis 23)
        :return: Ein Array mit Shape (2,), enthält Erwartungswert und Standardabweichung für die spezifizierte Stunde
        """
        # Umwandlung des Datums-Strings in ein datetime-Objekt
        date = datetime.strptime(date_str, "%Y-%m-%d")
        
        # Berechnung des Tages des Jahres (1 bis 365)
        day_of_year = date.timetuple().tm_yday
        
        # Extraktion von Erwartungswert und Standardabweichung für die gegebene Stunde
        hourly_stats = self.data_year_energy[day_of_year - 1, :, hour]  # Zugriff auf die spezifische Stunde
        
        return hourly_stats

    def get_stats_for_date_range(self, start_date_str, end_date_str):
        """
        Gibt die Erwartungswerte und Standardabweichungen für einen Zeitraum zurück.

        :param start_date_str: Startdatum als String im Format "YYYY-MM-DD"
        :param end_date_str: Enddatum als String im Format "YYYY-MM-DD"
        :return: Ein Array mit den aggregierten Daten für den Zeitraum
        """
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        
        start_day_of_year = start_date.timetuple().tm_yday
        end_day_of_year = end_date.timetuple().tm_yday

        # Beachten, dass bei Schaltjahren der Tag des Jahres angepasst werden muss
        stats_for_range = self.data_year_energy[start_day_of_year-1:end_day_of_year]  # -1 da die Indizierung bei 0 beginnt
        
        # Hier kannst du entscheiden, wie du die Daten über den Zeitraum aggregieren möchtest
        # Zum Beispiel könntest du Mittelwerte, Summen oder andere Statistiken über diesen Zeitraum berechnen
        return stats_for_range



    def load_data(self):
        with open(self.filepath, 'r') as file:
            data = np.load(self.filepath)
            self.data = np.array(list(zip(data["yearly_profiles"],data["yearly_profiles_std"])))
            self.data_year_energy = self.data * self.year_energy
            #pprint(self.data_year_energy)

    def get_price_data(self):
        # load_profiles_exp_l = load_profiles_exp*year_energy
        # load_profiles_std_l = load_profiles_std*year_energy

        return self.price_data

# Beispiel für die Verwendung der Klasse
if __name__ == '__main__':
    filepath = r'..\load_profiles.npz'  # Pfad zur JSON-Datei anpassen
    lf = LoadForecast(filepath=filepath, year_energy=2000)
    #load_forecast = lf.get_price_data
    #
    #price_forecast = HourlyElectricityPriceForecast(filepath)
    specific_date_prices = lf.get_daily_stats('2024-02-16')  # Datum anpassen
    specific_date_prices = lf.get_hourly_stats('2024-02-16', 12)  # Datum anpassen
    print(specific_date_prices)
    #for price in price_forecast.get_price_data():
    #    print(price.get_starts_at(), price.get_total(), price.get_currency())
