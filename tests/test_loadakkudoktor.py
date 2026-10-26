import asyncio
from unittest.mock import patch

import numpy as np
import pendulum
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.measurement.measurement import MeasurementDataRecord
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorAdjusted,
    LoadAkkudoktorCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration


@pytest.fixture
def loadakkudoktor(config_eos):
    """Fixture to initialise the LoadAkkudoktor instance."""
    settings = {
        "load": {
            "provider": "LoadAkkudoktor",
            "loadakkudoktor": {
                "loadakkudoktor_year_energy_kwh": "1000",
            },
        },
    }
    config_eos.merge_settings_from_dict(settings)
    assert config_eos.load.provider == "LoadAkkudoktor"
    assert config_eos.load.loadakkudoktor.loadakkudoktor_year_energy_kwh == 1000
    return LoadAkkudoktor()

@pytest.fixture
def loadakkudoktoradjusted(config_eos):
    """Fixture to initialise the LoadAkkudoktorAdjusted instance."""
    settings = {
        "load": {
            "provider": "LoadAkkudoktorAdjusted",
            "loadakkudoktor": {
                "loadakkudoktor_year_energy_kwh": "1000",
            },
        },
        "measurement": {
            "load_emr_keys": ["load0_mr", "load1_mr"]
        }
    }
    config_eos.merge_settings_from_dict(settings)
    assert config_eos.load.provider == "LoadAkkudoktorAdjusted"
    assert config_eos.load.loadakkudoktor.loadakkudoktor_year_energy_kwh == 1000
    return LoadAkkudoktorAdjusted()

@pytest_asyncio.fixture
async def measurement_eos():
    """Fixture to initialise the Measurement instance."""
    # Load meter readings are in kWh
    measurement = get_measurement()
    load0_mr = 500.0
    load1_mr = 500.0
    dt = to_datetime("2024-01-01T00:00:00")
    interval = to_duration("1 hour")
    for i in range(25):
        await measurement.insert_by_datetime(
            MeasurementDataRecord(
                date_time=dt,
                load0_mr=load0_mr,
                load1_mr=load1_mr,
            )
        )
        dt += interval
        # 0.05 kWh = 50 Wh
        load0_mr += 0.05
        load1_mr += 0.05
    min_dt = await measurement.min_datetime()
    max_dt = await measurement.max_datetime()
    assert min_dt is not None
    assert max_dt is not None
    assert compare_datetimes(min_dt, to_datetime("2024-01-01T00:00:00")).equal
    assert compare_datetimes(max_dt, to_datetime("2024-01-02T00:00:00")).equal
    return measurement


@pytest.fixture
def mock_load_profiles_file(tmp_path):
    """Fixture to create a mock load profiles file."""
    load_profiles_path = tmp_path / "load_profiles.npz"
    np.savez(
        load_profiles_path,
        yearly_profiles=np.random.rand(365, 24),  # Random load profiles
        yearly_profiles_std=np.random.rand(365, 24),  # Random standard deviation
    )
    return load_profiles_path


@pytest.mark.asyncio
class TestLoadAkkudoktor:

    async def test_loadakkudoktor_settings_validator(self):
        """Test the field validator for `loadakkudoktor_year_energy_kwh`."""
        settings = LoadAkkudoktorCommonSettings(loadakkudoktor_year_energy_kwh=1234)
        assert isinstance(settings.loadakkudoktor_year_energy_kwh, float)
        assert settings.loadakkudoktor_year_energy_kwh == 1234.0

        settings = LoadAkkudoktorCommonSettings(loadakkudoktor_year_energy_kwh=1234.56)
        assert isinstance(settings.loadakkudoktor_year_energy_kwh, float)
        assert settings.loadakkudoktor_year_energy_kwh == 1234.56

    async def test_loadakkudoktor_provider_id(self, loadakkudoktor):
        """Test the `provider_id` class method."""
        assert loadakkudoktor.provider_id() == "LoadAkkudoktor"

    @patch("akkudoktoreos.prediction.loadakkudoktor.np.load")
    async def test_load_data_from_mock(self, mock_np_load, mock_load_profiles_file, loadakkudoktor):
        """Test the `load_data` method."""
        # Mock numpy load to return data similar to what would be in the file
        mock_np_load.return_value = {
            "yearly_profiles": np.ones((365, 24)),
            "yearly_profiles_std": np.zeros((365, 24)),
        }

        # Test data loading
        data_year_energy = loadakkudoktor.load_data()
        assert data_year_energy is not None
        assert data_year_energy.shape == (365, 2, 24)

    async def test_load_data_from_file(self, loadakkudoktor):
        """Test `load_data` loads data from the profiles file."""
        data_year_energy = loadakkudoktor.load_data()
        assert data_year_energy is not None

    @patch("akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktor.load_data")
    async def test_update_data(self, mock_load_data, loadakkudoktor):
        """Test the `_update` method."""
        mock_load_data.return_value = np.random.rand(365, 2, 24)

        # Mock methods for updating values
        ems_eos = get_ems()
        ems_eos.set_start_datetime(pendulum.datetime(2024, 1, 1))

        # Assure there are no prediction records
        await loadakkudoktor.delete_by_datetime(start_datetime=None, end_datetime=None)
        assert len(loadakkudoktor) == 0

        # Execute the method
        await loadakkudoktor._update_data()

        # Validate that update_value is called
        assert len(loadakkudoktor) > 0


