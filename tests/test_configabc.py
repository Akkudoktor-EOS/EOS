from typing import List, Literal, Optional, no_type_check

import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel


class SettingsModel(SettingsBaseModel):
    name: str = "Default Name"
    age: int = 18
    tags: List[str] = Field(default_factory=list)
    readonly_field: Literal["ReadOnly"] = "ReadOnly"  # Use Literal instead of const


def test_reset_to_defaults():
    """Test resetting to default values."""
    instance = SettingsModel(name="Custom Name", age=25, tags=["tag1", "tag2"])

    # Modify the instance
    instance.name = "Modified Name"
    instance.age = 30
    instance.tags.append("tag3")

    # Ensure the instance is modified
    assert instance.name == "Modified Name"
    assert instance.age == 30
    assert instance.tags == ["tag1", "tag2", "tag3"]

    # Reset to defaults
    instance.reset_to_defaults()

    # Verify default values
    assert instance.name == "Default Name"
    assert instance.age == 18
    assert instance.tags == []
    assert instance.readonly_field == "ReadOnly"


@no_type_check
def test_reset_to_defaults_readonly_field():
    """Ensure read-only fields remain unchanged."""
    instance = SettingsModel()

    # Attempt to modify readonly_field (should raise an error)
    with pytest.raises(ValidationError):
        instance.readonly_field = "New Value"

    # Reset to defaults
    instance.reset_to_defaults()

    # Ensure readonly_field is still at its default value
    assert instance.readonly_field == "ReadOnly"


def test_reset_to_defaults_with_default_factory():
    """Test reset with fields having default_factory."""

    class FactoryModel(SettingsBaseModel):
        items: List[int] = Field(default_factory=lambda: [1, 2, 3])
        value: Optional[int] = None

    instance = FactoryModel(items=[4, 5, 6], value=10)

    # Ensure instance has custom values
    assert instance.items == [4, 5, 6]
    assert instance.value == 10

    # Reset to defaults
    instance.reset_to_defaults()

    # Verify reset values
    assert instance.items == [1, 2, 3]
    assert instance.value is None


@no_type_check
def test_reset_to_defaults_error_handling():
    """Ensure reset_to_defaults skips fields that cannot be set."""

    class ReadOnlyModel(SettingsBaseModel):
        readonly_field: Literal["ReadOnly"] = "ReadOnly"

    instance = ReadOnlyModel()

    # Attempt to modify readonly_field (should raise an error)
    with pytest.raises(ValidationError):
        instance.readonly_field = "New Value"

    # Reset to defaults
    instance.reset_to_defaults()

    # Ensure readonly_field is unaffected
    assert instance.readonly_field == "ReadOnly"
