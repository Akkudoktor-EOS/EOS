"""Electricity price provider for Tibber."""

from datetime import datetime
from typing import Any, List, Optional

import pandas as pd
import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import to_datetime

TIBBER_GRAPHQL_URL = "https://api.tibber.com/v1-beta/gql"
TIBBER_PRICE_QUERY = """
query TibberPriceInfo {
  viewer {
    homes {
      id
      currentSubscription {
        priceInfo {
          today {
            startsAt
            total
            energy
            tax
          }
          tomorrow {
            startsAt
            total
            energy
            tax
          }
        }
      }
    }
  }
}
"""


class ElecPriceTibberCommonSettings(SettingsBaseModel):
    """Common settings for the Tibber electricity price provider."""

    access_token: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Tibber API access token.",
            "examples": ["tibber_pat_..."],
        },
    )

    home_id: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Tibber home id to read prices from.",
            "examples": ["00000000-0000-0000-0000-000000000000"],
        },
    )


class TibberPricePoint(PydanticBaseModel):
    """Single Tibber price point."""

    startsAt: datetime
    total: float
    energy: Optional[float] = None
    tax: Optional[float] = None


class TibberPriceInfo(PydanticBaseModel):
    """Tibber price info for today and tomorrow."""

    today: List[TibberPricePoint] = Field(default_factory=list)
    tomorrow: List[TibberPricePoint] = Field(default_factory=list)


class TibberSubscription(PydanticBaseModel):
    """Tibber subscription data."""

    priceInfo: TibberPriceInfo


class TibberHome(PydanticBaseModel):
    """Tibber home data."""

    id: str
    currentSubscription: Optional[TibberSubscription] = None


class TibberViewer(PydanticBaseModel):
    """Tibber viewer data."""

    homes: List[TibberHome] = Field(default_factory=list)


class TibberData(PydanticBaseModel):
    """Tibber GraphQL data payload."""

    viewer: TibberViewer


class TibberGraphQLResponse(PydanticBaseModel):
    """Tibber GraphQL response payload."""

    data: Optional[TibberData] = None
    errors: Optional[list[dict[str, Any]]] = None


class ElecPriceTibber(ElecPriceProvider):
    """Fetch and store Tibber electricity import prices."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Tibber provider."""
        return "ElecPriceTibber"

    @cache_in_file(with_ttl="5 minutes")
    def _request_forecast(self, force_update: Optional[bool] = False) -> TibberGraphQLResponse:
        """Fetch electricity price data from the Tibber GraphQL API."""
        access_token = self.config.elecprice.tibber.access_token
        if not access_token:
            raise ValueError("Tibber access_token is required")

        response = requests.post(
            TIBBER_GRAPHQL_URL,
            json={"query": TIBBER_PRICE_QUERY},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()

        try:
            return TibberGraphQLResponse.model_validate_json(response.content)
        except ValidationError as exc:
            logger.error("Tibber schema validation failed: {}", exc)
            raise ValueError(f"Tibber schema validation failed: {exc}") from exc

    def _select_home(self, response: TibberGraphQLResponse) -> TibberHome:
        """Select the configured Tibber home from a GraphQL response."""
        home_id = self.config.elecprice.tibber.home_id
        if not home_id:
            raise ValueError("Tibber home_id is required")

        if response.errors:
            raise ValueError(f"Tibber GraphQL error: {response.errors}")

        if response.data is None:
            raise ValueError("Tibber response does not contain data")

        for home in response.data.viewer.homes:
            if home.id == home_id:
                return home

        raise ValueError("Tibber home_id not found")

    def _parse_data(self, response: TibberGraphQLResponse) -> pd.Series:
        """Parse Tibber prices into EOS market prices in EUR/Wh."""
        home = self._select_home(response)

        if home.currentSubscription is None:
            raise ValueError("Tibber home has no current subscription")

        price_info = home.currentSubscription.priceInfo
        points = list(price_info.today) + list(price_info.tomorrow)

        if not price_info.tomorrow:
            logger.warning("Tibber tomorrow prices not available yet")

        if not points:
            raise ValueError("Tibber response contains no price points")

        values: dict[datetime, float] = {}

        for point in points:
            dt = to_datetime(point.startsAt, in_timezone=self.config.general.timezone)
            values[dt] = point.total / 1000.0

        return pd.Series(values, dtype=float).sort_index()

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update EOS electricity prices from Tibber price data."""
        response = self._request_forecast(force_update=force_update)
        series_data = self._parse_data(response)
        self.key_from_series("elecprice_marketprice_wh", series_data)
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)

        logger.info("Updated ElecPriceTibber with {} price points", len(series_data))
