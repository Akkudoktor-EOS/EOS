import asyncio
import json
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
import pytest_asyncio
from pydantic import Field, PrivateAttr

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

# ---------------------------------------------------------------------------
# Derived classes for testing
# ---------------------------------------------------------------------------

class DerivedRecord(DataRecord):
    """DataRecord with a numeric field and configured field-like data."""

    data_value: Optional[float] = Field(default=None, description="Data Value")

    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        return ["dish_washer_emr", "solar_power", "temp"]


class DerivedDataProvider(DataProvider):
    """Concrete DataProvider for testing."""

    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = True
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    def db_namespace(self) -> str:
        return "DerivedDataProvider"

    def provider_id(self) -> str:
        return "DerivedDataProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        DerivedDataProvider.provider_updated = True


class DerivedDataContainer(DataContainer):
    providers: List[Union[DerivedDataProvider, DataProvider]] = Field(
        default_factory=list, description="List of data providers"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(date, value: float) -> DerivedRecord:
    return DerivedRecord(date_time=to_datetime(date), data_value=value)


async def make_provider_with_records() -> DerivedDataProvider:
    """Return a fresh provider with three hourly records."""
    provider = DerivedDataProvider()
    await provider.delete_by_datetime()          # wipe singleton state
    await provider.insert_by_datetime(make_record(datetime(2024, 1, 1, 0), 1.0))
    await provider.insert_by_datetime(make_record(datetime(2024, 1, 1, 1), 2.0))
    await provider.insert_by_datetime(make_record(datetime(2024, 1, 1, 2), 3.0))
    return provider


async def make_container() -> DerivedDataContainer:
    """Return a container with one populated provider."""
    provider = await make_provider_with_records()
    container = DerivedDataContainer()
    container.providers.clear()
    container.providers.append(provider)
    return container


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDataContainer:

    # -----------------------------------------------------------------------
    # Fixtures
    # -----------------------------------------------------------------------

    @pytest_asyncio.fixture
    async def container(self):
        """Empty container (no providers)."""
        c = DerivedDataContainer()
        c.providers.clear()
        return c

    @pytest_asyncio.fixture
    async def populated(self):
        """Container with one provider holding three records."""
        return await make_container()

    # -----------------------------------------------------------------------
    # Provider management
    # -----------------------------------------------------------------------

    async def test_append_provider(self, container):
        assert len(container.providers) == 0
        provider = DerivedDataProvider()
        container.providers.append(provider)
        assert len(container.providers) == 1
        assert isinstance(container.providers[0], DerivedDataProvider)

    async def test_enabled_providers_reflects_enabled_flag(self, populated):
        DerivedDataProvider.provider_enabled = True
        assert len(populated.enabled_providers) == 1

        DerivedDataProvider.provider_enabled = False
        assert len(populated.enabled_providers) == 0

        DerivedDataProvider.provider_enabled = True  # restore

    async def test_provider_by_id_found(self, populated):
        provider = populated.provider_by_id("DerivedDataProvider")
        assert isinstance(provider, DerivedDataProvider)

    async def test_provider_by_id_unknown_raises(self, populated):
        with pytest.raises(ValueError, match="Unknown provider id"):
            populated.provider_by_id("NonExistentProvider")

    # -----------------------------------------------------------------------
    # record_keys / record_keys_writable
    # -----------------------------------------------------------------------

    async def test_record_keys_contains_expected_fields(self, populated):
        keys = populated.record_keys
        assert "data_value" in keys
        assert "date_time" in keys
        # configured keys
        for k in ("dish_washer_emr", "solar_power", "temp"):
            assert k in keys

    async def test_record_keys_writable_contains_expected_fields(self, populated):
        keys = populated.record_keys_writable
        assert "data_value" in keys
        for k in ("dish_washer_emr", "solar_power", "temp"):
            assert k in keys

    async def test_record_keys_empty_when_no_providers(self, container):
        assert container.record_keys == []
        assert container.record_keys_writable == []

    # -----------------------------------------------------------------------
    # iter / len / repr / keys()
    # -----------------------------------------------------------------------

    async def test_iter_yields_record_keys(self, populated):
        keys = list(populated)
        assert "data_value" in keys

    async def test_len_equals_number_of_record_keys(self, populated):
        assert len(populated) == len(populated.record_keys)

    async def test_repr_contains_class_and_provider(self, populated):
        r = repr(populated)
        assert r.startswith("DerivedDataContainer(")
        assert "DerivedDataProvider" in r

    async def test_keys_view(self, populated):
        kv = populated.keys()
        assert "data_value" in kv

    # -----------------------------------------------------------------------
    # key_to_series
    # -----------------------------------------------------------------------

    async def test_key_to_series_returns_series(self, populated):
        series = await populated.key_to_series("data_value")
        assert isinstance(series, pd.Series)
        assert series.name == "data_value"

    async def test_key_to_series_values(self, populated):
        series = await populated.key_to_series("data_value")
        assert sorted(series.tolist()) == [1.0, 2.0, 3.0]

    async def test_key_to_series_with_datetime_range(self, populated):
        start = to_datetime(datetime(2024, 1, 1, 1))
        end = to_datetime(datetime(2024, 1, 1, 3))
        series = await populated.key_to_series("data_value", start_datetime=start, end_datetime=end)
        assert len(series) == 2
        assert sorted(series.tolist()) == [2.0, 3.0]

    async def test_key_to_series_unknown_key_raises(self, populated):
        with pytest.raises(KeyError, match="No data found for key"):
            await populated.key_to_series("non_existent_key")

    async def test_key_to_series_no_enabled_providers_raises(self, populated):
        DerivedDataProvider.provider_enabled = False
        try:
            with pytest.raises(KeyError, match="No data found for key"):
                await populated.key_to_series("data_value")
        finally:
            DerivedDataProvider.provider_enabled = True

    # -----------------------------------------------------------------------
    # key_to_array
    # -----------------------------------------------------------------------

    async def test_key_to_array_returns_ndarray(self, populated):
        start = to_datetime(datetime(2024, 1, 1, 0))
        end = to_datetime(datetime(2024, 1, 1, 3))
        array = await populated.key_to_array("data_value", start_datetime=start, end_datetime=end)
        assert isinstance(array, np.ndarray)
        assert len(array) == 3

    async def test_key_to_array_unknown_key_raises(self, populated):
        with pytest.raises(KeyError, match="No data found for key"):
            await populated.key_to_array("non_existent_key")

    # -----------------------------------------------------------------------
    # update_data
    # -----------------------------------------------------------------------

    async def test_update_data_calls_provider(self, populated):
        DerivedDataProvider.provider_updated = False
        DerivedDataProvider.provider_enabled = True
        await populated.update_data(force_enable=True)
        assert DerivedDataProvider.provider_updated is True

    async def test_update_data_skips_disabled_provider(self, populated):
        DerivedDataProvider.provider_enabled = False
        DerivedDataProvider.provider_updated = False
        await populated.update_data()
        assert DerivedDataProvider.provider_updated is False
        DerivedDataProvider.provider_enabled = True  # restore

    async def test_update_data_force_enable_runs_disabled_provider(self, populated):
        DerivedDataProvider.provider_enabled = False
        DerivedDataProvider.provider_updated = False
        await populated.update_data(force_enable=True)
        assert DerivedDataProvider.provider_updated is True
        DerivedDataProvider.provider_enabled = True  # restore

    # -----------------------------------------------------------------------
    # save / load
    # -----------------------------------------------------------------------

    async def test_save_and_load_roundtrip(self, populated):
        """Save then wipe in-memory records and verify load restores data if db is available."""
        start = to_datetime(datetime(2024, 1, 1, 0))
        end = to_datetime(datetime(2024, 1, 1, 3))

        # Confirm data is present before save
        series_before = await populated.key_to_series(
            "data_value", start_datetime=start, end_datetime=end
        )
        assert sorted(series_before.tolist()) == [1.0, 2.0, 3.0]

        for provider in populated.providers:
            assert provider.db_enabled == False

        saved = await populated.save()

        if not saved:
            # No database configured — verify save correctly reported nothing was persisted
            pytest.skip("No database configured, skipping roundtrip persistence check")

        # Wipe in-memory state only (not the database)
        for provider in populated.providers:
            provider.records.clear()
        assert all(len(p.records) == 0 for p in populated.providers)

        loaded = await populated.load()
        assert loaded is True

        # Verify data is restored via the public async API
        series_after = await populated.key_to_series(
            "data_value", start_datetime=start, end_datetime=end
        )
        assert sorted(series_after.tolist()) == [1.0, 2.0, 3.0]

    # -----------------------------------------------------------------------
    # db_get_stats
    # -----------------------------------------------------------------------

    async def test_db_get_stats_returns_dict(self, populated):
        stats = await populated.db_get_stats()
        assert isinstance(stats, dict)
        assert "DerivedDataProvider" in stats
