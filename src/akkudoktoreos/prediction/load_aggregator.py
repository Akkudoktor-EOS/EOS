from collections import defaultdict
from collections.abc import Sequence


class LoadAggregator:
    def __init__(self, prediction_hours: int = 24) -> None:
        """Initializes the LoadAggregator object with the number of prediction hours.

        :param prediction_hours: Number of hours to predict (default: 24)
        """
        self.loads: defaultdict[str, list[float]] = defaultdict(
            list
        )  # Dictionary to hold load arrays for different sources
        self.prediction_hours: int = prediction_hours

    def add_load(self, name: str, last_array: Sequence[float]) -> None:
        """Adds a load array for a specific source. Accepts a Sequence of floats.

        :param name: Name of the load source (e.g., "Household", "Heat Pump").
        :param last_array: Sequence of loads, where each entry corresponds to an hour.
        :raises ValueError: If the length of last_array doesn't match the prediction hours.
        """
        # Check length of the array without converting
        if len(last_array) != self.prediction_hours:
            raise ValueError(f"Total load inconsistent lengths in arrays: {name} {len(last_array)}")
        self.loads[name] = list(last_array)

    def calculate_total_load(self) -> list[float]:
        """Calculates the total load for each hour by summing up the loads from all sources.

        :return: A list representing the total load for each hour.
                 Returns an empty list if no loads have been added.
        """
        # Optimize the summation using a single loop with zip
        total_load = [sum(hourly_loads) for hourly_loads in zip(*self.loads.values())]

        return total_load
