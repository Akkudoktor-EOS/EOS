"""Abstract and base classes for electricity price predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

import pandas as pd
from pydantic import Field, computed_field

from akkudoktoreos.prediction.predictionabc import PredictionRecord
from akkudoktoreos.prediction.priceabc import PricePredictionProviderBase


class ElecPriceDataRecord(PredictionRecord):
    """Represents a electricity price data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    elecprice_marketprice_raw_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "Raw electricity market price per Wh, always excluding fees [amount/Wh]"
        },
    )

    elecprice_marketprice_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "Electricity market price per Wh, including fees if configured [amount/Wh]"
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def elecprice_marketprice_kwh(self) -> Optional[float]:
        """Electricity market price per kWh [amount/kWh].

        Convenience attribute calculated from `elecprice_marketprice_wh`.
        """
        if self.elecprice_marketprice_wh is None:
            return None
        return self.elecprice_marketprice_wh * 1000.0


class ElecPriceProvider(PricePredictionProviderBase):
    """Abstract base class for electricity price providers.

    ElecPriceProvider is a thread-safe singleton, ensuring only one instance of this class is created.
    """

    # overload
    records: List[ElecPriceDataRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of ElecPriceDataRecord records"},
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "ElecPriceProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.elecprice.provider

    # --- PricePredictionProviderBase hooks -------------------------------
    #
    # Concrete for every electricity-price data source: the raw/gross record
    # keys, the consumption-fee keys, and the fee formula itself don't vary
    # by provider (ElecPriceEnergyCharts, ElecPriceAkkudoktor, ...), only by
    # "electricity price" vs. "feed-in tariff" - so, unlike `provider_id`,
    # these are NOT left abstract for concrete providers to fill in.

    @property
    def _raw_key(self) -> str:
        """Record key holding the fee-free raw series."""
        return "elecprice_marketprice_raw_wh"

    @property
    def _gross_key(self) -> str:
        """Record key to write the fee-inclusive series to."""
        return "elecprice_marketprice_wh"

    @property
    def _fee_keys(self) -> list[str]:
        """Prediction keys to fetch for fee computation."""
        return ["elecfee_consumption_amt_wh", "elecfee_consumption_percent_amt"]

    def _compute_gross(self, raw_amt_wh: pd.Series, df_fee: pd.DataFrame) -> pd.Series:
        """Add the per-Wh consumption fee, then apply the percent surcharge (e.g. VAT).

        gross = (raw + elecfee_consumption_amt_wh) * (100 + elecfee_consumption_percent_amt) / 100

        Args:
            raw_amt_wh: Raw electricity market price (amount/Wh), fee-free.
            df_fee: Fee dataframe aligned to `raw_amt_wh`'s index, with columns
                matching `_fee_keys`.

        Returns:
            pd.Series: Gross electricity price (amount/Wh), same index as `raw_amt_wh`.
        """
        return (
            (raw_amt_wh + df_fee["elecfee_consumption_amt_wh"])
            * (100.0 + df_fee["elecfee_consumption_percent_amt"])
            / 100.0
        )