@pytest.mark.asyncio
class TestLoadAkkudoktorAdjusted:

    async def test_calculate_adjustment(self, loadakkudoktoradjusted, measurement_eos):
        """Test `_calculate_adjustment` for various scenarios."""
        data_year_energy = np.random.rand(365, 2, 24)

        # Check the test setup
        assert loadakkudoktoradjusted.measurement is measurement_eos
        min_dt = await measurement_eos.min_datetime()
        assert min_dt == to_datetime("2024-01-01T00:00:00")
        max_dt = await measurement_eos.max_datetime()
        assert max_dt == to_datetime("2024-01-02T00:00:00")
        # Use same calculation as in _calculate_adjustment
        compare_start = max_dt - to_duration("7 days")
        if compare_datetimes(compare_start, min_dt).lt:
            # Not enough measurements for 7 days - use what is available
            compare_start = min_dt
        compare_end = max_dt
        compare_interval = to_duration("1 hour")
        load_total_kwh_array = await measurement_eos.load_total_kwh(
            start_datetime=compare_start,
            end_datetime=compare_end,
            interval=compare_interval,
        )
        np.testing.assert_allclose(load_total_kwh_array, [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])

        # Call the method and validate results
        weekday_adjust, weekend_adjust = await loadakkudoktoradjusted._calculate_adjustment(data_year_energy)
        assert weekday_adjust.shape == (24,)
        assert weekend_adjust.shape == (24,)

        data_year_energy = np.zeros((365, 2, 24))
        weekday_adjust, weekend_adjust = await loadakkudoktoradjusted._calculate_adjustment(data_year_energy)

        assert weekday_adjust.shape == (24,)
        expected = np.array(
            [
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
                100.0,
            ]
        )
        np.testing.assert_allclose(weekday_adjust, expected)

        assert weekend_adjust.shape == (24,)
        expected = np.array(
            [
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ]
        )
        np.testing.assert_array_equal(weekend_adjust, expected)

    async def test_provider_adjustments_with_mock_data(self, loadakkudoktoradjusted):
        """Test full integration of adjustments with mock data."""
        with patch(
            "akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktorAdjusted._calculate_adjustment"
        ) as mock_adjust:
            mock_adjust.return_value = (np.zeros(24), np.zeros(24))

            # Test execution
            await loadakkudoktoradjusted._update_data()
            assert mock_adjust.called



@pytest_asyncio.fixture
async def empty_measurement(config_eos):
    """Own, empty measurement store (the singleton is shared between tests)."""
    measurement = get_measurement()
    await measurement.delete_by_datetime(None, None)
    config_eos.merge_settings_from_dict(
        {"devices": {"max_batteries": 1, "batteries": {"storage": {"capacity_wh": 2000}}}}
    )
    try:
        yield measurement
    finally:
        await measurement.delete_by_datetime(None, None)


async def _adjustment_window(adjusted, measurement):
    measurement_class = type(measurement)
    original = measurement_class.load_total_kwh
    calls = []

    async def spy(self, *args, **kwargs):
        calls.append(kwargs)
        return await original(self, *args, **kwargs)

    with patch.object(measurement_class, "load_total_kwh", spy):
        result = await adjusted._calculate_adjustment(np.random.rand(365, 2, 24))
    return result, calls


@pytest.mark.asyncio
async def test_adjustment_buckets_stay_on_full_hours(loadakkudoktoradjusted, empty_measurement):
    """A newer off-hour record (e.g. a device SoC) must not shift the hourly buckets (#1447)."""
    dt = to_datetime("2024-01-01T00:00:00")
    for hour in range(25):
        await empty_measurement.update_value(dt.add(hours=hour), "load0_mr", 500.0 + hour)
    await empty_measurement.update_value(
        to_datetime("2024-01-02T00:11:00"), "storage-soc-factor", 0.42
    )
    _, calls = await _adjustment_window(loadakkudoktoradjusted, empty_measurement)
    assert calls
    assert calls[0]["start_datetime"] == to_datetime("2024-01-01T00:00:00")
    assert calls[0]["end_datetime"] == to_datetime("2024-01-02T00:00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "records",
    [
        [("14:15", "load0_mr", 1000.0), ("14:26", "storage-soc-factor", 0.42)],
        [("14:11", "storage-soc-factor", 0.42)],
    ],
)
async def test_adjustment_within_first_hour_returns_zero(
    loadakkudoktoradjusted, empty_measurement, records
):
    """Less than one full hour of data yields no adjustment instead of an error."""
    for time, key, value in records:
        await empty_measurement.update_value(to_datetime(f"2026-08-01T{time}:00"), key, value)
    (weekday, weekend), calls = await _adjustment_window(loadakkudoktoradjusted, empty_measurement)
    assert calls == []
    assert not weekday.any() and not weekend.any()
