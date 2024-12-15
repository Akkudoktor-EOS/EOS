#!/usr/bin/env python3

import argparse
import cProfile
import pstats
import sys
import time

import numpy as np

from akkudoktoreos.config.config import get_config
from akkudoktoreos.optimization.genetic import (
    OptimizationParameters,
    optimization_problem,
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

    # Start Solution (binary)
    start_solution = None

    # Define parameters for the optimization problem
    return OptimizationParameters(
        **{
            "ems": {
                "preis_euro_pro_wh_akku": 0e-05,
                "einspeiseverguetung_euro_pro_wh": 7e-05,
                "gesamtlast": gesamtlast,
                "pv_prognose_wh": pv_forecast,
                "strompreis_euro_pro_wh": strompreis_euro_pro_wh,
            },
            "pv_akku": {
                "kapazitaet_wh": 26400,
                "start_soc_prozent": 15,
                "min_soc_prozent": 15,
            },
            "eauto": {
                "min_soc_prozent": 50,
                "kapazitaet_wh": 60000,
                "lade_effizienz": 0.95,
                "max_ladeleistung_w": 11040,
                "start_soc_prozent": 5,
            },
            "temperature_forecast": temperature_forecast,
            "start_solution": start_solution,
        }
    )


def run_optimization(start_hour: int = 0, verbose: bool = False) -> dict:
    """Run the optimization problem.

    Args:
        start_hour (int, optional): Starting hour for optimization. Defaults to 0.
        verbose (bool, optional): Whether to print verbose output. Defaults to False.

    Returns:
        dict: Optimization result as a dictionary
    """
    # Initialize the optimization problem using the default configuration
    config_eos = get_config()
    config_eos.merge_settings_from_dict({"prediction_hours": 48, "optimization_hours": 24})
    opt_class = optimization_problem(verbose=verbose, fixed_seed=42)

    # Prepare parameters
    parameters = prepare_optimization_parameters()

    # Perform the optimisation based on the provided parameters and start hour
    result = opt_class.optimierung_ems(parameters=parameters, start_hour=start_hour)

    return result.model_dump()


def main():
    """Main function to run the optimization script with optional profiling."""
    parser = argparse.ArgumentParser(description="Run Energy Optimization Simulation")
    parser.add_argument("--profile", action="store_true", help="Enable performance profiling")
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose output during optimization"
    )
    parser.add_argument(
        "--start-hour", type=int, default=0, help="Starting hour for optimization (default: 0)"
    )

    args = parser.parse_args()

    if args.profile:
        # Run with profiling
        profiler = cProfile.Profile()
        try:
            result = profiler.runcall(
                run_optimization, start_hour=args.start_hour, verbose=args.verbose
            )
            # Print profiling statistics
            stats = pstats.Stats(profiler)
            stats.strip_dirs().sort_stats("cumulative").print_stats(200)
            # Print result
            print("\nOptimization Result:")
            print(result)

        except Exception as e:
            print(f"Error during optimization: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Run without profiling
        try:
            start_time = time.time()
            result = run_optimization(start_hour=args.start_hour, verbose=args.verbose)
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"\nElapsed time: {elapsed_time:.4f} seconds.")
            print("\nOptimization Result:")
            print(result)

        except Exception as e:
            print(f"Error during optimization: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
