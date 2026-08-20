"""Provide native quarter-hour feed-in prices from the Tibber API."""

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import requests
from loguru import logger

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.elecpricetibber import (
    TIBBER_GRAPHQL_URL,
    TIBBER_PRICE_QUERY_QUARTER_HOURLY,
    ElecPriceTibber,
    TibberGraphQLResponse,
    TibberPricePoint,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class FeedInTariffTibberCommonSettings(SettingsBaseModel):
    """Settings for the Tibber feed-in tariff provider.

    Authentication is shared with ``elecprice.tibber`` so the access token and
    home id do not have to be configured twice.
    """


class FeedInTariffTibber(FeedInTariffProvider):
    """Use Tibber's native quarter-hour energy component as feed-in price.

    Tibber documents ``energy`` as the spot-price component. Unlike the
    end-customer ``total`` component it excludes taxes.
    """

    highest_orig_datetime: Optional[datetime] = None

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique provider identifier."""
        return "FeedInTariffTibber"

    def historic_hours_min(self) -> int:
        """Keep enough history for seasonal price extrapolation."""
        return 24 * 35

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> TibberGraphQLResponse:
        """Request strictly quarter-hourly Tibber prices.

        Unlike the electricity-price provider, this provider deliberately has
        no hourly fallback because it promises a native 15-minute signal.
        """
        access_token = self.config.elecprice.tibber.access_token
        if not access_token:
            raise ValueError("Tibber access_token is required")

        response = requests.post(
            TIBBER_GRAPHQL_URL,
            json={"query": TIBBER_PRICE_QUERY_QUARTER_HOURLY},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        logger.debug("Response from Tibber GraphQL API for feed-in tariff: {}", response)
        response.raise_for_status()
        tibber_data = ElecPriceTibber._validate_data(response.content)
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return tibber_data

    def _price_points(self, response: TibberGraphQLResponse) -> list[TibberPricePoint]:
        """Collect historical, today, and tomorrow price points."""
        tibber = ElecPriceTibber()
        home = tibber._select_home(response)
        subscription = home.currentSubscription
        if subscription is None:
            raise ValueError("Tibber home has no current subscription")

        points: list[TibberPricePoint] = []
        if subscription.priceInfoRange is not None:
            points.extend(subscription.priceInfoRange.nodes)
        if subscription.priceInfo is not None:
            points.extend(subscription.priceInfo.today)
            points.extend(subscription.priceInfo.tomorrow)
            if not subscription.priceInfo.tomorrow:
                logger.warning("Tibber tomorrow prices not available yet")
        return points

    def _parse_data(self, response: TibberGraphQLResponse) -> pd.Series:
        """Convert Tibber's EUR/kWh spot-price component to EUR/Wh."""
        series = pd.Series(dtype=float)
        for point in self._price_points(response):
            if point.energy is None:
                raise ValueError("Tibber response does not contain the energy price component")
            timestamp = to_datetime(point.startsAt, in_timezone=self.config.general.timezone)
            series.at[timestamp] = point.energy / 1000.0

        if series.empty:
            raise ValueError("Tibber response contains no feed-in price points")
        return ElecPriceTibber()._normalize_series(series)

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Store native 15-minute values and forecast missing horizon slots."""
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        try:
            data = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
            series = self._parse_data(data)
            resolution_seconds = ElecPriceTibber()._resolution_seconds(series)
            if resolution_seconds != 900:
                raise ValueError(
                    "FeedInTariffTibber requires native 15-minute prices; "
                    f"received {resolution_seconds}-second intervals"
                )
            self.highest_orig_datetime = to_datetime(
                series.index.max(), in_timezone=self.config.general.timezone
            )
            await self.key_from_series("feed_in_tariff_wh", series)
        except Exception as exc:
            if self.highest_orig_datetime is None:
                raise
            logger.warning(
                "Tibber feed-in tariff update failed ({}); retaining existing 15-minute data.",
                exc,
            )

        if self.highest_orig_datetime is None:
            raise ValueError("Highest original datetime not available")

        interval_seconds = 900
        history = np.asarray(
            await self.key_to_array(
                key="feed_in_tariff_wh",
                end_datetime=self.highest_orig_datetime,
                interval=to_duration(f"{interval_seconds} seconds"),
                fill_method="linear",
            ),
            dtype=float,
        )
        covered_slots = 0
        if self.highest_orig_datetime >= self.ems_start_datetime:
            covered_slots = (
                int(
                    (self.highest_orig_datetime - self.ems_start_datetime).total_seconds()
                    // interval_seconds
                )
                + 1
            )
        needed_slots = self.config.prediction.hours * 4 - covered_slots
        if needed_slots <= 0:
            return

        prediction = ElecPriceTibber()._predict_missing_prices(
            history, slots=needed_slots, slots_per_hour=4
        )
        prediction_series = pd.Series(
            data=prediction,
            index=[
                self.highest_orig_datetime + to_duration(f"{(i + 1) * interval_seconds} seconds")
                for i in range(len(prediction))
            ],
        )
        await self.key_from_series("feed_in_tariff_wh", prediction_series)
