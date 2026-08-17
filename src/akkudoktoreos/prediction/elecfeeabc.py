"""Abstract and base classes for electricity fee predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

from pydantic import Field, computed_field

from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord


class ElecFeeDataRecord(PredictionRecord):
    """Represents a electricity price data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    elecfee_consumption_amt_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": (
                "Total fixed fee for consumed energy per Wh [amount/Wh]. "
                "This is the accumulation of all fixed per-Wh fees payable on "
                "consumed energy - such as network charge, concession fee, "
                "and electricity charge - into a single amount."
            ),
        },
    )

    elecfee_consumption_percent_amt: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": (
                "Total fixed surcharge on consumed energy, given as a "
                "percentage of the monetary amount already charged for that "
                "energy [%]. This is the accumulation of all percentage-based "
                "surcharges payable on top of the consumed-energy fee - such "
                "as VAT - into a single percentage. This is a percentage of "
                "the fee amount, not a per-Wh rate."
            ),
        },
    )

    elecfee_feedin_amt_wh: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": (
                "Total fixed deduction from feed-in energy per Wh [amount/Wh]. "
                "This is the accumulation of all fixed per-Wh charges deducted from "
                "feed-in energy - such as metering fees or grid-operator handling "
                "charges - into a single amount. Applied after the percentage-based "
                "deduction, i.e. it reduces the price by a flat amount per Wh "
                "rather than by a share of the raw price."
            ),
        },
    )

    elecfee_feedin_percent_amt: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": (
                "Total percentage deducted from the raw feed-in price (spot price) "
                "[%]. This is the accumulation of all percentage-based deductions "
                "payable on the feed-in tariff - such as a marketing or balancing "
                "fee retained by the aggregator - into a single percentage. It is "
                "applied as `raw_price * (100 - percent) / 100`, i.e. it scales "
                "down the raw price rather than adding a surcharge to it."
            ),
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def elecfee_consumption_amt_kwh(self) -> Optional[float]:
        """Electricity fee for consumed energy per kWh [amount/kWh].

        This is the aggregation of fees that are to be paid by consumed energy per kWh - "
        like network charge, concession fee, electricity charge."

        Convenience attribute calculated from `elecfee_consumption_amt_wh`.
        """
        if self.elecfee_consumption_amt_wh is None:
            return None
        return self.elecfee_consumption_amt_wh * 1000.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def elecfee_feedin_amt_kwh(self) -> Optional[float]:
        """Electricity fee for feed-in energy per kWh [amount/kWh].

        This is the aggregation of fees that are to be paid by feed-in energy per kWh - "
        like network charge, concession fee, electricity charge."

        Convenience attribute calculated from `elecfee_feedin_amt_wh`.
        """
        if self.elecfee_feedin_amt_wh is None:
            return None
        return self.elecfee_feedin_amt_wh * 1000.0


class ElecFeeProvider(PredictionProvider):
    """Abstract base class for electricity fee providers.

    Electricity fee providers predict fees on consumed and feed-in electricity to be used by
    electricity and feed-in price providers.

    ElecFeeProvider is a thread-safe singleton, ensuring only one instance of this class is created.
    """

    # overload
    records: List[ElecFeeDataRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of ElecFeeDataRecord records"},
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "ElecFeeProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.elecfee.provider
