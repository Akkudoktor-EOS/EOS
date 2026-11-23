"""Parameter preparation of GENETIC0 (ADR 0003, 0006).

The only part of GENETIC0 that reads configuration, predictions and measurements. It prepares the
optimization parameters once before each run: the request (forecasts and devices), the algorithm
settings and the data the solution conversion presents. The energy management passes the run
start, the observation time, the warm start and run-time overrides as arguments; the preparation
reads no EMS state.

The preparation always creates a valid parameter set (energy-domain skill, "Optimization
parameters are always valid"): it never fails, never cancels the run, never writes the
configuration and never invents devices. A missing or invalid input is replaced by a fallback
value, logged at error level with the input and the substitute.
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

# Fallback values of the preparation (energy-domain skill). Settings use their defaults.
FALLBACK_PREDICTION_HOURS = 48
FALLBACK_INDIVIDUALS = 300
FALLBACK_GENERATIONS = 400
# No price known at all: 0, like a fixed-price provider without time windows.
FALLBACK_PRICE_PER_WH = 0.0
# No feed-in tariff known at all: no feed-in revenue.
FALLBACK_FEED_IN_TARIFF_PER_WH = 0.0
# No load forecast: 3000 kWh per year, spread evenly over the hours of the year.
FALLBACK_LOAD_W = 3000.0 * 1000.0 / 8760.0


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
        The optimization parameters of the run; always valid, never ``None``.
    """
    start_solution = None
    if warm_start is not None and warm_start.start_solution:
        start_solution = warm_start.start_solution
    request = await prepare_request(start_datetime, observation_datetime, start_solution)
    # The solution shows the home appliance the preparation put into the request.
    return await _complete(
        request, start_datetime, overrides, home_appliance_enabled=request.dishwasher is not None
    )


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
    # A client's request is shown with the configured home appliances, as before.
    max_home_appliances = get_config().devices.max_home_appliances
    return await _complete(
        request,
        start_datetime,
        overrides,
        home_appliance_enabled=bool(max_home_appliances and max_home_appliances > 0),
    )


async def _complete(
    request: Genetic0Request,
    start_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]],
    home_appliance_enabled: bool,
) -> Genetic0Parameters:
    settings = _run_settings(overrides)
    return Genetic0Parameters(
        request=request,
        start_hour=start_datetime.hour,
        settings=settings,
        presentation=await _presentation(start_datetime, settings, home_appliance_enabled),
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

    prediction_hours = _prediction_hours()
    horizon_hours = genetic0.horizon_hours
    individuals = genetic0.individuals
    if individuals is None:
        individuals = FALLBACK_INDIVIDUALS
        logger.error("GENETIC0: optimization.genetic0.individuals not set, using {}.", individuals)
    if values["generations"] is None:
        values["generations"] = FALLBACK_GENERATIONS
        logger.error(
            "GENETIC0: optimization.genetic0.generations not set, using {}.", values["generations"]
        )

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
        horizon_hours=horizon_hours,
        individuals=individuals,
        generations=values["generations"],
        seed=values["seed"],
        penalties=dict(genetic0.penalties),
        ev_charge_rates=ev_charge_rates,
        battery_charge_rates=battery_charge_rates,
        self_consumption_interpolator=configured_grid_interpolator_backend(),
        verbose=bool(config.server.verbose),
    )


