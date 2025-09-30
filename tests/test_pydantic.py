from typing import Any, Optional

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
    merge_models,
)
from akkudoktoreos.utils.datetimeutil import DateTime, compare_datetimes, to_datetime


class PydanticTestModel(PydanticBaseModel):
    datetime_field: DateTime = Field(
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


class SampleNestedModel(PydanticBaseModel):
    threshold: int
    enabled: bool = True


class SampleModel(PydanticBaseModel):
    name: str
    count: int
    config: SampleNestedModel
    optional: str | None = None


class TestMergeModels:
    """Test suite for the merge_models utility function with None overriding."""

    def test_flat_override(self):
        """Top-level fields in update_dict override those in source, including None."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5})
        update = {"name": "Updated"}
        result = merge_models(source, update)

        assert result["name"] == "Updated"
        assert result["count"] == 10
        assert result["config"]["threshold"] == 5

    def test_flat_override_with_none(self):
        """Update with None value should override source value."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5}, optional="keep me")
        update = {"optional": None}
        result = merge_models(source, update)

        assert result["optional"] is None

    def test_nested_override(self):
        """Nested fields in update_dict override nested fields in source, including None."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5, "enabled": True})
        update = {"config": {"threshold": 99, "enabled": False}}
        result = merge_models(source, update)

        assert result["config"]["threshold"] == 99
        assert result["config"]["enabled"] is False

    def test_nested_override_with_none(self):
        """Nested update with None should override nested source values."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5, "enabled": True})
        update = {"config": {"threshold": None}}
        result = merge_models(source, update)

        assert result["config"]["threshold"] is None
        assert result["config"]["enabled"] is True  # untouched because not in update

    def test_preserve_source_values(self):
        """Source values are preserved if not overridden in update_dict."""
        source = SampleModel(name="Source", count=7, config={"threshold": 1})
        update: dict[str, Any] = {}
        result = merge_models(source, update)

        assert result["name"] == "Source"
        assert result["count"] == 7
        assert result["config"]["threshold"] == 1

    def test_update_extends_source(self):
        """Optional fields in update_dict are added to result."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5})
        update = {"optional": "new value"}
        result = merge_models(source, update)

        assert result["optional"] == "new value"

    def test_update_extends_source_with_none(self):
        """Optional field with None in update_dict is added and overrides source."""
        source = SampleModel(name="Test", count=10, config={"threshold": 5}, optional="value")
        update = {"optional": None}
        result = merge_models(source, update)

        assert result["optional"] is None

    def test_deep_merge_behavior(self):
        """Nested updates merge with source, overriding only specified subkeys."""
        source = SampleModel(name="Model", count=3, config={"threshold": 1, "enabled": False})
        update = {"config": {"enabled": True}}
        result = merge_models(source, update)

        assert result["config"]["enabled"] is True
        assert result["config"]["threshold"] == 1

    def test_override_all(self):
        """All fields in update_dict override all fields in source, including None."""
        source = SampleModel(name="Orig", count=1, config={"threshold": 10, "enabled": True})
        update = {
            "name": "New",
            "count": None,
            "config": {"threshold": 50, "enabled": None}
        }
        result = merge_models(source, update)

        assert result["name"] == "New"
        assert result["count"] is None
        assert result["config"]["threshold"] == 50
        assert result["config"]["enabled"] is None


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

    def test_get_key_types_for_instance_raises(self, user_instance):
        """Test _get_key_types raises an error for an instance."""
        with pytest.raises(TypeError):
            PydanticModelNestedValueMixin._get_key_types(user_instance, "unknown_field")

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

        with pytest.raises(TypeError):
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

    def test_track_nested_value_simple_callback(self, user_instance):
        user_instance.set_nested_value("addresses/0/city", "NY")
        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "NY"

        callback_calls = []
        def cb(model, path, old, new):
            callback_calls.append((path, old, new))

        user_instance.track_nested_value("addresses/0/city", cb)
        user_instance.set_nested_value("addresses/0/city", "LA")
        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "LA"
        assert callback_calls == [("addresses/0/city", "NY", "LA")]

    def test_track_nested_value_prefix_triggers(self, user_instance):
        user_instance.set_nested_value("addresses/0", Address(city="Berlin", postal_code="10000"))
        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "Berlin"

        cb_prefix = []
        cb_exact = []

        def cb1(model, path, old, new):
            cb_prefix.append((path, old, new))
        def cb2(model, path, old, new):
            cb_exact.append((path, old, new))

        user_instance.track_nested_value("addresses/0", cb1)
        user_instance.track_nested_value("addresses/0/city", cb2)
        user_instance.set_nested_value("addresses/0/city", "Munich")
        assert user_instance.addresses is not None
        assert user_instance.addresses[0].city == "Munich"

        # Both callbacks should be triggered
        assert cb_prefix == [("addresses/0/city", "Berlin", "Munich")]
        assert cb_exact == [("addresses/0/city", "Berlin", "Munich")]

    def test_track_nested_value_multiple_callbacks_same_path(self, user_instance):
        user_instance.set_nested_value("addresses/0/city", "Berlin")
        calls1 = []
        calls2 = []

        user_instance.track_nested_value("addresses/0/city", lambda lib, path, o, n: calls1.append((path, o, n)))
        user_instance.track_nested_value("addresses/0/city", lambda lib, path, o, n: calls2.append((path, o, n)))
        user_instance.set_nested_value("addresses/0/city", "Stuttgart")

        assert calls1 == [("addresses/0/city", "Berlin", "Stuttgart")]
        assert calls2 == [("addresses/0/city", "Berlin", "Stuttgart")]

    def test_track_nested_value_invalid_path_raises(self, user_instance):
        with pytest.raises(ValueError) as excinfo:
            user_instance.track_nested_value("unknown_field", lambda model, path, o, n: None)
        assert "is invalid" in str(excinfo.value)

        with pytest.raises(ValueError) as excinfo:
            user_instance.track_nested_value("unknown_field/0/city", lambda model, path, o, n: None)
        assert "is invalid" in str(excinfo.value)

    def test_track_nested_value_list_and_dict_path(self):
        class Book(PydanticBaseModel):
            title: str

        class Library(PydanticBaseModel):
            books: list[Book]
            meta: dict[str, str] = {}

        lib = Library(books=[Book(title="A")], meta={"location": "center"})
        assert lib.meta["location"] == "center"
        calls = []

        # For list, only root attribute structure is checked, not indices
        lib.track_nested_value("books/0/title", lambda lib, path, o, n: calls.append((path, o, n)))
        lib.set_nested_value("books/0/title", "B")
        assert lib.books[0].title == "B"
        assert calls == [("books/0/title", "A", "B")]

        # For dict, only root attribute structure is checked
        meta_calls = []
        lib.track_nested_value("meta/location", lambda lib, path, o, n: meta_calls.append((path, o, n)))
        assert lib.meta["location"] == "center"
        lib.set_nested_value("meta/location", "north")
        assert lib.meta["location"] == "north"
        assert meta_calls == [("meta/location", "center", "north")]


class TestPydanticBaseModel:
    def test_valid_pendulum_datetime(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        assert model.datetime_field == dt

    def test_invalid_datetime_string(self):
        with pytest.raises(ValueError):
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
        """Ensure conversion from and to DataFrame preserves index and values."""
        df = pd.DataFrame(
            {
                "value": [100, 200],
            },
            index=pd.to_datetime(["2024-12-21", "2024-12-22"]),
        )
        model = PydanticDateTimeDataFrame.from_dataframe(df)
        result = model.to_dataframe()

        assert len(result.index) == len(df.index)
        for i, dt in enumerate(df.index):
            expected_dt = to_datetime(dt)
            result_dt = to_datetime(result.index[i])
            assert compare_datetimes(result_dt, expected_dt).equal

    def test_add_row(self):
        """Verify that a new row can be inserted with matching columns."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.add_row("2024-12-22T00:00:00", {"value": 200})

        # Normalize key the same way the model stores it
        key = model._normalize_index("2024-12-22T00:00:00")

        assert key in model.data
        assert model.data[key]["value"] == 200

    def test_add_row_column_mismatch_raises(self):
        """Ensure adding a row with mismatched columns raises ValueError."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        with pytest.raises(ValueError):
            model.add_row("2024-12-22T00:00:00", {"wrong": 200})

    def test_update_row(self):
        """Check updating an existing row's values works."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.update_row("2024-12-21T00:00:00", {"value": 999})

        key = model._normalize_index("2024-12-21T00:00:00")
        assert model.data[key]["value"] == 999

    def test_update_row_missing_raises(self):
        """Verify updating a non-existing row raises KeyError."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        with pytest.raises(KeyError):
            model.update_row("2024-12-22T00:00:00", {"value": 999})

    def test_delete_row(self):
        """Ensure rows can be deleted by index."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.delete_row("2024-12-21T00:00:00")
        assert "2024-12-21T00:00:00" not in model.data

    def test_set_and_get_value(self):
        """Confirm set_value and get_value operate correctly."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.set_value("2024-12-21T00:00:00", "value", 555)
        assert model.get_value("2024-12-21T00:00:00", "value") == 555

    def test_add_column(self):
        """Check that a new column can be added with default value."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.add_column("extra", default=0, dtype="int64")

        key = model._normalize_index("2024-12-21T00:00:00")
        assert model.data[key]["extra"] == 0
        assert model.dtypes["extra"] == "int64"

    def test_rename_column(self):
        """Ensure renaming a column updates all rows and dtypes."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100}}, dtypes={"value": "int64"}
        )
        model.rename_column("value", "renamed")

        key = model._normalize_index("2024-12-21T00:00:00")
        assert "renamed" in model.data[key]
        assert "value" not in model.data[key]
        assert model.dtypes["renamed"] == "int64"

    def test_drop_column(self):
        """Verify dropping a column removes it from both data and dtypes."""
        model = PydanticDateTimeDataFrame(
            data={"2024-12-21T00:00:00": {"value": 100, "extra": 1}}, dtypes={"value": "int64", "extra": "int64"}
        )
        model.drop_column("extra")

        key = model._normalize_index("2024-12-21T00:00:00")
        assert "extra" not in model.data[key]
        assert "extra" not in model.dtypes


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
