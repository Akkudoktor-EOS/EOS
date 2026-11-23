"""Parameter preparation of GENETIC0 (ADR 0003, 0006).

The only part of GENETIC0 that reads configuration, predictions and measurements. It prepares the
optimization parameters once before each run: the request (forecasts and devices), the algorithm
settings and the data the solution conversion presents. The energy management passes the run
start, the observation time, the warm start and run-time overrides as arguments; the preparation
reads no EMS state.

Transitional (migration step 3): ``prepare_request()`` is the legacy preparation moved here
unchanged. It may still write demo providers and devices into the configuration and may cancel
the run; step 3b makes it follow the "optimization parameters are always valid" rule.
"""

import math
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.core.coreabc import get_config, get_measurement, get_prediction
from akkudoktoreos.core.types import FillMethod
from akkudoktoreos.optimization.genetic0.genetic0parameters import (
    Genetic0Parameters,
    Genetic0Presentation,
    Genetic0RunSettings,
)
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0EnergyManagementParameters,
    Genetic0Request,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
)
from akkudoktoreos.utils.gridinterpolator import configured_grid_interpolator_backend

# Settings that a run can override.
OVERRIDE_KEYS = ("generations", "seed")

# Prediction series the solution conversion presents: key and fill method.
PRESENTATION_PREDICTIONS: tuple[tuple[str, FillMethod], ...] = (
    ("pvforecast_ac_power", "linear"),
    ("pvforecast_dc_power", "linear"),
    ("elecprice_marketprice_wh", "ffill"),
    ("feed_in_tariff_wh", "ffill"),
    ("weather_temp_air", "linear"),
    ("loadforecast_power_w", "linear"),
    ("loadakkudoktor_std_power_w", "linear"),
    ("loadakkudoktor_mean_power_w", "linear"),
)

# Device IDs of the solution. GENETIC0 looks the configured EV and home appliance up with
# ``dict.values()[0]``, which always fails, so their IDs were always these fallbacks; kept as they
# are (GENETIC0 is frozen, ADR 0008).
FALLBACK_BATTERY_DEVICE_ID = "battery1"
EV_DEVICE_ID = "ev1"
HOME_APPLIANCE_DEVICE_ID = "homeappliance1"


# Demo devices: used if no device of a kind is configured.
DEMO_BATTERY_SETTINGS: dict = {
    "device_id": "battery1",
    "profile_adjustment": {"nominal_capacity_wh": 8000},
}
DEMO_INVERTER_SETTINGS: dict = {
    "device_id": "inverter1",
    "profile_adjustment": {"nominal_power_w": 10000},
}
DEMO_ELECTRIC_VEHICLE_SETTINGS: dict = {
    "device_id": "ev1",
    "profile_adjustment": {"nominal_capacity_wh": 50000},
    "charge_rates": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "min_soc_percentage": 70,
}


