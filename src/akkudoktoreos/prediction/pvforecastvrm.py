"""Retrieves pvforecast data from VRM API."""

from typing import Any, Optional, Union

import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


class VrmForecastRecords(PydanticBaseModel):
    vrm_consumption_fc: list[tuple[int, float]]
    solar_yield_forecast: list[tuple[int, float]]


class VrmForecastResponse(PydanticBaseModel):
    success: bool
    records: VrmForecastRecords
    totals: dict


class PVForecastVrmCommonSettings(SettingsBaseModel):
    """Common settings for PV forecast VRM API."""

    pvforecast_vrm_token: str = Field(
        default="your-token",
        json_schema_extra={
            "description": "Token for Connecting VRM API",
            "examples": ["your-token"],
        },
    )
    pvforecast_vrm_idsite: int = Field(
        default=12345, json_schema_extra={"description": "VRM-Installation-ID", "examples": [12345]}
    )


class PVForecastVrm(PVForecastProvider):
    """Fetch and process PV forecast data from VRM API."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastVrm"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> VrmForecastResponse:
        """Validate the VRM forecast response data against the expected schema."""
        try:
            return VrmForecastResponse.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = "\n".join(
                f"Field: {' -> '.join(str(x) for x in err['loc'])}\n"
                f"Error: {err['msg']}\nType: {err['type']}"
                for err in e.errors()
            )
            logger.error(f"VRM-API schema change:\n{error_msg}")
            raise ValueError(error_msg)

    def _request_forecast(self, start_ts: int, end_ts: int) -> VrmForecastResponse:
        """Fetch forecast data from Victron VRM API."""
        source = "https://vrmapi.victronenergy.com/v2/installations"
        id_site = self.config.pvforecast.provider_settings.PVForecastVrm.pvforecast_vrm_idsite
        api_token = self.config.pvforecast.provider_settings.PVForecastVrm.pvforecast_vrm_token
        headers = {"X-Authorization": f"Token {api_token}", "Content-Type": "application/json"}
        url = f"{source}/{id_site}/stats?type=forecast&start={start_ts}&end={end_ts}&interval=hours"
        logger.debug(f"Requesting VRM forecast: {url}")

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch pvforecast: {e}")
            raise RuntimeError("Failed to fetch pvforecast from VRM API") from e

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return self._validate_data(response.content)

    def _ts_to_datetime(self, timestamp: int) -> DateTime:
        """Convert UNIX ms timestamp to timezone-aware datetime."""
        return to_datetime(timestamp / 1000, in_timezone=self.config.general.timezone)

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        start_date = self.ems_start_datetime.start_of("day")
        end_date = self.ems_start_datetime.add(hours=self.config.prediction.hours)
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        logger.info(f"Updating PV forecast from VRM: {start_date} to {end_date}")
        vrm_forecast_data = self._request_forecast(start_ts, end_ts)

        pv_forecast = []
        for timestamp, value in vrm_forecast_data.records.solar_yield_forecast:
            date = self._ts_to_datetime(timestamp)
            dc_power = round(value, 2)
            ac_power = round(dc_power * 0.96, 2)
            self.update_value(
                date, {"pvforecast_dc_power": dc_power, "pvforecast_ac_power": ac_power}
            )
            pv_forecast.append((date, dc_power))

        logger.debug(f"Updated pvforecast_dc_power with {len(pv_forecast)} entries.")
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example usage
if __name__ == "__main__":
    pv = PVForecastVrm()
    pv._update_data()
