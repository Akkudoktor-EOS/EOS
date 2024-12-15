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
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class LoadImportCommonSettings(SettingsBaseModel):
    """Common settings for load data import from file."""

    load0_import_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import load data from."
    )
    load0_import_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of load forecast value lists."
    )
    load1_import_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import load data from."
    )
    load1_import_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of load forecast value lists."
    )
    load2_import_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import load data from."
    )
    load2_import_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of load forecast value lists."
    )
    load3_import_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import load data from."
    )
    load3_import_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of load forecast value lists."
    )
    load4_import_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import load data from."
    )
    load4_import_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of load forecast value lists."
    )

    # Validators
    @field_validator(
        "load0_import_file_path",
        "load1_import_file_path",
        "load2_import_file_path",
        "load3_import_file_path",
        "load4_import_file_path",
        mode="after",
    )
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
        for load in self.loads():
            attr_file_path = f"{load}_import_file_path"
            attr_json = f"{load}_import_json"
            import_file_path = getattr(self.config, attr_file_path)
            if import_file_path is not None:
                self.import_from_file(import_file_path, key_prefix=load)
            import_json = getattr(self.config, attr_json)
            if import_json is not None:
                self.import_from_json(import_json, key_prefix=load)
