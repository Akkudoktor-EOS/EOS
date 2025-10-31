"""Retrieves load forecast data from VRM API."""

from typing import Any, Optional, Union

import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


class VrmForecastRecords(PydanticBaseModel):
    vrm_consumption_fc: list[tuple[int, float]]
    solar_yield_forecast: list[tuple[int, float]]


class VrmForecastResponse(PydanticBaseModel):
    success: bool
    records: VrmForecastRecords
    totals: dict


class LoadVrmCommonSettings(SettingsBaseModel):
    """Common settings for VRM API."""

    load_vrm_token: str = Field(
        default="your-token", description="Token for Connecting VRM API", examples=["your-token"]
    )
    load_vrm_idsite: int = Field(default=12345, description="VRM-Installation-ID", examples=[12345])


class LoadVrm(LoadProvider):
    """Fetch Load forecast data from VRM API."""

    @classmethod
    def provider_id(cls) -> str:
        return "LoadVrm"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> VrmForecastResponse:
        """Validate the VRM API load forecast response."""
        try:
            return VrmForecastResponse.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = "\n".join(
                f"Field: {' -> '.join(str(x) for x in err['loc'])}\n"
                f"Error: {err['msg']}\nType: {err['type']}"
                for err in e.errors()
            )
            logger.error(f"VRM-API schema validation failed:\n{error_msg}")
            raise ValueError(error_msg)

    def _request_forecast(self, start_ts: int, end_ts: int) -> VrmForecastResponse:
        """Fetch forecast data from Victron VRM API."""
        base_url = "https://vrmapi.victronenergy.com/v2/installations"
        installation_id = self.config.load.provider_settings.LoadVrm.load_vrm_idsite
        api_token = self.config.load.provider_settings.LoadVrm.load_vrm_token

        url = f"{base_url}/{installation_id}/stats?type=forecast&start={start_ts}&end={end_ts}&interval=hours"
        headers = {"X-Authorization": f"Token {api_token}", "Content-Type": "application/json"}

        logger.debug(f"Requesting VRM load forecast: {url}")
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error during VRM API request: {e}")
            raise RuntimeError("Failed to fetch load forecast from VRM API") from e

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return self._validate_data(response.content)

    def _ts_to_datetime(self, timestamp: int) -> DateTime:
        """Convert UNIX ms timestamp to timezone-aware datetime."""
        return to_datetime(timestamp / 1000, in_timezone=self.config.general.timezone)

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Fetch and store VRM load forecast as loadforecast_power_w and related values."""
        start_date = self.ems_start_datetime.start_of("day")
        end_date = self.ems_start_datetime.add(hours=self.config.prediction.hours)
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        logger.info(f"Updating Load forecast from VRM: {start_date} to {end_date}")
        vrm_forecast_data = self._request_forecast(start_ts, end_ts)

        loadforecast_power_w_data = []
        for timestamp, value in vrm_forecast_data.records.vrm_consumption_fc:
            date = self._ts_to_datetime(timestamp)
            rounded_value = round(value, 2)

            self.update_value(
                date,
                {"loadforecast_power_w": rounded_value},
            )

            loadforecast_power_w_data.append((date, rounded_value))

        logger.debug(f"Updated loadforecast_power_w with {len(loadforecast_power_w_data)} entries.")
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


if __name__ == "__main__":
    lv = LoadVrm()
    lv._update_data()
