"""Retrieves feed in tariff forecast data from an import file.

This module provides classes and mappings to manage feed in tariff data obtained from
an import file. The data is mapped to the `FeedInTariffDataRecord` format, enabling consistent
access to forecasted and historical feed in tariff attributes.
"""

from pathlib import Path
from typing import Optional, Union

from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider


class FeedInTariffImportCommonSettings(SettingsBaseModel):
    """Common settings for feed in tariff data import from file or JSON string."""

    import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        json_schema_extra={
            "description": "Path to the file to import feed in tariff data from.",
            "examples": [None, "/path/to/feedintariff.json"],
        },
    )
    import_json: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "JSON string, dictionary of feed in tariff forecast value lists.",
            "examples": ['{"fead_in_tariff_wh": [0.000078, 0.000078, 0.000023]}'],
        },
    )

    # Validators
    @field_validator("import_file_path", mode="after")
    @classmethod
    def validate_feedintariffimport_file_path(
        cls, value: Optional[Union[str, Path]]
    ) -> Optional[Path]:
        if value is None:
            return None
        if isinstance(value, str):
            value = Path(value)
        """Ensure file is available."""
        value.resolve()
        if not value.is_file():
            raise ValueError(f"Import file path '{value}' is not a file.")
        return value


class FeedInTariffImport(FeedInTariffProvider, PredictionImportProvider):
    """Fetch Feed In Tariff data from import file or JSON string.

    FeedInTariffImport is a singleton-based class that retrieves fedd in tariff forecast data
    from a file or JSON string and maps it to `FeedInTariffDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the FeedInTariffImport provider."""
        return "FeedInTariffImport"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        if self.config.feedintariff.provider_settings.FeedInTariffImport is None:
            logger.debug(f"{self.provider_id()} data update without provider settings.")
            return
        if self.config.feedintariff.provider_settings.FeedInTariffImport.import_file_path:
            self.import_from_file(
                self.config.provider_settings.FeedInTariffImport.import_file_path,
                key_prefix="feedintariff",
            )
        if self.config.feedintariff.provider_settings.FeedInTariffImport.import_json:
            self.import_from_json(
                self.config.feedintariff.provider_settings.FeedInTariffImport.import_json,
                key_prefix="feedintariff",
            )
