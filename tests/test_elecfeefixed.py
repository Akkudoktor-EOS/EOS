"""Tests for fixed electricity fee prediction module."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.config.configabc import ValueTimeWindow, ValueTimeWindowSequence
from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecfeefixed import (
    ElecFeeFixed,
    ElecFeeFixedCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import Duration, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")
FILE_TESTDATA_ELECFEEFIXED_CONFIG_JSON = DIR_TESTDATA.joinpath("elecfeefixed_config.json")


class TestElecFeeFixedCommonSettings:
    """Tests for ElecFeeFixedCommonSettings model."""

    def test_create_settings_with_consumption_amt_kwh(self):
        """Test creating settings with consumption_amt_kwh windows."""
        settings_dict = {
            "consumption_amt_kwh": {
                "windows": [
                    {"start_time": "00:00", "duration": "8 hours", "value": 0.00288},
                    {"start_time": "08:00", "duration": "16 hours", "value": 0.0034},
                ]
            }
        }

        settings = ElecFeeFixedCommonSettings(**settings_dict)
        assert settings is not None
        assert settings.consumption_amt_kwh is not None
        assert settings.consumption_amt_kwh.windows is not None
        assert len(settings.consumption_amt_kwh.windows) == 2

    def test_create_settings_with_consumption_percent_amt(self):
        """Test creating settings with consumption_percent_amt windows."""
        settings_dict = {
            "consumption_percent_amt": {
                "windows": [
                    {"start_time": "00:00", "duration": "24 hours", "value": 19.0},
                ]
            }
        }

        settings = ElecFeeFixedCommonSettings(**settings_dict)
        assert settings is not None
        assert settings.consumption_percent_amt is not None
        assert len(settings.consumption_percent_amt.windows) == 1

    def test_create_settings_with_feedin_amt_kwh(self):
        """Test creating settings with feedin_amt_kwh windows."""
        settings_dict = {
            "feedin_amt_kwh": {
                "windows": [
                    {"start_time": "00:00", "duration": "8 hours", "value": 0.00008},
                    {"start_time": "08:00", "duration": "16 hours", "value": 0.0001},
                ]
            }
        }

        settings = ElecFeeFixedCommonSettings(**settings_dict)
        assert settings is not None
        assert settings.feedin_amt_kwh is not None
        assert len(settings.feedin_amt_kwh.windows) == 2

    def test_create_settings_with_feedin_percent_amt(self):
        """Test creating settings with feedin_percent_amt windows."""
        settings_dict = {
            "feedin_percent_amt": {
                "windows": [
                    {"start_time": "00:00", "duration": "24 hours", "value": 5.0},
                ]
            }
        }

        settings = ElecFeeFixedCommonSettings(**settings_dict)
        assert settings is not None
        assert settings.feedin_percent_amt is not None
        assert len(settings.feedin_percent_amt.windows) == 1

    def test_create_settings_without_windows(self):
        """Test creating settings without any windows configured."""
        settings = ElecFeeFixedCommonSettings()
        assert settings.consumption_amt_kwh is not None
        assert settings.consumption_amt_kwh.windows == []
        assert settings.consumption_percent_amt is not None
        assert settings.consumption_percent_amt.windows == []
        assert settings.feedin_amt_kwh is not None
        assert settings.feedin_amt_kwh.windows == []
        assert settings.feedin_percent_amt is not None
        assert settings.feedin_percent_amt.windows == []


@pytest.fixture
def elecfeefixed_settings():
    """Fully configured ElecFeeFixedCommonSettings covering all 4 sequences.

    Rates are chosen to be distinguishable per sequence and per window so
    that assertions can pin down exactly which value landed at which
    timestamp.
    """
    consumption_amt_kwh = ValueTimeWindowSequence(
        windows=[
            ValueTimeWindow(start_time="00:00", duration="8 hours", value=0.288),
            ValueTimeWindow(start_time="08:00", duration="16 hours", value=0.34),
        ]
    )
    consumption_percent_amt = ValueTimeWindowSequence(
        windows=[
            ValueTimeWindow(start_time="00:00", duration="24 hours", value=19.0),
        ]
    )
    feedin_amt_kwh = ValueTimeWindowSequence(
        windows=[
            ValueTimeWindow(start_time="00:00", duration="8 hours", value=0.08),
            ValueTimeWindow(start_time="08:00", duration="16 hours", value=0.10),
        ]
    )
    feedin_percent_amt = ValueTimeWindowSequence(
        windows=[
            ValueTimeWindow(start_time="00:00", duration="24 hours", value=5.0),
        ]
    )

    return ElecFeeFixedCommonSettings(
        consumption_amt_kwh=consumption_amt_kwh,
        consumption_percent_amt=consumption_percent_amt,
        feedin_amt_kwh=feedin_amt_kwh,
        feedin_percent_amt=feedin_percent_amt,
    )


@pytest.fixture
def provider(monkeypatch, config_eos, elecfeefixed_settings):
    """Fixture to create an ElecFeeFixed provider instance."""
    # Set environment variables
    monkeypatch.setenv("EOS_ELECFEE__ELECFEE_PROVIDER", "ElecFeeFixed")

    # Assign settings to config
    config_eos.elecfee.elecfeefixed = elecfeefixed_settings

    ElecFeeFixed.reset_instance()
    return ElecFeeFixed()


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


class TestElecFeeFixed:
    """Tests for ElecFeeFixed provider."""

    def test_provider_id(self, provider):
        """Test provider ID returns correct value."""
        assert provider.provider_id() == "ElecFeeFixed"

    def test_singleton_instance(self, provider):
        """Test that ElecFeeFixed behaves as a singleton."""
        another_instance = ElecFeeFixed()
        assert provider is another_instance

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_ELECFEE__ELECFEE_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()

    @pytest.mark.asyncio
    async def test_update_data_15min_intervals_all_sequences(self, provider, config_eos):
        """Test updating data with 15-minute intervals across all 4 fee sequences."""
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        config_eos.prediction.hours = 10  # spans both windows: 00:00-10:00 = 40 intervals

        await provider.update_data(force_enable=True, force_update=True)

        # 10 hours * 4 intervals per hour = 40 intervals
        assert len(provider) == 40

        records = provider.records

        # Check timestamps are on 15-minute boundaries
        for record in records:
            assert record.date_time.minute in (0, 15, 30, 45)
            assert record.date_time.second == 0

        # --- consumption_amt_kwh -> elecfee_consumption_amt_wh (converted /1000) ---
        # First 32 intervals: 00:00-08:00, night rate (8h * 4 = 32)
        for i in range(32):
            assert abs(records[i].elecfee_consumption_amt_wh - 0.000288) < 1e-9, (
                f"Expected night consumption fee at interval {i}, "
                f"got {records[i].elecfee_consumption_amt_wh}"
            )
        # Remaining 8 intervals: 08:00-10:00, day rate (2h * 4 = 8)
        for i in range(32, 40):
            assert abs(records[i].elecfee_consumption_amt_wh - 0.00034) < 1e-9, (
                f"Expected day consumption fee at interval {i}, "
                f"got {records[i].elecfee_consumption_amt_wh}"
            )

        # --- consumption_percent_amt -> elecfee_consumption_percent_amt (no conversion) ---
        for i in range(40):
            assert abs(records[i].elecfee_consumption_percent_amt - 19.0) < 1e-9, (
                f"Expected constant consumption percent fee at interval {i}, "
                f"got {records[i].elecfee_consumption_percent_amt}"
            )

        # --- feedin_amt_kwh -> elecfee_feedin_amt_wh (converted /1000) ---
        for i in range(32):
            assert abs(records[i].elecfee_feedin_amt_wh - 0.00008) < 1e-9, (
                f"Expected night feedin fee at interval {i}, "
                f"got {records[i].elecfee_feedin_amt_wh}"
            )
        for i in range(32, 40):
            assert abs(records[i].elecfee_feedin_amt_wh - 0.0001) < 1e-9, (
                f"Expected day feedin fee at interval {i}, "
                f"got {records[i].elecfee_feedin_amt_wh}"
            )

        # --- feedin_percent_amt -> elecfee_feedin_percent_amt (no conversion) ---
        for i in range(40):
            assert abs(records[i].elecfee_feedin_percent_amt - 5.0) < 1e-9, (
                f"Expected constant feedin percent fee at interval {i}, "
                f"got {records[i].elecfee_feedin_percent_amt}"
            )

    @pytest.mark.asyncio
    async def test_update_data_without_config(self, caplog, provider, config_eos):
        """Test update_data fails without any elecfeefixed configuration."""
        # Remove elecfeefixed settings entirely
        config_eos.elecfee.elecfeefixed = {}

        with caplog.at_level("WARNING"):
            await provider.update_data(force_enable=True, force_update=True)
        assert "No time windows configured for `elecfee_consumption_amt_wh`" in caplog.text
        assert "No time windows configured for `elecfee_consumption_percent_amt`" in caplog.text
        assert "No time windows configured for `elecfee_feedin_amt_wh`" in caplog.text
        assert "No time windows configured for `elecfee_feedin_percent_amt`" in caplog.text

    @pytest.mark.asyncio
    async def test_update_data_without_time_windows(self, caplog, provider, config_eos):
        """Test update_data fails when all 4 sequences are empty."""
        empty_settings = ElecFeeFixedCommonSettings(
            consumption_amt_kwh=ValueTimeWindowSequence(windows=[]),
            consumption_percent_amt=ValueTimeWindowSequence(windows=[]),
            feedin_amt_kwh=ValueTimeWindowSequence(windows=[]),
            feedin_percent_amt=ValueTimeWindowSequence(windows=[]),
        )
        config_eos.elecfee.elecfeefixed = empty_settings

        with caplog.at_level("WARNING"):
            await provider.update_data(force_enable=True, force_update=True)
        assert "No time windows configured for `elecfee_consumption_amt_wh`" in caplog.text
        assert "No time windows configured for `elecfee_consumption_percent_amt`" in caplog.text
        assert "No time windows configured for `elecfee_feedin_amt_wh`" in caplog.text
        assert "No time windows configured for `elecfee_feedin_percent_amt`" in caplog.text

    @pytest.mark.asyncio
    async def test_update_data_missing_single_sequence(self, caplog, provider, config_eos):
        """Test that a single empty sequence among 4 still raises, naming that key.

        `consumption_amt_kwh` is populated (first in insertion order), so the
        loop should fail on the first sequence that is actually empty:
        `consumption_percent_amt` -> `elecfee_consumption_percent_amt`.
        """
        partial_settings = ElecFeeFixedCommonSettings(
            consumption_amt_kwh=ValueTimeWindowSequence(
                windows=[
                    ValueTimeWindow(start_time="00:00", duration="24 hours", value=0.3),
                ]
            ),
            consumption_percent_amt=ValueTimeWindowSequence(windows=[]),
            feedin_amt_kwh=ValueTimeWindowSequence(
                windows=[
                    ValueTimeWindow(start_time="00:00", duration="24 hours", value=0.1),
                ]
            ),
            feedin_percent_amt=ValueTimeWindowSequence(
                windows=[
                    ValueTimeWindow(start_time="00:00", duration="24 hours", value=5.0),
                ]
            ),
        )
        config_eos.elecfee.elecfeefixed = partial_settings

        with caplog.at_level("WARNING"):
            await provider.update_data(force_enable=True, force_update=True)
        assert "No time windows configured for `elecfee_consumption_percent_amt`" in caplog.text

    @pytest.mark.asyncio
    async def test_key_to_array_resampling(self, provider, config_eos):
        """Test that key_to_array can resample the consumption fee to different intervals."""
        # Provider provides 15-minutes data
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        config_eos.prediction.hours = 24

        await provider.update_data(force_enable=True, force_update=True)

        # Get data as hourly array (original)
        hourly_array = await provider.key_to_array(
            key="elecfee_consumption_amt_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            fill_method="ffill",
        )

        assert len(hourly_array) == 24
        assert abs(hourly_array[0] - 0.000288) < 1e-9  # Night rate
        assert abs(hourly_array[8] - 0.00034) < 1e-9  # Day rate

        # Resample to 15-minute intervals
        quarter_hour_array = await provider.key_to_array(
            key="elecfee_consumption_amt_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            interval="15 minutes",
            fill_method="ffill",
        )

        assert len(quarter_hour_array) == 96  # 24 * 4
        # First 4 15-min intervals should be night rate
        for i in range(4):
            assert abs(quarter_hour_array[i] - 0.000288) < 1e-9

        # Resample to 30-minute intervals
        half_hour_array = await provider.key_to_array(
            key="elecfee_consumption_amt_wh",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            interval="30 minutes",
            fill_method="ffill",
        )

        assert len(half_hour_array) == 48  # 24 * 2
        # First 2 30-min intervals should be night rate
        for i in range(2):
            assert abs(half_hour_array[i] - 0.000288) < 1e-9

        # Resample the percent-based feedin fee, which should NOT be
        # kWh -> Wh converted and should be constant across the day.
        percent_array = await provider.key_to_array(
            key="elecfee_feedin_percent_amt",
            start_datetime=start_dt,
            end_datetime=start_dt.add(hours=24),
            fill_method="ffill",
        )
        assert len(percent_array) == 24
        assert np.allclose(percent_array, 5.0)


class TestElecFeeFixedIntegration:
    """Integration tests for ElecFeeFixed."""

    @pytest.mark.skip(reason="For development only")
    async def test_fixed_fee_development(self, config_eos):
        """Test fixed fee provider with real configuration."""
        # Create provider with config
        provider = ElecFeeFixed()

        # Setup realistic test scenario
        ems_eos = get_ems()
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start_dt)

        # Configure with realistic German electricity fees (2024)
        consumption_amt_kwh = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="00:00", duration="8 hours", value=0.288),
                ValueTimeWindow(start_time="08:00", duration="16 hours", value=0.34),
            ]
        )
        consumption_percent_amt = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="00:00", duration="24 hours", value=19.0),
            ]
        )
        feedin_amt_kwh = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="00:00", duration="8 hours", value=0.08),
                ValueTimeWindow(start_time="08:00", duration="16 hours", value=0.10),
            ]
        )
        feedin_percent_amt = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="00:00", duration="24 hours", value=5.0),
            ]
        )

        config_eos.elecfee.elecfeefixed = ElecFeeFixedCommonSettings(
            consumption_amt_kwh=consumption_amt_kwh,
            consumption_percent_amt=consumption_percent_amt,
            feedin_amt_kwh=feedin_amt_kwh,
            feedin_percent_amt=feedin_percent_amt,
        )
        config_eos.prediction.hours = 168  # 7 days

        # Update data
        await provider.update_data(force_enable=True, force_update=True)

        # Verify data
        expected_intervals = 168 * 4  # 7 days * 24h * 4 intervals
        assert len(provider) == expected_intervals

        # Save configuration for documentation
        config_data = {
            "consumption_amt_kwh": [
                {
                    "start_time": str(window.start_time),
                    "duration": str(window.duration),
                    "value": window.value,
                }
                for window in config_eos.elecfee.elecfeefixed.consumption_amt_kwh.windows
            ],
            "consumption_percent_amt": [
                {
                    "start_time": str(window.start_time),
                    "duration": str(window.duration),
                    "value": window.value,
                }
                for window in config_eos.elecfee.elecfeefixed.consumption_percent_amt.windows
            ],
            "feedin_amt_kwh": [
                {
                    "start_time": str(window.start_time),
                    "duration": str(window.duration),
                    "value": window.value,
                }
                for window in config_eos.elecfee.elecfeefixed.feedin_amt_kwh.windows
            ],
            "feedin_percent_amt": [
                {
                    "start_time": str(window.start_time),
                    "duration": str(window.duration),
                    "value": window.value,
                }
                for window in config_eos.elecfee.elecfeefixed.feedin_percent_amt.windows
            ],
        }

        with FILE_TESTDATA_ELECFEEFIXED_CONFIG_JSON.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
