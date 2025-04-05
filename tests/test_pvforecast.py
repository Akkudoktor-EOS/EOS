import pytest

from akkudoktoreos.prediction.pvforecast import (
    PVForecastCommonSettings,
    PVForecastPlaneSetting,
)


@pytest.fixture
def settings():
    """Fixture that creates an empty PVForecastSettings."""
    settings = PVForecastCommonSettings()
    assert settings.planes is None
    return settings


def test_planes_peakpower_computation(settings):
    """Test computation of peak power for active planes."""
    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
            peakpower=5.0,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
            peakpower=3.5,
        ),
        PVForecastPlaneSetting(
            surface_tilt=30.0,
            surface_azimuth=30.0,
            modules_per_string=20,  # Should use default 5000W
        ),
    ]

    expected_peakpower = [5.0, 3.5, 5000.0]
    assert settings.planes_peakpower == expected_peakpower


def test_planes_azimuth_computation(settings):
    """Test computation of azimuth values for active planes."""
    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
        ),
    ]

    expected_azimuths = [10.0, 20.0]
    assert settings.planes_azimuth == expected_azimuths


def test_planes_tilt_computation(settings):
    """Test computation of tilt values for active planes."""
    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
        ),
    ]

    expected_tilts = [10.0, 20.0]
    assert settings.planes_tilt == expected_tilts


def test_planes_userhorizon_computation(settings):
    """Test computation of user horizon values for active planes."""
    horizon1 = [10.0, 20.0, 30.0]
    horizon2 = [5.0, 15.0, 25.0]

    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
            userhorizon=horizon1,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
            userhorizon=horizon2,
        ),
    ]

    expected_horizons = [horizon1, horizon2]
    assert settings.planes_userhorizon == expected_horizons


def test_planes_inverter_paco_computation(settings):
    """Test computation of inverter power rating for active planes."""
    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
            inverter_paco=6000,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
            inverter_paco=4000,
        ),
    ]

    expected_paco = [6000, 4000]
    assert settings.planes_inverter_paco == expected_paco


def test_mixed_plane_configuration(settings):
    """Test mixed configuration with some planes having peak power and others having modules."""
    settings.planes = [
        PVForecastPlaneSetting(
            surface_tilt=10.0,
            surface_azimuth=10.0,
            peakpower=5.0,
        ),
        PVForecastPlaneSetting(
            surface_tilt=20.0,
            surface_azimuth=20.0,
            modules_per_string=20,
            strings_per_inverter=2,
        ),
        PVForecastPlaneSetting(
            surface_tilt=40.0,
            surface_azimuth=40.0,
            peakpower=3.0,
        ),
    ]

    # First plane uses specified peak power, second uses default, third uses specified
    assert settings.planes_peakpower == [5.0, 5000.0, 3.0]


def test_none_plane_settings():
    """Test that optional parameters can be None for non-zero planes."""
    setting = PVForecastPlaneSetting(
        peakpower=5.0,
        albedo=None,
        module_model=None,
        userhorizon=None,
    )
