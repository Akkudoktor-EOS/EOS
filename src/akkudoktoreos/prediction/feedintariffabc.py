"""Abstract and base classes for feed in tariff predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

import pandas as pd
from pydantic import Field, computed_field

from akkudoktoreos.prediction.predictionabc import PredictionRecord
from akkudoktoreos.prediction.priceabc import PricePredictionProviderBase


class FeedInTariffDataRecord(PredictionRecord):
    """Represents a feed in tariff data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    feed_in_tariff_raw_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "Raw feed in tariff per Wh, always excluding fees [amount/Wh]"
        },
    )

    feed_in_tariff_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "Feed in tariff per Wh, including fees if configured [amount/Wh]"
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def feed_in_tariff_kwh(self) -> Optional[float]:
        """Feed in tariff per kWh [amount/kWh].

        Convenience attribute calculated from `feed_in_tariff_wh`.
        """
        if self.feed_in_tariff_wh is None:
            return None
        return self.feed_in_tariff_wh * 1000.0


class FeedInTariffProvider(PricePredictionProviderBase):
    """Abstract base class for feed in tariff providers.

    FeedInTariffProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        feedintariff.provider (str): Prediction provider for feed in tarif.
    """

    # overload
    records: List[FeedInTariffDataRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of FeedInTariffDataRecord records"},
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "FeedInTariffProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.feedintariff.provider

    # --- PricePredictionProviderBase hooks -------------------------------
    #
    # Concrete for every feed-in tariff data source: the raw/gross record
    # keys, the feed-in-fee keys, and the fee formula itself don't vary by
    # provider, only by "electricity price" vs. "feed-in tariff" - so, unlike
    # `provider_id`, these are NOT left abstract for concrete providers to
    # fill in.

    @property
    def _raw_key(self) -> str:
        """Record key holding the fee-free raw series."""
        return "feed_in_tariff_raw_wh"

    @property
    def _gross_key(self) -> str:
        """Record key to write the fee-inclusive series to."""
        return "feed_in_tariff_wh"

    @property
    def _fee_keys(self) -> list[str]:
        """Prediction keys to fetch for fee computation."""
        return ["elecfee_feedin_amt_wh", "elecfee_feedin_percent_amt"]

    def _compute_gross(self, raw_amt_wh: pd.Series, df_fee: pd.DataFrame) -> pd.Series:
        """Apply the percent surcharge (e.g. VAT), then subtract the per-Wh feed-in fee.

        gross = raw * (100 - elecfee_feedin_percent_amt) / 100 - elecfee_feedin_amt_wh

        Args:
            raw_amt_wh: Raw feed-in tariff (amount/Wh), fee-free.
            df_fee: Fee dataframe aligned to `raw_amt_wh`'s index, with columns
                matching `_fee_keys`.

        Returns:
            pd.Series: Gross feed-in tariff (amount/Wh), same index as `raw_amt_wh`.
        """
        return (
            raw_amt_wh * (100.0 - df_fee["elecfee_feedin_percent_amt"]) / 100.0
            - df_fee["elecfee_feedin_amt_wh"]
        )
