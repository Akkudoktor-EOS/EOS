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
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
        "pvforecast": {
            "provider": "PVForecastAkkudoktor",
            "planes": [
                {
                    "peakpower": 5.0,
                    "surface_azimuth": -10,
                    "surface_tilt": 7,
                    "userhorizon": [20, 27, 22, 20],
                    "inverter_paco": 10000,
                },
                {
                    "peakpower": 4.8,
                    "surface_azimuth": -90,
                    "surface_tilt": 7,
                    "userhorizon": [30, 30, 30, 50],
                    "inverter_paco": 10000,
                },
                {
                    "peakpower": 1.4,
                    "surface_azimuth": -40,
                    "surface_tilt": 60,
                    "userhorizon": [60, 30, 0, 30],
                    "inverter_paco": 2000,
                },
                {
                    "peakpower": 1.6,
                    "surface_azimuth": 5,
                    "surface_tilt": 45,
                    "userhorizon": [45, 25, 30, 60],
                    "inverter_paco": 1400,
                },
            ],
        },
    }
    return settings


def config_weather() -> dict:
    """Configure settings for weather forecast."""
    settings = {
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
        "weather": dict(),
    }
    return settings


def config_elecprice() -> dict:
    """Configure settings for electricity price forecast."""
    settings = {
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
        "elecprice": dict(),
    }
    return settings


def config_load() -> dict:
    """Configure settings for load forecast."""
    settings = {
        "general": {
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "prediction": {
            "hours": 48,
            "historic_hours": 24,
        },
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
    if provider_id in ("PVForecastAkkudoktor",):
        settings = config_pvforecast()
        forecast = "pvforecast"
    elif provider_id in ("BrightSky", "ClearOutside"):
        settings = config_weather()
        forecast = "weather"
    elif provider_id in ("ElecPriceAkkudoktor",):
        settings = config_elecprice()
        forecast = "elecprice"
    elif provider_id in ("LoadAkkudoktor",):
        settings = config_elecprice()
        forecast = "load"
        settings["load"]["loadakkudoktor_year_energy"] = 1000
    else:
        raise ValueError(f"Unknown provider '{provider_id}'.")
    settings[forecast]["provider"] = provider_id
    config_eos.merge_settings_from_dict(settings)

    provider = prediction_eos.provider_by_id(provider_id)

    prediction_eos.update_data()

    # Return result of prediction
    if verbose:
        print(f"\nProvider ID: {provider.provider_id()}")
        print("----------")
        print("\nSettings\n----------")
        print(settings)
        print("\nProvider\n----------")
        print(f"elecprice.provider: {config_eos.elecprice.provider}")
        print(f"load.provider: {config_eos.load.provider}")
        print(f"pvforecast.provider: {config_eos.pvforecast.provider}")
        print(f"weather.provider: {config_eos.weather.provider}")
        print(f"enabled: {provider.enabled()}")
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
