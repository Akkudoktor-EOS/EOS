"""Tests for fixed electricity price prediction module."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.config.configabc import ValueTimeWindow, ValueTimeWindowSequence
from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpricefixed import (
    ElecPriceFixed,
    ElecPriceFixedCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import Duration, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")
FILE_TESTDATA_ELECPRICEFIXED_CONFIG_JSON = DIR_TESTDATA.joinpath("elecpricefixed_config.json")


class TestElecPriceFixedCommonSettings:
    """Tests for ElecPriceFixedCommonSettings model."""

    def test_create_settings_with_windows(self):
        """Test creating settings with time windows."""
        settings_dict = {
            "time_windows": {
                "windows": [
                    {
                        "start_time": "00:00",
                        "duration": "8 hours",
                        "value": 0.288
                    },
                    {
                        "start_time": "08:00",
                        "duration": "16 hours",
                        "value": 0.34
                    }
                ]
            }
        }

        settings = ElecPriceFixedCommonSettings(**settings_dict)
        assert settings is not None
        assert settings.time_windows is not None
        assert settings.time_windows.windows is not None
        assert len(settings.time_windows.windows) == 2

    def test_create_settings_without_windows(self):
        """Test creating settings without time windows."""
        settings = ElecPriceFixedCommonSettings()
        assert settings.time_windows is not None
        assert settings.time_windows.windows == []


@pytest.fixture
def provider(monkeypatch, config_eos):
    """Fixture to create a ElecPriceFixed provider instance."""
    # Set environment variables
    monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "ElecPriceFixed")

    # Create time windows
    time_windows = ValueTimeWindowSequence(
        windows=[
            ValueTimeWindow(
                start_time="00:00",
                duration="8 hours",
                value=0.288
            ),
            ValueTimeWindow(
                start_time="08:00",
                duration="16 hours",
                value=0.34
            )
        ]
    )

    # Create settings and assign to config
    config_eos.elecprice.elecpricefixed = ElecPriceFixedCommonSettings(time_windows=time_windows)

    ElecPriceFixed.reset_instance()
    return ElecPriceFixed()


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


class TestElecPriceFixed:
    """Tests for ElecPriceFixed provider."""

    def test_provider_id(self, provider):
        """Test provider ID returns correct value."""
        assert provider.provider_id() == "ElecPriceFixed"

    def test_singleton_instance(self, provider):
        """Test that ElecPriceFixed behaves as a singleton."""
        another_instance = ElecPriceFixed()
        assert provider is another_instance

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()

    def test_update_data_hourly_intervals(self, provider, config_eos):
        """Test updating data with hourly intervals (3600s)."""
        # Set start datetime
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        # Configure hourly intervals
        config_eos.optimization.interval = 3600
        config_eos.prediction.hours = 24

        # Update data
        provider.update_data(force_enable=True, force_update=True)

        # Verify data was generated
        assert len(provider) == 24  # 24 hours * 1 interval per hour

        # Check prices
        records = provider.records

        # First 8 hours should be night rate (0.288 kWh = 0.000288 Wh)
        for i in range(8):
            assert abs(records[i].elecprice_marketprice_wh - 0.000288) < 1e-6
            # Verify timestamps are on hour boundaries
            assert records[i].date_time.minute == 0
            assert records[i].date_time.second == 0

        # Next 16 hours should be day rate (0.34 kWh = 0.00034 Wh)
        for i in range(8, 24):
            assert abs(records[i].elecprice_marketprice_wh - 0.00034) < 1e-6

    def test_update_data_15min_intervals(self, provider, config_eos):
        """Test updating data with 15-minute intervals (900s)."""
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        config_eos.optimization.interval = 900
        config_eos.prediction.hours = 10  # spans both windows: 00:00–10:00 = 40 intervals

        provider.update_data(force_enable=True, force_update=True)

        # 10 hours * 4 intervals per hour = 40 intervals
        assert len(provider) == 40

        records = provider.records

        # Check timestamps are on 15-minute boundaries
        for record in records:
            assert record.date_time.minute in (0, 15, 30, 45)
            assert record.date_time.second == 0

        # First 32 intervals: 00:00–08:00, night rate (8h * 4 = 32)
        for i in range(32):
            assert abs(records[i].elecprice_marketprice_wh - 0.000288) < 1e-6, (
                f"Expected night rate at interval {i}, got {records[i].elecprice_marketprice_wh}"
            )

        # Remaining 8 intervals: 08:00–10:00, day rate (2h * 4 = 8)
        for i in range(32, 40):
            assert abs(records[i].elecprice_marketprice_wh - 0.00034) < 1e-6, (
                f"Expected day rate at interval {i}, got {records[i].elecprice_marketprice_wh}"
            )

    def test_update_data_30min_intervals(self, provider, config_eos):
        """Test updating data with 30-minute intervals (1800s)."""
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        config_eos.optimization.interval = 1800
        config_eos.prediction.hours = 10  # spans both windows: 00:00–10:00 = 20 intervals

        provider.update_data(force_enable=True, force_update=True)

        # 10 hours * 2 intervals per hour = 20 intervals
        assert len(provider) == 20

        records = provider.records

        # Check timestamps are on 30-minute boundaries
        for record in records:
            assert record.date_time.minute in (0, 30)
            assert record.date_time.second == 0

        # First 16 intervals: 00:00–08:00, night rate (8h * 2 = 16)
        for i in range(16):
            assert abs(records[i].elecprice_marketprice_wh - 0.000288) < 1e-6, (
                f"Expected night rate at interval {i}, got {records[i].elecprice_marketprice_wh}"
            )

        # Remaining 4 intervals: 08:00–10:00, day rate (2h * 2 = 4)
        for i in range(16, 20):
            assert abs(records[i].elecprice_marketprice_wh - 0.00034) < 1e-6, (
                f"Expected day rate at interval {i}, got {records[i].elecprice_marketprice_wh}"
            )

    def test_update_data_without_config(self, provider, config_eos):
        """Test update_data fails without configuration."""
        # Remove elecpricefixed settings
        config_eos.elecprice.elecpricefixed = {}

        with pytest.raises(ValueError, match="No time windows configured"):
            provider.update_data(force_enable=True, force_update=True)

    def test_update_data_without_time_windows(self, provider, config_eos):
        """Test update_data fails without time windows."""
        # Set empty time windows
        empty_settings = ElecPriceFixedCommonSettings(time_windows=ValueTimeWindowSequence(windows=[]))
        config_eos.elecprice.elecpricefixed = empty_settings

        with pytest.raises(ValueError, match="No time windows configured"):
            provider.update_data(force_enable=True, force_update=True)

    def test_key_to_array_resampling(self, provider, config_eos):
        """Test that key_to_array can resample to different intervals."""
        # Setup provider with hourly data
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        config_eos.optimization.interval = 3600
        config_eos.prediction.hours = 24

        provider.update_data(force_enable=True, force_update=True)

        # Get data as hourly array (original)
        hourly_array = provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24)
        )

        assert len(hourly_array) == 24
        assert abs(hourly_array[0] - 0.000288) < 1e-6  # Night rate
        assert abs(hourly_array[8] - 0.00034) < 1e-6   # Day rate

        # Resample to 15-minute intervals
        quarter_hour_array = provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            interval="15 minutes"
        )

        assert len(quarter_hour_array) == 96  # 24 * 4
        # First 4 15-min intervals should be night rate
        for i in range(4):
            assert abs(quarter_hour_array[i] - 0.000288) < 1e-6

        # Resample to 30-minute intervals
        half_hour_array = provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            interval="30 minutes"
        )

        assert len(half_hour_array) == 48  # 24 * 2
        # First 2 30-min intervals should be night rate
        for i in range(2):
            assert abs(half_hour_array[i] - 0.000288) < 1e-6


class TestElecPriceFixedIntegration:
    """Integration tests for ElecPriceFixed."""

    @pytest.mark.skip(reason="For development only")
    def test_fixed_price_development(self, config_eos):
        """Test fixed price provider with real configuration."""
        # Create provider with config
        provider = ElecPriceFixed()

        # Setup realistic test scenario
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        # Configure with realistic German electricity prices (2024)
        time_windows = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(
                    start_time="00:00",
                    duration="8 hours",
                    value=0.288  # Night rate
                ),
                ValueTimeWindow(
                    start_time="08:00",
                    duration="16 hours",
                    value=0.34   # Day rate
                )
            ]
        )

        config_eos.elecprice.elecpricefixed = ElecPriceFixedCommonSettings(time_windows=time_windows)
        config_eos.prediction.hours = 168  # 7 days
        config_eos.optimization.interval = 900  # 15 minutes

        # Update data
        provider.update_data(force_enable=True, force_update=True)

        # Verify data
        expected_intervals = 168 * 4  # 7 days * 24h * 4 intervals
        assert len(provider) == expected_intervals

        # Save configuration for documentation
        config_data = {
            "time_windows": [
                {
                    "start_time": str(window.start_time),
                    "duration": str(window.duration),
                    "value": window.value
                }
                for window in config_eos.elecprice.elecpricefixed.time_windows.windows
            ]
        }

        with FILE_TESTDATA_ELECPRICEFIXED_CONFIG_JSON.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
