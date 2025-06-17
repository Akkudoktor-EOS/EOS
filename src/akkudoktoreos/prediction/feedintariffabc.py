"""Abstract and base classes for feed in tariff predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

from pydantic import Field, computed_field

from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord


class FeedInTariffDataRecord(PredictionRecord):
    """Represents a feed in tariff data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    feed_in_tariff_wh: Optional[float] = Field(None, description="Feed in tariff per Wh (€/Wh)")

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def feed_in_tariff_kwh(self) -> Optional[float]:
        """Feed in tariff per kWh (€/kWh).

        Convenience attribute calculated from `feed_in_tariff_wh`.
        """
        if self.feed_in_tariff_wh is None:
            return None
        return self.feed_in_tariff_wh * 1000.0


class FeedInTariffProvider(PredictionProvider):
    """Abstract base class for feed in tariff providers.

    FeedInTariffProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        feed in tariff_provider (str): Prediction provider for feed in tarif.
    """

    # overload
    records: List[FeedInTariffDataRecord] = Field(
        default_factory=list, description="List of FeedInTariffDataRecord records"
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "FeedInTariffProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.feedintariff.provider
