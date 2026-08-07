
"""Tests for PVForecastPVLib prediction provider."""

import bz2
import pickle
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.prediction.prediction import Prediction
from akkudoktoreos.prediction.pvforecastpvlib import (
    PVForecastPVLib,
    PVForecastPVLibCommonSettings,
    _cec_cache,
    _cec_inverters,
    _cec_inverters_path,
    _cec_modules,
    _cec_modules_path,
    _load_cec_database,
)
from akkudoktoreos.utils.datetimeutil import to_duration

DIR_TESTDATA = Path(__file__).parent / "testdata" / "pvforecastpvlib"

FILE_TESTDATA_CEC_INVERTERS_PBZ2 = DIR_TESTDATA / "cec_inverters.pbz2"
FILE_TESTDATA_CEC_MODULES_PBZ2 = DIR_TESTDATA / "cec_modules.pbz2"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# conftest for cec_databases fixture

@pytest.fixture
def provider(monkeypatch, config_eos):
    """Create a fresh PVForecastPVLib provider."""

    monkeypatch.setenv(
        "EOS_PVFORECAST__PVFORECAST_PROVIDER",
        "PVForecastPVLib",
    )

    PVForecastPVLib.reset_instance()
    return PVForecastPVLib()

@pytest.fixture
def cache_store():
    """Provide a fresh cache store."""
    return CacheFileStore()


@pytest.fixture(autouse=True)
def clear_database_cache():
    """Clear global CEC cache before every test."""
    _cec_cache.clear()
    yield
    _cec_cache.clear()


@pytest.fixture
def sample_settings_4planes(config_eos):
    """Fixture that adds settings data to the global config."""
    settings = {
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
        "pvforecast": {
            "provider": "PVForecastPVLib",
            "max_planes": 4,
            "planes": [
                {
                    "surface_tilt": 7,
                    "surface_azimuth": 170,
                    "userhorizon": [20, 27, 22, 20],
                    "peakpower": 5.0,
                    "module_model": "AXITEC_AC_410MH_144S",
                    "inverter_model": "Sungrow__SH25T",
                    "inverter_paco": 10000,
                    "modules_per_string": 12,
                    "strings_per_inverter": 1,
                },
                {
                    "surface_tilt": 7,
                    "surface_azimuth": 90,
                    "userhorizon": [30, 30, 30, 50],
                    "peakpower": 4.8,
                    "module_model": "AXITEC_AC_410MH_144S",
                    "inverter_model": "Sungrow__SH25T",
                    "inverter_paco": 10000,
                    "modules_per_string": 12,
                    "strings_per_inverter": 1,
                },
                {
                    "surface_tilt": 60,
                    "surface_azimuth": 140,
                    "userhorizon": [60, 30, 0, 30],
                    "peakpower": 1.4,
                    "module_model": "AXITEC_AC_410MH_144S",
                    "inverter_model": "Sungrow__SH25T",
                    "inverter_paco": 2000,
                    "modules_per_string": 5,
                    "strings_per_inverter": 1,
                },
                {
                    "surface_tilt": 45,
                    "surface_azimuth": 185,
                    "userhorizon": [45, 25, 30, 60],
                    "peakpower": 1.6,
                    "module_model": "AXITEC_AC_410MH_144S",
                    "inverter_model": "Sungrow__SH25T",
                    "inverter_paco": 1400,
                    "modules_per_string": 4,
                    "strings_per_inverter": 1,
                },
            ],
        },
    }

    # Merge settings to config
    config_eos.merge_settings_from_dict(settings)
    assert config_eos.pvforecast.provider == "PVForecastPVLib"
    return config_eos


