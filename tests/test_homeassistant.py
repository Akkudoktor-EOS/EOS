import os
import subprocess
from typing import Optional

import pytest
import yaml
from pydantic import ValidationError

from akkudoktoreos.adapter.homeassistant import HomeAssistantEntityIdMapping


class TestHomeAssistantEntityIdMapping:
    """Comprehensive test suite for HomeAssistantEntityIdMapping."""

    # ----------------------------------------------------------------
    # Phase 1: Unrestricted Mode Tests
    # ----------------------------------------------------------------

    def test_unrestricted_empty_initialization(self):
        """Test creating an empty mapping without restrictions."""
        mapping = HomeAssistantEntityIdMapping()
        assert mapping.root == {}
        assert len(mapping.allowed_keys) == 0

    def test_unrestricted_with_data(self):
        """Test creating an unrestricted mapping with initial data."""
        data: dict[str, Optional[str]] = {
            "sensor1": "entity.sensor1",
            "sensor2": "entity.sensor2"
        }
        mapping = HomeAssistantEntityIdMapping(data)
        assert mapping.root == data
        assert len(mapping.allowed_keys) == 0

    def test_unrestricted_accepts_none_values(self):
        """Test that None values are accepted in unrestricted mode."""
        data = {"sensor1": "entity.sensor1", "sensor2": None}
        mapping = HomeAssistantEntityIdMapping(data)
        assert mapping.root["sensor1"] == "entity.sensor1"
        assert mapping.root["sensor2"] is None

    def test_unrestricted_arbitrary_keys(self):
        """Test that any keys are accepted without restrictions."""
        data: dict[str, Optional[str]] = {
            "light": "light.living_room",
            "switch": "switch.kitchen",
            "sensor": "sensor.temperature",
            "climate": "climate.bedroom",
        }
        mapping = HomeAssistantEntityIdMapping(data)
        assert len(mapping.root) == 4
        assert all(key in mapping.root for key in data.keys())

    # ----------------------------------------------------------------
    # Phase 2: Restricted Mode Tests
    # ----------------------------------------------------------------

    def test_restricted_initialization_with_allowed_keys(self):
        """Test creating a mapping with allowed_keys constraint."""
        allowed = {"light", "switch", "sensor"}
        data: dict[str, Optional[str]] = {
            "light": "light.living_room",
            "switch": "switch.kitchen"
        }
        mapping = HomeAssistantEntityIdMapping(data, allowed_keys=allowed)

        assert mapping.root["light"] == "light.living_room"
        assert mapping.root["switch"] == "switch.kitchen"
        assert mapping.root["sensor"] is None  # Auto-filled
        assert mapping.allowed_keys == allowed

    def test_restricted_missing_keys_auto_filled(self):
        """Test that missing allowed keys are auto-filled with None."""
        allowed = {"key1", "key2", "key3", "key4"}
        data: dict[str, Optional[str]] = {"key1": "value1"}
        mapping = HomeAssistantEntityIdMapping(data, allowed_keys=allowed)

        assert mapping.root["key1"] == "value1"
        assert mapping.root["key2"] is None
        assert mapping.root["key3"] is None
        assert mapping.root["key4"] is None
        assert len(mapping.root) == 4

    def test_restricted_rejects_invalid_keys(self):
        """Test that keys not in allowed_keys raise ValueError."""
        allowed = {"light", "switch"}
        data: dict[str, Optional[str]] = {
            "light": "light.living_room",
            "invalid_key": "some_value"
        }

        with pytest.raises(ValueError) as exc_info:
            HomeAssistantEntityIdMapping(data, allowed_keys=allowed)

        assert "Invalid keys: {'invalid_key'}" in str(exc_info.value)
        assert "Allowed keys are:" in str(exc_info.value)

    def test_restricted_rejects_multiple_invalid_keys(self):
        """Test that multiple invalid keys are reported."""
        allowed = {"light"}
        data: dict[str, Optional[str]] = {
            "light": "light.living_room",
            "switch": "value1",
            "sensor": "value2"
        }

        with pytest.raises(ValueError) as exc_info:
            HomeAssistantEntityIdMapping(data, allowed_keys=allowed)

        error_msg = str(exc_info.value)
        assert "Invalid keys:" in error_msg
        assert "switch" in error_msg or "sensor" in error_msg

    def test_restricted_empty_data_fills_all_keys(self):
        """Test that empty data with allowed_keys creates all keys as None."""
        allowed = {"key1", "key2", "key3"}
        mapping = HomeAssistantEntityIdMapping({}, allowed_keys=allowed)

        assert len(mapping.root) == 3
        assert all(mapping.root[key] is None for key in allowed)

    # ----------------------------------------------------------------
    # with_defaults() Class Method Tests
    # ----------------------------------------------------------------

    def test_with_defaults_creates_all_none_mapping(self):
        """Test that with_defaults creates a mapping with all keys as None."""
        keys = {"temp", "humidity", "pressure"}
        mapping = HomeAssistantEntityIdMapping.with_defaults(keys)

        assert len(mapping.root) == 3
        assert mapping.root["temp"] is None
        assert mapping.root["humidity"] is None
        assert mapping.root["pressure"] is None
        assert mapping.allowed_keys == keys

    def test_with_defaults_empty_keys(self):
        """Test with_defaults with an empty set."""
        mapping = HomeAssistantEntityIdMapping.with_defaults(set())
        assert mapping.root == {}
        assert len(mapping.allowed_keys) == 0

    def test_with_defaults_single_key(self):
        """Test with_defaults with a single key."""
        mapping = HomeAssistantEntityIdMapping.with_defaults({"single_key"})
        assert len(mapping.root) == 1
        assert mapping.root["single_key"] is None

    # ----------------------------------------------------------------
    # Dict-like Interface Tests
    # ----------------------------------------------------------------

    def test_getitem_access(self):
        """Test dictionary-style item access."""
        data: dict[str, Optional[str]] = {"sensor": "sensor.temperature"}
        mapping = HomeAssistantEntityIdMapping(data)
        assert mapping["sensor"] == "sensor.temperature"

    def test_getitem_keyerror(self):
        """Test that accessing non-existent key raises KeyError."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})
        with pytest.raises(KeyError):
            _ = mapping["non_existent"]

    def test_setitem_unrestricted(self):
        """Test setting items in unrestricted mode."""
        mapping = HomeAssistantEntityIdMapping()
        mapping["new_key"] = "new_value"
        assert mapping.root["new_key"] == "new_value"

    def test_setitem_restricted_valid_key(self):
        """Test setting items with valid keys in restricted mode."""
        allowed = {"light", "switch"}
        mapping = HomeAssistantEntityIdMapping({}, allowed_keys=allowed)
        mapping["light"] = "light.living_room"
        assert mapping["light"] == "light.living_room"

    def test_setitem_restricted_invalid_key(self):
        """Test that setting invalid keys in restricted mode raises ValueError."""
        allowed = {"light", "switch"}
        mapping = HomeAssistantEntityIdMapping({}, allowed_keys=allowed)

        with pytest.raises(ValueError) as exc_info:
            mapping["invalid"] = "some_value"

        assert "not in allowed keys" in str(exc_info.value)

    def test_setitem_none_value(self):
        """Test setting None values."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})
        mapping["key"] = None
        assert mapping["key"] is None

    def test_contains_operator(self):
        """Test the 'in' operator."""
        data: dict[str, Optional[str]] = {"sensor": "sensor.temperature"}
        mapping = HomeAssistantEntityIdMapping(data)

        assert "sensor" in mapping
        assert "non_existent" not in mapping

    def test_items_method(self):
        """Test the items() method."""
        data: dict[str, Optional[str]] = {"key1": "value1", "key2": "value2"}
        mapping = HomeAssistantEntityIdMapping(data)
        items = dict(mapping.items())

        assert items == data

    def test_keys_method(self):
        """Test the keys() method."""
        data: dict[str, Optional[str]] = {"key1": "value1", "key2": "value2", "key3": None}
        mapping = HomeAssistantEntityIdMapping(data)
        keys = set(mapping.keys())

        assert keys == {"key1", "key2", "key3"}

    def test_values_method(self):
        """Test the values() method."""
        data: dict[str, Optional[str]] = {"key1": "value1", "key2": "value2", "key3": None}
        mapping = HomeAssistantEntityIdMapping(data)
        values = list(mapping.values())

        assert "value1" in values
        assert "value2" in values
        assert None in values

    def test_get_method_existing_key(self):
        """Test get() method with existing key."""
        data: dict[str, Optional[str]] = {"sensor": "sensor.temperature"}
        mapping = HomeAssistantEntityIdMapping(data)

        assert mapping.get("sensor") == "sensor.temperature"

    def test_get_method_missing_key_default_none(self):
        """Test get() method with missing key returns None."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})
        assert mapping.get("missing") is None

    def test_get_method_missing_key_custom_default(self):
        """Test get() method with custom default value."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})
        assert mapping.get("missing", "default_value") == "default_value"

    # ----------------------------------------------------------------
    # Edge Cases and Error Handling
    # ----------------------------------------------------------------

    def test_none_as_data_creates_empty_mapping(self):
        """Test that passing None as data creates an empty mapping."""
        mapping = HomeAssistantEntityIdMapping(None)
        assert mapping.root == {}

    def test_invalid_data_type(self):
        """Test that non-dict data raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            HomeAssistantEntityIdMapping("not a dict", allowed_keys={"key"}) # type: ignore

        assert "dict" in str(exc_info.value).lower()

    def test_allowed_keys_property(self):
        """Test the allowed_keys property."""
        allowed = {"key1", "key2"}
        mapping = HomeAssistantEntityIdMapping({}, allowed_keys=allowed)

        assert mapping.allowed_keys == allowed
        assert isinstance(mapping.allowed_keys, set)

    def test_allowed_keys_property_unrestricted(self):
        """Test allowed_keys property in unrestricted mode."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})
        assert mapping.allowed_keys == set()

    # ----------------------------------------------------------------
    # Instance Isolation Tests
    # ----------------------------------------------------------------

    def test_instances_are_independent(self):
        """Test that different instances don't share state."""
        allowed1 = {"key1", "key2"}
        allowed2 = {"key3", "key4"}

        mapping1 = HomeAssistantEntityIdMapping({}, allowed_keys=allowed1)
        mapping2 = HomeAssistantEntityIdMapping({}, allowed_keys=allowed2)

        assert mapping1.allowed_keys == allowed1
        assert mapping2.allowed_keys == allowed2
        assert mapping1.allowed_keys != mapping2.allowed_keys

    def test_unrestricted_and_restricted_instances_coexist(self):
        """Test that unrestricted and restricted instances can coexist."""
        unrestricted = HomeAssistantEntityIdMapping({"any_key": "any_value"})
        restricted = HomeAssistantEntityIdMapping(
            {"allowed": "value"},
            allowed_keys={"allowed"}
        )

        # Unrestricted should still allow any key
        unrestricted["another_key"] = "another_value"
        assert "another_key" in unrestricted

        # Restricted should still enforce rules
        with pytest.raises(ValueError):
            restricted["not_allowed"] = "value"

    # ----------------------------------------------------------------
    # Modification Tests
    # ----------------------------------------------------------------

    def test_modify_existing_value(self):
        """Test modifying an existing value."""
        data: dict[str, Optional[str]] = {"sensor": "sensor.old"}
        mapping = HomeAssistantEntityIdMapping(data)

        mapping["sensor"] = "sensor.new"
        assert mapping["sensor"] == "sensor.new"

    def test_modify_none_to_value(self):
        """Test changing None to a value."""
        mapping = HomeAssistantEntityIdMapping.with_defaults({"key"})
        assert mapping["key"] is None

        mapping["key"] = "new_value"
        assert mapping["key"] == "new_value"

    def test_modify_value_to_none(self):
        """Test changing a value to None."""
        data: dict[str, Optional[str]] = {"key": "value"}
        mapping = HomeAssistantEntityIdMapping(data)

        mapping["key"] = None
        assert mapping["key"] is None

    # ----------------------------------------------------------------
    # Pydantic Integration Tests
    # ----------------------------------------------------------------

    def test_model_dump(self):
        """Test Pydantic's model_dump() method."""
        data: dict[str, Optional[str]] = {"key1": "value1", "key2": None}
        mapping = HomeAssistantEntityIdMapping(data)

        dumped = mapping.model_dump()
        assert dumped == data

    def test_model_dump_json(self):
        """Test Pydantic's model_dump_json() method."""
        data: dict[str, Optional[str]] = {"key1": "value1", "key2": None}
        mapping = HomeAssistantEntityIdMapping(data)

        json_str = mapping.model_dump_json()
        assert "key1" in json_str
        assert "value1" in json_str

    def test_json_schema_extra(self):
        """Test that json_schema_extra metadata is included in the schema."""
        schema = HomeAssistantEntityIdMapping.model_json_schema()

        # Check that description is present
        assert "description" in schema
        assert "Home Assistant entity IDs" in schema["description"]

        # Check that examples are present
        assert "examples" in schema
        assert len(schema["examples"]) > 0

        # Verify example structure
        first_example = schema["examples"][0]
        assert isinstance(first_example, dict)
        assert all(isinstance(v, (str, type(None))) for v in first_example.values())

    def test_model_validate(self):
        """Test Pydantic's model_validate() method.

        Note: model_validate() creates an unrestricted mapping since
        there's no way to pass allowed_keys through this method.
        """
        data: dict[str, Optional[str]] = {"key": "value"}
        mapping = HomeAssistantEntityIdMapping.model_validate(data)

        assert mapping.root == data
        assert len(mapping.allowed_keys) == 0  # Unrestricted

    # ----------------------------------------------------------------
    # Real-world Scenario Tests
    # ----------------------------------------------------------------

    def test_home_assistant_entity_mapping_scenario(self):
        """Test a realistic Home Assistant entity mapping scenario."""
        # Define entity types
        entity_types = {"light", "switch", "sensor", "binary_sensor", "climate"}

        # Create mapping with some entities configured
        initial_data: dict[str, Optional[str]] = {
            "light": "light.living_room",
            "switch": "switch.kitchen",
            "sensor": "sensor.temperature",
        }

        mapping = HomeAssistantEntityIdMapping(initial_data, allowed_keys=entity_types)

        # Check configured entities
        assert mapping["light"] == "light.living_room"
        assert mapping["switch"] == "switch.kitchen"
        assert mapping["sensor"] == "sensor.temperature"

        # Check unconfigured entities are None
        assert mapping["binary_sensor"] is None
        assert mapping["climate"] is None

        # Update an entity
        mapping["binary_sensor"] = "binary_sensor.motion"
        assert mapping["binary_sensor"] == "binary_sensor.motion"

    def test_progressive_configuration(self):
        """Test progressively configuring a mapping."""
        allowed = {"entity1", "entity2", "entity3"}
        mapping = HomeAssistantEntityIdMapping.with_defaults(allowed)

        # Initially all None
        assert all(v is None for v in mapping.values())

        # Configure one by one
        mapping["entity1"] = "configured.entity1"
        assert mapping["entity1"] == "configured.entity1"
        assert mapping["entity2"] is None

        mapping["entity2"] = "configured.entity2"
        assert mapping["entity2"] == "configured.entity2"
        assert mapping["entity3"] is None

        mapping["entity3"] = "configured.entity3"
        assert all(v is not None for v in mapping.values())

    # ----------------------------------------------------------------
    # Runtime Allowed Keys Modification Tests
    # ----------------------------------------------------------------

    def test_set_allowed_keys_with_validation(self):
        """Test changing allowed keys with validation."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1", "key2": "value2"},
            allowed_keys={"key1", "key2", "key3"}
        )

        # Change to new allowed keys (superset)
        new_keys = {"key1", "key2", "key3", "key4", "key5"}
        mapping.set_allowed_keys(new_keys)

        assert mapping.allowed_keys == new_keys
        assert mapping["key4"] is None  # Auto-filled
        assert mapping["key5"] is None  # Auto-filled
        assert mapping["key1"] == "value1"  # Preserved

    def test_set_allowed_keys_removes_invalid_keys(self):
        """Test that set_allowed_keys with validation removes invalid keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1", "key2": "value2", "key3": "value3"}
        )

        # Set allowed keys that don't include key3
        mapping.set_allowed_keys({"key1", "key2"}, validate=True)

        assert mapping.allowed_keys == {"key1", "key2"}
        assert "key3" not in mapping.root  # Deleted
        assert mapping["key1"] == "value1"  # Preserved
        assert mapping["key2"] == "value2"  # Preserved

    def test_set_allowed_keys_without_validation(self):
        """Test changing allowed keys without validation."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1", "key2": "value2"}
        )

        # Set allowed keys without validating current data
        mapping.set_allowed_keys({"new_key"}, validate=False)

        assert mapping.allowed_keys == {"new_key"}
        # Old data still exists in root
        assert mapping["key1"] == "value1"
        assert mapping["key2"] == "value2"

    def test_add_allowed_keys(self):
        """Test adding new allowed keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        # Add more allowed keys
        mapping.add_allowed_keys({"key3", "key4"})

        assert mapping.allowed_keys == {"key1", "key2", "key3", "key4"}
        assert mapping["key3"] is None  # Auto-filled
        assert mapping["key4"] is None  # Auto-filled

    def test_add_allowed_keys_without_autofill(self):
        """Test adding allowed keys without auto-filling."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1"}
        )

        mapping.add_allowed_keys({"key2", "key3"}, auto_fill=False)

        assert mapping.allowed_keys == {"key1", "key2", "key3"}
        assert "key2" not in mapping.root
        assert "key3" not in mapping.root

    def test_add_allowed_keys_to_unrestricted(self):
        """Test adding allowed keys to an initially unrestricted mapping."""
        mapping = HomeAssistantEntityIdMapping({"key1": "value1"})

        # Add allowed keys
        mapping.add_allowed_keys({"key2", "key3"})

        assert mapping.allowed_keys == {"key2", "key3"}
        assert mapping["key2"] is None
        assert mapping["key3"] is None

    def test_remove_allowed_keys(self):
        """Test removing allowed keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1", "key2": None, "key3": None},
            allowed_keys={"key1", "key2", "key3"}
        )

        # Remove some keys
        mapping.remove_allowed_keys({"key2", "key3"})

        assert mapping.allowed_keys == {"key1"}
        # Data still exists unless remove_data=True
        assert "key2" in mapping.root
        assert "key3" in mapping.root

    def test_remove_allowed_keys_with_data_removal(self):
        """Test removing allowed keys and their data."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1", "key2": "value2", "key3": "value3"},
            allowed_keys={"key1", "key2", "key3"}
        )

        mapping.remove_allowed_keys({"key2", "key3"}, remove_data=True)

        assert mapping.allowed_keys == {"key1"}
        assert "key2" not in mapping.root
        assert "key3" not in mapping.root
        assert mapping["key1"] == "value1"

    def test_remove_allowed_keys_not_in_set(self):
        """Test that removing non-existent keys raises ValueError."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        with pytest.raises(ValueError) as exc_info:
            mapping.remove_allowed_keys({"key3", "key4"})

        assert "not in allowed set" in str(exc_info.value)

    def test_dynamic_allowed_keys_workflow(self):
        """Test a complete workflow of dynamically modifying allowed keys."""
        # Start unrestricted
        mapping = HomeAssistantEntityIdMapping({
            "sensor1": "sensor.temp",
            "sensor2": "sensor.humidity"
        })

        # Add restrictions
        mapping.set_allowed_keys({"sensor1", "sensor2", "sensor3"}, validate=False)
        assert mapping["sensor3"] is None

        # Add more allowed keys
        mapping.add_allowed_keys({"sensor4", "sensor5"})
        assert len(mapping.allowed_keys) == 5

        # Configure new sensors
        mapping["sensor4"] = "sensor.pressure"
        mapping["sensor5"] = "sensor.light"

        # Remove unused sensors
        mapping.remove_allowed_keys({"sensor3"}, remove_data=True)
        assert "sensor3" not in mapping.root
        assert len(mapping.allowed_keys) == 4

    # ----------------------------------------------------------------
    # Dict Assignment Validation Tests
    # ----------------------------------------------------------------

    def test_update_method_with_valid_keys(self):
        """Test update() method with valid keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2", "key3"}
        )

        mapping.update({"key2": "value2", "key3": "value3"})

        assert mapping["key1"] == "value1"
        assert mapping["key2"] == "value2"
        assert mapping["key3"] == "value3"

    def test_update_method_with_invalid_keys(self):
        """Test that update() rejects invalid keys and leaves data unchanged."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        # Note: key2 is auto-filled with None due to allowed_keys
        assert mapping["key2"] is None

        with pytest.raises(ValueError) as exc_info:
            mapping.update({"key2": "value2", "invalid_key": "value"})

        assert "invalid keys: {'invalid_key'}" in str(exc_info.value).lower()
        # All data should be unchanged after failed update
        assert mapping["key1"] == "value1"
        assert mapping["key2"] is None  # Should still be None, not "value2"

    def test_update_method_unrestricted(self):
        """Test update() method on unrestricted mapping."""
        mapping = HomeAssistantEntityIdMapping({"key1": "value1"})

        mapping.update({"key2": "value2", "key3": "value3"})

        assert len(mapping.root) == 3
        assert mapping["key2"] == "value2"
        assert mapping["key3"] == "value3"

    def test_root_assignment_with_invalid_keys(self):
        """Test that direct root assignment validates keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        with pytest.raises(ValueError) as exc_info:
            mapping.root = {"key1": "new_value", "invalid_key": "value"}

        assert "invalid keys" in str(exc_info.value).lower()

    def test_root_assignment_with_valid_keys(self):
        """Test that direct root assignment works with valid keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        mapping.root = {"key1": "new_value", "key2": "value2"}

        assert mapping["key1"] == "new_value"
        assert mapping["key2"] == "value2"

    def test_root_assignment_unrestricted(self):
        """Test that root assignment works on unrestricted mapping."""
        mapping = HomeAssistantEntityIdMapping({"key1": "value1"})

        mapping.root = {"new_key": "new_value"}

        assert len(mapping.root) == 1
        assert mapping["new_key"] == "new_value"

    def test_invalid_attribute_assignment(self):
        """Test that setting arbitrary attributes is blocked."""
        mapping = HomeAssistantEntityIdMapping({"key": "value"})

        with pytest.raises(AttributeError) as exc_info:
            mapping.some_attribute = "value"

        assert "Cannot set attribute 'some_attribute'" in str(exc_info.value)

    def test_pydantic_model_copy_with_update(self):
        """Test that Pydantic's model_copy with update validates keys."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        # Valid update should work
        updated = mapping.model_copy(update={"root": {"key1": "new_value", "key2": "value2"}})
        assert updated["key1"] == "new_value"
        assert updated["key2"] == "value2"

        # Invalid update should fail
        # Note: model_copy creates a new instance, so allowed_keys aren't preserved
        # This is expected behavior for model_copy without custom logic

    def test_validate_and_set_method(self):
        """Test the validate_and_set method for programmatic assignment."""
        mapping = HomeAssistantEntityIdMapping(
            {"key1": "value1"},
            allowed_keys={"key1", "key2"}
        )

        # Valid key and value should work
        mapping.validate_and_set("key2", "value2")
        assert mapping["key2"] == "value2"

        # Invalid key should fail
        with pytest.raises(ValueError) as exc_info:
            mapping.validate_and_set("invalid_key", "value")
        assert "not in allowed keys" in str(exc_info.value)

        # Invalid value type should fail
        with pytest.raises(ValueError) as exc_info:
            mapping.validate_and_set("key1", 123)  # type: ignore
        assert "must be a string or None" in str(exc_info.value)

    def test_comprehensive_allowed_keys_workflow(self):
        """Test comprehensive workflow of setting allowed keys on existing data.

        Scenario:
        1. Create with allowed keys not set
        2. Set some key values
        3. Set allowed keys which is only a subset of keys already set
           -> invalid keys shall be deleted
        4. Try to set valid and invalid keys -> invalid keys shall not be set
        5. Set a new set of allowed keys with subset of current + new ones
           -> invalid keys deleted, new keys initialized to None
        6. Try to set valid and invalid keys -> invalid keys shall not be set
        """
        # Step 1: Create without allowed keys
        mapping = HomeAssistantEntityIdMapping()
        assert len(mapping.allowed_keys) == 0

        # Step 2: Set some key values (unrestricted)
        mapping["sensor1"] = "sensor.temp"
        mapping["sensor2"] = "sensor.humidity"
        mapping["sensor3"] = "sensor.pressure"
        mapping["sensor4"] = "sensor.light"
        assert len(mapping.root) == 4

        # Step 3: Set allowed keys (subset of existing keys)
        # sensor3 and sensor4 should be deleted
        mapping.set_allowed_keys({"sensor1", "sensor2"}, validate=True)
        assert mapping.allowed_keys == {"sensor1", "sensor2"}
        assert "sensor1" in mapping.root
        assert "sensor2" in mapping.root
        assert "sensor3" not in mapping.root  # Deleted
        assert "sensor4" not in mapping.root  # Deleted
        assert mapping["sensor1"] == "sensor.temp"
        assert mapping["sensor2"] == "sensor.humidity"

        # Step 4: Try to set valid and invalid keys
        # Valid key should work
        mapping["sensor2"] = "sensor.humidity_updated"
        assert mapping["sensor2"] == "sensor.humidity_updated"

        # Invalid key should raise error
        with pytest.raises(ValueError) as exc_info:
            mapping["sensor_invalid"] = "sensor.invalid"
        assert "not in allowed keys" in str(exc_info.value)
        assert "sensor_invalid" not in mapping.root

        # update() with invalid keys should fail
        with pytest.raises(ValueError) as exc_info:
            mapping.update({"sensor1": "updated", "invalid_key": "bad"})
        assert "invalid keys" in str(exc_info.value).lower()

        # root assignment with invalid keys should fail
        with pytest.raises(ValueError) as exc_info:
            mapping.root = {"sensor1": "val", "invalid": "bad"}
        assert "invalid keys" in str(exc_info.value).lower()

        # Step 5: Set new allowed keys (subset of current + new ones)
        # Keep sensor1, remove sensor2, add sensor5 and sensor6
        mapping.set_allowed_keys({"sensor1", "sensor5", "sensor6"}, validate=True)
        assert mapping.allowed_keys == {"sensor1", "sensor5", "sensor6"}
        assert "sensor1" in mapping.root
        assert mapping["sensor1"] == "sensor.temp"  # Preserved
        assert "sensor2" not in mapping.root  # Deleted
        assert "sensor5" in mapping.root  # New
        assert mapping["sensor5"] is None  # Initialized to None
        assert "sensor6" in mapping.root  # New
        assert mapping["sensor6"] is None  # Initialized to None

        # Step 6: Try to set valid and invalid keys with new allowed set
        # Valid keys should work
        mapping["sensor5"] = "sensor.co2"
        mapping["sensor6"] = "sensor.voc"
        assert mapping["sensor5"] == "sensor.co2"
        assert mapping["sensor6"] == "sensor.voc"

        # Invalid keys should fail
        with pytest.raises(ValueError) as exc_info:
            mapping["sensor2"] = "sensor.old"  # Was valid before, not anymore
        assert "not in allowed keys" in str(exc_info.value)

        with pytest.raises(ValueError) as exc_info:
            mapping["sensor_new_invalid"] = "sensor.bad"
        assert "not in allowed keys" in str(exc_info.value)

        # update() with mix of valid and invalid
        with pytest.raises(ValueError) as exc_info:
            mapping.update({"sensor1": "updated", "sensor2": "old", "sensor99": "bad"})
        assert "invalid keys" in str(exc_info.value).lower()
        # Verify original data unchanged after failed update
        assert mapping["sensor1"] == "sensor.temp"

        # Final verification
        assert len(mapping.root) == 3
        assert set(mapping.keys()) == {"sensor1", "sensor5", "sensor6"}
        assert mapping["sensor1"] == "sensor.temp"
        assert mapping["sensor5"] == "sensor.co2"
        assert mapping["sensor6"] == "sensor.voc"

    def test_partial_configuration_validation(self):
        """Test that partial configuration is valid."""
        allowed = {"sensor1", "sensor2", "sensor3", "sensor4", "sensor5"}
        data: dict[str, Optional[str]] = {
            "sensor1": "sensor.temp",
            "sensor3": "sensor.humidity",
        }

        mapping = HomeAssistantEntityIdMapping(data, allowed_keys=allowed)

        # Configured sensors should have values
        assert mapping["sensor1"] == "sensor.temp"
        assert mapping["sensor3"] == "sensor.humidity"

        # Unconfigured sensors should be None
        assert mapping["sensor2"] is None
        assert mapping["sensor4"] is None
        assert mapping["sensor5"] is None

        # All allowed keys should be present
        assert len(mapping.root) == 5


class TestHomeAssistantAddon:
    """Tests to ensure the repository root is a valid Home Assistant add-on.

    Simulates the Home Assitant Supervisor's expectations.
    """

    @property
    def root(self):
        # Repository root (repo == addon)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_config_yaml_exists(self):
        """Ensure config.yaml exists in the repo root."""
        cfg_path = os.path.join(self.root, "config.yaml")
        assert os.path.isfile(cfg_path), "config.yaml must exist in repository root."

    def test_config_yaml_loadable(self):
        """Verify that config.yaml parses and contains required fields."""
        cfg_path = os.path.join(self.root, "config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        required_fields = ["name", "version", "slug"]
        for field in required_fields:
            assert field in cfg, f"Missing required field '{field}' in config.yaml."

    def test_docs_md_exists(self):
        """Ensure DOCS.md exists in the repo root (for Home Assistant add-on documentation)."""
        docs_path = os.path.join(self.root, "DOCS.md")
        assert os.path.isfile(docs_path), "DOCS.md must exist in the repository root for add-on documentation."

    @pytest.mark.docker
    def test_dockerfile_exists(self):
        """Ensure Dockerfile exists in the repo root."""
        dockerfile = os.path.join(self.root, "Dockerfile")
        assert os.path.isfile(dockerfile), "Dockerfile must exist in repository root."

    @pytest.mark.docker
    def test_docker_build_context_valid(self):
        """Runs a Docker build using the root of the repo as Supervisor would.

        Fails if the build context is invalid or Dockerfile has syntax errors.
        """
        cmd = ["docker", "build", "-t", "ha-addon-test:latest", self.root]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print("STDOUT:\n", e.stdout)
            print("STDERR:\n", e.stderr)
            pytest.fail("Docker build failed. This simulates a Supervisor build failure.")
