import json
from datetime import datetime, timedelta, timezone
import numpy as np
from pprint import pprint

class Gesamtlast:
    def __init__(self, prediction_hours=24):
        self.lasten = {}  # Enthält Namen und Lasten-Arrays für verschiedene Quellen
        self.prediction_hours=prediction_hours
        
    def hinzufuegen(self, name, last_array):
        """
        Fügt ein Array von Lasten für eine bestimmte Quelle hinzu.
        
        :param name: Name der Lastquelle (z.B. "Haushalt", "Wärmepumpe")
        :param last_array: Array von Lasten, wobei jeder Eintrag einer Stunde entspricht
        """
        if(len(last_array) != self.prediction_hours):
                raise ValueError(f"Gesamtlast Inkonsistente Längen bei den Arrays: ", name," ", len(last_array)  ) 
        self.lasten[name] = last_array
    
    
    def gesamtlast_berechnen(self):
        """
        Berechnet die gesamte Last für jede Stunde und gibt ein Array der Gesamtlasten zurück.
        
        :return: Array der Gesamtlasten, wobei jeder Eintrag einer Stunde entspricht
        """
        if not self.lasten:
            return []
        
        # Annahme: Alle Lasten-Arrays haben die gleiche Länge
        stunden = len(next(iter(self.lasten.values())))
        gesamtlast_array = [0] * stunden
        for last_array in self.lasten.values():
            
            gesamtlast_array = [gesamtlast + stundenlast for gesamtlast, stundenlast in zip(gesamtlast_array, last_array)]
        
        return np.array(gesamtlast_array)