async def prepare(
    start_datetime: DateTime,
    observation_datetime: DateTime,
    warm_start: Optional[Genetic0Solution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> Genetic0Parameters:
    """Prepare the parameters of a run from configuration, predictions and measurements.

    Args:
        start_datetime: Start of the optimization run.
        observation_datetime: Moment of the run that measurements refer to.
        warm_start: Result of the previous GENETIC0 run, if any.
        overrides: Run-time overrides of ``generations`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        RuntimeError: The legacy preparation could not build the request (until step 3b).
    """
    start_solution = None
    if warm_start is not None and warm_start.start_solution:
        start_solution = warm_start.start_solution
    request = await prepare_request(start_datetime, observation_datetime, start_solution)
    if request is None:
        raise RuntimeError("Could not prepare optimisation parameters.")
    return await _complete(request, start_datetime, overrides)


async def prepare_from_request(
    request: Genetic0Request,
    start_datetime: DateTime,
    observation_datetime: DateTime,
    warm_start: Optional[Genetic0Solution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> Genetic0Parameters:
    """Complete a client's optimization request into the parameters of a run.

    Args:
        request: The client's forecast and device parameters, used as they are.
        start_datetime: Start of the optimization run.
        observation_datetime: Moment of the run; not used, the request carries the device state.
        warm_start: Result of the previous GENETIC0 run; not used, the request carries its own
            start solution.
        overrides: Run-time overrides of ``generations`` and ``seed``.

    Returns:
        The optimization parameters of the run.
    """
    return await _complete(request, start_datetime, overrides)


async def _complete(
    request: Genetic0Request,
    start_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]],
) -> Genetic0Parameters:
    settings = _run_settings(overrides)
    return Genetic0Parameters(
        request=request,
        start_hour=start_datetime.hour,
        settings=settings,
        presentation=await _presentation(start_datetime, settings),
    )


def _run_settings(overrides: Optional[Mapping[str, Any]]) -> Genetic0RunSettings:
    """Algorithm settings from the configuration, as the algorithm read them before."""
    config = get_config()
    genetic0 = config.optimization.genetic0
    values: dict[str, Any] = {"generations": genetic0.generations, "seed": genetic0.seed}
    for key, value in (overrides or {}).items():
        if key not in OVERRIDE_KEYS:
            logger.error("GENETIC0: Unknown override '{}' ignored.", key)
        elif value is not None:
            values[key] = value

    prediction_hours = config.prediction.hours
    if prediction_hours is None:
        prediction_hours = 48
        logger.error("GENETIC0: prediction.hours not configured, using {}.", prediction_hours)

    ev_charge_rates = None
    electric_vehicles = config.devices.electric_vehicles
    if electric_vehicles:
        rates = list(electric_vehicles.values())[0].charge_rates
        if rates is not None:
            ev_charge_rates = tuple(rates)

    battery_charge_rates = None
    batteries = config.devices.batteries
    if batteries:
        battery_rates = list(batteries.values())[0].charge_rates
        if battery_rates:
            battery_charge_rates = tuple(battery_rates)

    return Genetic0RunSettings(
        prediction_hours=prediction_hours,
        horizon_hours=genetic0.horizon_hours,
        individuals=genetic0.individuals,
        generations=values["generations"],
        seed=values["seed"],
        penalties=dict(genetic0.penalties),
        ev_charge_rates=ev_charge_rates,
        battery_charge_rates=battery_charge_rates,
        self_consumption_interpolator=configured_grid_interpolator_backend(),
        verbose=bool(config.server.verbose),
    )


async def _presentation(
    start_datetime: DateTime, settings: Genetic0RunSettings
) -> Genetic0Presentation:
    """The data the solution conversion presents, read once before the run."""
    config = get_config()
    batteries = config.devices.batteries
    if batteries:
        battery_device_id = list(batteries.values())[0].device_id
    else:
        battery_device_id = FALLBACK_BATTERY_DEVICE_ID

    # The window the conversion covers: from the start to the end of the prediction day(s),
    # counted from the start hour of the run and from the start hour in the configured time zone
    # (``None`` means the local zone, as in the conversion).
    timezone = config.general.timezone
    hours = min(
        settings.prediction_hours - start_datetime.hour,
        settings.prediction_hours - to_datetime(start_datetime, in_timezone=timezone).hour,
    )
    end_datetime = start_datetime.add(hours=max(hours, 0))
    prediction = get_prediction()
    series: dict[str, tuple[float, ...]] = {}
    for key, fill_method in PRESENTATION_PREDICTIONS:
        if hours <= 0 or key not in prediction.record_keys:
            continue
        array = await prediction.key_to_array(
            key=key,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=to_duration("1 hours"),
            fill_method=fill_method,
        )
        if array is not None:
            series[key] = tuple(float(value) for value in np.asarray(array, dtype=float))

    max_home_appliances = config.devices.max_home_appliances
    return Genetic0Presentation(
        start_datetime=start_datetime,
        timezone=timezone,
        battery_device_id=battery_device_id,
        ev_device_id=EV_DEVICE_ID,
        home_appliance_device_id=HOME_APPLIANCE_DEVICE_ID,
        home_appliance_enabled=bool(max_home_appliances and max_home_appliances > 0),
        prediction_series=series,
    )


async def bounded_forecast_array(
    prediction: Any,
    *,
    key: str,
    start_datetime: DateTime,
    end_datetime: DateTime,
    interval: Duration,
) -> np.ndarray:
    """Integrate stored interval averages only across completely covered target slots.

    Copied from GENETIC's ``forecast`` module: optimizers share no code (ADR 0004).

    Source timestamps are interval starts. Infer their smallest positive cadence,
    capped at one hour. A missing source interval, explicit NaN or the end of the
    available forecast remains missing. UTC elapsed time handles DST transitions.
    The returned values retain the input units; callers convert W to slot Wh once.
    """
    seconds = int(interval.total_seconds())
    start = start_datetime.timestamp()
    end = end_datetime.timestamp()
    if seconds <= 0 or end <= start or (end - start) % seconds:
        raise ValueError("Forecast bounds must contain a positive whole number of slots.")
    result = np.full(int((end - start) / seconds), np.nan)
    try:
        series = await prediction.key_to_raw_series(key=key, dropna=False)
    except KeyError:
        return result
    if series.empty:
        return result
    series = pd.to_numeric(series, errors="coerce").sort_index()
    series = series[~series.index.duplicated(keep="last")]
    stamps = pd.to_datetime(series.index, utc=True).as_unit("ns").asi8 / 1e9
    values = series.to_numpy(dtype=float)
    differences = np.diff(stamps)
    cadence = min(3600.0, float(np.min(differences))) if len(differences) else 3600.0
    for slot in range(len(result)):
        left = start + slot * seconds
        right = left + seconds
        position = max(0, int(np.searchsorted(stamps, left, side="right")) - 1)
        covered = 0.0
        weighted = 0.0
        while position < len(stamps) and stamps[position] < right:
            source_end = stamps[position] + cadence
            if position + 1 < len(stamps):
                source_end = min(source_end, stamps[position + 1])
            overlap = max(0.0, min(right, source_end) - max(left, stamps[position]))
            if overlap and np.isfinite(values[position]):
                covered += overlap
                weighted += (overlap / seconds) * values[position]
            position += 1
        if abs(covered - seconds) < 1e-6:
            result[slot] = weighted
    return result


async def _nearest_soc_factor(key: str, target: DateTime) -> float:
    """SoC factor nearest to the run time within +/-24 h.

    Readings carry their real time, not the slot start, and records without a
    SoC value (e.g. an adapter's meter reading) are skipped (#1447).
    """
    window = to_duration("24 hours")
    dates, values = await get_measurement().key_to_lists(
        key=key,
        start_datetime=target - window,
        end_datetime=target + window,
        dropna=False,
    )
    samples = [
        (abs(date.timestamp() - target.timestamp()), value)
        for date, value in zip(dates, values)
        if value is not None and value is not pd.NA
    ]
    if not samples:
        raise ValueError(f"No SoC value for '{key}' within 24 hours of {target}.")
    # Convert only the selected sample: an unrelated older value must not fail.
    return float(min(samples, key=lambda sample: sample[0])[1])


async def prepare_request(
    start_datetime: DateTime,
    observation_datetime: DateTime,
    start_solution: Optional[list[float]] = None,
) -> Optional[Genetic0Request]:
    """Prepare optimization parameters from config, forecast and measurement data.

    Fills in values needed for optimization from available configuration, predictions and
    measurements. If some data is missing, default or demo values are used.

    The parameter arrays start at hour 0 of the start day, not at the start of the run; the
    run starts at index ``start_datetime.hour``.

    Args:
        start_datetime: Start of the optimization run.
        observation_datetime: Moment of the run that measured states of charge refer to.
        start_solution: Start solution of the previous run (warm start), passed in by the
            parameter preparation.

    Returns:
        Genetic0Request: The fully prepared optimization parameters.

    Raises:
        ValueError: If required configuration values like start time are missing.
    """
    config = get_config()
    prediction = get_prediction()

    # The optimization paramters
    oparams: "Optional[Genetic0Request]" = None

    # Check for run definitions
    if start_datetime is None:
        error_msg = "Start datetime unknown."
        logger.error(error_msg)
        raise ValueError(error_msg)
    # Check for general predictions conditions
    if config.general.latitude is None:
        default_latitude = 52.52
        logger.info(f"Latitude unknown - defaulting to {default_latitude}.")
        config.general.latitude = default_latitude
    if config.general.longitude is None:
        default_longitude = 13.405
        logger.info(f"Longitude unknown - defaulting to {default_longitude}.")
        config.general.longitude = default_longitude
    if config.prediction.hours is None:
        logger.info("Prediction hours unknown - defaulting to 48 hours.")
        config.prediction.hours = 48
    if config.prediction.historic_hours is None:
        logger.info("Prediction historic hours unknown - defaulting to 24 hours.")
        config.prediction.historic_hours = 24
    # Check optimization definitions. interval_sec is fixed to 1 hour.
    if config.optimization.genetic0.horizon_hours is None:
        logger.info("Optimization horizon unknown - defaulting to 24 hours.")
        config.optimization.genetic0.horizon_hours = 24
    # Check genetic algorithm definitions
    if config.optimization.genetic0.individuals is None:
        logger.info("Genetic individuals unknown - defaulting to 300.")
        config.optimization.genetic0.individuals = 300
    if config.optimization.genetic0.generations is None:
        logger.info("Genetic generations unknown - defaulting to 400.")
        config.optimization.genetic0.generations = 400
    if "ev_soc_miss" not in config.optimization.genetic0.penalties:
        logger.info("Genetic penalties unknown - defaulting to ev_soc_miss = 10.")
        config.optimization.genetic0.penalties["ev_soc_miss"] = 10
    # Setup some basic providers if not set
    if not config.weather.provider:
        config.weather.provider = "OpenMeteo"
    if not config.load.provider:
        config.load.provider = "LoadAkkudoktor"

    # Add forecast and device data
    interval = to_duration(config.optimization.genetic0.interval_sec)
    power_to_energy_per_interval_factor = config.optimization.genetic0.interval_sec / 3600
    # GENETIC0 indexes the parameter arrays by wall-clock hour (the simulation
    # starts at ``start_datetime.hour``), while the arrays themselves are
    # sampled in elapsed time. On a DST transition day local midnight is 23 or
    # 25 elapsed hours away from the same wall-clock hour, so step back
    # ``hour`` elapsed hours instead of setting the hour to midnight. This keeps
    # array index ``start_datetime.hour`` aligned with the start slot.
    parameter_start_datetime = start_datetime.set(second=0, microsecond=0).subtract(
        hours=start_datetime.hour
    )
    parameter_end_datetime = parameter_start_datetime.add(hours=config.prediction.hours)
    max_retries = 10

    for attempt in range(1, max_retries + 1):
        # Collect all the data for optimisation, but do not exceed max retries
        if attempt > max_retries:
            error_msg = f"Maximum retries {max_retries} for parameter collection exceeded. Parameter preparation attempt {attempt}."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Assure predictions are uptodate
        await prediction.update_data()

        try:  # Try first - predition is also needed by the default PV forecast
            array = await prediction.key_to_array(
                key="weather_temp_air",
                start_datetime=parameter_start_datetime,
                end_datetime=parameter_end_datetime,
                interval=interval,
                fill_method="ffill",
            )
            weather_temp_air = array.tolist()
        except Exception as e:
            logger.info(
                "No weather forecast data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                attempt,
                e,
            )
            config.weather.provider = "OpenMeteo"
            # Retry
            continue
        # Try electricity fees next - predition is also needed by the default electricity price
        # If no provider is set the fees default to 0 anyway
        if config.elecfee.provider:
            try:
                array = await prediction.key_to_array(
                    key="elecfee_consumption_amt_kwh",
                    start_datetime=parameter_start_datetime,
                    end_datetime=parameter_end_datetime,
                    interval=interval,
                    fill_method="ffill",
                )
            except Exception as e:
                logger.info(
                    "No electricity fee data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                    attempt,
                    e,
                )
                config.merge_settings_from_dict(
                    {
                        "elecfee": {
                            "provider": "ElecFeeFixed",
                            "elecfeefixed": {
                                "consumption_amt_kwh": {
                                    "windows": [
                                        {
                                            "start_time": "00:00",
                                            "duration": "24 hours",
                                            "value": 0.21,
                                        },
                                    ]
                                },
                                "consumption_percent_amt": {
                                    "windows": [
                                        {
                                            "start_time": "00:00",
                                            "duration": "24 hours",
                                            "value": 19.0,
                                        },
                                    ]
                                },
                                "feedin_amt_kwh": {
                                    "windows": [
                                        {
                                            "start_time": "00:00",
                                            "duration": "24 hours",
                                            "value": 0.0,
                                        },
                                    ]
                                },
                                "feedin_percent_amt": {
                                    "windows": [
                                        {
                                            "start_time": "00:00",
                                            "duration": "24 hours",
                                            "value": 0.0,
                                        },
                                    ]
                                },
                            },
                        },
                    }
                )
                # Retry
                continue
        try:
            array = await prediction.key_to_array(
                key="pvforecast_ac_power",
                start_datetime=parameter_start_datetime,
                end_datetime=parameter_end_datetime,
                interval=interval,
                fill_method="linear",
            )
            pvforecast_ac_power = (array * power_to_energy_per_interval_factor).tolist()
        except Exception as e:
            logger.info(
                "No PV forecast data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                attempt,
                e,
            )
            config.merge_settings_from_dict(
                {
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
            )
            # Retry
            continue
        try:
            array = await prediction.key_to_array(
                key="elecprice_marketprice_wh",
                start_datetime=parameter_start_datetime,
                end_datetime=parameter_end_datetime,
                interval=interval,
                fill_method="ffill",
            )
            elecprice_marketprice_wh = array.tolist()
        except Exception as e:
            logger.info(
                "No Electricity Marketprice forecast data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                attempt,
                e,
            )
            config.merge_settings_from_dict(
                {
                    "elecprice": {
                        "elecpricefixed": {
                            "elecprice_marketprice_amt_kwh": {
                                "windows": [
                                    {
                                        "duration": "1 day",
                                        "start_time": "00:00:00.000000",
                                        "value": 0.288,
                                    }
                                ]
                            }
                        },
                        "provider": "ElecPriceFixed",
                    },
                },
            )
            # Retry
            continue
        try:
            array = await prediction.key_to_array(
                key="loadforecast_power_w",
                start_datetime=parameter_start_datetime,
                end_datetime=parameter_end_datetime,
                interval=interval,
                fill_method="ffill",
            )
            loadforecast_power_w = array.tolist()
        except Exception as e:
            logger.info(
                "No Load forecast data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                attempt,
                e,
            )
            config.merge_settings_from_dict(
                {
                    "load": {
                        "provider": "LoadAkkudoktor",
                        "loadakkudoktor": {
                            "loadakkudoktor_year_energy_kwh": "3000",
                        },
                    },
                }
            )
            # Retry
            continue
        tariff_values = await bounded_forecast_array(
            prediction,
            key="feed_in_tariff_wh",
            start_datetime=parameter_start_datetime,
            end_datetime=parameter_end_datetime,
            interval=interval,
        )
        first = int(
            (start_datetime - parameter_start_datetime).total_seconds() / interval.total_seconds()
        )
        last = first + config.optimization.genetic0.horizon_hours
        if last > len(tariff_values) or not all(
            math.isfinite(value) for value in tariff_values[first:last]
        ):
            logger.error(
                "Missing feed-in tariff within the GENETIC0 control horizon; "
                "canceling optimization with provider {}.",
                config.feedintariff.provider,
            )
            return None
        feed_in_tariff_wh = tariff_values.tolist()

        # Add device data

        # Batteries
        # ---------
        if config.devices.max_batteries is None:
            logger.info("Number of battery devices not configured - defaulting to 1.")
            config.devices.max_batteries = 1
        if config.devices.max_batteries == 0:
            battery_params = None
            battery_lcos_kwh = 0
        else:
            if config.devices.batteries is None:
                logger.info("No battery device data available - defaulting to demo data.")
                config.devices.batteries = {"battery1": dict(DEMO_BATTERY_SETTINGS)}  # type: ignore[dict-item]
            # Take first battery
            battery_config = list(config.devices.batteries.values())[0]
            try:
                # A configuration error gives default values, reported as error.
                (
                    battery_parameters,
                    _,
                ) = await battery_config.resolve_current_operating_parameters_or_defaults()
                battery_params = battery_config.to_genetic0_pv_bat_param(battery_parameters)
            except Exception as e:
                # Never cancel the run, never change the user's configuration and
                # never invent a device: optimize without this battery.
                logger.error(
                    "Battery '{}' cannot be prepared for the optimization: {} "
                    "The battery is left out of this run.",
                    battery_config.device_id,
                    e,
                )
                battery_params = None
                battery_lcos_kwh = 0
            if battery_params is not None:
                # Levelized cost of ownership
                if battery_config.levelized_cost_of_storage_amt_kwh is None:
                    logger.info(
                        "No battery device LCOS data available - defaulting to 0 [amount/kWh]. Parameter preparation attempt {}.",
                        attempt,
                    )
                    battery_config.levelized_cost_of_storage_amt_kwh = 0
                battery_lcos_kwh = battery_config.levelized_cost_of_storage_amt_kwh  # type: ignore[assignment]
                # Initial SOC
                try:
                    initial_soc_factor = await _nearest_soc_factor(
                        battery_config.measurement_key_soc_factor, observation_datetime
                    )
                    if initial_soc_factor > 1.0 or initial_soc_factor < 0.0:
                        logger.error(
                            f"Invalid battery initial SoC factor {initial_soc_factor} - defaulting to 0.0."
                        )
                        initial_soc_factor = 0.0
                    # genetic parameter is 0..100 as int
                    initial_soc_percentage = int(initial_soc_factor * 100)
                except Exception:
                    initial_soc_percentage = None
                if initial_soc_percentage is None:
                    logger.info(
                        f"No battery device SoC data (measurement key = '{battery_config.measurement_key_soc_factor}') available - defaulting to 0."
                    )
                    initial_soc_percentage = 0
                battery_params.initial_soc_percentage = initial_soc_percentage

        # Electric Vehicles
        # -----------------
        if config.devices.max_electric_vehicles is None:
            logger.info("Number of electric_vehicle devices not configured - defaulting to 1.")
            config.devices.max_electric_vehicles = 1
        if config.devices.max_electric_vehicles == 0:
            electric_vehicle_params = None
        else:
            if config.devices.electric_vehicles is None:
                logger.info("No electric vehicle device data available - defaulting to demo data.")
                config.devices.max_electric_vehicles = 1
                config.devices.electric_vehicles = {
                    "ev1": dict(DEMO_ELECTRIC_VEHICLE_SETTINGS)  # type: ignore[dict-item]
                }
            # Take first electric_vehicle
            electric_vehicle_config = list(config.devices.electric_vehicles.values())[0]
            try:
                # A configuration error gives default values, reported as error.
                (
                    ev_parameters,
                    _,
                ) = await electric_vehicle_config.resolve_current_operating_parameters_or_defaults()
                electric_vehicle_params = electric_vehicle_config.to_genetic0_ev_bat_param(
                    ev_parameters
                )
            except Exception as e:
                # Never cancel the run, never change the user's configuration and
                # never invent a device: optimize without this electric vehicle.
                logger.error(
                    "Electric vehicle '{}' cannot be prepared for the optimization: {} "
                    "The electric vehicle is left out of this run.",
                    electric_vehicle_config.device_id,
                    e,
                )
                electric_vehicle_params = None
            if electric_vehicle_params is not None:
                # Initial SOC
                try:
                    initial_soc_factor = await _nearest_soc_factor(
                        electric_vehicle_config.measurement_key_soc_factor,
                        observation_datetime,
                    )
                    if initial_soc_factor > 1.0 or initial_soc_factor < 0.0:
                        logger.error(
                            f"Invalid electric vehicle initial SoC factor {initial_soc_factor} - defaulting to 0.0."
                        )
                        initial_soc_factor = 0.0
                    # genetic parameter is 0..100 as int
                    initial_soc_percentage = int(initial_soc_factor * 100)
                except Exception:
                    initial_soc_percentage = None
                if initial_soc_percentage is None:
                    logger.info(
                        f"No electric vehicle device SoC data (measurement key = '{electric_vehicle_config.measurement_key_soc_factor}') available - defaulting to 0."
                    )
                    initial_soc_percentage = 0
                electric_vehicle_params.initial_soc_percentage = initial_soc_percentage

        # Inverters
        # ---------
        if config.devices.max_inverters is None:
            logger.info("Number of inverter devices not configured - defaulting to 1.")
            config.devices.max_inverters = 1
        if config.devices.max_inverters == 0:
            inverter_params = None
        else:
            if config.devices.inverters is None:
                logger.info("No inverter device data available - defaulting to demo data.")
                config.devices.inverters = {
                    "inverter1": {  # type: ignore[dict-item]
                        **DEMO_INVERTER_SETTINGS,
                        "battery_id": battery_config.device_id,
                    },
                }
            # Take first inverter
            inverter_config = list(config.devices.inverters.values())[0]
            try:
                # A configuration error gives default values, reported as error.
                inverter_params = inverter_config.to_genetic0_param(
                    await inverter_config.resolve_current_operating_parameters_or_defaults()
                )
            except Exception as e:
                # Never cancel the run, never change the user's configuration and
                # never invent a device: optimize without this inverter.
                logger.error(
                    "Inverter '{}' cannot be prepared for the optimization: {} "
                    "The inverter is left out of this run.",
                    inverter_config.device_id,
                    e,
                )
                inverter_params = None

        # Home Appliances
        # ---------------
        if config.devices.max_home_appliances is None:
            logger.info("Number of home appliance devices not configured - defaulting to 1.")
            config.devices.max_home_appliances = 1
        if config.devices.max_home_appliances == 0:
            home_appliance_params = None
        else:
            home_appliance_params = None
            if config.devices.home_appliances is None:
                logger.info("No home appliance device data available - defaulting to demo data.")
                config.devices.home_appliances = {
                    "dishwasher1": {
                        "device_id": "dishwasher1",
                        "consumption_wh": 2000,
                        "duration_h": 3.0,
                        "cycle_time_windows": {
                            "windows": [
                                {
                                    "start_time": "08:00",
                                    "duration": "5 hours",
                                },
                                {
                                    "start_time": "15:00",
                                    "duration": "3 hours",
                                },
                            ],
                        },
                    },
                }
            try:
                # Take first appliance
                home_appliance_config = list(config.devices.home_appliances.values())[0]
                home_appliance_params = home_appliance_config.to_genetic0_param()
            except Exception as e:
                logger.info(
                    "No home appliance device data available - defaulting to demo data. Parameter preparation attempt {}: {}",
                    attempt,
                    e,
                )
                config.devices.home_appliances = {
                    "dishwasher1": {  # type: ignore[dict-item]
                        "device_id": "dishwasher1",
                        "consumption_wh": 2000,
                        "duration_h": 3.0,
                        "cycle_time_windows": None,
                    },
                }
                # Retry
                continue

        # We got all parameter data
        try:
            oparams = Genetic0Request(
                ems=Genetic0EnergyManagementParameters(
                    pv_forecast_wh=pvforecast_ac_power,
                    electricity_price_per_wh=elecprice_marketprice_wh,
                    feed_in_tariff_per_wh=feed_in_tariff_wh,
                    total_load=loadforecast_power_w,
                    price_per_wh_battery=battery_lcos_kwh / 1000,
                ),
                temperature_forecast=weather_temp_air,
                pv_battery=battery_params,
                ev=electric_vehicle_params,
                inverter=inverter_params,
                dishwasher=home_appliance_params,
                start_solution=start_solution,
            )
        except Exception as e:
            logger.info(
                "Can not prepare optimization parameters - will retry. Parameter preparation attempt {}: {}",
                attempt,
                e,
            )
            oparams = None
            # Retry
            continue

        # Parameters prepared
        break

    return oparams
