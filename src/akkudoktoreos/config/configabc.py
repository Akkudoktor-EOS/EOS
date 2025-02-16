"""Abstract and base classes for configuration."""

from typing import Any, ClassVar

from akkudoktoreos.core.pydantic import PydanticBaseModel


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations."""

    # EOS configuration - set by ConfigEOS
    config: ClassVar[Any] = None
