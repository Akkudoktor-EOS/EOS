"""Retrieves load forecast data from an import file.

This module provides classes and mappings to manage load data obtained from
an import file, including support for various load attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `LoadDataRecord`
format, enabling consistent access to forecasted and historical load attributes.
"""

from pathlib import Path
from typing import Optional, Union

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider

logger = get_logger(__name__)


class LoadImportCommonSettings(SettingsBaseModel):
    """Common settings for load data import from file or JSON string."""

    load_import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to the file to import load data from.",
        examples=[None, "/path/to/yearly_load.json"],
    )
    load_import_json: Optional[str] = Field(
        default=None,
        description="JSON string, dictionary of load forecast value lists.",
        examples=['{"load0_mean": [676.71, 876.19, 527.13]}'],
    )

    # Validators
    @field_validator("load_import_file_path", mode="after")
    @classmethod
    def validate_loadimport_file_path(cls, value: Optional[Union[str, Path]]) -> Optional[Path]:
        if value is None:
            return None
        if isinstance(value, str):
            value = Path(value)
        """Ensure file is available."""
        value.resolve()
        if not value.is_file():
            raise ValueError(f"Import file path '{value}' is not a file.")
        return value


class LoadImport(LoadProvider, PredictionImportProvider):
    """Fetch Load data from import file or JSON string.

    LoadImport is a singleton-based class that retrieves load forecast data
    from a file or JSON string and maps it to `LoadDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the LoadImport provider."""
        return "LoadImport"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        if self.config.load.provider_settings.load_import_file_path is not None:
            self.import_from_file(
                self.config.provider_settings.load_import_file_path, key_prefix="load"
            )
        if self.config.load.provider_settings.load_import_json is not None:
            self.import_from_json(
                self.config.load.provider_settings.load_import_json, key_prefix="load"
            )
