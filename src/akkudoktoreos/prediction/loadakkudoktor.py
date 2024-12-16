"""Retrieves load forecast data from Akkudoktor load profiles."""

from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class LoadAkkudoktorCommonSettings(SettingsBaseModel):
    """Common settings for load data import from file."""

    loadakkudoktor_year_energy: Optional[float] = Field(
        default=None, description="Yearly energy consumption (kWh)."
    )


class LoadAkkudoktor(LoadProvider):
    """Fetch Load forecast data from Akkudoktor load profiles."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the LoadAkkudoktor provider."""
        return "LoadAkkudoktor"

    def load_data(self) -> np.ndarray:
        """Loads data from the Akkudoktor load file."""
        load_file = Path(__file__).parent.parent.joinpath("data/load_profiles.npz")
        data_year_energy = None
        try:
            file_data = np.load(load_file)
            profile_data = np.array(
                list(zip(file_data["yearly_profiles"], file_data["yearly_profiles_std"]))
            )
            # Calculate values in W by relative profile data and yearly consumption given in kWh
            data_year_energy = profile_data * self.config.loadakkudoktor_year_energy * 1000
        except FileNotFoundError:
            error_msg = f"Error: File {load_file} not found."
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        except Exception as e:
            error_msg = f"An error occurred while loading data: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        return data_year_energy

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Adds the load means and standard deviations."""
        data_year_energy = self.load_data()
        date = self.start_datetime
        for i in range(self.config.prediction_hours):
            # Extract mean and standard deviation for the given day and hour
            # Day indexing starts at 0, -1 because of that
            hourly_stats = data_year_energy[date.day_of_year - 1, :, date.hour]
            self.update_value(date, "load_mean", hourly_stats[0])
            self.update_value(date, "load_std", hourly_stats[1])
            date += to_duration("1 hour")
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.timezone)
