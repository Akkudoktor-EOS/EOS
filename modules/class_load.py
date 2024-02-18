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
        

    # def get_prices_for_date(self, query_date):
        # query_date = datetime.strptime(query_date, '%Y-%m-%d').date()
        # prices_for_date = [price for price in self.price_data if price.starts_at.date() == query_date]
        # return prices_for_date

    # def get_price_for_datetime(self, query_datetime):
        # query_datetime = datetime.strptime(query_datetime, '%Y-%m-%d %H').replace(minute=0, second=0, microsecond=0)
        # query_datetime = query_datetime.replace(tzinfo=timezone(timedelta(hours=1)))

        # for price in self.price_data:
            # #print(price.starts_at.replace(minute=0, second=0, microsecond=0) , "  ", query_datetime, " == ",price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime)
            # if price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime:
                # return price
        # return None
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



    def load_data(self):
        with open(self.filepath, 'r') as file:
            data = np.load(self.filepath)
            self.data = np.array(list(zip(data["yearly_profiles"],data["yearly_profiles_std"])))
            self.data_year_energy = self.data * self.year_energy
            pprint(self.data_year_energy)

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
