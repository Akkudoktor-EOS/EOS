import asyncio
import json
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
from pydantic import Field, PrivateAttr, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.dataabc import (
    DataABC,
    DataContainer,
    DataImportProvider,
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# Derived classes for testing
# ---------------------------

class DerivedConfig(SettingsBaseModel):
    env_var: Optional[int] = Field(default=None, description="Test config by environment var")
    instance_field: Optional[str] = Field(default=None, description="Test config by instance field")
    class_constant: Optional[int] = Field(default=None, description="Test config by class constant")


class DerivedBase(DataABC):
    instance_field: Optional[str] = Field(default=None, description="Field Value")
    class_constant: ClassVar[int] = 30


class DerivedRecord(DataRecord):
    """Date Record derived from base class DataRecord.

    The derived data record got the
    - `data_value` field and the
    - `dish_washer_emr`, `solar_power`, `temp` configurable field like data.
    """

    data_value: Optional[float] = Field(default=None, description="Data Value")

    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        return ["dish_washer_emr", "solar_power", "temp"]


class DerivedSequence(DataSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    def db_namespace(self) -> str:
        return "DerivedSequence"


class DerivedSequence2(DataSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    def db_namespace(self) -> str:
        return "DerivedSequence2"


class DerivedDataProvider(DataProvider):
    """A concrete subclass of DataProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    def db_namespace(self) -> str:
        return "DerivedDataProvider"

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedDataProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedDataProvider.provider_updated = True


class DerivedDataImportProvider(DataImportProvider):
    """A concrete subclass of DataImportProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False
    _updates: list = PrivateAttr(default_factory=list)

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedDataImportProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedDataProvider.provider_updated = True

    async def _update_value(self, date, *args, **kwargs) -> None:
        # Simulate update logic
        self._updates.append((date, args, kwargs))
        await super()._update_value(date, *args, **kwargs)


class DerivedDataContainer(DataContainer):
    providers: List[Union[DerivedDataProvider, DataProvider]] = Field(
        default_factory=list, description="List of data providers"
    )


# Tests
# ----------


@pytest.mark.asyncio
class TestDataProvider:
    # Fixtures and helper functions
    @pytest.fixture
    def provider(self):
        """Fixture to provide an instance of TestDataProvider for testing."""
        DerivedDataProvider.provider_enabled = True
        DerivedDataProvider.provider_updated = False
        return DerivedDataProvider()

    @pytest.fixture
    def sample_start_datetime(self):
        """Fixture for a sample start datetime."""
        return to_datetime(datetime(2024, 11, 1, 12, 0))

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    # Tests

    async def test_singleton_behavior(self, provider):
        """Test that DataProvider enforces singleton behavior."""
        instance1 = provider
        instance2 = DerivedDataProvider()
        assert instance1 is instance2, (
            "Singleton pattern is not enforced; instances are not the same."
        )

    async def test_update_method_with_defaults(self, provider, sample_start_datetime, monkeypatch):
        """Test the `update` method with default parameters."""
        ems_eos = get_ems()

        ems_eos.set_start_datetime(sample_start_datetime)
        await provider.update_data()

        assert provider.ems_start_datetime == sample_start_datetime

    async def test_update_method_force_enable(self, provider, monkeypatch):
        """Test that `update` executes when `force_enable` is True, even if `enabled` is False."""
        # Override enabled to return False for this test
        DerivedDataProvider.provider_enabled = False
        DerivedDataProvider.provider_updated = False
        await provider.update_data(force_enable=True)
        assert provider.enabled() is False, "Provider should be disabled, but enabled() is True."
        assert DerivedDataProvider.provider_updated is True, (
            "Provider should have been executed, but was not."
        )

    async def test_delete_by_datetime(self, provider, sample_start_datetime):
        """Test `delete_by_datetime` method for removing records by datetime range."""
        # Add records to the provider for deletion testing
        records = [
            self.create_test_record(sample_start_datetime - to_duration("3 hours"), 1),
            self.create_test_record(sample_start_datetime - to_duration("1 hour"), 2),
            self.create_test_record(sample_start_datetime + to_duration("1 hour"), 3),
        ]
        for record in records:
            await provider.insert_by_datetime(record)

        await provider.delete_by_datetime(
            start_datetime=sample_start_datetime - to_duration("2 hours"),
            end_datetime=sample_start_datetime + to_duration("2 hours"),
        )
        assert len(provider.records) == 1, (
            "Only one record should remain after deletion by datetime."
        )
        assert provider.records[0].date_time == sample_start_datetime - to_duration("3 hours"), (
            "Unexpected record remains."
        )


@pytest.mark.asyncio
class TestDataImportProvider:

    @pytest.fixture
    def provider(self):
        DerivedDataImportProvider.provider_enabled = True
        DerivedDataImportProvider.provider_updated = True
        p = DerivedDataImportProvider()
        p._updates.clear()
        p.records.clear()
        return p

    async def test_import_from_dict_basic(self, provider):
        data = {
            "start_datetime": "2024-01-01 00:00:00",
            "interval": "1 hour",
            "solar_power": [1, 2, 3],
        }
        await provider.import_from_dict(data)
        assert provider.records is not None
        assert provider.records[0]["solar_power"] == 1
        assert provider.records[1]["solar_power"] == 2

    async def test_import_from_dict_default_start_and_interval(self, provider):
        data = {"solar_power": [10, 20]}
        await provider.import_from_dict(data)
        assert len(provider._updates) == 2

    async def test_import_from_dict_with_prefix(self, provider):
        data = {
            "dish_washer_emr": [1, 2],
            "data_value": [5, 6],
        }
        await provider.import_from_dict(data, key_prefix="dish")
        assert len(provider._updates) == 2
        assert all(update[1][0] == "dish_washer_emr" for update in provider._updates)

    async def test_import_from_dict_mismatching_lengths(self, provider):
        data = {
            "solar_power": [1, 2],
            "temp": [1],
        }
        with pytest.raises(ValueError):
            await provider.import_from_dict(data)

    async def test_import_from_dict_invalid_interval(self, provider):
        data = {
            "interval": "17 minutes",
            "solar_power": [1, 2, 3],
        }
        with pytest.raises(NotImplementedError):
            await provider.import_from_dict(data)

    async def test_import_from_dict_skips_none_and_nan(self, provider):
        data = {"solar_power": [1, None, np.nan, 4]}
        await provider.import_from_dict(data)
        assert len(provider._updates) == 2
        assert provider._updates[0][1][1] == 1
        assert provider._updates[1][1][1] == 4

    async def test_import_from_dict_invalid_value_type(self, provider):
        data = {"solar_power": "not a list"}
        with pytest.raises(ValueError):
            await provider.import_from_dict(data)

    async def test_import_from_dataframe_with_datetime_index(self, provider):
        index = pd.date_range("2024-01-01", periods=3, freq="h")
        df = pd.DataFrame({"solar_power": [1, 2, 3]}, index=index)
        await provider.import_from_dataframe(df)
        assert len(provider._updates) == 3
        assert provider._updates[0][1][1] == 1

    async def test_import_from_dataframe_without_datetime_index(self, provider):
        df = pd.DataFrame({"solar_power": [5, 6, 7]})
        await provider.import_from_dataframe(
            df,
            start_datetime=to_datetime(datetime(2024, 1, 1)),
            interval=to_duration("1 hour"),
        )
        assert len(provider._updates) == 3

    async def test_import_from_dataframe_prefix_filter(self, provider):
        df = pd.DataFrame({
            "dish_washer_emr": [1, 2],
            "data_value": [3, 4],
        })
        await provider.import_from_dataframe(df, key_prefix="dish")
        assert len(provider._updates) == 2
        assert all(update[1][0] == "dish_washer_emr" for update in provider._updates)

    async def test_import_from_dataframe_invalid_input(self, provider):
        with pytest.raises(ValueError):
            await provider.import_from_dataframe("not a dataframe")

    async def test_import_from_json_simple_dict(self, provider):
        json_str = json.dumps({"solar_power": [1, 2, 3]})
        await provider.import_from_json(json_str)
        assert len(provider._updates) == 3

    async def test_import_from_json_invalid(self, provider):
        with pytest.raises(ValueError):
            await provider.import_from_json("this is not json")

    async def test_import_from_file(self, provider, tmp_path):
        file_path = tmp_path / "data.json"
        file_path.write_text(json.dumps({"solar_power": [1, 2]}))
        await provider.import_from_file(file_path)
        assert len(provider._updates) == 2
