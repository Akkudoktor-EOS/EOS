"""Abstract and base classes for pvforecast predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

from pydantic import Field

from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class PVForecastDataRecord(PredictionRecord):
    """Represents a pvforecast data record containing various pvforecast attributes at a specific datetime."""

    pvforecast_dc_power: Optional[float] = Field(default=None, description="Total DC power (W).")
    pvforecast_ac_power: Optional[float] = Field(default=None, description="Total AC power (W).")


class PVForecastProvider(PredictionProvider):
    """Abstract base class for pvforecast providers.

    PVForecastProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        pvforecast_provider (str): Prediction provider for pvforecast.

    Attributes:
        prediction_hours (int, optional): The number of hours into the future for which predictions are generated.
        prediction_historic_hours (int, optional): The number of past hours for which historical data is retained.
        latitude (float, optional): The latitude in degrees, must be within -90 to 90.
        longitude (float, optional): The longitude in degrees, must be within -180 to 180.
        start_datetime (datetime, optional): The starting datetime for predictions (inlcusive), defaults to the current datetime if unspecified.
        end_datetime (datetime, computed): The datetime representing the end of the prediction range (exclusive),
            calculated based on `start_datetime` and `prediction_hours`.
        keep_datetime (datetime, computed): The earliest datetime for retaining historical data (inclusive), calculated
            based on `start_datetime` and `prediction_historic_hours`.
    """

    # overload
    records: List[PVForecastDataRecord] = Field(
        default_factory=list, description="List of PVForecastDataRecord records"
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "PVForecastProvider"

    def enabled(self) -> bool:
        logger.debug(
            f"PVForecastProvider ID {self.provider_id()} vs. config {self.config.pvforecast_provider}"
        )
        return self.provider_id() == self.config.pvforecast_provider
