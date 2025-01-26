"""Abstract and base classes for configuration."""

from akkudoktoreos.core.pydantic import PydanticBaseModel


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations."""

    pass
