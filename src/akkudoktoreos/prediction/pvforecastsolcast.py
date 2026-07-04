"""Retrieves PV forecast data from the Solcast API.

Solcast (https://solcast.com) provides high-accuracy PV forecasts for a rooftop
site that the operator registers in the Solcast web app. The operator enters the
API key + the rooftop resource id (site id):

    GET https://api.solcast.com.au/rooftop_sites/{site_id}/forecasts?format=json&hours=72

Each forecast row carries ``pv_estimate`` (in kW) and ``period_end`` (UTC) plus
an ISO-8601 ``period`` duration. The estimate is converted to watts and the
timestamp is normalised to the period START (``period_end - period``) so it sits
on the same axis EOS resamples onto.

Notes:
    - Solcast's free tier limits the number of calls per day; the response is
      cached (1 hour TTL) to stay within budget.
"""

import re
from typing import Any, Optional
from urllib.parse import quote

import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import to_datetime

SOLCAST_BASE = "https://api.solcast.com.au/rooftop_sites"

_PERIOD_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")


class PVForecastSolcastCommonSettings(SettingsBaseModel):
    """Common settings for the Solcast PV forecast provider."""

    api_key: str = Field(
        default="",
        json_schema_extra={
            "description": "Solcast API key (Bearer auth). Required.",
            "examples": ["your-solcast-key"],
        },
    )
    site_id: str = Field(
        default="",
        json_schema_extra={
            "description": "Solcast rooftop site (resource) id. Required.",
            "examples": ["abcd-1234-efgh-5678"],
        },
    )


class PVForecastSolcast(PVForecastProvider):
    """Fetch and process PV forecast data from the Solcast API."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastSolcast"

    @property
    def _settings(self) -> PVForecastSolcastCommonSettings:
        settings = self.config.pvforecast.provider_settings.PVForecastSolcast
        if settings is None:
            settings = PVForecastSolcastCommonSettings()
        return settings

    @staticmethod
    def _period_minutes(period: Optional[str]) -> int:
        """Parse an ISO-8601 period like 'PT30M' or 'PT1H' into minutes (0 if unknown)."""
        match = _PERIOD_RE.match(str(period or ""))
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        return hours * 60 + minutes

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> Any:
        """Fetch the rooftop-site forecast from Solcast."""
        settings = self._settings
        if not settings.api_key or not settings.site_id:
            raise ValueError("PVForecastSolcast requires api_key and site_id")

        url = f"{SOLCAST_BASE}/{quote(settings.site_id, safe='')}/forecasts"
        params = {"format": "json", "hours": "72"}
        headers = {"Authorization": f"Bearer {settings.api_key}", "Accept": "application/json"}
        logger.debug(f"Requesting Solcast forecast: {url}")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch pvforecast from Solcast: {e}")
            raise RuntimeError("Failed to fetch pvforecast from Solcast API") from e

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return response.json()

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        if not self.enabled():
            logger.info("PVForecastSolcast is disabled, skipping update.")
            return

        body = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
        forecasts = body.get("forecasts", []) if isinstance(body, dict) else []

        count = 0
        for entry in forecasts:
            if not isinstance(entry, dict):
                continue
            estimate_kw = entry.get("pv_estimate")
            if estimate_kw is None:
                estimate_kw = entry.get("pv_estimate_period")
            period_end = entry.get("period_end")
            if estimate_kw is None or period_end is None:
                continue
            try:
                end = to_datetime(period_end)
            except Exception as e:  # noqa: BLE001 - skip unparseable rows
                logger.warning(f"Solcast: skipping unparseable period_end {period_end!r}: {e}")
                continue
            minutes = self._period_minutes(entry.get("period"))
            date = end.subtract(minutes=minutes) if minutes else end
            power_w = round(float(estimate_kw) * 1000.0, 1)
            self.update_value(
                date,
                {"pvforecast_ac_power": power_w, "pvforecast_dc_power": power_w},
            )
            count += 1

        logger.debug(f"Updated pvforecast from Solcast with {count} entries.")
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example usage
if __name__ == "__main__":
    pv = PVForecastSolcast()
    pv._update_data()