async def _presentation(
    start_datetime: DateTime, settings: Genetic0RunSettings, home_appliance_enabled: bool
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
        try:
            array = await prediction.key_to_array(
                key=key,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                interval=to_duration("1 hours"),
                fill_method=fill_method,
            )
        except Exception as error:
            logger.error("GENETIC0: Prediction '{}' not readable, not presented: {}", key, error)
            continue
        if array is not None:
            series[key] = tuple(float(value) for value in np.asarray(array, dtype=float))

    return Genetic0Presentation(
        start_datetime=start_datetime,
        timezone=timezone,
        battery_device_id=battery_device_id,
        ev_device_id=EV_DEVICE_ID,
        home_appliance_device_id=HOME_APPLIANCE_DEVICE_ID,
        home_appliance_enabled=home_appliance_enabled,
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
) -> Genetic0Request:
    """Prepare the forecast and device parameters from configuration, predictions and measurements.

    Never fails: a missing or invalid input is replaced by a fallback value and logged at error
    level. The configuration is never changed and no device is invented.

    The parameter arrays start at hour 0 of the start day, not at the start of the run; the
    run starts at index ``start_datetime.hour``.

    Args:
        start_datetime: Start of the optimization run.
        observation_datetime: Moment of the run that measured states of charge refer to.
        start_solution: Start solution of the previous run (warm start), passed in by the
            parameter preparation.

    Returns:
        The forecast and device parameters of the run.
    """
    config = get_config()
    prediction = get_prediction()

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
    parameter_end_datetime = parameter_start_datetime.add(hours=_prediction_hours())
    slots = int(
        (parameter_end_datetime - parameter_start_datetime).total_seconds()
        / interval.total_seconds()
    )

    # Assure predictions are up to date; stored predictions are used if the update fails.
    try:
        await prediction.update_data()
    except Exception as error:
        logger.error("GENETIC0: Prediction update failed, using the stored predictions: {}", error)

    async def read(key: str, fill_method: FillMethod) -> Optional[np.ndarray]:
        try:
            array = await prediction.key_to_array(
                key=key,
                start_datetime=parameter_start_datetime,
                end_datetime=parameter_end_datetime,
                interval=interval,
                fill_method=fill_method,
            )
        except Exception as error:
            logger.error("GENETIC0: Prediction '{}' not available: {}", key, error)
            return None
        if array is None or len(array) != slots:
            logger.error("GENETIC0: Prediction '{}' does not cover the run.", key)
            return None
        return np.asarray(array, dtype=float)

    # Temperature: optional, only presented.
    temperature = await read("weather_temp_air", "ffill")
    weather_temp_air: Optional[list[Optional[float]]] = None
    if temperature is None:
        logger.error("GENETIC0: No temperature forecast; the run has none.")
    else:
        weather_temp_air = [None if math.isnan(value) else value for value in temperature]

    # PV: missing values are 0.
    pv = await read("pvforecast_ac_power", "linear")
    if pv is None:
        logger.error("GENETIC0: No PV forecast; using 0 Wh.")
        pv = np.zeros(slots)
    elif np.isnan(pv).any():
        logger.error("GENETIC0: PV forecast incomplete; using 0 Wh for the missing hours.")
        pv = np.nan_to_num(pv, nan=0.0)
    pvforecast_ac_power = (pv * power_to_energy_per_interval_factor).tolist()

    # Prices: the last known price carried forward.
    price = await read("elecprice_marketprice_wh", "ffill")
    elecprice_marketprice_wh = _carry_forward(
        price, slots, FALLBACK_PRICE_PER_WH, "electricity price"
    ).tolist()

    # Load: the load default.
    load = await read("loadforecast_power_w", "ffill")
    loadforecast_power_w = _carry_forward(load, slots, FALLBACK_LOAD_W, "load forecast").tolist()

    # Feed-in tariff: a covered control horizon is used as it is, otherwise carried forward.
    try:
        tariff_values = await bounded_forecast_array(
            prediction,
            key="feed_in_tariff_wh",
            start_datetime=parameter_start_datetime,
            end_datetime=parameter_end_datetime,
            interval=interval,
        )
    except Exception as error:
        logger.error("GENETIC0: Feed-in tariff not available: {}", error)
        tariff_values = np.full(slots, np.nan)
    first = int(
        (start_datetime - parameter_start_datetime).total_seconds() / interval.total_seconds()
    )
    last = first + config.optimization.genetic0.horizon_hours
    if last > len(tariff_values) or not all(
        math.isfinite(value) for value in tariff_values[first:last]
    ):
        logger.error(
            "GENETIC0: Missing feed-in tariff within the control horizon (provider {}); "
            "carrying the last known tariff forward.",
            config.feedintariff.provider,
        )
        tariff_values = _carry_forward(
            tariff_values, slots, FALLBACK_FEED_IN_TARIFF_PER_WH, "feed-in tariff"
        )
    feed_in_tariff_wh = tariff_values.tolist()

    battery_params, battery_lcos_kwh = await _battery(observation_datetime)
    electric_vehicle_params = await _electric_vehicle(observation_datetime)
    inverter_params = await _inverter()
    home_appliance_params = _home_appliance()

    ems = {
        "pv_forecast_wh": pvforecast_ac_power,
        "electricity_price_per_wh": elecprice_marketprice_wh,
        "feed_in_tariff_per_wh": feed_in_tariff_wh,
        "total_load": loadforecast_power_w,
    }
    try:
        return Genetic0Request(
            ems=Genetic0EnergyManagementParameters(
                **ems, price_per_wh_battery=battery_lcos_kwh / 1000
            ),
            temperature_forecast=weather_temp_air,
            pv_battery=battery_params,
            ev=electric_vehicle_params,
            inverter=inverter_params,
            dishwasher=home_appliance_params,
            start_solution=start_solution,
        )
    except Exception as error:
        logger.error(
            "GENETIC0: Parameters with devices are invalid ({}); optimizing without devices, "
            "temperature forecast and start solution.",
            error,
        )
    try:
        return Genetic0Request(
            ems=Genetic0EnergyManagementParameters(**ems, price_per_wh_battery=0.0),
            temperature_forecast=None,
            pv_battery=None,
            ev=None,
            inverter=None,
            dishwasher=None,
        )
    except Exception as error:
        logger.error(
            "GENETIC0: Forecast parameters are invalid ({}); using 0 Wh PV, price and feed-in "
            "tariff 0 and the fallback load.",
            error,
        )
    zeros = [0.0] * slots
    return Genetic0Request(
        ems=Genetic0EnergyManagementParameters(
            pv_forecast_wh=zeros,
            electricity_price_per_wh=zeros,
            feed_in_tariff_per_wh=zeros,
            total_load=[FALLBACK_LOAD_W * power_to_energy_per_interval_factor] * slots,
            price_per_wh_battery=0.0,
        ),
        temperature_forecast=None,
        pv_battery=None,
        ev=None,
        inverter=None,
        dishwasher=None,
    )


def _prediction_hours() -> int:
    """``prediction.hours``, or its fallback."""
    hours = get_config().prediction.hours
    if hours is None:
        logger.error(
            "GENETIC0: prediction.hours not configured, using {}.", FALLBACK_PREDICTION_HOURS
        )
        return FALLBACK_PREDICTION_HOURS
    return hours


def _carry_forward(
    values: Optional[np.ndarray], slots: int, fallback: float, name: str
) -> np.ndarray:
    """Fill gaps with the last known value, leading gaps with the first known one.

    Args:
        values: The series, or ``None`` if it is missing.
        slots: Length of the series.
        fallback: Value if nothing is known.
        name: Name of the series for the error log.

    Returns:
        A series of ``slots`` finite values.
    """
    if values is None or len(values) != slots:
        values = np.full(slots, np.nan)
    series = pd.Series(np.asarray(values, dtype=float)).replace([np.inf, -np.inf], np.nan)
    if not series.isna().any():
        return series.to_numpy()
    if series.isna().all():
        logger.error("GENETIC0: No {} known; using {}.", name, fallback)
        return np.full(slots, fallback)
    logger.error("GENETIC0: {} incomplete; carrying the last known value forward.", name)
    return series.ffill().bfill().to_numpy()


async def _battery(observation_datetime: DateTime) -> tuple[Optional[Any], float]:
    """Parameters and levelized cost per kWh of the first configured battery, if it can be prepared."""
    config = get_config()
    batteries = config.devices.batteries
    if config.devices.max_batteries == 0 or not batteries:
        logger.info("GENETIC0: No battery configured; optimizing without battery.")
        return None, 0.0
    battery_config = list(batteries.values())[0]
    try:
        # A configuration error gives default values, reported as error.
        (
            battery_parameters,
            _,
        ) = await battery_config.resolve_current_operating_parameters_or_defaults()
        battery_params = battery_config.to_genetic0_pv_bat_param(battery_parameters)
    except Exception as error:
        logger.error(
            "Battery '{}' cannot be prepared for the optimization: {} "
            "The battery is left out of this run.",
            battery_config.device_id,
            error,
        )
        return None, 0.0

    battery_lcos_kwh = battery_config.levelized_cost_of_storage_amt_kwh
    if battery_lcos_kwh is None:
        logger.error(
            "GENETIC0: Battery '{}' has no levelized cost of storage; using 0 per kWh.",
            battery_config.device_id,
        )
        battery_lcos_kwh = 0.0
    battery_params.initial_soc_percentage = await _initial_soc_percentage(
        "battery", battery_config.measurement_key_soc_factor, observation_datetime
    )
    return battery_params, float(battery_lcos_kwh)


async def _electric_vehicle(observation_datetime: DateTime) -> Optional[Any]:
    """Parameters of the first configured electric vehicle, if it can be prepared."""
    config = get_config()
    electric_vehicles = config.devices.electric_vehicles
    if config.devices.max_electric_vehicles == 0 or not electric_vehicles:
        logger.info("GENETIC0: No electric vehicle configured; optimizing without one.")
        return None
    electric_vehicle_config = list(electric_vehicles.values())[0]
    try:
        # A configuration error gives default values, reported as error.
        (
            ev_parameters,
            _,
        ) = await electric_vehicle_config.resolve_current_operating_parameters_or_defaults()
        electric_vehicle_params = electric_vehicle_config.to_genetic0_ev_bat_param(ev_parameters)
    except Exception as error:
        logger.error(
            "Electric vehicle '{}' cannot be prepared for the optimization: {} "
            "The electric vehicle is left out of this run.",
            electric_vehicle_config.device_id,
            error,
        )
        return None
    electric_vehicle_params.initial_soc_percentage = await _initial_soc_percentage(
        "electric vehicle", electric_vehicle_config.measurement_key_soc_factor, observation_datetime
    )
    return electric_vehicle_params


async def _inverter() -> Optional[Any]:
    """Parameters of the first configured inverter, if it can be prepared."""
    config = get_config()
    inverters = config.devices.inverters
    if config.devices.max_inverters == 0 or not inverters:
        logger.info("GENETIC0: No inverter configured; optimizing without inverter.")
        return None
    inverter_config = list(inverters.values())[0]
    try:
        # A configuration error gives default values, reported as error.
        return inverter_config.to_genetic0_param(
            await inverter_config.resolve_current_operating_parameters_or_defaults()
        )
    except Exception as error:
        logger.error(
            "Inverter '{}' cannot be prepared for the optimization: {} "
            "The inverter is left out of this run.",
            inverter_config.device_id,
            error,
        )
        return None


def _home_appliance() -> Optional[Any]:
    """Parameters of the first configured home appliance, if it can be prepared."""
    config = get_config()
    home_appliances = config.devices.home_appliances
    if config.devices.max_home_appliances == 0 or not home_appliances:
        logger.info("GENETIC0: No home appliance configured; optimizing without one.")
        return None
    home_appliance_config = list(home_appliances.values())[0]
    try:
        return home_appliance_config.to_genetic0_param()
    except Exception as error:
        logger.error(
            "Home appliance '{}' cannot be prepared for the optimization: {} "
            "The home appliance is left out of this run.",
            home_appliance_config.device_id,
            error,
        )
        return None


async def _initial_soc_percentage(kind: str, key: str, observation_datetime: DateTime) -> int:
    """Measured state of charge in percent, or 0 if none is known or it is invalid."""
    try:
        initial_soc_factor = await _nearest_soc_factor(key, observation_datetime)
    except Exception as error:
        logger.error(
            "GENETIC0: No {} state of charge (measurement key '{}'): {}; using 0 %.",
            kind,
            key,
            error,
        )
        return 0
    if not math.isfinite(initial_soc_factor) or not 0.0 <= initial_soc_factor <= 1.0:
        logger.error(
            "GENETIC0: Invalid {} state of charge factor {}; using 0 %.", kind, initial_soc_factor
        )
        return 0
    # genetic parameter is 0..100 as int
    return int(initial_soc_factor * 100)
