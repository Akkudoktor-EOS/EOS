import pytest

from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings


@pytest.fixture
def settings():
    """Fixture that creates an empty PVForecastSettings."""
    settings = PVForecastCommonSettings()

    # Check default values for plane 0
    assert settings.pvforecast0_surface_tilt is None
    assert settings.pvforecast0_surface_azimuth is None
    assert settings.pvforecast0_pvtechchoice == "crystSi"
    assert settings.pvforecast0_mountingplace == "free"
    assert settings.pvforecast0_trackingtype is None
    assert settings.pvforecast0_optimal_surface_tilt is False
    assert settings.pvforecast0_optimalangles is False
    # Check default values for plane 1
    assert settings.pvforecast1_surface_azimuth is None
    assert settings.pvforecast1_pvtechchoice == "crystSi"
    assert settings.pvforecast1_mountingplace == "free"
    assert settings.pvforecast1_trackingtype is None
    assert settings.pvforecast1_optimal_surface_tilt is False
    assert settings.pvforecast1_optimalangles is False

    expected_planes: list[str] = []
    assert settings.pvforecast_planes == expected_planes

    return settings


def test_active_planes_detection(settings):
    """Test that active planes are correctly detected based on tilt and azimuth."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0

    expected_planes = ["pvforecast1", "pvforecast2"]
    assert settings.pvforecast_planes == expected_planes


def test_planes_peakpower_computation(settings):
    """Test computation of peak power for active planes."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast1_peakpower = 5.0
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0
    settings.pvforecast2_peakpower = 3.5
    settings.pvforecast3_surface_tilt = 30.0
    settings.pvforecast3_surface_azimuth = 30.0
    settings.pvforecast3_modules_per_string = 20  # Should use default 5000W

    expected_peakpower = [5.0, 3.5, 5000.0]
    assert settings.pvforecast_planes_peakpower == expected_peakpower


def test_planes_azimuth_computation(settings):
    """Test computation of azimuth values for active planes."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0

    expected_azimuths = [10.0, 20.0]
    assert settings.pvforecast_planes_azimuth == expected_azimuths


def test_planes_tilt_computation(settings):
    """Test computation of tilt values for active planes."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0

    expected_tilts = [10.0, 20.0]
    assert settings.pvforecast_planes_tilt == expected_tilts


def test_planes_userhorizon_computation(settings):
    """Test computation of user horizon values for active planes."""
    horizon1 = [10.0, 20.0, 30.0]
    horizon2 = [5.0, 15.0, 25.0]

    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast1_userhorizon = horizon1
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0
    settings.pvforecast2_userhorizon = horizon2

    expected_horizons = [horizon1, horizon2]
    assert settings.pvforecast_planes_userhorizon == expected_horizons


def test_planes_inverter_paco_computation(settings):
    """Test computation of inverter power rating for active planes."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast1_inverter_paco = 6000
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0
    settings.pvforecast2_inverter_paco = 4000

    expected_paco = [6000, 4000]
    assert settings.pvforecast_planes_inverter_paco == expected_paco


def test_non_sequential_plane_numbers(settings):
    """Test that non-sequential plane numbers are handled correctly."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast1_peakpower = 5.0
    settings.pvforecast3_surface_tilt = 30.0
    settings.pvforecast3_surface_azimuth = 30.0
    settings.pvforecast3_peakpower = 3.5
    settings.pvforecast5_surface_tilt = 50.0
    settings.pvforecast5_surface_azimuth = 50.0
    settings.pvforecast5_peakpower = 2.0

    expected_planes = ["pvforecast1", "pvforecast3", "pvforecast5"]
    assert settings.pvforecast_planes == expected_planes
    assert settings.pvforecast_planes_peakpower == [5.0, 3.5, 2.0]


def test_mixed_plane_configuration(settings):
    """Test mixed configuration with some planes having peak power and others having modules."""
    settings.pvforecast1_surface_tilt = 10.0
    settings.pvforecast1_surface_azimuth = 10.0
    settings.pvforecast1_peakpower = 5.0
    settings.pvforecast2_surface_tilt = 20.0
    settings.pvforecast2_surface_azimuth = 20.0
    settings.pvforecast2_modules_per_string = 20
    settings.pvforecast2_strings_per_inverter = 2
    settings.pvforecast4_surface_tilt = 40.0
    settings.pvforecast4_surface_azimuth = 40.0
    settings.pvforecast4_peakpower = 3.0

    expected_planes = ["pvforecast1", "pvforecast2", "pvforecast4"]
    assert settings.pvforecast_planes == expected_planes
    # First plane uses specified peak power, second uses default, third uses specified
    assert settings.pvforecast_planes_peakpower == [5.0, 5000.0, 3.0]


def test_max_planes_limit(settings):
    """Test that the maximum number of planes is enforced."""
    assert settings.pvforecast_max_planes == 6

    # Create settings with more planes than allowed (should only recognize up to max)
    plane_settings = {}
    for i in range(1, 8):  # Try to set up 7 planes, skipping plane 0
        plane_settings[f"pvforecast{i}_peakpower"] = 5.0

    settings = PVForecastCommonSettings(**plane_settings)
    assert len(settings.pvforecast_planes) <= settings.pvforecast_max_planes


def test_optional_parameters_non_zero_plane(settings):
    """Test that optional parameters can be None for non-zero planes."""
    settings.pvforecast1_peakpower = 5.0
    settings.pvforecast1_albedo = None
    settings.pvforecast1_module_model = None
    settings.pvforecast1_userhorizon = None

    assert settings.pvforecast1_albedo is None
    assert settings.pvforecast1_module_model is None
    assert settings.pvforecast1_userhorizon is None


def test_tracking_type_values_non_zero_plane(settings):
    """Test valid tracking type values for non-zero planes."""
    valid_types = [0, 1, 2, 3, 4, 5]

    for tracking_type in valid_types:
        settings.pvforecast1_peakpower = 5.0
        settings.pvforecast1_trackingtype = tracking_type
        assert settings.pvforecast1_trackingtype == tracking_type


def test_pv_technology_values_non_zero_plane(settings):
    """Test valid PV technology values for non-zero planes."""
    valid_technologies = ["crystSi", "CIS", "CdTe", "Unknown"]

    for tech in valid_technologies:
        settings.pvforecast2_peakpower = 5.0
        settings.pvforecast2_pvtechchoice = tech
        assert settings.pvforecast2_pvtechchoice == tech


def test_mounting_place_values_non_zero_plane(settings):
    """Test valid mounting place values for non-zero planes."""
    valid_mounting = ["free", "building"]

    for mounting in valid_mounting:
        settings.pvforecast3_peakpower = 5.0
        settings.pvforecast3_mountingplace = mounting
        assert settings.pvforecast3_mountingplace == mounting
