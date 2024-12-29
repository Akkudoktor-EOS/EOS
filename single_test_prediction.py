#!/usr/bin/env python3

import argparse
import cProfile
import pstats
import sys
import time

from akkudoktoreos.config.config import get_config
from akkudoktoreos.prediction.prediction import get_prediction

config_eos = get_config()
prediction_eos = get_prediction()


def config_pvforecast() -> dict:
    """Configure settings for PV forecast."""
    settings = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
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
    }
    return settings


def config_weather() -> dict:
    """Configure settings for weather forecast."""
    settings = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
    }
    return settings


def config_elecprice() -> dict:
    """Configure settings for electricity price forecast."""
    settings = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
    }
    return settings


def config_load() -> dict:
    """Configure settings for load forecast."""
    settings = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
    }
    return settings


def run_prediction(provider_id: str, verbose: bool = False) -> str:
    """Run the prediction.

    Args:
        provider_id (str): ID of prediction provider.
        verbose (bool, optional): Whether to print verbose output. Defaults to False.

    Returns:
        dict: Prediction result as a dictionary
    """
    # Initialize the oprediction
    config_eos = get_config()
    prediction_eos = get_prediction()
    if verbose:
        print(f"\nProvider ID: {provider_id}")
    if provider_id in ("PVForecastAkkudoktor",):
        settings = config_pvforecast()
        settings["pvforecast_provider"] = provider_id
    elif provider_id in ("BrightSky", "ClearOutside"):
        settings = config_weather()
        settings["weather_provider"] = provider_id
    elif provider_id in ("ElecPriceAkkudoktor",):
        settings = config_elecprice()
        settings["elecprice_provider"] = provider_id
    elif provider_id in ("LoadAkkudoktor",):
        settings = config_elecprice()
        settings["loadakkudoktor_year_energy"] = 1000
        settings["load_provider"] = provider_id
    else:
        raise ValueError(f"Unknown provider '{provider_id}'.")
    config_eos.merge_settings_from_dict(settings)

    prediction_eos.update_data()

    # Return result of prediction
    provider = prediction_eos.provider_by_id(provider_id)
    if verbose:
        for key in provider.record_keys:
            print(f"\n{key}\n----------")
            print(f"Array: {provider.key_to_array(key)}")
    return provider.model_dump_json(indent=4)


def main():
    """Main function to run the optimization script with optional profiling."""
    parser = argparse.ArgumentParser(description="Run Prediction")
    parser.add_argument("--profile", action="store_true", help="Enable performance profiling")
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose output during prediction"
    )
    parser.add_argument("--provider-id", type=str, default=0, help="Provider ID of prediction")

    args = parser.parse_args()

    if args.profile:
        # Run with profiling
        profiler = cProfile.Profile()
        try:
            result = profiler.runcall(
                run_prediction, provider_id=args.provider_id, verbose=args.verbose
            )
            # Print profiling statistics
            stats = pstats.Stats(profiler)
            stats.strip_dirs().sort_stats("cumulative").print_stats(200)
            # Print result
            print("\nPrediction Result:")
            print(result)

        except Exception as e:
            print(f"Error during prediction: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Run without profiling
        try:
            start_time = time.time()
            result = run_prediction(provider_id=args.provider_id, verbose=args.verbose)
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"\nElapsed time: {elapsed_time:.4f} seconds.")
            print("\nPrediction Result:")
            print(result)

        except Exception as e:
            print(f"Error during prediction: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
