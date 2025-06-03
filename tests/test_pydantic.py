from typing import Optional

import pandas as pd
import pendulum
import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeData,
    PydanticDateTimeDataFrame,
    PydanticDateTimeSeries,
    PydanticModelNestedValueMixin,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime


class PydanticTestModel(PydanticBaseModel):
    datetime_field: pendulum.DateTime = Field(
        ..., description="A datetime field with pendulum support."
    )
    optional_field: Optional[str] = Field(default=None, description="An optional field.")


class Address(PydanticBaseModel):
    city: Optional[str] = None
    postal_code: Optional[str] = None


class User(PydanticBaseModel):
    name: str
    addresses: Optional[list[Address]] = None
    settings: Optional[dict[str, str]] = None


class TestPydanticModelNestedValueMixin:
    """Umbrella test class to group all test cases for `PydanticModelNestedValueMixin`."""

    @pytest.fixture
    def user_instance(self):
        """Fixture to initialize a sample User instance."""
        return User(name="Alice", addresses=None, settings=None)

    def test_get_key_types_for_simple_field(self):
        """Test _get_key_types for a simple string field."""
        key_types = PydanticModelNestedValueMixin._get_key_types(User, "name")
        assert key_types == [str], f"Expected [str], got {key_types}"

    def test_get_key_types_for_list_of_models(self):
        """Test _get_key_types for a list of Address models."""
        key_types = PydanticModelNestedValueMixin._get_key_types(User, "addresses")
        assert key_types == [list, Address], f"Expected [list, Address], got {key_types}"

    def test_get_key_types_for_dict_field(self):
        """Test _get_key_types for a dictionary field."""
        key_types = PydanticModelNestedValueMixin._get_key_types(User, "settings")
        assert key_types == [dict, str], f"Expected [dict, str], got {key_types}"

    def test_get_key_types_for_optional_field(self):
        """Test _get_key_types correctly handles Optional fields."""
        key_types = PydanticModelNestedValueMixin._get_key_types(Address, "city")
        assert key_types == [str], f"Expected [str], got {key_types}"

    def test_get_key_types_for_non_existent_field(self):
        """Test _get_key_types raises an error for non-existent field."""
        with pytest.raises(TypeError):
            PydanticModelNestedValueMixin._get_key_types(User, "unknown_field")

    def test_set_nested_value_in_model(self, user_instance):
        """Test setting nested value in a model field (Address -> city)."""
        assert user_instance.addresses is None

        user_instance.set_nested_value("addresses/0/city", "New York")

        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "New York", "The city should be set to 'New York'"

    def test_set_nested_value_in_dict(self, user_instance):
        """Test setting nested value in a dictionary field (settings -> theme)."""
        assert user_instance.settings is None

        user_instance.set_nested_value("settings/theme", "dark")

        assert user_instance.settings is not None
        assert user_instance.settings["theme"] == "dark", "The theme should be set to 'dark'"

    def test_set_nested_value_in_list(self, user_instance):
        """Test setting nested value in a list of models (addresses -> 1 -> city)."""
        user_instance.set_nested_value("addresses/1/city", "Los Angeles")

        # Check if the city in the second address is set correctly
        assert user_instance.addresses[1].city == "Los Angeles", (
            "The city at index 1 should be set to 'Los Angeles'"
        )

    def test_set_nested_value_in_optional_field(self, user_instance):
        """Test setting value in an Optional field (addresses)."""
        user_instance.set_nested_value("addresses/0", Address(city="Chicago"))

        # Check if the first address is set correctly
        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "Chicago", "The city should be set to 'Chicago'"

    def test_set_nested_value_with_empty_list(self):
        """Test setting value in an empty list of models."""
        user = User(name="Bob", addresses=[])
        user.set_nested_value("addresses/0/city", "Seattle")

        assert user.addresses is not None
        assert user.addresses[0].city == "Seattle", (
            "The first address should have the city 'Seattle'"
        )

    def test_set_nested_value_with_missing_key_in_dict(self, user_instance):
        """Test setting value in a dict when the key does not exist."""
        user_instance.set_nested_value("settings/language", "English")

        assert user_instance.settings["language"] == "English", (
            "The language setting should be 'English'"
        )

    def test_set_nested_value_for_non_existent_field(self):
        """Test attempting to set value for a non-existent field."""
        user = User(name="John")

        with pytest.raises(ValueError):
            user.set_nested_value("non_existent_field", "Some Value")

    def test_set_nested_value_with_invalid_type(self, user_instance):
        """Test setting value with an invalid type."""
        with pytest.raises(ValueError):
            user_instance.set_nested_value(
                "addresses/0/city", 1234
            )  # city should be a string, not an integer

    def test_set_nested_value_with_model_initialization(self):
        """Test setting a value in a model that should initialize a missing model."""
        user = User(name="James", addresses=None)
        user.set_nested_value("addresses/0/city", "Boston")

        assert user.addresses is not None
        assert user.addresses[0].city == "Boston", "The city should be set to 'Boston'"
        assert isinstance(user.addresses[0], Address), (
            "The first address should be an instance of Address"
        )


