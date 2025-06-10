"""Settings for logging.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logabc import LOGGING_LEVELS


class LoggingCommonSettings(SettingsBaseModel):
    """Logging Configuration."""

    level: Optional[str] = Field(
        default=None,
        deprecated="This is deprecated. Use console_level and file_level instead.",
    )

    console_level: Optional[str] = Field(
        default=None,
        description="Logging level when logging to console.",
        examples=LOGGING_LEVELS,
    )

    file_level: Optional[str] = Field(
        default=None,
        description="Logging level when logging to file.",
        examples=LOGGING_LEVELS,
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def file_path(self) -> Optional[Path]:
        """Computed log file path based on data output path."""
        try:
            path = SettingsBaseModel.config.general.data_output_path / "eos.log"
        except:
            # Config may not be fully set up
            path = None
        return path

    # Validators
    @field_validator("console_level", "file_level", mode="after")
    @classmethod
    def validate_level(cls, value: Optional[str]) -> Optional[str]:
        """Validate logging level string."""
        if value is None:
            # Nothing to set
            return None
        if isinstance(value, str):
            level = value.upper()
            if level == "NONE":
                return None
            if level not in LOGGING_LEVELS:
                raise ValueError(f"Logging level {value} not supported")
            value = level
        else:
            raise TypeError(f"Invalid {type(value)} of logging level {value}")
        return value
