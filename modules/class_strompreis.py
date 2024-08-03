import json
from datetime import datetime, timedelta, timezone
import numpy as np
import json, os
from datetime import datetime
import hashlib, requests
import pytz

# Beispiel: Umwandlung eines UTC-Zeitstempels in lokale Zeit
utc_time = datetime.strptime('2024-03-28T01:00:00.000Z', '%Y-%m-%dT%H:%M:%S.%fZ')
utc_time = utc_time.replace(tzinfo=pytz.utc)

# Ersetzen Sie 'Europe/Berlin' mit Ihrer eigenen Zeitzone
local_time = utc_time.astimezone(pytz.timezone('Europe/Berlin'))
print(local_time)

def repeat_to_shape(array, target_shape):
    # Prüfen , ob das Array in die Zielgröße passt
    if len(target_shape) != array.ndim:
        raise ValueError("Array and target shape must have the same number of dimensions")

    # die Anzahl der Wiederholungen pro Dimension
    repeats = tuple(target_shape[i] // array.shape[i] for i in range(array.ndim))

    #  np.tile, um das Array zu erweitern
    expanded_array = np.tile(array, repeats)
    return expanded_array



class HourlyElectricityPriceForecast:
    def __init__(self, source, cache_dir='cache', abgaben=0.000228, prediction_hours=24): #228
        self.cache_dir = cache_dir
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        self.cache_time_file = os.path.join(self.cache_dir, 'cache_timestamp.txt')
        self.prices = self.load_data(source)
        self.abgaben = abgaben
        self.prediction_hours = prediction_hours
    
    def load_data(self, source):
        cache_filename = self.get_cache_filename(source)
        if source.startswith('http'):
            if os.path.exists(cache_filename) and not self.is_cache_expired():
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
                    self.update_cache_timestamp()
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
    
    def is_cache_expired(self):
        if not os.path.exists(self.cache_time_file):
            return True
        with open(self.cache_time_file, 'r') as file:
            timestamp_str = file.read()
            last_cache_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        return datetime.now() - last_cache_time > timedelta(hours=1)
    
    def update_cache_timestamp(self):
        with open(self.cache_time_file, 'w') as file:
            file.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


    
    def get_price_for_date(self, date_str):
        """Gibt alle Preise für das spezifizierte Datum zurück."""
        #date_prices = [entry["marketpriceEurocentPerKWh"]+self.abgaben for entry in self.prices if date_str in entry['end']]
        """Gibt alle Preise für das spezifizierte Datum zurück, inklusive des Preises von 0:00 des vorherigen Tages."""
        # Datumskonversion von String zu datetime-Objekt
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        
        # Berechnung des Vortages
        previous_day = date_obj - timedelta(days=1)
        previous_day_str = previous_day.strftime('%Y-%m-%d')
        
        # Extrahieren des Preises von 0:00 des vorherigen Tages
        last_price_of_previous_day = [entry["marketpriceEurocentPerKWh"]+self.abgaben for entry in self.prices if previous_day_str in entry['end']][-1]
        
        # Extrahieren aller Preise für das spezifizierte Datum
        date_prices = [entry["marketpriceEurocentPerKWh"]+self.abgaben for entry in self.prices if date_str in entry['end']]
        print("getPRice:",len(date_prices))
        
        # Hinzufügen des letzten Preises des vorherigen Tages am Anfang der Liste
        if len(date_prices) == 23:
                date_prices.insert(0, last_price_of_previous_day)

        return np.array(date_prices)/(1000.0*100.0) + self.abgaben
    
    def get_price_for_daterange(self, start_date_str, end_date_str):
        print(start_date_str)
        print(end_date_str)
        """Gibt alle Preise zwischen dem Start- und Enddatum zurück."""
        start_date_utc = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=pytz.utc)
        end_date_utc = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=pytz.utc)
        start_date = start_date_utc.astimezone(pytz.timezone('Europe/Berlin'))
        end_date = end_date_utc.astimezone(pytz.timezone('Europe/Berlin'))

        
        price_list = []

        while start_date < end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            daily_prices = self.get_price_for_date(date_str)
            print(date_str," ",daily_prices)
            print(len(self.get_price_for_date(date_str)))
            
            if daily_prices.size ==24:
                price_list.extend(daily_prices)
            start_date += timedelta(days=1)
        
        if self.prediction_hours>0:
            price_list = repeat_to_shape(np.array(price_list),(self.prediction_hours,))        
        return price_list