class TestPydanticBaseModel:
    def test_valid_pendulum_datetime(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        assert model.datetime_field == dt

    def test_invalid_datetime_string(self):
        with pytest.raises(ValidationError, match="Cannot convert 'invalid_datetime' to datetime"):
            PydanticTestModel(datetime_field="invalid_datetime")

    def test_iso8601_serialization(self):
        dt = pendulum.datetime(2024, 12, 21, 15, 0, 0)
        model = PydanticTestModel(datetime_field=dt)
        serialized = model.to_dict()
        expected_dt = to_datetime(dt)
        result_dt = to_datetime(serialized["datetime_field"])
        assert compare_datetimes(result_dt, expected_dt)

    def test_reset_to_defaults(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt, optional_field="some value")
        model.reset_to_defaults()
        assert model.datetime_field == dt
        assert model.optional_field is None

    def test_from_dict_and_to_dict(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        data = model.to_dict()
        restored_model = PydanticTestModel.from_dict(data)
        assert restored_model.datetime_field == dt

    def test_to_json_and_from_json(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        json_data = model.to_json()
        restored_model = PydanticTestModel.from_json(json_data)
        assert restored_model.datetime_field == dt


class TestPydanticDateTimeData:
    def test_valid_list_lengths(self):
        data = {
            "timestamps": ["2024-12-21T15:00:00+00:00"],
            "values": [100],
        }
        model = PydanticDateTimeData(root=data)
        assert pendulum.parse(model.root["timestamps"][0]) == pendulum.parse(
            "2024-12-21T15:00:00+00:00"
        )

    def test_invalid_list_lengths(self):
        data = {
            "timestamps": ["2024-12-21T15:00:00+00:00"],
            "values": [100, 200],
        }
        with pytest.raises(
            ValidationError, match="All lists in the dictionary must have the same length"
        ):
            PydanticDateTimeData(root=data)


class TestPydanticDateTimeDataFrame:
    def test_valid_dataframe(self):
        df = pd.DataFrame(
            {
                "value": [100, 200],
            },
            index=pd.to_datetime(["2024-12-21", "2024-12-22"]),
        )
        model = PydanticDateTimeDataFrame.from_dataframe(df)
        result = model.to_dataframe()

        # Check index
        assert len(result.index) == len(df.index)
        for i, dt in enumerate(df.index):
            expected_dt = to_datetime(dt)
            result_dt = to_datetime(result.index[i])
            assert compare_datetimes(result_dt, expected_dt).equal


class TestPydanticDateTimeSeries:
    def test_valid_series(self):
        series = pd.Series([100, 200], index=pd.to_datetime(["2024-12-21", "2024-12-22"]))
        model = PydanticDateTimeSeries.from_series(series)
        result = model.to_series()

        # Check index
        assert len(result.index) == len(series.index)
        for i, dt in enumerate(series.index):
            expected_dt = to_datetime(dt)
            result_dt = to_datetime(result.index[i])
            assert compare_datetimes(result_dt, expected_dt).equal