@pytest.fixture
def sample_settings_1plane(config_eos):
    """Fixture that adds settings data to the global config."""
    settings = {
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
        "pvforecast": {
            "provider": "PVForecastPVLib",
            "planes": [
                {
                    "surface_tilt": 7,
                    "surface_azimuth": 170,
                    "userhorizon": [20, 27, 22, 20],
                    "peakpower": 5.0,
                    "module_model": "AXITEC_AC_410MH_144S",
                    "inverter_model": "Sungrow__SH25T",
                    "inverter_paco": 10000,
                    "modules_per_string": 12,
                    "strings_per_inverter": 1,
                },
            ],
        },
    }

    # Merge settings to config
    config_eos.merge_settings_from_dict(settings)
    assert config_eos.pvforecast.provider == "PVForecastPVLib"
    return config_eos


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class TestLoadUpdateCECDatabase:
    """Test for CEC databases."""

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib._cec_inverters_path",
    )
    def test_load_cec_inverters_database(
        self,
        mock_cec_inverters_path,
        config_eos,
    ):
        """Test generated inverter database looks sane."""

        mock_cec_inverters_path.return_value = FILE_TESTDATA_CEC_INVERTERS_PBZ2

        inverters = _cec_inverters()

        assert "Sungrow__SH25T" in inverters

        inverter = inverters["Sungrow__SH25T"]

        required = [
            "Paco",
            "Pdco",
            "Vdco",
            "Pso",
            "Mppt_low",
            "Mppt_high",
        ]

        for key in required:
            assert key in inverter.index
            assert pd.notna(inverter[key]), f"{key} is NaN"

        assert inverter["Paco"] > 0
        assert inverter["Pdco"] > 0
        assert inverter["Vdco"] > 0
        assert inverter["Mppt_low"] < inverter["Mppt_high"]


    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib._cec_modules_path",
    )
    def test_load_cec_modules_database(
        self,
        mock_cec_modules_path,
        config_eos,
    ):
        """Test generated module database looks sane."""

        mock_cec_modules_path.return_value = FILE_TESTDATA_CEC_MODULES_PBZ2

        modules = _cec_modules()

        assert "AXITEC_AC_410MH_144S" in modules

        module = modules["AXITEC_AC_410MH_144S"]

        #
        # Required CEC parameters
        #
        required = [
            "STC",
            "PTC",
            "I_sc_ref",
            "V_oc_ref",
            "I_mp_ref",
            "V_mp_ref",
            "a_ref",
            "I_L_ref",
            "I_o_ref",
            "R_s",
            "R_sh_ref",
            "Adjust",
            "alpha_sc",
            "beta_oc",
            "gamma_pmp",
            "N_s",
        ]
        for key in required:
            assert key in module.index
            assert pd.notna(module[key]), f"{key} is NaN"

        #
        # Numeric CEC parameters
        #
        numeric = [
            "STC",
            "I_sc_ref",
            "V_oc_ref",
            "I_mp_ref",
            "V_mp_ref",
            "a_ref",
            "I_L_ref",
            "I_o_ref",
            "R_s",
            "R_sh_ref",
        ]
        for key in numeric:
            assert isinstance(module[key], (int, float))

        #
        # Physical sanity checks
        #
        assert module["STC"] > 0
        assert module["PTC"] > 0

        assert module["V_mp_ref"] > 0
        assert module["I_mp_ref"] > 0
        assert module["V_oc_ref"] > module["V_mp_ref"]
        assert module["I_sc_ref"] > module["I_mp_ref"]

        assert module["a_ref"] > 0
        assert module["I_L_ref"] > 0
        assert module["I_o_ref"] > 0
        assert module["R_s"] >= 0
        assert module["R_sh_ref"] > 0

        #
        # STC consistency (within ~5%)
        #
        assert abs(
            module["V_mp_ref"] * module["I_mp_ref"] - module["STC"]
        ) < 0.05 * module["STC"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestPVForecastPVLibCommonSettings:
    """Tests for PVForecastPVLibCommonSettings."""

    def test_create_settings(self):
        """Settings object can be created."""
        settings = PVForecastPVLibCommonSettings()

        assert settings is not None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class TestCECDatabase:
    """Tests for CEC database handling."""

    def test_modules_database_path(self, config_eos):
        """Module database path ends with expected filename."""

        path = _cec_modules_path()

        assert path.name == "cec_modules.pbz2"

    def test_inverters_database_path(self, config_eos):
        """Inverter database path ends with expected filename."""

        path = _cec_inverters_path()

        assert path.name == "cec_inverters.pbz2"

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.pickle.load",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.bz2.BZ2File",
    )
    def test_load_database(
        self,
        mock_bz2,
        mock_pickle,
        tmp_path,
    ):
        """Database is loaded from pickle."""

        database = pd.DataFrame({"A": [1]})

        mock_pickle.return_value = database

        path = tmp_path / "db.pbz2"

        path.touch()

        loaded = _load_cec_database(path)

        assert loaded is database

        mock_pickle.assert_called_once()

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.pickle.load",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.bz2.BZ2File",
    )
    def test_load_database_uses_cache(
        self,
        mock_bz2,
        mock_pickle,
        tmp_path,
    ):
        """Loading same database twice uses memory cache."""

        database = pd.DataFrame({"A": [1]})

        mock_pickle.return_value = database

        path = tmp_path / "db.pbz2"

        path.touch()

        db1 = _load_cec_database(path)
        db2 = _load_cec_database(path)

        assert db1 is db2

        mock_pickle.assert_called_once()

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib._update_cec_database",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.pickle.load",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.bz2.BZ2File",
    )
    def test_missing_database_creates_database(
        self,
        mock_bz2,
        mock_pickle,
        mock_update,
        tmp_path,
    ):
        """Missing database triggers recreation."""

        database = pd.DataFrame({"A": [1]})

        mock_pickle.return_value = database

        path = tmp_path / "missing.pbz2"

        #
        # Simulate creation by update routine.
        #
        def create_database():
            path.touch()

        mock_update.side_effect = create_database

        loaded = _load_cec_database(path)

        assert loaded is database

        mock_update.assert_called_once()

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.pickle.load",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.bz2.BZ2File",
    )
    def test_cache_contains_loaded_database(
        self,
        mock_bz2,
        mock_pickle,
        tmp_path,
    ):
        """Database is inserted into cache after loading."""

        database = pd.DataFrame({"A": [1]})

        mock_pickle.return_value = database

        path = tmp_path / "db.pbz2"

        path.touch()

        _load_cec_database(path)

        assert path in _cec_cache

        _, cached = _cec_cache[path]

        assert cached is database

    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.pickle.load",
    )
    @patch(
        "akkudoktoreos.prediction.pvforecastpvlib.bz2.BZ2File",
    )
    def test_reload_when_timestamp_changes(
        self,
        mock_bz2,
        mock_pickle,
        tmp_path,
    ):
        """Changing mtime forces reload."""

        db1 = pd.DataFrame({"A": [1]})
        db2 = pd.DataFrame({"A": [2]})

        mock_pickle.side_effect = [
            db1,
            db2,
        ]

        path = tmp_path / "db.pbz2"

        path.touch()

        first = _load_cec_database(path)

        #
        # Force new modification time.
        #
        path.touch()

        second = _load_cec_database(path)

        assert first is db1
        assert second is db2

        assert mock_pickle.call_count == 2


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for helper methods."""

    @pytest.mark.parametrize(
        ("device_type", "params", "expected"),
        [
            (
                "module",
                {"STC": 410.0},
                410.0,
            ),
            (
                "module",
                {
                    "I_mp_ref": 10.0,
                    "V_mp_ref": 40.0,
                },
                400.0,
            ),
            (
                "inverter",
                {"Paco": 5000.0},
                5000.0,
            ),
            (
                "inverter",
                {"Pdco": 5100.0},
                5100.0,
            ),
            (
                "module",
                {},
                None,
            ),
            (
                "inverter",
                {},
                None,
            ),
            (
                "unknown",
                {},
                None,
            ),
        ],
    )
    def test_get_model_power(
        self,
        provider,
        device_type,
        params,
        expected,
    ):
        """Test extraction of power from model parameters."""

        assert (
            provider._get_model_power(
                params,
                device_type,
            )
            == expected
        )

    @patch("akkudoktoreos.prediction.pvforecastpvlib.logger.warning")
    def test_warn_once(self, mock_warning, provider):
        """A warning shall only be logged once."""
        provider._warned_features.clear()

        provider._warn_once("feature1", "test warning")
        provider._warn_once("feature1", "test warning")

        mock_warning.assert_called_once_with("test warning")

    @patch("akkudoktoreos.prediction.pvforecastpvlib.logger.warning")
    def test_warn_twice_for_different_features(self, mock_warning, provider):
        """Different features shall each generate one warning."""
        provider._warned_features.clear()

        provider._warn_once("feature1", "test warning1")
        provider._warn_once("feature2", "test warning2")

        assert mock_warning.call_count == 2

    def test_find_closest_module(self, provider):
        """Closest module shall be selected."""

        database = pd.DataFrame(
            {
                "ModuleA": {
                    "STC": 400.0,
                },
                "ModuleB": {
                    "STC": 425.0,
                },
                "ModuleC": {
                    "STC": 600.0,
                },
            }
        )

        model = provider._find_closest_model(
            430.0,
            database,
            "module",
        )

        assert model is not None
        assert model.name == "ModuleB"

    def test_find_closest_inverter(self, provider):
        """Closest inverter shall be selected."""

        database = pd.DataFrame(
            {
                "InvA": {
                    "Paco": 3000,
                },
                "InvB": {
                    "Paco": 5000,
                },
                "InvC": {
                    "Paco": 8000,
                },
            }
        )

        model = provider._find_closest_model(
            5200,
            database,
            "inverter",
        )

        assert model is not None
        assert model.name == "InvB"

    def test_find_closest_returns_none(self, provider):
        """No suitable model returns None."""

        database = pd.DataFrame(
            {
                "ModuleA": {
                    "Foo": 1,
                },
                "ModuleB": {
                    "Bar": 2,
                },
            }
        )

        model = provider._find_closest_model(
            400,
            database,
            "module",
        )

        assert model is None

    def test_get_model_by_name(self, provider):
        """Retrieve model by exact name."""

        database = {
            "ModuleA": {
                "STC": 400,
            },
            "ModuleB": {
                "STC": 500,
            },
        }

        model = provider._get_model(
            "ModuleB",
            database,
            "module",
        )

        assert model["STC"] == 500

    def test_get_model_by_numeric_string(self, provider):
        """Numeric strings shall search nearest model."""

        database = pd.DataFrame(
            {
                "ModuleA": {
                    "STC": 400,
                },
                "ModuleB": {
                    "STC": 450,
                },
            }
        )

        model = provider._get_model(
            "430",
            database,
            "module",
        )

        assert model.name == "ModuleB"

    def test_get_model_by_integer(self, provider):
        """Integer power shall search nearest model."""

        database = pd.DataFrame(
            {
                "ModuleA": {
                    "STC": 300,
                },
                "ModuleB": {
                    "STC": 420,
                },
            }
        )

        model = provider._get_model(
            410,
            database,
            "module",
        )

        assert model.name == "ModuleB"

    def test_get_model_by_float(self, provider):
        """Float power shall search nearest model."""

        database = pd.DataFrame(
            {
                "ModuleA": {
                    "STC": 410.0,
                },
                "ModuleB": {
                    "STC": 600.0,
                },
            }
        )

        model = provider._get_model(
            405.0,
            database,
            "module",
        )

        assert model.name == "ModuleA"

    def test_get_model_invalid_name(self, provider):
        """Unknown model names shall raise KeyError."""

        database = {
            "ModuleA": {
                "STC": 300,
            },
        }

        with pytest.raises(KeyError):
            provider._get_model(
                "UnknownModule",
                database,
                "module",
            )

    def test_get_model_invalid_type(self, provider):
        """Unsupported model specification returns None."""

        database: dict = {}

        model = provider._get_model(
            ["invalid"],
            database,
            "module",
        )

        assert model is None

    def test_get_model_empty_database(self, provider):
        """Empty database returns None."""

        database = pd.DataFrame()

        model = provider._find_closest_model(
            400,
            database,
            "module",
        )

        assert model is None

# ---------------------------------------------------------------------------
# Static helper methods
# ---------------------------------------------------------------------------


class TestStaticMethods:
    """Tests for static helper methods."""

    def test_add_cyclic_hour_features(self):
        """Hour sine/cosine features shall be added."""

        index = pd.date_range(
            "2024-06-01",
            periods=24,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.add_cyclic_hour_features(weather)

        assert "hour_sin" in result.columns
        assert "hour_cos" in result.columns

        assert len(result) == 24

    def test_add_cyclic_hour_features_preserves_dataframe(self):
        """Original weather columns shall be preserved."""

        index = pd.date_range(
            "2024-06-01",
            periods=4,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [100, 200, 300, 400],
                "temp_air": [10, 11, 12, 13],
            },
            index=index,
        )

        result = PVForecastPVLib.add_cyclic_hour_features(weather)

        assert "ghi" in result.columns
        assert "temp_air" in result.columns
        assert "hour_sin" in result.columns
        assert "hour_cos" in result.columns

    def test_add_cyclic_hour_features_midnight(self):
        """Midnight encoding shall be (0,+1)."""

        index = pd.DatetimeIndex(
            [
                "2024-06-01 00:00",
            ],
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.add_cyclic_hour_features(weather)

        assert abs(result.iloc[0]["hour_sin"]) < 1e-10
        assert result.iloc[0]["hour_cos"] == pytest.approx(1.0)

    def test_add_cyclic_hour_features_noon(self):
        """Noon encoding shall be (0,-1)."""

        index = pd.DatetimeIndex(
            [
                "2024-06-01 12:00",
            ],
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.add_cyclic_hour_features(weather)

        assert abs(result.iloc[0]["hour_sin"]) < 1e-10
        assert result.iloc[0]["hour_cos"] == pytest.approx(-1.0)

    def test_add_cyclic_hour_features_requires_datetimeindex(self):
        """DatetimeIndex is required."""

        weather = pd.DataFrame(index=[0, 1, 2])

        with pytest.raises(
            ValueError,
            match="DatetimeIndex",
        ):
            PVForecastPVLib.add_cyclic_hour_features(weather)

    def test_compute_solar_angles(self):
        """Solar angles shall be added."""

        index = pd.date_range(
            "2024-06-01",
            periods=8,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.compute_solar_angles(
            weather,
            latitude=48.0,
            longitude=10.0,
        )

        assert "solar_azimuth" in result.columns
        assert "solar_elevation" in result.columns

        assert len(result) == len(weather)

    def test_compute_solar_angles_preserves_columns(self):
        """Existing weather columns shall remain."""

        index = pd.date_range(
            "2024-06-01",
            periods=3,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [0, 200, 800],
                "dni": [0, 150, 700],
            },
            index=index,
        )

        result = PVForecastPVLib.compute_solar_angles(
            weather,
            latitude=48.0,
            longitude=10.0,
        )

        assert "ghi" in result.columns
        assert "dni" in result.columns
        assert "solar_azimuth" in result.columns
        assert "solar_elevation" in result.columns

    def test_compute_solar_angles_elevation_range(self):
        """Solar elevation shall stay within physical limits."""

        index = pd.date_range(
            "2024-06-01",
            periods=24,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.compute_solar_angles(
            weather,
            latitude=48.0,
            longitude=10.0,
        )

        assert (result["solar_elevation"] <= 90).all()
        assert (result["solar_elevation"] >= -90).all()

    def test_compute_solar_angles_azimuth_range(self):
        """Solar azimuth shall stay within physical limits."""

        index = pd.date_range(
            "2024-06-01",
            periods=24,
            freq="h",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(index=index)

        result = PVForecastPVLib.compute_solar_angles(
            weather,
            latitude=48.0,
            longitude=10.0,
        )

        assert (result["solar_azimuth"] >= 0).all()
        assert (result["solar_azimuth"] <= 360).all()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class TestProvider:
    """Tests for PVForecastPVLib provider."""

    def test_provider_id(self, provider):
        """Provider shall return correct identifier."""

        assert provider.provider_id() == "PVForecastPVLib"

    def test_singleton_instance(self, provider):
        """Provider behaves as singleton."""

        another = PVForecastPVLib()

        assert provider is another

    def test_enabled(self, config_eos, provider):
        """Provider shall be enabled by default."""
        config_eos.pvforecast.provider = "PVForecastPVLib"

        assert provider.enabled()

    def test_disabled_when_wrong_provider(
        self,
        monkeypatch,
        provider,
    ):
        """Wrong provider name disables provider."""

        monkeypatch.setenv(
            "EOS_PVFORECAST__PVFORECAST_PROVIDER",
            "InvalidProvider",
        )

        provider.config.reset_settings()

        assert not provider.enabled()

    @pytest.mark.asyncio
    async def test_update_without_planes(
        self,
        provider,
        config_eos,
    ):
        """No PV planes shall raise."""

        config_eos.pvforecast.planes = []

        with pytest.raises(
            ValueError,
            match="plane",
        ):
            await provider.update_data(
                force_enable=True,
                force_update=True,
            )

    @pytest.mark.asyncio
    async def test_update_requests_weather_dataframe(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Weather dataframe shall be requested with correct parameters."""

        weather = pd.DataFrame()



        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=weather,
        ) as mock_weather:

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=pd.DataFrame(columns=["dc", "ac"]),
            ):

                await provider.update_data(
                    force_enable=True,
                    force_update=True,
                )

        mock_weather.assert_called_once()

        kwargs = mock_weather.call_args.kwargs

        assert kwargs["interval"] == to_duration("15 minutes")
        assert kwargs["fill_method"] == "linear"
        assert kwargs["resample_method"] == "mean"
        assert kwargs["dropna"] is True
        assert kwargs["boundary"] == "context"
        assert kwargs["align_to_interval"] is True

    @pytest.mark.asyncio
    async def test_update_requests_expected_weather_keys(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Expected weather keys shall be requested."""

        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=pd.DataFrame(),
        ) as mock_weather:

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=pd.DataFrame(columns=["dc", "ac"]),
            ):

                await provider.update_data(
                    force_enable=True,
                    force_update=True,
                )

        keys = mock_weather.call_args.kwargs["keys"]

        assert keys == [
            "weather_temp_air",
            "weather_relative_humidity",
            "weather_preciptable_water",
            "weather_total_clouds",
            "weather_wind_speed",
            "weather_ghi",
            "weather_dhi",
            "weather_dni",
        ]

    @pytest.mark.asyncio
    async def test_weather_columns_are_renamed(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Weather dataframe shall be renamed before calculation."""

        weather = pd.DataFrame(
            {
                "weather_temp_air": [20],
                "weather_relative_humidity": [60],
                "weather_preciptable_water": [2],
                "weather_total_clouds": [50],
                "weather_wind_speed": [4],
                "weather_ghi": [500],
                "weather_dhi": [100],
                "weather_dni": [600],
            }
        )

        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=weather,
        ):

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=pd.DataFrame(columns=["dc", "ac"]),
            ) as mock_calc:

                await provider.update_data(
                    force_enable=True,
                    force_update=True,
                )

        dataframe = mock_calc.call_args.args[0]

        assert "temp_air" in dataframe.columns
        assert "relative_humidity" in dataframe.columns
        assert "precipitable_water" in dataframe.columns
        assert "cloud_cover" in dataframe.columns
        assert "wind_speed" in dataframe.columns
        assert "ghi" in dataframe.columns
        assert "dhi" in dataframe.columns
        assert "dni" in dataframe.columns

    @pytest.mark.asyncio
    async def test_calculate_called_once(
        self,
        provider,
        sample_settings_1plane,
    ):
        """PVLib calculation shall be executed once."""

        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=pd.DataFrame(),
        ):

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=pd.DataFrame(columns=["dc", "ac"]),
            ) as mock_calc:

                await provider.update_data(
                    force_enable=True,
                    force_update=True,
                )

        mock_calc.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_values_written(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Calculated values shall be stored."""

        weather = pd.DataFrame()

        forecast = pd.DataFrame(
            {
                "pv_dc_power": [1000.0, 1100.0],
                "ac_power": [950.0, 1050.0],
            },
            index=pd.date_range(
                "2024-01-01",
                periods=2,
                freq="15min",
                tz="Europe/Berlin",
            ),
        )

        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=weather,
        ):

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=forecast,
            ):

                with patch.object(
                    provider,
                    "_update_value",
                ) as mock_update:

                    await provider.update_data(
                        force_enable=True,
                        force_update=True,
                    )

        assert mock_update.call_count == 4

    @pytest.mark.asyncio
    async def test_empty_forecast(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Empty forecast shall not write values."""

        with patch.object(
            Prediction,
            "keys_to_dataframe",
            return_value=pd.DataFrame(),
        ):

            with patch.object(
                provider,
                "_calculate_pvlib_power",
                return_value=pd.DataFrame(columns=["dc", "ac"]),
            ):

                with patch.object(
                    provider,
                    "_update_value",
                ) as mock_update:

                    await provider.update_data(
                        force_enable=True,
                        force_update=True,
                    )

        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# PVLib calculation
# ---------------------------------------------------------------------------


class TestCalculation:
    """Tests for PVLib power calculation."""

    @pytest.mark.skip("PVLib bails out on empty weather data")
    def test_calculate_empty_weather(self, provider, sample_settings_4planes):
        """Empty weather dataframe returns empty result."""

        weather = pd.DataFrame(
            columns=[
                "ghi",
                "dni",
                "dhi",
                "temp_air",
                "wind_speed",
                "relative_humidity",
                "precipitable_water",
                "cloud_cover",
            ],
            index=pd.DatetimeIndex([], tz="Europe/Berlin"),
        )

        result = provider._calculate_pvlib_power(weather)

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @patch("akkudoktoreos.prediction.pvforecastpvlib.ModelChain")
    @patch("akkudoktoreos.prediction.pvforecastpvlib.Location")
    def test_location_created(
        self,
        mock_location,
        mock_modelchain,
        provider,
        sample_settings_1plane,
    ):
        """Location shall be created with configured coordinates."""

        weather = pd.DataFrame(
            {
                "ghi": [0.0],
                "dni": [0.0],
                "dhi": [0.0],
                "temp_air": [20.0],
                "wind_speed": [2.0],
                "relative_humidity": [60.0],
                "precipitable_water": [1.5],
                "cloud_cover": [50.0],
            },
            index=pd.date_range(
                "2024-06-01",
                periods=1,
                freq="15min",
                tz="Europe/Berlin",
            ),
        )

        mc = MagicMock()
        mc.results.ac = pd.Series([0.0], index=weather.index)
        mc.results.dc = pd.DataFrame(
            {
                "p_mp": [0.0],
            },
            index=weather.index,
        )
        mock_modelchain.return_value = mc

        provider._calculate_pvlib_power(weather)

        mock_location.assert_called_once()

    @patch("akkudoktoreos.prediction.pvforecastpvlib.ModelChain")
    def test_modelchain_run_called(
        self,
        mock_modelchain,
        provider,
        sample_settings_1plane,
    ):
        """ModelChain.run_model shall be executed."""

        weather = pd.DataFrame(
            {
                "ghi": [0.0],
                "dni": [0.0],
                "dhi": [0.0],
                "temp_air": [20.0],
                "wind_speed": [2.0],
                "relative_humidity": [60.0],
                "precipitable_water": [1.5],
                "cloud_cover": [50.0],
            },
            index=pd.date_range(
                "2024-06-01",
                periods=1,
                freq="15min",
                tz="Europe/Berlin",
            ),
        )

        mc = MagicMock()
        mc.results.ac = pd.Series([0.0], index=weather.index)
        mc.results.dc = pd.DataFrame(
            {
                "p_mp": [0.0],
            },
            index=weather.index,
        )

        mock_modelchain.return_value = mc

        provider._calculate_pvlib_power(weather)

        mc.run_model.assert_called_once_with(weather)

    @patch("akkudoktoreos.prediction.pvforecastpvlib.ModelChain")
    def test_negative_power_clipped(
        self,
        mock_modelchain,
        provider,
        sample_settings_4planes,
    ):
        """Negative power shall be clipped to zero."""

        weather = pd.DataFrame(
            {
                "ghi": [0.0],
                "dni": [0.0],
                "dhi": [0.0],
                "temp_air": [20.0],
                "wind_speed": [2.0],
                "relative_humidity": [60.0],
                "precipitable_water": [1.5],
                "cloud_cover": [50.0],
            },
            index=pd.date_range(
                "2024-06-01",
                periods=2,
                freq="15min",
                tz="Europe/Berlin",
            ),
        )

        mc = MagicMock()

        mc.results.dc = pd.DataFrame(
            {
                "p_mp": [-100.0, 500.0],
            },
            index=weather.index,
        )

        mc.results.ac = pd.Series(
            [-50.0, 450.0],
            index=weather.index,
        )

        mock_modelchain.return_value = mc

        result = provider._calculate_pvlib_power(weather)

        assert (result["pv_dc_power"] >= 0).all()
        assert (result["ac_power"] >= 0).all()

    @patch("akkudoktoreos.prediction.pvforecastpvlib.ModelChain")
    def test_dataframe_columns(
        self,
        mock_modelchain,
        provider,
        sample_settings_4planes,
    ):
        """Returned dataframe contains expected columns."""

        weather = pd.DataFrame(
            {
                "ghi": [0.0],
                "dni": [0.0],
                "dhi": [0.0],
                "temp_air": [20.0],
                "wind_speed": [2.0],
                "relative_humidity": [60.0],
                "precipitable_water": [1.5],
                "cloud_cover": [50.0],
            },
            index=pd.date_range(
                "2024-06-01",
                periods=1,
                freq="15min",
                tz="Europe/Berlin",
            ),
        )

        mc = MagicMock()

        mc.results.dc = pd.DataFrame(
            {
                "p_mp": [1000.0],
            },
            index=weather.index,
        )
        mc.results.ac = pd.Series([950.0], index=weather.index)

        mock_modelchain.return_value = mc

        result = provider._calculate_pvlib_power(weather)

        assert "pv_dc_power" in result.columns
        assert "ac_power" in result.columns

    @patch("akkudoktoreos.prediction.pvforecastpvlib.ModelChain")
    def test_result_index_preserved(
        self,
        mock_modelchain,
        provider,
        sample_settings_4planes,
    ):
        """Returned dataframe preserves weather index."""

        index = pd.date_range(
            "2024-06-01",
            periods=4,
            freq="15min",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [0.0] * 4,
                "dni": [0.0] * 4,
                "dhi": [0.0] * 4,
                "temp_air": [20.0] * 4,
                "wind_speed": [2.0] * 4,
                "relative_humidity": [60.0] * 4,
                "precipitable_water": [1.5] * 4,
                "cloud_cover": [50.0] * 4,
            },
            index=index,
        )

        mc = MagicMock()

        mc.results.dc = pd.DataFrame(
            {
                "p_mp": [1.0] * 4,
            },
            index=index,
        )

        mc.results.ac = pd.Series(
            [1.0] * 4,
            index=index,
        )

        mock_modelchain.return_value = mc

        result = provider._calculate_pvlib_power(weather)

        pd.testing.assert_index_equal(
            result.index,
            weather.index,
        )

    def test_get_model_power_none(self, provider):
        """Unknown model parameters return None."""

        assert provider._get_model_power({}, "module") is None

    def test_find_closest_model_empty(self, provider):
        """Empty model database returns None."""

        model = provider._find_closest_model(
            400,
            pd.DataFrame(),
            "module",
        )

        assert model is None


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests using the real PVLib implementation."""

    def test_single_plane_produces_power(
        self,
        provider,
        sample_settings_1plane,
    ):
        """A realistic weather profile shall produce PV power."""

        index = pd.date_range(
            "2024-06-21 08:00",
            periods=16,
            freq="15min",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [0, 50, 150, 300, 500, 700, 850, 900,
                        900, 850, 700, 500, 300, 150, 50, 0],
                "dni": [0, 30, 120, 250, 450, 650, 800, 850,
                        850, 800, 650, 450, 250, 120, 30, 0],
                "dhi": [0, 20, 30, 50, 60, 70, 80, 90,
                        90, 80, 70, 60, 50, 30, 20, 0],
                "temp_air": [20.0] * 16,
                "wind_speed": [2.0] * 16,
                "relative_humidity": [55.0] * 16,
                "precipitable_water": [1.5] * 16,
                "cloud_cover": [10.0] * 16,
            },
            index=index,
        )

        result = provider._calculate_pvlib_power(weather)

        assert not result.empty
        assert (result["pv_dc_power"] >= 0).all()
        assert (result["ac_power"] >= 0).all()

        #
        # At least one timestep shall generate power.
        #
        assert result["pv_dc_power"].max() > 0
        assert result["ac_power"].max() > 0

    def test_four_planes_generate_more_energy(
        self,
        provider,
        sample_settings_4planes,
    ):
        """Four PV planes shall produce positive total power."""

        index = pd.date_range(
            "2024-06-21 11:00",
            periods=8,
            freq="15min",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [900.0] * 8,
                "dni": [800.0] * 8,
                "dhi": [100.0] * 8,
                "temp_air": [25.0] * 8,
                "wind_speed": [2.0] * 8,
                "relative_humidity": [45.0] * 8,
                "precipitable_water": [1.5] * 8,
                "cloud_cover": [0.0] * 8,
            },
            index=index,
        )

        result = provider._calculate_pvlib_power(weather)

        assert result["pv_dc_power"].sum() > 0
        assert result["ac_power"].sum() > 0

    def test_zero_irradiance_gives_zero_power(
        self,
        provider,
        sample_settings_1plane,
    ):
        """No irradiance shall result in zero power."""

        index = pd.date_range(
            "2024-06-21",
            periods=8,
            freq="15min",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [0.0] * 8,
                "dni": [0.0] * 8,
                "dhi": [0.0] * 8,
                "temp_air": [20.0] * 8,
                "wind_speed": [2.0] * 8,
                "relative_humidity": [60.0] * 8,
                "precipitable_water": [1.5] * 8,
                "cloud_cover": [100.0] * 8,
            },
            index=index,
        )

        result = provider._calculate_pvlib_power(weather)

        assert result["pv_dc_power"].max() == pytest.approx(0.0)
        assert result["ac_power"].max() == pytest.approx(0.0)

    def test_result_preserves_weather_index(
        self,
        provider,
        sample_settings_1plane,
    ):
        """Returned dataframe shall preserve the weather index."""

        index = pd.date_range(
            "2024-06-21 10:00",
            periods=12,
            freq="15min",
            tz="Europe/Berlin",
        )

        weather = pd.DataFrame(
            {
                "ghi": [500.0] * 12,
                "dni": [450.0] * 12,
                "dhi": [50.0] * 12,
                "temp_air": [22.0] * 12,
                "wind_speed": [2.5] * 12,
                "relative_humidity": [50.0] * 12,
                "precipitable_water": [1.4] * 12,
                "cloud_cover": [15.0] * 12,
            },
            index=index,
        )

        # sanity check on module data
        module = _cec_modules()[sample_settings_1plane.pvforecast.planes[0].module_model]
        assert module["I_o_ref"] > 0
        assert pd.notna(module["a_ref"])
        assert pd.notna(module["R_sh_ref"])

        result = provider._calculate_pvlib_power(weather)

        pd.testing.assert_index_equal(result.index, weather.index)
