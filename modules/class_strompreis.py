import json
from datetime import datetime, timedelta, timezone
import numpy as np
import json, os
from datetime import datetime
import hashlib, requests

class HourlyElectricityPriceForecast:
    def __init__(self, source, cache_dir='cache'):
        self.cache_dir = cache_dir
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        self.prices = self.load_data(source)
    
    def load_data(self, source):
        if source.startswith('http'):
            cache_filename = self.get_cache_filename(source)
            if os.path.exists(cache_filename):
                print("Lade Daten aus dem Cache...")
                with open(cache_filename, 'r') as file:
                    data = json.load(file)
            else:
                print("Lade Daten von der URL...")
                response = requests.get(source)
                if response.status_code == 200:
                    data = response.json()
                    with open(cache_filename, 'w') as file:
                        json.dump(data, file)
                else:
                    raise Exception(f"Fehler beim Abrufen der Daten: {response.status_code}")
        else:
            with open(source, 'r') as file:
                data = json.load(file)
        return data['values']
    
    def get_cache_filename(self, url):
        hash_object = hashlib.sha256(url.encode())
        hex_dig = hash_object.hexdigest()
        return os.path.join(self.cache_dir, f"cache_{hex_dig}.json")


    
    def get_price_for_date(self, date_str):
        """Gibt alle Preise für das spezifizierte Datum zurück."""
        date_prices = [entry["marketpriceEurocentPerKWh"] for entry in self.prices if date_str in entry['end']]
        return np.array(date_prices)/(1000.0*100.0)
    
    def get_price_for_daterange(self, start_date_str, end_date_str):
        """Gibt alle Preise zwischen dem Start- und Enddatum zurück."""
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        price_list = []

        while start_date <= end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            daily_prices = self.get_price_for_date(date_str)
            #print(len(self.get_price_for_date(date_str)))
            if daily_prices.size > 0:
                price_list.extend(daily_prices)
            start_date += timedelta(days=1)
        
        return np.array(price_list)



    
    # def get_price_for_hour(self, datetime_str):
        # """Gibt den Preis für die spezifizierte Stunde zurück."""
        # hour_price = [entry for entry in self.prices if datetime_str in entry['start']]
        # return hour_price[0] if hour_price else None

# # Beispiel zur Verwendung der Klasse
# filepath = '/mnt/data/strompreise_akkudokAPI.json'  # Pfad zur JSON-Datei
# strompreise = Strompreise(filepath)

# # Preise für ein spezifisches Datum erhalten
# date_str = '2024-02-25'
# prices_for_date = strompreise.get_price_for_date(date_str)
# print(f"Preise für {date_str}: {prices_for_date}")

# # Preis für eine spezifische Stunde erhalten
# datetime_str = '2024-02-25T15:00:00.000Z'
# price_for_hour = strompreise.get_price_for_hour(datetime_str)
# print(f"Preis für {datetime_str}: {price_for_hour}")





# class HourlyElectricityPriceForecast:
    # class PriceData:
        # def __init__(self, total, energy, tax, starts_at, currency, level):
            # self.total = total/1000.0
            # self.energy = energy/1000.0
            # self.tax = tax/1000.0
            # self.starts_at = datetime.strptime(starts_at, '%Y-%m-%dT%H:%M:%S.%f%z')

            # self.currency = currency
            # self.level = level

        # # Getter-Methoden
        # def get_total(self):
            # return self.total

        # def get_energy(self):
            # return self.energy

        # def get_tax(self):
            # return self.tax

        # def get_starts_at(self):
            # return self.starts_at

        # def get_currency(self):
            # return self.currency

        # def get_level(self):
            # return self.level

    # def __init__(self, filepath):
        # self.filepath = filepath
        # self.price_data = []
        # self.load_data()

    # def get_prices_for_date(self, query_date):
        # query_date = datetime.strptime(query_date, '%Y-%m-%d').date()
        # prices_for_date = [price.get_total() for price in self.price_data if price.starts_at.date() == query_date]
        
        # return np.array(prices_for_date)

    # def get_price_for_datetime(self, query_datetime):
        # query_datetime = datetime.strptime(query_datetime, '%Y-%m-%d %H').replace(minute=0, second=0, microsecond=0)
        # query_datetime = query_datetime.replace(tzinfo=timezone(timedelta(hours=1)))

        # for price in self.price_data:
            # #print(price.starts_at.replace(minute=0, second=0, microsecond=0) , "  ", query_datetime, " == ",price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime)
            # if price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime:
                # return np.array(price)
        # return None


    # def load_data(self):
        # with open(self.filepath, 'r') as file:
            # data = json.load(file)
            # for item in data['payload']:
                # self.price_data.append(self.PriceData(
                    # total=item['total'],
                    # energy=item['energy'],
                    # tax=item['tax'],
                    # starts_at=item['startsAt'],
                    # currency=item['currency'],
                    # level=item['level']
                # ))

    # def get_price_data(self):
        # return self.price_data

# # Beispiel für die Verwendung der Klasse
# if __name__ == '__main__':
    # filepath = r'..\test_data\strompreis.json'  # Pfad zur JSON-Datei anpassen
    # price_forecast = HourlyElectricityPriceForecast(filepath)
    # specific_date_prices = price_forecast.get_prices_for_date('2024-02-16')  # Datum anpassen
    
    # specific_date_prices = price_forecast.get_price_for_datetime('2024-02-16 12')
    # print(specific_date_prices)
    # #for price in price_forecast.get_price_data():
    # #    print(price.get_starts_at(), price.get_total(), price.get_currency())
