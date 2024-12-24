"""Abstract and base classes for configuration."""

from akkudoktoreos.core.pydantic import PydanticBaseModel


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations.

    Note:
        Settings property names shall be disjunctive to all existing settings' property names.
    """

    pass
