"""Abstract and base classes for load predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

from pydantic import Field, computed_field

from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class LoadDataRecord(PredictionRecord):
    """Represents a load data record containing various load attributes at a specific datetime."""

    load0_mean: Optional[float] = Field(default=None, description="Load 0 mean value (W)")
    load0_std: Optional[float] = Field(default=None, description="Load 0 standard deviation (W)")
    load1_mean: Optional[float] = Field(default=None, description="Load 1 mean value (W)")
    load1_std: Optional[float] = Field(default=None, description="Load 1 standard deviation (W)")
    load2_mean: Optional[float] = Field(default=None, description="Load 2 mean value (W)")
    load2_std: Optional[float] = Field(default=None, description="Load 2 standard deviation (W)")
    load3_mean: Optional[float] = Field(default=None, description="Load 3 mean value (W)")
    load3_std: Optional[float] = Field(default=None, description="Load 3 standard deviation (W)")
    load4_mean: Optional[float] = Field(default=None, description="Load 4 mean value (W)")
    load4_std: Optional[float] = Field(default=None, description="Load 4 standard deviation (W)")

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def load_total_mean(self) -> float:
        """Total load mean value (W)."""
        total_mean = 0.0
        for i in range(5):
            load_mean_attr = f"load{i}_mean"
            value = getattr(self, load_mean_attr)
            if value:
                total_mean += value
        return total_mean

    @computed_field  # type: ignore[prop-decorator]
    @property
    def load_total_std(self) -> float:
        """Total load standard deviation (W)."""
        total_std = 0.0
        for i in range(5):
            load_std_attr = f"load{i}_std"
            value = getattr(self, load_std_attr)
            if value:
                total_std += value
        return total_std


class LoadProvider(PredictionProvider):
    """Abstract base class for load providers.

    LoadProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        load_provider (str): Prediction provider for load.

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
    records: List[LoadDataRecord] = Field(
        default_factory=list, description="List of LoadDataRecord records"
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "LoadProvider"

    def enabled(self) -> bool:
        logger.debug(
            f"LoadProvider ID {self.provider_id()} vs. config {self.config.load_providers}"
        )
        return self.provider_id() == self.config.load_providers

    def loads(self) -> List[str]:
        """Returns a list of key prefixes of the loads managed by this provider."""
        loads_prefix = []
        for i in range(self.config.load_count):
            load_provider_attr = f"load{i}_provider"
            value = getattr(self.config, load_provider_attr)
            if value == self.provider_id():
                loads_prefix.append(f"load{i}")
        return loads_prefix
