"""Retrieves elecprice forecast data from an import file.

This module provides classes and mappings to manage elecprice data obtained from
an import file, including support for various elecprice attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical elecprice attributes.
"""

from pathlib import Path
from typing import Optional, Union

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider

logger = get_logger(__name__)


class ElecPriceImportCommonSettings(SettingsBaseModel):
    """Common settings for elecprice data import from file or JSON String."""

    import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to the file to import elecprice data from.",
        examples=[None, "/path/to/prices.json"],
    )

    import_json: Optional[str] = Field(
        default=None,
        description="JSON string, dictionary of electricity price forecast value lists.",
        examples=['{"elecprice_marketprice_wh": [0.0003384, 0.0003318, 0.0003284]}'],
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


class ElecPriceImport(ElecPriceProvider, PredictionImportProvider):
    """Fetch PV forecast data from import file or JSON string.

    ElecPriceImport is a singleton-based class that retrieves elecprice forecast data
    from a file or JSON string and maps it to `ElecPriceDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the ElecPriceImport provider."""
        return "ElecPriceImport"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        if self.config.elecprice.provider_settings.import_file_path is not None:
            self.import_from_file(
                self.config.elecprice.provider_settings.import_file_path,
                key_prefix="elecprice",
            )
        if self.config.elecprice.provider_settings.import_json is not None:
            self.import_from_json(
                self.config.elecprice.provider_settings.import_json, key_prefix="elecprice"
            )
