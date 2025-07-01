"""Retrieves pvforecast data from VRM API."""

from typing import Any, Optional, Union

import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import to_datetime


class VrmForecastRecords(PydanticBaseModel):
    vrm_consumption_fc: list[tuple[int, float]]
    solar_yield_forecast: list[tuple[int, float]]


class VrmForecastResponse(PydanticBaseModel):
    success: bool
    records: VrmForecastRecords
    totals: dict


class PVforecastVrmCommonSettings(SettingsBaseModel):
    """Common settings for VRM API."""

    pv_vrm_api_token: str = Field(description="Token for Connecting VRM API")
    pv_vrm_installation_id: int = Field(description="VRM-Installation-ID")


class PVForecastVrm(PVForecastProvider):
    """Fetch and process PV forecast data from VRM API."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastVrm"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> VrmForecastResponse:
        """Validate Energy-Charts Electricity Price forecast data."""
        try:
            vrm_forecast_data = VrmForecastResponse.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = ""
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                message = error["msg"]
                error_type = error["type"]
                error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
            logger.error(f"VRM-API schema change: {error_msg}")
            raise ValueError(error_msg)
        return vrm_forecast_data

    def _request_forecast(self, start_ts: int, end_ts: int) -> VrmForecastResponse:
        """Fetch forecast data from Victron VRM API.

        This method sends a request to VRM API to retrieve forecast data for a specified
        date range. The response data is parsed and returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Victron VRM API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `forecast` data.
        """
        source = "https://vrmapi.victronenergy.com/v2/installations"
        id_site = self.config.pvforecast.provider_settings.pv_vrm_installation_id
        api_token = self.config.pvforecast.provider_settings.pv_vrm_api_token
        headers = {"X-Authorization": f"Token {api_token}", "Content-Type": "application/json"}
        url = f"{source}/{id_site}/stats?type=forecast&start={start_ts}&end={end_ts}&interval=hours"
        logger.debug(f"Request {url}")
        response = requests.get(url, headers=headers, timeout=30)
        vrm_forecast_data = self._validate_data(response.content)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return vrm_forecast_data

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format.

        Retrieves data from VRM API. The processed data is inserted into the sequence as
        `PVForecastDataRecord`:
        - pvforecast_dc_power and
        - pvforecast_ac_power = pvforecast_dc_power * 0.96
        """
        """Get pv forecast from VRM and store into pvforecast."""
        # We provide prediction starting at start of day, to be compatible to old system.
        # End date for prediction is prediction hours from now.
        start_date = self.start_datetime.start_of("day")
        end_date = self.start_datetime.add(hours=self.config.prediction.hours)
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        logger.info(f"Update PV-Forcast from VRM start:{start_date}, end:{end_date}")
        # Request and validate
        vrm_forecast_data = self._request_forecast(start_ts, end_ts)

        # Parse data and store
        pv_forecast = []
        # Iterate over timestamps and vrm_consumption_fc
        # for timestamp, value in vrm_forecast_data.records.vrm_consumption_fc:
        #    date = to_datetime(timestamp / 1000, in_timezone=self.config.general.timezone)
        #    self.update_value(date, {"load_mean": round(value, 2)})
        #    load_mean.append((date, round(value, 2)))
        # logger.debug(f"Update load_mean from VRM with: {load_mean}")

        for timestamp, value in vrm_forecast_data.records.solar_yield_forecast:
            date = to_datetime(timestamp / 1000, in_timezone=self.config.general.timezone)
            self.update_value(date, {"pvforecast_dc_power": round(value, 2)})
            self.update_value(date, {"pvforecast_ac_power": round(value * 0.96, 2)})
            pv_forecast.append((date, round(value, 2)))
        logger.debug(f"Update pvforecast_dc_power from VRM with: {pv_forecast}")

        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example of how to use the PVForecastVrm class
if __name__ == "__main__":
    pv = PVForecastVrm()
    pv._update_data()
