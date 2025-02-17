from typing import Any, Dict

import numpy as np
import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.optimization.exact_optimization import (
    ExactSolutionResponse,
    MILPOptimization,
)
from akkudoktoreos.optimization.genetic import OptimizationParameters
from akkudoktoreos.prediction.prediction import get_prediction


class TestExactOptimization:
    """Test suite for the MILP (Mixed Integer Linear Programming) optimization module.

    This class contains comprehensive tests for the exact optimization functionality
    of the energy management system (EMS). It verifies the behavior of the optimization
    algorithm under various scenarios including different battery configurations,
    load profiles, and price conditions.

    Class Attributes:
        prediction_hours (int): Number of hours to predict ahead (default: 24)
        optimization_hours (int): Number of hours to optimize for (default: 24)
    """

    prediction_hours: int = 24
    optimization_hours: int = 24

    def set_remaining_params(self):
        """Configure additional system parameters required for testing.

        This method sets up the configuration parameters for prediction and optimization
        hours using the system's configuration module.
        """
        config_eos = get_config()
        config_eos.merge_settings_from_dict(
            {
                "prediction_hours": self.prediction_hours,
                "optimization_hours": self.optimization_hours,
            }
        )

    def base_test_params(self) -> Dict[str, Any]:
        """Create base test parameters for optimization testing.

        This method sets up a comprehensive set of test parameters that mirror the
        structure used in production environment. It includes settings for:
        - PV (Photovoltaic) forecast
        - Temperature forecast
        - Electricity prices
        - System load profiles
        - General system configuration
        - Battery and inverter specifications

        Returns:
            Dict[str, Any]: A dictionary containing all necessary parameters for running
                           optimization tests, including EMS settings, battery configurations,
                           and environmental forecasts.
        """
        # Test parameter setup code remains the same...
        # PV Forecast (in W)
        pv_forecast = np.zeros(48)
        pv_forecast[12] = 5000

        # Temperature Forecast (in degree C)
        temperature_forecast = [
            18.3,
            17.8,
            16.9,
            16.2,
            15.6,
            15.1,
            14.6,
            14.2,
            14.3,
            14.8,
            15.7,
            16.7,
            17.4,
            18.0,
            18.6,
            19.2,
            19.1,
            18.7,
            18.5,
            17.7,
            16.2,
            14.6,
            13.6,
            13.0,
            12.6,
            12.2,
            11.7,
            11.6,
            11.3,
            11.0,
            10.7,
            10.2,
            11.4,
            14.4,
            16.4,
            18.3,
            19.5,
            20.7,
            21.9,
            22.7,
            23.1,
            23.1,
            22.8,
            21.8,
            20.2,
            19.1,
            18.0,
            17.4,
        ]

        # Electricity Price (in Euro per Wh)
        strompreis_euro_pro_wh = np.full(48, 0.001)
        strompreis_euro_pro_wh[0:10] = 0.00001
        strompreis_euro_pro_wh[11:15] = 0.00005
        strompreis_euro_pro_wh[20] = 0.00001

        # Overall System Load (in W)
        gesamtlast = [
            676.71,
            876.19,
            527.13,
            468.88,
            531.38,
            517.95,
            483.15,
            472.28,
            1011.68,
            995.00,
            1053.07,
            1063.91,
            1320.56,
            1132.03,
            1163.67,
            1176.82,
            1216.22,
            1103.78,
            1129.12,
            1178.71,
            1050.98,
            988.56,
            912.38,
            704.61,
            516.37,
            868.05,
            694.34,
            608.79,
            556.31,
            488.89,
            506.91,
            804.89,
            1141.98,
            1056.97,
            992.46,
            1155.99,
            827.01,
            1257.98,
            1232.67,
            871.26,
            860.88,
            1158.03,
            1222.72,
            1221.04,
            949.99,
            987.01,
            733.99,
            592.97,
        ]

        # Make a config
        settings = {
            # -- General --
            "prediction_hours": 48,
            "prediction_historic_hours": 24,
            "latitude": 52.52,
            "longitude": 13.405,
            # -- Predictions --
            # PV Forecast
            "pvforecast_provider": "PVForecastAkkudoktor",
            "pvforecast0_peakpower": 5.0,
            "pvforecast0_surface_azimuth": -10,
            "pvforecast0_surface_tilt": 7,
            "pvforecast0_userhorizon": [20, 27, 22, 20],
            "pvforecast0_inverter_paco": 10000,
            "pvforecast1_peakpower": 4.8,
            "pvforecast1_surface_azimuth": -90,
            "pvforecast1_surface_tilt": 7,
            "pvforecast1_userhorizon": [30, 30, 30, 50],
            "pvforecast1_inverter_paco": 10000,
            "pvforecast2_peakpower": 1.4,
            "pvforecast2_surface_azimuth": -40,
            "pvforecast2_surface_tilt": 60,
            "pvforecast2_userhorizon": [60, 30, 0, 30],
            "pvforecast2_inverter_paco": 2000,
            "pvforecast3_peakpower": 1.6,
            "pvforecast3_surface_azimuth": 5,
            "pvforecast3_surface_tilt": 45,
            "pvforecast3_userhorizon": [45, 25, 30, 60],
            "pvforecast3_inverter_paco": 1400,
            "pvforecast4_peakpower": None,
            # Weather Forecast
            # Electricity Price Forecast
            "elecprice_provider": "ElecPriceAkkudoktor",
            # Load Forecast
            "load_provider": "LoadAkkudoktor",
            "loadakkudoktor_year_energy": 5000,  # Energy consumption per year in kWh
            # -- Simulations --
        }
        config_eos = get_config()
        prediction_eos = get_prediction()
        ems_eos = get_ems()

        # Update/ set configuration
        config_eos.merge_settings_from_dict(settings)

        # Get current prediction data for optimization run
        ems_eos.set_start_datetime()
        prediction_eos.update_data()

        start_solution = None
        # Define parameters for the optimization problem
        return {
            "ems": {
                "preis_euro_pro_wh_akku": 0e-05,
                "einspeiseverguetung_euro_pro_wh": 7e-05,
                "gesamtlast": gesamtlast,
                "pv_prognose_wh": pv_forecast,
                "strompreis_euro_pro_wh": strompreis_euro_pro_wh,
            },
            "pv_akku": {
                "capacity_wh": 26400,
                "initial_soc_percentage": 15,
                "min_soc_percentage": 15,
                "max_charge_power_w": 5000,
            },
            "eauto": {
                "min_soc_percentage": 50,
                "capacity_wh": 60000,
                "charging_efficiency": 0.95,
                "max_charge_power_w": 11040,
                "initial_soc_percentage": 5,
            },
            "inverter": {
                "max_power_wh": 10000,
            },
            "temperature_forecast": temperature_forecast,
            "start_solution": start_solution,
        }

    def test_base_optimization(self):
        """Test the basic optimization scenario with all system components active.

        Verifies that the optimization algorithm correctly handles a complete system
        setup including both stationary battery and electric vehicle battery. Checks
        that the optimization produces valid charging schedules for both storage types.

        Assertions:
            - Result is an instance of ExactSolutionResponse
            - Battery charging schedule has correct length (24 hours)
            - Electric vehicle charging schedule exists and has correct length
        """
        optimizer = MILPOptimization(verbose=False)
        params = self.base_test_params()
        params = OptimizationParameters(**params)
        self.set_remaining_params()
        result = optimizer.optimize_ems(params)

        assert isinstance(result, ExactSolutionResponse)
        assert len(result.akku_charge) == 24
        assert result.eauto_charge is not None
        assert len(result.eauto_charge) == 24

    def test_no_battery_optimization(self):
        """Test optimization scenario without any battery storage components.

        Verifies that the system can handle optimization when no battery storage
        is available (neither stationary battery nor electric vehicle).

        Assertions:
            - Battery charging schedule is empty
            - Electric vehicle charging schedule is None
        """
        test_params = self.base_test_params()
        test_params["pv_akku"] = None
        test_params["eauto"] = None
        params = OptimizationParameters(**test_params)

        optimizer = MILPOptimization(verbose=False)

        result = optimizer.optimize_ems(params)
        assert len(result.akku_charge) == 0
        assert result.eauto_charge is None

    def test_only_pv_battery(self):
        """Test optimization with only a stationary PV battery system.

        Verifies the optimization behavior when only the stationary battery is
        present, without an electric vehicle in the system.

        Assertions:
            - Battery charging schedule has correct length
            - Electric vehicle charging schedule is None
        """
        test_params = self.base_test_params()
        test_params["eauto"] = None

        optimizer = MILPOptimization(verbose=False)
        params = OptimizationParameters(**test_params)
        self.set_remaining_params()
        result = optimizer.optimize_ems(params)
        assert len(result.akku_charge) == 24
        assert result.eauto_charge is None

    @pytest.mark.parametrize("load_value", [0.0, 500.0, 2000.0])
    def test_different_loads(self, load_value):
        """Test optimization response to various load profiles.

        Verifies that the optimization algorithm produces valid charging schedules
        under different constant load scenarios. Tests the system's behavior with
        zero load, moderate load, and high load conditions.

        Args:
            load_value (float): The constant load value to test with (in watts)

        Assertions:
            - All charging/discharging values are within the battery's power limits
        """
        test_params = self.base_test_params()
        old_length = len(test_params["ems"]["gesamtlast"])
        test_params["ems"]["gesamtlast"] = [load_value] * old_length

        optimizer = MILPOptimization(verbose=False)
        params = OptimizationParameters(**test_params)
        self.set_remaining_params()
        result = optimizer.optimize_ems(params)
        max_power = test_params["pv_akku"]["max_charge_power_w"]
        delta = 0.01
        assert all(-max_power - delta <= x <= max_power + delta for x in result.akku_charge)

    def test_price_sensitivity(self):
        """Test optimization response to electricity price variations.

        Verifies that the optimization algorithm responds appropriately to price
        differentials between day and night periods. Expects the system to prefer
        charging during lower-price periods.

        Assertions:
            - Night charging (lower price) should be greater than or equal to
              day charging (higher price)
        """
        test_params = self.base_test_params()
        old_length = len(test_params["ems"]["strompreis_euro_pro_wh"])
        test_params["ems"]["strompreis_euro_pro_wh"] = [0.40] * (old_length // 2) + [0.20] * (
            old_length // 2
        )

        optimizer = MILPOptimization(verbose=False)
        params = OptimizationParameters(**test_params)
        self.set_remaining_params()
        result = optimizer.optimize_ems(params)
        night_charging = sum(result.akku_charge[12:])
        day_charging = sum(result.akku_charge[:12])
        assert night_charging >= day_charging

    def test_high_pv_generation(self):
        """Test optimization behavior with high PV generation.

        Verifies that the system appropriately utilizes battery storage when there
        is consistent high PV generation throughout the day.

        Assertions:
            - At least some positive charging occurs during the period
        """
        test_params = self.base_test_params()
        old_length = len(test_params["ems"]["pv_prognose_wh"])
        test_params["ems"]["pv_prognose_wh"] = [2000.0] * old_length

        optimizer = MILPOptimization(verbose=False)
        self.set_remaining_params()
        params = OptimizationParameters(**test_params)

        result = optimizer.optimize_ems(params)
        assert any(x > 0 for x in result.akku_charge)

    @pytest.mark.parametrize("initial_soc", [15, 50, 85])
    def test_different_initial_soc(self, initial_soc):
        """Test optimization with various initial battery state of charge (SOC) levels.

        Verifies that the optimization algorithm handles different initial battery
        states appropriately and maintains valid state of charge throughout the
        optimization period.

        Args:
            initial_soc (int): Initial state of charge percentage to test with

        Assertions:
            - Charging schedule has correct length
            - Final state of charge remains below 100%
        """
        test_params = self.base_test_params()
        test_params["pv_akku"] = {
            "capacity_wh": 26400,
            "initial_soc_percentage": initial_soc,
            "min_soc_percentage": 15,
        }
        params = OptimizationParameters(**test_params)
        self.set_remaining_params()
        optimizer = MILPOptimization(verbose=False)

        result = optimizer.optimize_ems(params)
        assert len(result.akku_charge) == 24

        # Calculate final SOC
        capacity_wh = test_params["pv_akku"]["capacity_wh"]
        initial_energy = (initial_soc / 100) * capacity_wh
        net_energy_change = sum(result.akku_charge)
        final_energy = initial_energy + net_energy_change
        final_soc = (final_energy / capacity_wh) * 100

        assert final_soc <= 100, f"Final SOC ({final_soc:.2f}%) exceeded 100%"
