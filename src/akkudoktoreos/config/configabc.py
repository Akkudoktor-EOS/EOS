"""Abstract and base classes for configuration."""

from akkudoktoreos.core.pydantic import PydanticBaseModel


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations.

    Note:
        Settings property names shall be disjunctive to all existing settings' property names.
    """

    def reset_to_defaults(self) -> None:
        """Resets the fields to their default values."""
        for field_name, field_info in self.model_fields.items():
            if field_info.default_factory is not None:  # Handle fields with default_factory
                default_value = field_info.default_factory()
            else:
                default_value = field_info.default
            try:
                setattr(self, field_name, default_value)
            except (AttributeError, TypeError):
                # Skip fields that are read-only or dynamically computed
                pass
