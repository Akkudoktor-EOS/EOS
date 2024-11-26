import numpy as np


class Gesamtlast:
    def __init__(self, prediction_hours: int = 24):
        self.lasten: dict[
            str, np.ndarray
        ] = {}  # Contains names and load arrays for different sources
        self.prediction_hours = prediction_hours

    def hinzufuegen(self, name: str, last_array: np.ndarray) -> None:
        """Adds an array of loads for a specific source.

        :param name: Name of the load source (e.g., "Household", "Heat Pump")
        :param last_array: Array of loads, where each entry corresponds to an hour
        """
        if len(last_array) != self.prediction_hours:
            raise ValueError(f"Total load inconsistent lengths in arrays: {name} {len(last_array)}")
        self.lasten[name] = last_array

    def gesamtlast_berechnen(self) -> np.ndarray:
        """Calculates the total load for each hour and returns an array of total loads.

        :return: Array of total loads, where each entry corresponds to an hour
        """
        if not self.lasten:
            return np.ndarray(0)

        # Assumption: All load arrays have the same length
        stunden = len(next(iter(self.lasten.values())))
        gesamtlast_array = [0] * stunden

        for last_array in self.lasten.values():
            gesamtlast_array = [
                gesamtlast + stundenlast
                for gesamtlast, stundenlast in zip(gesamtlast_array, last_array)
            ]

        return np.array(gesamtlast_array)
