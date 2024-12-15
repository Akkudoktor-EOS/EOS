from typing import Dict, List, Tuple, Union

import numpy as np


class LoadAggregator:
    def __init__(self, prediction_hours: int = 24) -> None:
        """Initializes the LoadAggregator object with the number of prediction hours.

        :param prediction_hours: Number of hours to predict (default: 24)
        """
        self.loads: Dict[
            str, List[float]
        ] = {}  # Dictionary to hold load arrays for different sources
        self.prediction_hours: int = prediction_hours

    def add_load(self, name: str, last_array: Union[List[float], Tuple[float, ...]]) -> None:
        """Adds a load array for a specific source. Accepts either a Python list or tuple.

        :param name: Name of the load source (e.g., "Household", "Heat Pump").
        :param last_array: List or tuple of loads, where each entry corresponds to an hour.
        :raises ValueError: If the length of last_array doesn't match the prediction hours.
        """
        # Check length of the array without converting
        if len(last_array) != self.prediction_hours:
            raise ValueError(f"Total load inconsistent lengths in arrays: {name} {len(last_array)}")
        self.loads[name] = list(last_array)

    def calculate_total_load(self) -> List[float]:
        """Calculates the total load for each hour by summing up the loads from all sources.

        :return: A list representing the total load for each hour.
                 Returns an empty list if no loads have been added.
        """
        if not self.loads:
            return []  # Return empty list if no loads are present

        # Stack all load arrays vertically and sum them across the vertical axis (axis=0)
        LoadAggregator_array = np.sum(
            [np.array(loads_array) for loads_array in self.loads.values()], axis=0
        )

        return LoadAggregator_array.tolist()
