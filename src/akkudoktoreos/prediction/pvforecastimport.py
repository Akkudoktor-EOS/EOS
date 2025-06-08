"""Retrieves pvforecast forecast data from an import file.

This module provides classes and mappings to manage pvforecast data obtained from
an import file, including support for various pvforecast attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `PVForecastDataRecord`
format, enabling consistent access to forecasted and historical pvforecast attributes.
"""

from pathlib import Path
from typing import Optional, Union

from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider


class PVForecastImportCommonSettings(SettingsBaseModel):
    """Common settings for pvforecast data import from file or JSON string."""

    import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to the file to import PV forecast data from.",
        examples=[None, "/path/to/pvforecast.json"],
    )

    import_json: Optional[str] = Field(
        default=None,
        description="JSON string, dictionary of PV forecast value lists.",
        examples=['{"pvforecast_ac_power": [0, 8.05, 352.91]}'],
    )

    # Validators
    @field_validator("import_file_path", mode="after")
    @classmethod
    def validate_import_file_path(cls, value: Optional[Union[str, Path]]) -> Optional[Path]:
        if value is None:
            return None
        if isinstance(value, str):
            value = Path(value)
        """Ensure file is available."""
        value.resolve()
        if not value.is_file():
            raise ValueError(f"Import file path '{value}' is not a file.")
        return value


class PVForecastImport(PVForecastProvider, PredictionImportProvider):
    """Fetch PV forecast data from import file or JSON string.

    PVForecastImport is a singleton-based class that retrieves pvforecast forecast data
    from a file or JSON string and maps it to `PVForecastDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PVForecastImport provider."""
        return "PVForecastImport"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        if self.config.pvforecast.provider_settings is None:
            logger.debug(f"{self.provider_id()} data update without provider settings.")
            return
        if self.config.pvforecast.provider_settings.import_file_path is not None:
            self.import_from_file(
                self.config.pvforecast.provider_settings.import_file_path,
                key_prefix="pvforecast",
            )
        if self.config.pvforecast.provider_settings.import_json is not None:
            self.import_from_json(
                self.config.pvforecast.provider_settings.import_json,
                key_prefix="pvforecast",
            )
