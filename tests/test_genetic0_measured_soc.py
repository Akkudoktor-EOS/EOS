"""GENETIC0 must use the SoC read during the run, not an older reading (#1447)."""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest_asyncio.fixture
async def isolated_measurement(config_eos):
    """Keep synthetic records out of the process-wide measurement singleton."""
    measurement = get_measurement()
    await measurement.delete_by_datetime(None, None)
    try:
        yield measurement
    finally:
        await measurement.delete_by_datetime(None, None)


@pytest.mark.asyncio
async def test_soc_read_late_in_slot_beats_previous_reading(config_eos, isolated_measurement):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"genetic0": {"horizon_hours": 24}},
            "elecfee": {"provider": None},
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
            },
        }
    )
    # A run at 14:40 belongs to the 14:00 slot. The previous run (13:40) is closer
    # to the slot start than the reading taken now.
    run_time = to_datetime("2026-08-01T14:40:00+00:00")
    get_ems().set_start_datetime(run_time)
    measurement = isolated_measurement
    await measurement.update_value(run_time.subtract(hours=1), "storage-soc-factor", 0.10)
    await measurement.update_value(run_time, "storage-soc-factor", 0.42)

    tariff = pd.Series(
        0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
    )

    async def read_array(**kwargs):
        return np.full(48, 0.0003)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(return_value=tariff),
    )
    with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
        parameters = await Genetic0OptimizationParameters.prepare()

    assert parameters is not None
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42


@pytest.mark.asyncio
async def test_meter_only_record_does_not_mask_soc(config_eos, isolated_measurement):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"genetic0": {"horizon_hours": 24}},
            "elecfee": {"provider": None},
            "measurement": {"load_emr_keys": ["load_meter"]},
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
            },
        }
    )
    run_time = to_datetime("2026-08-01T14:40:00+00:00")
    get_ems().set_start_datetime(run_time)
    measurement = isolated_measurement
    # SoC through the measurement API at 13:30, the adapter's meter reading at the
    # slot start (14:00); the run at 14:40 must still find the SoC.
    await measurement.update_value(run_time.subtract(minutes=70), "storage-soc-factor", 0.42)
    await measurement.update_value(get_ems().start_datetime, "load_meter", 1234.5)

    tariff = pd.Series(
        0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
    )

    async def read_array(**kwargs):
        return np.full(48, 0.0003)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(return_value=tariff),
    )
    with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
        parameters = await Genetic0OptimizationParameters.prepare()

    assert parameters is not None
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42


@pytest.mark.asyncio
async def test_older_missing_value_does_not_spoil_current_soc(config_eos, isolated_measurement):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"genetic0": {"horizon_hours": 24}},
            "elecfee": {"provider": None},
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 0,
                "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
            },
        }
    )
    run_time = to_datetime("2026-08-01T14:40:00+00:00")
    get_ems().set_start_datetime(run_time)
    measurement = isolated_measurement
    # An older reading stored as pandas NA (e.g. a JSON null in a Float64 series).
    await measurement.update_value(run_time.subtract(hours=1), "storage-soc-factor", pd.NA)
    await measurement.update_value(run_time, "storage-soc-factor", 0.42)

    tariff = pd.Series(
        0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
    )

    async def read_array(**kwargs):
        return np.full(48, 0.0003)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(return_value=tariff),
    )
    with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
        parameters = await Genetic0OptimizationParameters.prepare()

    assert parameters is not None
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42
