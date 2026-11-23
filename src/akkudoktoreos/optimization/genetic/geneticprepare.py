"""Parameter preparation of GENETIC (ADR 0003, 0006).

The only part of GENETIC that reads configuration, predictions and measurements. It completes
the optimization parameters once before each run; the energy management passes the run start,
the observation time, the warm start and run-time overrides as arguments.

Transitional (migration step 3): a configuration or forecast that cannot serve the run still
raises, and the energy management cancels the run; step 3b makes the preparation always valid.
"""

import math
from collections.abc import Mapping
from typing import Any, Optional

import pandas as pd
from loguru import logger

from akkudoktoreos.core.coreabc import get_config, get_measurement, get_prediction
from akkudoktoreos.devices.settings.batteryresolution import BatteryFullCharge
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic.geneticforecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.geneticfullcharge import (
    enforced_full_charge_deadline,
    full_charge_deadline,
)
from akkudoktoreos.optimization.genetic.geneticparameters import (
    GeneticParameters,
    GeneticRunSettings,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
from akkudoktoreos.optimization.genetic.geneticsettings import TerminalValueMode
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimizer import RunClock
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration

# Settings that a run can override.
OVERRIDE_KEYS = ("generations", "individuals", "seed")

# How far back the latest "run started" report of a home appliance is looked up.
RUN_STARTED_LOOKBACK_HOURS = 48


def run_clock() -> RunClock:
    """The configured slot length; runs follow the site's local day.

    Returns:
        The clock of the next GENETIC run.
    """
    config = get_config()
    return RunClock(
        slot_seconds=config.optimization.genetic.interval_sec,
        timezone=config.general.timezone,
    )


async def prepare(
    start_datetime: DateTime,
    observation_datetime: DateTime,
    warm_start: Optional[GeneticSolution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> GeneticParameters:
    """Prepare the parameters of a run from configuration, predictions and measurements.

    Args:
        start_datetime: Start of the run's first slot.
        observation_datetime: Unrounded moment of the run that measurements refer to.
        warm_start: Result of the previous GENETIC run, if any.
        overrides: Run-time overrides of ``generations``, ``individuals`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        RuntimeError: Configured devices or forecasts are incomplete (until step 3b).
    """
    try:
        request = await resolve_request(
            GeneticRequest(), start_datetime, observation_datetime, warm_start
        )
    except Exception as exc:
        # Missing inputs cancel the run without changing providers or inventing devices.
        logger.error(
            "Cannot prepare GENETIC parameters; canceling optimization with provider {}: {}",
            get_config().feedintariff.provider,
            exc,
        )
        raise RuntimeError("Could not prepare optimisation parameters.") from exc
    return _complete(request, start_datetime, observation_datetime, overrides)


async def prepare_from_request(
    request: GeneticRequest,
    start_datetime: DateTime,
    observation_datetime: DateTime,
    warm_start: Optional[GeneticSolution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> GeneticParameters:
    """Complete a client's optimization request into the parameters of a run.

    Args:
        request: Runtime inputs of the client; omitted inputs come from the configured devices
            and providers.
        start_datetime: Start of the run's first slot.
        observation_datetime: Unrounded moment of the run that measurements refer to.
        warm_start: Result of the previous GENETIC run, if any.
        overrides: Run-time overrides of ``generations``, ``individuals`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        ValueError: The request, configured devices or forecasts are incomplete (until step 3b).
    """
    parameters = await resolve_request(request, start_datetime, observation_datetime, warm_start)
    return _complete(parameters, start_datetime, observation_datetime, overrides)


def run_settings(
    start_datetime: DateTime,
    observation_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]] = None,
) -> GeneticRunSettings:
    """Resolve the algorithm settings and run clock of a run from the configuration.

    Args:
        start_datetime: Start of the run's first slot.
        observation_datetime: Unrounded moment of the run that measurements refer to.
        overrides: Run-time overrides of ``generations``, ``individuals`` and ``seed``.

    Returns:
        The settings of the run.

    Raises:
        ValueError: No prediction horizon is configured (until step 3b).
    """
    config = get_config()
    genetic = config.optimization.genetic
    chosen: dict[str, Any] = {
        "generations": genetic.generations,
        "individuals": genetic.individuals,
        "seed": genetic.seed,
    }
    for key, value in (overrides or {}).items():
        if key not in OVERRIDE_KEYS:
            logger.error("GENETIC: Unknown override '{}' ignored.", key)
        elif value is not None:
            chosen[key] = value
    if config.prediction.hours is None:
        raise ValueError("Configure a positive prediction horizon.")
    battery = next(iter((config.devices.batteries or {}).values()), None)
    ev = next(iter((config.devices.electric_vehicles or {}).values()), None)
    return GeneticRunSettings(
        interval_sec=genetic.interval_sec,
        horizon_hours=genetic.horizon_hours,
        tail_horizon_hours=genetic.tail_horizon_hours,
        prediction_hours=config.prediction.hours,
        individuals=chosen["individuals"],
        generations=chosen["generations"],
        seed=chosen["seed"],
        penalties=dict(genetic.penalties),
        workers=genetic.workers,
        pin_workers=genetic.pin_workers,
        fitness_cache_max_entries=genetic.fitness_cache_max_entries,
        terminal_value_mode=TerminalValueMode(genetic.terminal_value_mode),
        terminal_value_window_hours=genetic.terminal_value_window_hours,
        direct_marketing_enabled=bool(config.feedintariff.direct_marketing_enabled),
        battery_charge_rates=_rates(None if battery is None else battery.charge_rates),
        battery_grid_export_rates=_rates(None if battery is None else battery.grid_export_rates),
        ev_charge_rates=_rates(None if ev is None else ev.charge_rates),
        start_datetime=start_datetime,
        observation_datetime=observation_datetime,
        timezone=config.general.timezone,
        verbose=bool(config.server.verbose),
    )


def _rates(values: Optional[list[float]]) -> Optional[tuple[float, ...]]:
    return None if values is None else tuple(values)


def _complete(
    request: GeneticOptimizationParameters,
    start_datetime: DateTime,
    observation_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]],
) -> GeneticParameters:
    return GeneticParameters(
        request=request,
        start_hour=start_datetime.hour,
        settings=run_settings(start_datetime, observation_datetime, overrides),
    )


async def _run_started(device: Any, observation_time: DateTime) -> Optional[DateTime]:
    """Start of the appliance's run in progress from its measurement, or None.

    The latest report at or before the observation time holds the run start
    as UNIX seconds, 0 when the device reported the run as finished. An
    invalid report is logged and treated as no run in progress, so the
    preparation never fails on it.

    Args:
        device: Home appliance settings.
        observation_time: Moment of the optimization run.

    Returns:
        The run start, or None when no run is in progress.
    """
    key = device.run_started_measurement_key or f"{device.device_id}.run_started_at"
    try:
        dates, values = await get_measurement().key_to_lists(
            key=key,
            start_datetime=observation_time.subtract(hours=RUN_STARTED_LOOKBACK_HOURS),
            end_datetime=observation_time.add(seconds=1),
            dropna=False,
        )
    except KeyError:
        return None
    # Skip records without a value (e.g. an adapter's meter reading, #1447).
    samples = [
        (date, value)
        for date, value in zip(dates, values)
        if date.timestamp() <= observation_time.timestamp()
        and value is not None
        and value is not pd.NA
    ]
    if not samples:
        return None
    value = max(samples, key=lambda sample: sample[0].timestamp())[1]
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = math.nan
    if seconds == 0:
        return None
    if not math.isfinite(seconds) or seconds < 0 or seconds > observation_time.timestamp():
        logger.error(
            "Invalid run start {!r} in '{}' for home appliance '{}'; planned as not running.",
            value,
            key,
            device.device_id,
        )
        return None
    started = to_datetime(seconds)
    timezone = observation_time.timezone
    return started.in_timezone(timezone) if timezone is not None else started


async def resolve_request(
    request: GeneticRequest,
    start_datetime: DateTime,
    observation_datetime: DateTime,
    warm_start: Optional[GeneticSolution],
) -> GeneticOptimizationParameters:
    """Complete a request with configured devices, measurements and forecasts.

    Args:
        request: Runtime inputs of a client; omitted inputs come from the configured devices,
            measurements and prediction providers.
        start_datetime: Start of the run's first slot.
        observation_datetime: Unrounded moment of the run that measurements refer to.
        warm_start: Result of the previous GENETIC run of this server, if any. Its genome
            seeds the run when the request carries none; a request that echoes it without
            a start datetime gets the start datetime of that run.

    Returns:
        The forecast and device parameters of the run.

    Raises:
        ValueError: Configured devices or forecasts are incomplete (until step 3b).
    """
    config = get_config()
    settings = config.optimization.genetic
    config.validate_optimization_horizons()
    start = start_datetime
    observation_time = observation_datetime
    measurement = get_measurement()
    prediction = get_prediction()
    devices = config.devices
    groups: dict[str, list[Any]] = {}
    for name in ("batteries", "electric_vehicles", "inverters", "home_appliances"):
        entries = list((getattr(devices, name) or {}).values())
        maximum = getattr(devices, "max_" + name)
        if maximum is not None and len(entries) > maximum:
            raise ValueError(f"devices.{name} exceeds configured maximum {maximum}.")
        if name != "home_appliances" and len(entries) > 1:
            raise ValueError(f"GENETIC supports at most one device in devices.{name}.")
        groups[name] = entries
    ids = [device.device_id for entries in groups.values() for device in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Configured device IDs must be unique across device groups.")
    storage = groups["batteries"] + groups["electric_vehicles"]
    unknown = set(request.soc) - {device.device_id for device in storage}
    if unknown:
        raise ValueError(f"SoC supplied for unconfigured devices: {sorted(unknown)}.")

    async def state(device: BatteriesCommonSettings) -> int:
        if device.device_id in request.soc:
            return request.soc[device.device_id]
        try:
            dates, values = await measurement.key_to_lists(
                key=device.measurement_key_soc_factor,
                start_datetime=observation_time.subtract(
                    seconds=settings.measurement_max_age_seconds
                ),
                end_datetime=observation_time.add(seconds=1),
                dropna=False,
            )
            # Records without a SoC value (e.g. a meter reading stored by an
            # adapter, or pandas NA) must not hide an older SoC within the age
            # limit (#1447). An explicit NaN stays an invalid sample.
            samples = [
                (date, value)
                for date, value in zip(dates, values)
                if date.timestamp() <= observation_time.timestamp()
                and value is not None
                and value is not pd.NA
            ]
            date, value = max(samples, key=lambda sample: sample[0].timestamp())
            if (
                observation_time.timestamp() - date.timestamp()
                > settings.measurement_max_age_seconds
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Invalid or stale SoC factor")
            return int(value * 100)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "No usable fresh SoC for {}; assuming 0% initial SoC for this "
                "optimization (not a measurement): {}",
                device.device_id,
                exc,
            )
            return 0

    async def full_charge(
        device: BatteriesCommonSettings, policy: BatteryFullCharge
    ) -> Optional[DateTime]:
        """Deadline of the next periodic full charge, None if disabled or unknown."""
        interval_days = policy.interval_days
        if interval_days is None:
            return None
        try:
            dates, values = await measurement.key_to_lists(
                key=device.measurement_key_soc_factor,
                start_datetime=observation_time.subtract(days=math.ceil(interval_days)),
                end_datetime=observation_time.add(seconds=1),
                dropna=False,
            )
        except KeyError:
            dates, values = [], []
        deadline = full_charge_deadline(
            zip(dates, values),
            now=observation_time,
            interval_days=interval_days,
            detect_percentage=policy.detect_percentage,
        )
        if deadline is None:
            logger.warning(
                "No state of charge history for {}; cannot schedule its periodic full charge.",
                device.device_id,
            )
        return deadline

    # A battery configuration error does not cancel the run: the battery gets
    # default values for what cannot be resolved, and an error is logged.
    battery = None
    if groups["batteries"]:
        device = groups["batteries"][0]
        (
            operating_parameters,
            use_profile_tables,
        ) = await device.resolve_current_operating_parameters_or_defaults(observation_time)
        battery = device.to_genetic_pv_bat_param(operating_parameters, use_profile_tables)
        battery.initial_soc_percentage = await state(device)
        policy = device.resolve_full_charge()
        deadline = await full_charge(device, policy)
        horizon_end = start.add(seconds=settings.horizon * settings.interval_sec)
        if deadline is not None and deadline.timestamp() <= horizon_end.timestamp():
            # Due within the horizon: lift the upper limit so the battery may
            # reach the target, and enforce the target only if grid charging
            # is allowed for it. The target is within the technical SOC bounds.
            target = policy.soc_percentage
            battery.max_soc_percentage = max(battery.max_soc_percentage, target)
            if device.full_charge_allow_grid:
                enforced = enforced_full_charge_deadline(deadline, observation_time)
                battery.full_charge_soc_percentage = target
                battery.full_charge_deadline_datetime = enforced
                logger.info(
                    "Periodic full charge of {} to {}% due by {}; enforced by {}.",
                    device.device_id,
                    target,
                    deadline,
                    enforced,
                )
            else:
                logger.info(
                    "Periodic full charge of {} to {}% due by {}; PV surplus only.",
                    device.device_id,
                    target,
                    deadline,
                )
    ev = None
    if groups["electric_vehicles"]:
        device = groups["electric_vehicles"][0]
        (
            operating_parameters,
            use_profile_tables,
        ) = await device.resolve_current_operating_parameters_or_defaults(observation_time)
        ev = device.to_genetic_ev_bat_param(operating_parameters, use_profile_tables)
        ev.initial_soc_percentage = await state(device)
    inverter = None
    if groups["inverters"]:
        device = groups["inverters"][0]
        if device.battery_id != (battery.device_id if battery else None):
            raise ValueError("Inverter battery_id must match the configured battery.")
        # The derating of the inverter profile applies at the measured
        # ambient temperature, 45 degC without a measurement.
        inverter = device.to_genetic_param(
            await device.resolve_current_operating_parameters_or_defaults(observation_time)
        )
    if inverter is None:
        raise ValueError("Configure an inverter to model PV and grid energy flows.")
    appliances = [device.to_genetic_param() for device in groups["home_appliances"]]
    for device, appliance in zip(groups["home_appliances"], appliances):
        key = device.cycles_completed_measurement_key or f"{device.device_id}.cycles_completed"
        try:
            dates, counts = await measurement.key_to_lists(
                key=key,
                start_datetime=observation_time.start_of("day"),
                end_datetime=observation_time.add(seconds=1),
                dropna=False,
            )
        except KeyError:
            continue
        # Skip records without a count (e.g. an adapter's meter reading, #1447).
        samples = [
            (date, count)
            for date, count in zip(dates, counts)
            if date.timestamp() <= observation_time.timestamp()
            and count is not None
            and count is not pd.NA
        ]
        if not samples:
            continue
        count = max(samples, key=lambda item: item[0].timestamp())[1]
        if (
            not math.isfinite(count)
            or int(count) != count
            or not 0 <= count <= appliance.num_cycles
        ):
            raise ValueError(f"Invalid completed cycle count for {device.device_id}.")
        appliance.completed_cycles = int(count)
    for device, appliance in zip(groups["home_appliances"], appliances):
        appliance.run_started_datetime = await _run_started(device, observation_time)

    origin = start.start_of("day")
    if config.prediction.hours is None or config.prediction.hours <= 0:
        raise ValueError("Configure a positive prediction horizon.")
    end = start.add(hours=config.prediction.hours)
    interval = to_duration(settings.interval_sec)
    updated = False

    async def forecast(
        supplied: Optional[list[float]], key: str, energy: bool = False
    ) -> list[float]:
        nonlocal updated
        if supplied is not None:
            return list(supplied)
        if not updated:
            await prediction.update_data()
            updated = True
        values = await bounded_forecast_array(
            prediction, key=key, start_datetime=origin, end_datetime=end, interval=interval
        )
        if energy:
            values = values * (settings.interval_sec / 3600)
        return values.tolist()

    supplied = request.forecasts
    pv = await forecast(supplied.pv_forecast_wh, "pvforecast_ac_power", energy=True)
    load = await forecast(supplied.total_load, "loadforecast_power_w", energy=True)
    prices = await forecast(supplied.electricity_price_per_wh, "elecprice_marketprice_wh")
    tariffs = await forecast(supplied.feed_in_tariff_per_wh, "feed_in_tariff_wh")
    length = min(len(pv), len(load), len(prices), len(tariffs))
    if not length:
        raise ValueError("Forecast series must be nonempty.")
    pv, load, prices, tariffs = (values[:length] for values in (pv, load, prices, tariffs))
    first = int((start - origin).total_seconds() / settings.interval_sec)
    last = first + settings.horizon
    if settings.horizon <= 0 or len(pv) < last:
        raise ValueError("Forecast does not cover the positive GENETIC control horizon.")
    for key, values in (
        ("PV", pv),
        ("load", load),
        ("prices", prices),
        ("feed-in tariff", tariffs),
    ):
        missing = [index for index in range(first, last) if not math.isfinite(values[index])]
        if missing:
            left = origin.add(seconds=missing[0] * settings.interval_sec)
            right = left.add(seconds=settings.interval_sec)
            raise ValueError(
                f"Missing or invalid {key} within the control horizon. "
                f"First invalid interval: [{left.isoformat()}, {right.isoformat()}); "
                f"{len(missing)} invalid interval(s)."
            )
    if any(value < 0 for values in (pv, load) for value in values[first:last]):
        raise ValueError("PV and load energy must be nonnegative.")
    start_solution = request.start_solution
    start_solution_time = request.start_solution_datetime
    if warm_start is not None:
        if start_solution is None:
            start_solution = warm_start.start_solution
            start_solution_time = warm_start.start_solution_datetime
        elif (
            start_solution_time is None
            and warm_start.start_solution is not None
            and list(warm_start.start_solution) == list(start_solution)
        ):
            # A client that only echoes the genome of this server's last solution.
            start_solution_time = warm_start.start_solution_datetime
    return GeneticOptimizationParameters(
        forecast_interval_seconds=settings.interval_sec,
        ems=GeneticEnergyManagementParameters(
            pv_forecast_wh=pv,
            total_load=load,
            electricity_price_per_wh=prices,
            feed_in_tariff_per_wh=tariffs,
            price_per_wh_battery=settings.terminal_value_euro_per_kwh / 1000,
        ),
        pv_battery=battery,
        ev=ev,
        inverter=inverter,
        home_appliances=appliances,
        temperature_forecast=(
            (supplied.temperature_forecast[:length] + [None] * length)[:length]
            if supplied.temperature_forecast is not None
            else None
        ),
        start_solution=start_solution,
        start_solution_datetime=start_solution_time,
    )
