import json
from datetime import datetime, timedelta, timezone

class HourlyElectricityPriceForecast:
    class PriceData:
        def __init__(self, total, energy, tax, starts_at, currency, level):
            self.total = total
            self.energy = energy
            self.tax = tax
            self.starts_at = datetime.strptime(starts_at, '%Y-%m-%dT%H:%M:%S.%f%z')

            self.currency = currency
            self.level = level

        # Getter-Methoden
        def get_total(self):
            return self.total

        def get_energy(self):
            return self.energy

        def get_tax(self):
            return self.tax

        def get_starts_at(self):
            return self.starts_at

        def get_currency(self):
            return self.currency

        def get_level(self):
            return self.level

    def __init__(self, filepath):
        self.filepath = filepath
        self.price_data = []
        self.load_data()

    def get_prices_for_date(self, query_date):
        query_date = datetime.strptime(query_date, '%Y-%m-%d').date()
        prices_for_date = [price for price in self.price_data if price.starts_at.date() == query_date]
        return prices_for_date

    def get_price_for_datetime(self, query_datetime):
        query_datetime = datetime.strptime(query_datetime, '%Y-%m-%d %H').replace(minute=0, second=0, microsecond=0)
        query_datetime = query_datetime.replace(tzinfo=timezone(timedelta(hours=1)))

        for price in self.price_data:
            #print(price.starts_at.replace(minute=0, second=0, microsecond=0) , "  ", query_datetime, " == ",price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime)
            if price.starts_at.replace(minute=0, second=0, microsecond=0) == query_datetime:
                return price
        return None


    def load_data(self):
        with open(self.filepath, 'r') as file:
            data = json.load(file)
            for item in data['payload']:
                self.price_data.append(self.PriceData(
                    total=item['total'],
                    energy=item['energy'],
                    tax=item['tax'],
                    starts_at=item['startsAt'],
                    currency=item['currency'],
                    level=item['level']
                ))

    def get_price_data(self):
        return self.price_data

# Beispiel für die Verwendung der Klasse
if __name__ == '__main__':
    filepath = r'..\test_data\strompreis.json'  # Pfad zur JSON-Datei anpassen
    price_forecast = HourlyElectricityPriceForecast(filepath)
    specific_date_prices = price_forecast.get_prices_for_date('2024-02-16')  # Datum anpassen
    
    specific_date_prices = price_forecast.get_price_for_datetime('2024-02-16 12')
    print(specific_date_prices)
    #for price in price_forecast.get_price_data():
    #    print(price.get_starts_at(), price.get_total(), price.get_currency())
