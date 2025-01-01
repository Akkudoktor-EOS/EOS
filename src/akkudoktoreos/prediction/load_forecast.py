from datetime import datetime
from pathlib import Path

import numpy as np

# Load the .npz file when the application starts


class LoadForecast:
    def __init__(self, filepath: str | Path, year_energy: float):
        self.filepath = filepath
        self.year_energy = year_energy
        self.load_data()

    def get_daily_stats(self, date_str: str) -> np.ndarray:
        """Returns the 24-hour profile with mean and standard deviation for a given date.

        :param date_str: Date as a string in the format "YYYY-MM-DD"
        :return: An array with shape (2, 24), contains means and standard deviations
        """
        # Convert the date string into a datetime object
        date = self._convert_to_datetime(date_str)

        # Calculate the day of the year (1 to 365)
        day_of_year = date.timetuple().tm_yday

        # Extract the 24-hour profile for the given date
        daily_stats = self.data_year_energy[day_of_year - 1]  # -1 because indexing starts at 0
        return daily_stats

    def get_hourly_stats(self, date_str: str, hour: int) -> np.ndarray:
        """Returns the mean and standard deviation for a specific hour of a given date.

        :param date_str: Date as a string in the format "YYYY-MM-DD"
        :param hour: Specific hour (0 to 23)
        :return: An array with shape (2,), contains mean and standard deviation for the specified hour
        """
        # Convert the date string into a datetime object
        date = self._convert_to_datetime(date_str)

        # Calculate the day of the year (1 to 365)
        day_of_year = date.timetuple().tm_yday

        # Extract mean and standard deviation for the given hour
        hourly_stats = self.data_year_energy[day_of_year - 1, :, hour]  # Access the specific hour

        return hourly_stats

    def get_stats_for_date_range(self, start_date_str: str, end_date_str: str) -> np.ndarray:
        """Returns the means and standard deviations for a date range.

        :param start_date_str: Start date as a string in the format "YYYY-MM-DD"
        :param end_date_str: End date as a string in the format "YYYY-MM-DD"
        :return: An array with aggregated data for the date range
        """
        start_date = self._convert_to_datetime(start_date_str)
        end_date = self._convert_to_datetime(end_date_str)

        start_day_of_year = start_date.timetuple().tm_yday
        end_day_of_year = end_date.timetuple().tm_yday

        # Note that in leap years, the day of the year may need adjustment
        stats_for_range = self.data_year_energy[
            start_day_of_year:end_day_of_year
        ]  # -1 because indexing starts at 0
        stats_for_range = stats_for_range.swapaxes(1, 0)

        stats_for_range = stats_for_range.reshape(stats_for_range.shape[0], -1)
        return stats_for_range

    def load_data(self) -> None:
        """Loads data from the specified file."""
        try:
            data = np.load(self.filepath)
            self.data = np.array(list(zip(data["yearly_profiles"], data["yearly_profiles_std"])))
            self.data_year_energy = self.data * self.year_energy
            # pprint(self.data_year_energy)
        except FileNotFoundError:
            print(f"Error: File {self.filepath} not found.")
        except Exception as e:
            print(f"An error occurred while loading data: {e}")

    def get_price_data(self) -> None:
        """Returns price data (currently not implemented)."""
        raise NotImplementedError
        # return self.price_data

    def _convert_to_datetime(self, date_str: str) -> datetime:
        """Converts a date string to a datetime object."""
        return datetime.strptime(date_str, "%Y-%m-%d")


# Example usage of the class
if __name__ == "__main__":
    filepath = Path(__file__).parent.parent / "data" / "load_profiles.npz"
    lf = LoadForecast(filepath=filepath, year_energy=2000)
    specific_date_prices = lf.get_daily_stats("2024-02-16")  # Adjust date as needed
    specific_hour_stats = lf.get_hourly_stats("2024-02-16", 12)  # Adjust date and hour as needed
    print(specific_hour_stats)
