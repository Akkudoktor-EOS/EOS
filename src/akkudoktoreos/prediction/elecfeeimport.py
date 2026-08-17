"""Retrieves elecfee forecast data from an import file.

This module provides classes and mappings to manage elecfee data obtained from
an import file. The data is mapped to the `ElecFeeDataRecord` format, enabling consistent access
to forecasted and historical elecfee attributes.
"""

from pathlib import Path
from typing import Optional, Union

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecfeeabc import ElecFeeProvider
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider


class ElecFeeImportCommonSettings(SettingsBaseModel):
    """Common settings for elecfee data import from file or JSON String."""

    import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        json_schema_extra={
            "description": "Path to the file to import elecfee data from.",
            "examples": [None, "/path/to/prices.json"],
        },
    )

    import_json: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "JSON string, dictionary of electricity fee forecast value lists.",
            "examples": ['{"elecfee_consumption_amt_wh": [0.0003384, 0.0003318, 0.0003284]}'],
        },
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


class ElecFeeImport(ElecFeeProvider, PredictionImportProvider):
    """Fetch PV forecast data from import file or JSON string.

    ElecFeeImport is a singleton-based class that retrieves elecfee forecast data
    from a file or JSON string and maps it to `ElecFeeDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the ElecFeeImport provider."""
        return "ElecFeeImport"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Both _sequence_lock and _record_lock are already held by the caller.
        # Use internal sync methods only — never await public async counterparts.
        if self.config.elecfee.elecfeeimport.import_file_path:
            await self._import_from_file(
                self.config.elecfee.elecfeeimport.import_file_path,
                key_prefix="elecfee",
            )
        if self.config.elecfee.elecfeeimport.import_json:
            await self._import_from_json(
                self.config.elecfee.elecfeeimport.import_json,
                key_prefix="elecfee",
            )
