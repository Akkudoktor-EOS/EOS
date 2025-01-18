"""Settings for logging.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

import logging
from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logabc import logging_str_to_level


class LoggingCommonSettings(SettingsBaseModel):
    """Logging Configuration."""

    level: Optional[str] = Field(
        default=None,
        description="EOS default logging level.",
        examples=["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"],
    )

    # Validators
    @field_validator("level", mode="after")
    @classmethod
    def set_default_logging_level(cls, value: Optional[str]) -> Optional[str]:
        if isinstance(value, str) and value.upper() == "NONE":
            value = None
        if value is None:
            return None
        level = logging_str_to_level(value)
        logging.getLogger().setLevel(level)
        return value

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def root_level(self) -> str:
        """Root logger logging level."""
        level = logging.getLogger().getEffectiveLevel()
        level_name = logging.getLevelName(level)
        return level_name
