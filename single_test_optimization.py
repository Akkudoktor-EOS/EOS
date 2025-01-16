#!/usr/bin/env python3

import argparse
import cProfile
import json
import pstats
import sys
import time
from typing import Any

import numpy as np

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.optimization.genetic import (
    OptimizationParameters,
    optimization_problem,
)
from akkudoktoreos.prediction.prediction import get_prediction

get_logger(__name__, logging_level="DEBUG")


def prepare_optimization_real_parameters() -> OptimizationParameters:
    """Prepare and return optimization parameters with real world data.

    Returns:
        OptimizationParameters: Configured optimization parameters
    """
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
        "weather_provider": "ClearOutside",
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
    print(
        f"Real data prediction from {prediction_eos.start_datetime} to {prediction_eos.end_datetime}"
    )
    prediction_eos.update_data()

    # PV Forecast (in W)
    pv_forecast = prediction_eos.key_to_array(
        key="pvforecast_ac_power",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    )
    print(f"pv_forecast: {pv_forecast}")

    # Temperature Forecast (in degree C)
    temperature_forecast = prediction_eos.key_to_array(
        key="weather_temp_air",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    )
    print(f"temperature_forecast: {temperature_forecast}")

    # Electricity Price (in Euro per Wh)
    electricity_price_euro_per_wh = prediction_eos.key_to_array(
        key="elecprice_marketprice_wh",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    )
    print(f"electricity_price_euro_per_wh: {electricity_price_euro_per_wh}")

    # Overall System Load (in W)
    gesamtlast = prediction_eos.key_to_array(
        key="load_mean",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    )
    print(f"gesamtlast: {gesamtlast}")

    # Start Solution (binary)
    start_solution = None
    print(f"start_solution: {start_solution}")

    # Define parameters for the optimization problem
    return OptimizationParameters(
        **{
            "ems": {
                "price_euro_per_wh_battery": 0e-05,
                "feed_in_tariff_euro_per_wh": 7e-05,
                "gesamtlast": gesamtlast,
                "pv_prediction_wh": pv_forecast,
                "electricity_price_euro_per_wh": electricity_price_euro_per_wh,
            },
            "pv_battery": {
                "capacity_wh": 26400,
                "initial_soc_percentage": 15,
                "min_soc_percentage": 15,
            },
            "ev": {
                "min_soc_percentage": 50,
                "capacity_wh": 60000,
                "charging_efficiency": 0.95,
                "max_charge_power_w": 11040,
                "initial_soc_percentage": 5,
            },
            "temperature_forecast": temperature_forecast,
            "start_solution": start_solution,
        }
    )


def prepare_optimization_parameters() -> OptimizationParameters:
    """Prepare and return optimization parameters with predefined data.

    Returns:
        OptimizationParameters: Configured optimization parameters
    """
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
    electricity_price_euro_per_wh = np.full(48, 0.001)
    electricity_price_euro_per_wh[0:10] = 0.00001
    electricity_price_euro_per_wh[11:15] = 0.00005
    electricity_price_euro_per_wh[20] = 0.00001

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

    # Start Solution (binary)
    start_solution = None

    # Define parameters for the optimization problem
    return OptimizationParameters(
        **{
            "ems": {
                "price_euro_per_wh_battery": 0e-05,
                "feed_in_tariff_euro_per_wh": 7e-05,
                "gesamtlast": gesamtlast,
                "pv_prediction_wh": pv_forecast,
                "electricity_price_euro_per_wh": electricity_price_euro_per_wh,
            },
            "pv_battery": {
                "capacity_wh": 26400,
                "initial_soc_percentage": 15,
                "min_soc_percentage": 15,
            },
            "ev": {
                "min_soc_percentage": 50,
                "capacity_wh": 60000,
                "charging_efficiency": 0.95,
                "max_charge_power_w": 11040,
                "initial_soc_percentage": 5,
            },
            "temperature_forecast": temperature_forecast,
            "start_solution": start_solution,
        }
    )


def run_optimization(
    real_world: bool, start_hour: int, verbose: bool, seed: int, parameters_file: str, ngen: int
) -> Any:
    """Run the optimization problem.

    Args:
        start_hour (int, optional): Starting hour for optimization. Defaults to 0.
        verbose (bool, optional): Whether to print verbose output. Defaults to False.

    Returns:
        dict: Optimization result as a dictionary
    """
    # Prepare parameters
    if parameters_file:
        with open(parameters_file, "r") as f:
            parameters = OptimizationParameters(**json.load(f))
    elif real_world:
        parameters = prepare_optimization_real_parameters()
    else:
        parameters = prepare_optimization_parameters()

    if verbose:
        print("\nOptimization Parameters:")
        print(parameters.model_dump_json(indent=4))

    # Initialize the optimization problem using the default configuration
    config_eos = get_config()
    config_eos.merge_settings_from_dict({"prediction_hours": 48, "optimization_hours": 48})
    opt_class = optimization_problem(verbose=verbose, fixed_seed=seed)

    # Perform the optimisation based on the provided parameters and start hour
    result = opt_class.optimierung_ems(parameters=parameters, start_hour=start_hour, ngen=ngen)

    return result.model_dump_json()


def main():
    """Main function to run the optimization script with optional profiling."""
    parser = argparse.ArgumentParser(description="Run Energy Optimization Simulation")
    parser.add_argument("--profile", action="store_true", help="Enable performance profiling")
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose output during optimization"
    )
    parser.add_argument(
        "--real-world", action="store_true", help="Use real world data for predictions"
    )
    parser.add_argument(
        "--start-hour", type=int, default=0, help="Starting hour for optimization (default: 0)"
    )
    parser.add_argument(
        "--parameters-file",
        type=str,
        default="",
        help="Load optimization parameters from json file (default: unset)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Use fixed random seed (default: 42)")
    parser.add_argument(
        "--ngen",
        type=int,
        default=400,
        help="Number of generations during optimization process (default: 400)",
    )

    args = parser.parse_args()

    if args.profile:
        # Run with profiling
        profiler = cProfile.Profile()
        try:
            result = profiler.runcall(
                run_optimization,
                real_world=args.real_world,
                start_hour=args.start_hour,
                verbose=args.verbose,
                seed=args.seed,
                parameters_file=args.parameters_file,
                ngen=args.ngen,
            )
            # Print profiling statistics
            stats = pstats.Stats(profiler)
            stats.strip_dirs().sort_stats("cumulative").print_stats(200)
            # Print result
            if args.verbose:
                print("\nOptimization Result:")
            print(result)

        except Exception as e:
            print(f"Error during optimization: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Run without profiling
        try:
            start_time = time.time()
            result = run_optimization(
                real_world=args.real_world,
                start_hour=args.start_hour,
                verbose=args.verbose,
                seed=args.seed,
                parameters_file=args.parameters_file,
                ngen=args.ngen,
            )
            end_time = time.time()
            elapsed_time = end_time - start_time
            if args.verbose:
                print(f"\nElapsed time: {elapsed_time:.4f} seconds.")
                print("\nOptimization Result:")
            print(result)

        except Exception as e:
            print(f"Error during optimization: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
