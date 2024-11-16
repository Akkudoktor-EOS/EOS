"""Abstract and base classes for electricity price predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

from pydantic import Field

from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class ElecPriceDataRecord(PredictionRecord):
    """Represents a electricity price data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    elecprice_marketprice: Optional[float] = Field(
        None, description="Electricity market price (€/KWh)"
    )


class ElecPriceProvider(PredictionProvider):
    """Abstract base class for electricity price providers.

    WeatherProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        electricity price_provider (str): Prediction provider for electricity price.

    Attributes:
        prediction_hours (int, optional): The number of hours into the future for which predictions are generated.
        prediction_historic_hours (int, optional): The number of past hours for which historical data is retained.
        latitude (float, optional): The latitude in degrees, must be within -90 to 90.
        longitude (float, optional): The longitude in degrees, must be within -180 to 180.
        start_datetime (datetime, optional): The starting datetime for predictions, defaults to the current datetime if unspecified.
        end_datetime (datetime, computed): The datetime representing the end of the prediction range,
            calculated based on `start_datetime` and `prediction_hours`.
        keep_datetime (datetime, computed): The earliest datetime for retaining historical data, calculated
            based on `start_datetime` and `prediction_historic_hours`.
    """

    # overload
    records: List[ElecPriceDataRecord] = Field(
        default_factory=list, description="List of ElecPriceDataRecord records"
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "ElecPriceProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.elecprice_provider
