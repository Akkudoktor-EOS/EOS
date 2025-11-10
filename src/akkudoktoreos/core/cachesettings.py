"""Settings for caching.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class CacheCommonSettings(SettingsBaseModel):
    """Cache Configuration."""

    subpath: Optional[Path] = Field(
        default="cache",
        json_schema_extra={"description": "Sub-path for the EOS cache data directory."},
    )

    cleanup_interval: float = Field(
        default=5 * 60,
        json_schema_extra={"description": "Intervall in seconds for EOS file cache cleanup."},
    )

    # Do not make this a pydantic computed field. The pydantic model must be fully initialized
    # to have access to config.general, which may not be the case if it is a computed field.
    def path(self) -> Optional[Path]:
        """Compute cache path based on general.data_folder_path."""
        data_cache_path = self.config.general.data_folder_path
        if data_cache_path is None or self.subpath is None:
            return None
        return data_cache_path.joinpath(self.subpath)
