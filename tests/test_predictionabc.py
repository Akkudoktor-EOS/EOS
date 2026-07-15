import os
from datetime import datetime
from typing import Any, ClassVar, List, Optional, Union

import pandas as pd
import pendulum
import pytest
import pytest_asyncio
from pydantic import Field

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.prediction import PredictionCommonSettings
from akkudoktoreos.prediction.predictionabc import (
    PredictionABC,
    PredictionContainer,
    PredictionProvider,
    PredictionRecord,
    PredictionSequence,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# Derived classes for testing
# ---------------------------


class DerivedConfig(PredictionCommonSettings):
    env_var: Optional[int] = Field(default=None, description="Test config by environment var")
    instance_field: Optional[str] = Field(default=None, description="Test config by instance field")
    class_constant: Optional[int] = Field(default=None, description="Test config by class constant")


class DerivedBase(PredictionABC):
    instance_field: Optional[str] = Field(default=None, description="Field Value")
    class_constant: ClassVar[int] = 30


class DerivedRecord(PredictionRecord):
    prediction_value: Optional[float] = Field(default=None, description="Prediction Value")


class DerivedSequence(PredictionSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord


class DerivedPredictionProvider(PredictionProvider):
    """A concrete subclass of PredictionProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedPredictionProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedPredictionProvider.provider_updated = True


class DerivedPredictionContainer(PredictionContainer):
    providers: List[Union[DerivedPredictionProvider, PredictionProvider]] = Field(
        default_factory=list, description="List of prediction providers"
    )


# Tests
# ----------


class TestPredictionABC:
    @pytest.fixture
    def base(self, monkeypatch):
        # Provide default values for configuration
        monkeypatch.setenv("EOS_PREDICTION__HOURS", "10")
        derived = DerivedBase()
        derived.config.reset_settings()
        assert derived.config.prediction.hours == 10
        return derived

    def test_config_value_from_env_variable(self, base, monkeypatch):
        # From Prediction Config
        monkeypatch.setenv("EOS_PREDICTION__HOURS", "2")
        base.config.reset_settings()
        assert base.config.prediction.hours == 2

    def test_config_value_from_field_default(self, base, monkeypatch):
        assert base.config.prediction.__class__.model_fields["historic_hours"].default == 48
        assert base.config.prediction.historic_hours == 48
        monkeypatch.setenv("EOS_PREDICTION__HISTORIC_HOURS", "128")
        base.config.reset_settings()
        assert base.config.prediction.historic_hours == 128
        monkeypatch.delenv("EOS_PREDICTION__HISTORIC_HOURS")
        base.config.reset_settings()
        assert base.config.prediction.historic_hours == 48

    def test_get_config_value_key_error(self, base):
        with pytest.raises(AttributeError):
            base.config.prediction.non_existent_key


# TestPredictionRecord fully covered by TestDataRecord
# ----------------------------------------------------


# TestPredictionSequence fully covered by TestDataSequence
# --------------------------------------------------------


# TestPredictionStartEndKeepMixin fully covered by TestPredictionContainer
# --------------------------------------------------------


@pytest.mark.asyncio
class TestPredictionProvider:
    # Fixtures and helper functions
    @pytest.fixture
    def provider(self):
        """Fixture to provide an instance of TestPredictionProvider for testing."""
        DerivedPredictionProvider.provider_enabled = True
        DerivedPredictionProvider.provider_updated = False
        return DerivedPredictionProvider()

    @pytest.fixture
    def sample_start_datetime(self):
        """Fixture for a sample start datetime."""
        return to_datetime(datetime(2024, 11, 1, 12, 0))

    def create_test_record(self, date, value):
        """Helper function to create a test PredictionRecord."""
        return DerivedRecord(date_time=date, prediction_value=value)

    # Tests

    async def test_singleton_behavior(self, provider):
        """Test that PredictionProvider enforces singleton behavior."""
        instance1 = provider
        instance2 = DerivedPredictionProvider()
        assert instance1 is instance2, (
            "Singleton pattern is not enforced; instances are not the same."
        )

    async def test_update_computed_fields(self, provider, sample_start_datetime):
        """Test that computed fields `end_datetime` and `keep_datetime` are correctly calculated."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(sample_start_datetime)
        provider.config.prediction.hours = 24  # 24 hours into the future
        provider.config.prediction.historic_hours = 48  # 48 hours into the past

        expected_end_datetime = sample_start_datetime + to_duration(
            provider.config.prediction.hours * 3600
        )
        expected_keep_datetime = sample_start_datetime - to_duration(
            provider.config.prediction.historic_hours * 3600
        )

        assert provider.end_datetime == expected_end_datetime, (
            "End datetime is not calculated correctly."
        )
        assert provider.keep_datetime == expected_keep_datetime, (
            "Keep datetime is not calculated correctly."
        )

    async def test_update_method_with_defaults(
        self, provider, sample_start_datetime, config_eos, monkeypatch
    ):
        """Test the `update` method with default parameters."""
        # EOS config supersedes
        ems_eos = get_ems()
        # The following values are currently not set in EOS config, we can override
        monkeypatch.setenv("EOS_PREDICTION__HISTORIC_HOURS", "2")
        assert os.getenv("EOS_PREDICTION__HISTORIC_HOURS") == "2"
        provider.config.reset_settings()

        ems_eos.set_start_datetime(sample_start_datetime)
        await provider.update_data()

        assert provider.config.prediction.hours == config_eos.prediction.hours
        assert provider.config.prediction.historic_hours == 2
        assert provider.ems_start_datetime == sample_start_datetime
        assert provider.end_datetime == sample_start_datetime + to_duration(
            f"{provider.config.prediction.hours} hours"
        )
        assert provider.keep_datetime == sample_start_datetime - to_duration("2 hours")

    async def test_update_method_force_enable(self, provider, monkeypatch):
        """Test that `update` executes when `force_enable` is True, even if `enabled` is False."""
        # Preset values that are needed by update
        monkeypatch.setenv("EOS_GENERAL__LATITUDE", "37.7749")
        monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "-122.4194")

        # Override enabled to return False for this test
        DerivedPredictionProvider.provider_enabled = False
        DerivedPredictionProvider.provider_updated = False
        await provider.update_data(force_enable=True)
        assert provider.enabled() is False, "Provider should be disabled, but enabled() is True."
        assert DerivedPredictionProvider.provider_updated is True, (
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
        assert len(provider) == 1, (
            "Only one record should remain after deletion by datetime."
        )
        assert provider.records[0].date_time == sample_start_datetime - to_duration("3 hours"), (
            "Unexpected record remains."
        )


@pytest.mark.asyncio
class TestPredictionContainer:
    # Fixture and helpers
    @pytest.fixture
    def container(self):
        container = DerivedPredictionContainer()
        return container

    @pytest_asyncio.fixture
    async def container_with_providers(self):
        records = [
            # Test records - include 'prediction_value' key
            self.create_test_record(datetime(2023, 11, 5), 1),
            self.create_test_record(datetime(2023, 11, 6), 2),
            self.create_test_record(datetime(2023, 11, 7), 3),
        ]
        provider = DerivedPredictionProvider()
        await provider.delete_by_datetime(start_datetime=None, end_datetime=None)
        assert len(provider) == 0
        for record in records:
            await provider.insert_by_datetime(record)
        assert len(provider) == 3
        container = DerivedPredictionContainer()
        container.providers.clear()
        assert len(container.providers) == 0
        container.providers.append(provider)
        assert len(container.providers) == 1
        return container

    def create_test_record(self, date, value):
        """Helper function to create a test PredictionRecord."""
        return DerivedRecord(date_time=date, prediction_value=value)

    # Tests
    @pytest.mark.parametrize(
        "start, hours, end",
        [
            ("2024-11-10 00:00:00", 24, "2024-11-11 00:00:00"),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, "2024-08-11 00:00:00"),  # DST in Germany
            ("2024-03-31 00:00:00", 24, "2024-04-01 00:00:00"),  # DST change (23 hours/ day)
            ("2024-10-27 00:00:00", 24, "2024-10-28 00:00:00"),  # DST change (25 hours/ day)
            ("2024-11-10 00:00:00", 48, "2024-11-12 00:00:00"),  # No DST in Germany
            ("2024-08-10 00:00:00", 48, "2024-08-12 00:00:00"),  # DST in Germany
            ("2024-03-31 00:00:00", 48, "2024-04-02 00:00:00"),  # DST change (47 hours/ day)
            ("2024-10-27 00:00:00", 48, "2024-10-29 00:00:00"),  # DST change (49 hours/ day)
        ],
    )
    async def test_end_datetime(self, container, start, hours, end):
        """Test end datetime calculation from start datetime."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "hours": hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        expected = to_datetime(end, in_timezone="Europe/Berlin")
        assert compare_datetimes(container.end_datetime, expected).equal

    @pytest.mark.parametrize(
        "start, historic_hours, expected_keep",
        [
            # Standard case
            (
                pendulum.datetime(2024, 8, 10, 0, 0, tz="Europe/Berlin"),
                24,
                pendulum.datetime(2024, 8, 9, 0, 0, tz="Europe/Berlin"),
            ),
            # With DST, but should not affect historical data
            (
                pendulum.datetime(2024, 4, 1, 0, 0, tz="Europe/Berlin"),
                24,
                pendulum.datetime(2024, 3, 30, 23, 0, tz="Europe/Berlin"),
            ),
        ],
    )
    async def test_keep_datetime(self, container, start, historic_hours, expected_keep):
        """Test the `keep_datetime` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "historic_hours": historic_hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        expected = to_datetime(expected_keep, in_timezone="Europe/Berlin")
        assert compare_datetimes(container.keep_datetime, expected).equal

    @pytest.mark.parametrize(
        "start, hours, expected_hours",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-03-31 00:00:00", 24, 23),  # DST change in Germany (23 hours/ day)
            ("2024-10-27 00:00:00", 24, 25),  # DST change in Germany (25 hours/ day)
        ],
    )
    async def test_total_hours(self, container, start, hours, expected_hours):
        """Test the `total_hours` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "hours": hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        assert container.total_hours == expected_hours

    @pytest.mark.parametrize(
        "start, historic_hours, expected_hours",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-04-01 00:00:00", 24, 24),  # DST change on 2024-03-31 in Germany (23 hours/ day)
            ("2024-10-28 00:00:00", 24, 24),  # DST change on 2024-10-27 in Germany (25 hours/ day)
        ],
    )
    async def test_keep_hours(self, container, start, historic_hours, expected_hours):
        """Test the `keep_hours` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "historic_hours": historic_hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        assert container.keep_hours == expected_hours

    async def test_append_provider(self, container):
        assert len(container.providers) == 0
        container.providers.append(DerivedPredictionProvider())
        assert len(container.providers) == 1
        assert isinstance(container.providers[0], DerivedPredictionProvider)

    @pytest.mark.skip(reason="type check not implemented")
    async def test_append_provider_invalid_type(self, container):
        with pytest.raises(ValueError, match="must be an instance of PredictionProvider"):
            container.providers.append("not_a_provider")

    async def test_len(self, container_with_providers):
        assert len(container_with_providers) == 2

    async def test_repr(self, container_with_providers):
        representation = repr(container_with_providers)
        assert representation.startswith("DerivedPredictionContainer(")
        assert "DerivedPredictionProvider" in representation

    async def test_to_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container_other = DerivedPredictionContainer.from_json(json_str)
        assert container_other == container_with_providers

    async def test_from_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container = DerivedPredictionContainer.from_json(json_str)
        assert isinstance(container, DerivedPredictionContainer)
        assert len(container.providers) == 1
        assert container.providers[0] == container_with_providers.providers[0]

    async def test_provider_by_id(self, container_with_providers):
        provider = container_with_providers.provider_by_id("DerivedPredictionProvider")
        assert isinstance(provider, DerivedPredictionProvider)
