"""Parameter preparation of GENETIC (ADR 0003, 0006).

The only part of GENETIC that reads configuration, predictions and measurements. It completes
the optimization parameters once before each run; the energy management passes the run start,
the observation time, the warm start and run-time overrides as arguments.

The preparation always creates a valid parameter set (energy-domain skill, "Optimization
parameters are always valid"): it never fails, never cancels the run, never writes the
configuration and never invents devices. A missing or invalid input is replaced by a fallback
value, logged at error level with the input and the substitute.
"""

import math
from collections.abc import Mapping
from typing import Any, NamedTuple, Optional

import pandas as pd
from loguru import logger

from akkudoktoreos.core.coreabc import get_config, get_measurement, get_prediction
from akkudoktoreos.devices.settings.batteryresolution import (
    BatteryFullCharge,
    BatteryOperatingParameters,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.homeappliancesettings import (
    HomeApplianceCommonSettings,
)
from akkudoktoreos.devices.settings.inverterresolution import (
    InverterOperatingParameters,
)
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.optimization.genetic.geneticforecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.geneticfullcharge import (
    enforced_full_charge_deadline,
    full_charge_deadline,
)
from akkudoktoreos.optimization.genetic.geneticparameters import (
    GeneticParameters,
    GeneticRunSettings,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticBatteryRequest,
    GeneticElectricVehicleRequest,
    GeneticEnergyManagementRequest,
    GeneticHomeApplianceRequest,
    GeneticInverterRequest,
    GeneticOptimizationParameters,
    GeneticRequest,
)
from akkudoktoreos.optimization.genetic.geneticsettings import TerminalValueMode
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimizer import RunClock
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration
from akkudoktoreos.utils.gridinterpolator import GridInterpolatorBackend

# Settings that a run can override.
OVERRIDE_KEYS = ("generations", "individuals", "seed")

# How far back the latest "run started" report of a home appliance is looked up.
RUN_STARTED_LOOKBACK_HOURS = 48

# Fallback values of the preparation (energy-domain skill). Settings use their defaults.
FALLBACK_PREDICTION_HOURS = 48
FALLBACK_HORIZON_HOURS = 24
FALLBACK_INDIVIDUALS = 300
FALLBACK_GENERATIONS = 400
# No load forecast: 3000 kWh per year, spread evenly over the hours of the year.
FALLBACK_LOAD_W = 3000.0 * 1000.0 / 8760.0


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
        The optimization parameters of the run; always valid, never ``None``.
    """
    request = await resolve_request(
        GeneticRequest(), start_datetime, observation_datetime, warm_start
    )
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
        The optimization parameters of the run; always valid. Inputs of the request that cannot
        serve the run are replaced like those of the configuration.
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
    if chosen["generations"] is None:
        chosen["generations"] = FALLBACK_GENERATIONS
        logger.error(
            "GENETIC: optimization.genetic.generations not set, using {}.", chosen["generations"]
        )
    if chosen["individuals"] is None:
        chosen["individuals"] = FALLBACK_INDIVIDUALS
        logger.error(
            "GENETIC: optimization.genetic.individuals not set, using {}.", chosen["individuals"]
        )
    battery = next(iter((config.devices.batteries or {}).values()), None)
    ev = next(iter((config.devices.electric_vehicles or {}).values()), None)
    return GeneticRunSettings(
        interval_sec=genetic.interval_sec,
        horizon_hours=_horizon_hours(),
        tail_horizon_hours=genetic.tail_horizon_hours,
        prediction_hours=_prediction_hours(),
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
        self_consumption_interpolator=GridInterpolatorBackend(
            config.optimization.self_consumption_interpolator
        ),
        verbose=bool(config.server.verbose),
    )


def _horizon_hours() -> int:
    """The control horizon in hours; positive.

    Returns:
        ``optimization.genetic.horizon_hours``, its default if not positive.
    """
    hours = get_config().optimization.genetic.horizon_hours
    if hours <= 0:
        logger.error(
            "GENETIC: optimization.genetic.horizon_hours {} is not positive, using {}.",
            hours,
            FALLBACK_HORIZON_HOURS,
        )
        hours = FALLBACK_HORIZON_HOURS
    return hours


def _prediction_hours() -> int:
    """The forecast budget in hours; at least the control horizon.

    Returns:
        ``prediction.hours``, its default if unset, the control horizon if shorter.
    """
    config = get_config()
    hours = config.prediction.hours
    if hours is None:
        hours = FALLBACK_PREDICTION_HOURS
        logger.error("GENETIC: prediction.hours not set, using {}.", hours)
    horizon_hours = _horizon_hours()
    if hours < horizon_hours:
        logger.error(
            "GENETIC: prediction.hours {} is shorter than the control horizon, using {}.",
            hours,
            horizon_hours,
        )
        hours = horizon_hours
    return hours


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
    except Exception as error:
        logger.error(
            "GENETIC: Run start '{}' of home appliance '{}' not readable; planned as not "
            "running: {}",
            key,
            device.device_id,
            error,
        )
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
        The forecast and device parameters of the run; always valid.
    """
    config = get_config()
    settings = config.optimization.genetic
    config.validate_optimization_horizons()
    start = start_datetime
    observation_time = observation_datetime
    measurement = get_measurement()
    prediction = get_prediction()
    horizon_slots = _horizon_hours() * 3600 // settings.interval_sec
    devices = config.devices
    groups = _device_groups(devices)
    storage = groups["batteries"] + groups["electric_vehicles"]
    supplied_soc = dict(request.soc)
    for device_id in sorted(set(supplied_soc) - {device.device_id for device in storage}):
        logger.error(
            "GENETIC: SoC {}% supplied for unconfigured device '{}', ignored.",
            supplied_soc.pop(device_id),
            device_id,
        )

    async def state(device: BatteriesCommonSettings) -> int:
        if device.device_id in supplied_soc:
            return supplied_soc[device.device_id]
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
        except Exception as exc:
            logger.error(
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
        try:
            deadline = full_charge_deadline(
                zip(dates, values),
                now=observation_time,
                interval_days=interval_days,
                detect_percentage=policy.detect_percentage,
            )
        except Exception as error:
            logger.error(
                "GENETIC: State of charge history of {} not usable; no periodic full charge "
                "scheduled: {}",
                device.device_id,
                error,
            )
            return None
        if deadline is None:
            logger.error(
                "No state of charge history for {}; cannot schedule its periodic full charge.",
                device.device_id,
            )
        return deadline

    # A battery configuration error does not cancel the run: the battery gets
    # default values for what cannot be resolved, and an error is logged. A battery
    # whose parameters cannot be derived at all is left out.
    battery = None
    if groups["batteries"]:
        device = groups["batteries"][0]
        try:
            (
                operating_parameters,
                use_profile_tables,
            ) = await device.resolve_current_operating_parameters_or_defaults(observation_time)
            battery = battery_request(device, operating_parameters, use_profile_tables)
        except Exception as error:
            logger.error(
                "GENETIC: Battery '{}' cannot be resolved, left out: {}", device.device_id, error
            )
    if battery is not None:
        device = groups["batteries"][0]
        battery.initial_soc_percentage = await state(device)
        try:
            policy = device.resolve_full_charge()
            deadline = await full_charge(device, policy)
        except Exception as error:
            logger.error(
                "GENETIC: Periodic full charge of {} not resolvable; none scheduled: {}",
                device.device_id,
                error,
            )
            deadline = None
        horizon_end = start.add(seconds=horizon_slots * settings.interval_sec)
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
        try:
            (
                operating_parameters,
                use_profile_tables,
            ) = await device.resolve_current_operating_parameters_or_defaults(observation_time)
            ev = electric_vehicle_request(device, operating_parameters, use_profile_tables)
        except Exception as error:
            logger.error(
                "GENETIC: Electric vehicle '{}' cannot be resolved, left out: {}",
                device.device_id,
                error,
            )
        if ev is not None:
            ev.initial_soc_percentage = await state(device)
    inverter = None
    if groups["inverters"]:
        device = groups["inverters"][0]
        try:
            # The derating of the inverter profile applies at the measured
            # ambient temperature, 45 degC without a measurement.
            inverter = inverter_request(
                device,
                await device.resolve_current_operating_parameters_or_defaults(observation_time),
            )
        except Exception as error:
            logger.error(
                "GENETIC: Inverter '{}' cannot be resolved, left out: {}", device.device_id, error
            )
    if inverter is None:
        logger.error(
            "GENETIC: No usable inverter; planning grid energy only, without PV and battery."
        )
    if battery is not None and (inverter is None or inverter.battery_id != battery.device_id):
        # The battery is only reachable through its inverter.
        logger.error(
            "GENETIC: Battery '{}' is not connected to the inverter (battery_id {}), left out.",
            battery.device_id,
            None if inverter is None else inverter.battery_id,
        )
        battery = None
    elif battery is None and inverter is not None and inverter.battery_id is not None:
        logger.error(
            "GENETIC: Battery '{}' of inverter '{}' is not available; inverter without battery.",
            inverter.battery_id,
            inverter.device_id,
        )
    resolved: list[tuple[Any, Any]] = []
    for device in groups["home_appliances"]:
        try:
            resolved.append((device, home_appliance_request(device)))
        except Exception as error:
            logger.error(
                "GENETIC: Home appliance '{}' cannot be resolved, left out: {}",
                device.device_id,
                error,
            )
    groups["home_appliances"] = [device for device, _ in resolved]
    appliances = [appliance for _, appliance in resolved]
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
        except Exception as error:
            logger.error(
                "GENETIC: Completed cycles '{}' of home appliance '{}' not readable, ignored: {}",
                key,
                device.device_id,
                error,
            )
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
        reported = max(samples, key=lambda item: item[0].timestamp())[1]
        try:
            count = float(reported)
        except (TypeError, ValueError):
            count = math.nan
        if (
            not math.isfinite(count)
            or int(count) != count
            or not 0 <= count <= appliance.num_cycles
        ):
            logger.error(
                "GENETIC: Invalid completed cycle count {!r} in '{}' for home appliance '{}', "
                "ignored.",
                reported,
                key,
                device.device_id,
            )
            continue
        appliance.completed_cycles = int(count)
    for device, appliance in zip(groups["home_appliances"], appliances):
        appliance.run_started_datetime = await _run_started(device, observation_time)

    origin = start.start_of("day")
    end = start.add(hours=_prediction_hours())
    interval = to_duration(settings.interval_sec)
    updated = False

    async def forecast(
        supplied: Optional[list[float]], key: str, energy: bool = False
    ) -> list[float]:
        nonlocal updated
        if supplied is not None:
            return list(supplied)
        if not updated:
            updated = True
            try:
                await prediction.update_data()
            except Exception as error:
                logger.error("GENETIC: Prediction update failed, using stored data: {}", error)
        try:
            values = await bounded_forecast_array(
                prediction, key=key, start_datetime=origin, end_datetime=end, interval=interval
            )
        except Exception as error:
            logger.error(
                "GENETIC: Prediction '{}' not readable, treated as missing: {}", key, error
            )
            return []
        if energy:
            values = values * (settings.interval_sec / 3600)
        return values.tolist()

    supplied = request.forecasts
    pv = await forecast(supplied.pv_forecast_wh, "pvforecast_ac_power", energy=True)
    load = await forecast(supplied.total_load, "loadforecast_power_w", energy=True)
    prices = await forecast(supplied.electricity_price_per_wh, "elecprice_marketprice_wh")
    tariffs = await forecast(supplied.feed_in_tariff_per_wh, "feed_in_tariff_wh")
    first = int((start - origin).total_seconds() / settings.interval_sec)
    last = first + horizon_slots
    # The series share the shortest length, at least the end of the control horizon.
    length = max(min(len(pv), len(load), len(prices), len(tariffs)), last)
    span = _Span(origin, settings.interval_sec, first, last)
    pv = _fill(span, "PV forecast", pv[:length], length, constant=0.0)
    load = _fill(
        span,
        "load forecast",
        load[:length],
        length,
        constant=FALLBACK_LOAD_W * settings.interval_sec / 3600,
    )
    prices = _fill(span, "electricity price", prices[:length], length)
    tariffs = _fill(span, "feed-in tariff", tariffs[:length], length)
    start_solution = _genome(request.start_solution, "start_solution of the request")
    start_solution_time = request.start_solution_datetime
    if warm_start is not None:
        if start_solution is None:
            start_solution = _genome(warm_start.start_solution, "start_solution of the last run")
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
        ems=GeneticEnergyManagementRequest(
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


def _device_groups(devices: Any) -> dict[str, list[Any]]:
    """The configured devices of each group that GENETIC simulates.

    GENETIC simulates at most one battery, one electric vehicle and one inverter, and at most
    ``devices.max_<group>`` devices of a group. Device IDs must be unique across the groups.
    The devices beyond that are left out, each logged at error level.

    Args:
        devices: The device configuration (``config.devices``).

    Returns:
        The devices by group name.
    """
    groups: dict[str, list[Any]] = {}
    seen: set[str] = set()
    for name in ("batteries", "electric_vehicles", "inverters", "home_appliances"):
        entries = list((getattr(devices, name) or {}).values())
        maximum = getattr(devices, "max_" + name)
        if name != "home_appliances":
            maximum = 1 if maximum is None else min(maximum, 1)
        kept: list[Any] = []
        for device in entries:
            if device.device_id in seen:
                logger.error(
                    "GENETIC: Device ID '{}' in devices.{} is not unique, device left out.",
                    device.device_id,
                    name,
                )
            elif maximum is not None and len(kept) >= maximum:
                logger.error(
                    "GENETIC: devices.{} holds more than {} device(s), '{}' left out.",
                    name,
                    maximum,
                    device.device_id,
                )
            else:
                kept.append(device)
                seen.add(device.device_id)
        groups[name] = kept
    return groups


class _Span(NamedTuple):
    """The slots of the forecast series and the control horizon within them."""

    origin: DateTime
    interval_sec: int
    first: int
    last: int


def _fill(
    span: _Span,
    name: str,
    values: list[float],
    length: int,
    constant: Optional[float] = None,
) -> list[float]:
    """Replace missing or invalid values within the control horizon.

    A value is missing if it is NaN or infinite, or the series ends before the control horizon
    does; a negative energy (``constant`` given) is invalid. Values outside the control horizon
    stay as they are: the run shortens a tail that has none.

    Args:
        span: The slots of the series and the control horizon.
        name: The input, for the log.
        values: The series from the start of the day.
        length: Length of the result; the series is extended with NaN to it.
        constant: Substitute of an energy series. Without it, a missing value is the last known
            value before it, else the next known one, else 0.

    Returns:
        The series, valid within the control horizon.
    """
    filled = list(values) + [math.nan] * (length - len(values))
    replaced = []
    for index in range(span.first, span.last):
        value = filled[index]
        if math.isfinite(value) and (constant is None or value >= 0):
            continue
        if constant is not None:
            substitute = 0.0 if math.isfinite(value) else constant
        else:
            before = [v for v in filled[:index] if math.isfinite(v)]
            after = [v for v in filled[index + 1 :] if math.isfinite(v)]
            substitute = before[-1] if before else (after[0] if after else 0.0)
        filled[index] = substitute
        replaced.append((index, value, substitute))
    if replaced:
        index, value, substitute = replaced[0]
        left = span.origin.add(seconds=index * span.interval_sec)
        logger.error(
            "GENETIC: {} missing or invalid in {} slot(s) of the control horizon, first at {} "
            "({}), replaced by {}.",
            name,
            len(replaced),
            left.isoformat(),
            value,
            substitute,
        )
    return filled


def _genome(genome: Optional[list[float]], name: str) -> Optional[list[float]]:
    """A warm start genome, or ``None`` if it has fewer than the two genes a genome needs.

    Args:
        genome: The genome.
        name: The input, for the log.

    Returns:
        The genome, or ``None``.
    """
    if genome is not None and len(genome) < 2:
        logger.error("GENETIC: {} {} has fewer than two genes, no warm start genome.", name, genome)
        return None
    return genome


def battery_request(
    settings: BatteriesCommonSettings,
    operating_parameters: Optional[BatteryOperatingParameters] = None,
    use_profile_tables: bool = True,
) -> GeneticBatteryRequest:
    """GENETIC parameters of a stationary battery from its settings.

    Args:
        settings: The battery settings.
        operating_parameters: Resolved snapshot to convert, e.g. from
            ``resolve_current_operating_parameters``. None resolves at the
            reference operating point.
        use_profile_tables: Whether to add the SoC dependent power limits
            and efficiency curves of the profile. False for a snapshot
            resolved without profile (see
            ``resolve_operating_parameters_or_defaults``); requires
            ``operating_parameters``.

    Returns:
        The battery request.

    Raises:
        BatteryResolutionError: See ``resolve_operating_parameters``.
    """
    p, profile = settings.snapshot_with_profile(operating_parameters, use_profile_tables)
    return GeneticBatteryRequest(
        device_id=settings.device_id,
        capacity_wh=p.capacity_wh,
        charging_efficiency=p.charging_efficiency,
        discharging_efficiency=p.discharging_efficiency,
        min_soc_percentage=p.min_soc_percentage,
        max_soc_percentage=p.max_soc_percentage,
        charge_rates=settings.charge_rates,
        grid_export_rates=settings.grid_export_rates,
        **settings.power_and_efficiency_fields(p, profile),
        levelized_cost_of_storage_kwh=settings.levelized_cost_of_storage_amt_kwh,
    )


def electric_vehicle_request(
    settings: BatteriesCommonSettings,
    operating_parameters: Optional[BatteryOperatingParameters] = None,
    use_profile_tables: bool = True,
) -> GeneticElectricVehicleRequest:
    """GENETIC parameters of an electric vehicle from its settings.

    Args:
        settings: The electric vehicle's battery settings.
        operating_parameters: Resolved snapshot to convert, e.g. from
            ``resolve_current_operating_parameters``. None resolves at the
            reference operating point.
        use_profile_tables: Whether to add the SoC dependent power limits
            and efficiency curves of the profile. False for a snapshot
            resolved without profile (see
            ``resolve_operating_parameters_or_defaults``); requires
            ``operating_parameters``.

    Returns:
        The electric vehicle request.

    Raises:
        BatteryResolutionError: See ``resolve_operating_parameters``.
    """
    p, profile = settings.snapshot_with_profile(operating_parameters, use_profile_tables)
    return GeneticElectricVehicleRequest(
        device_id=settings.device_id,
        capacity_wh=p.capacity_wh,
        charging_efficiency=p.charging_efficiency,
        discharging_efficiency=p.discharging_efficiency,
        charge_rates=settings.charge_rates,
        min_soc_percentage=p.min_soc_percentage,
        max_soc_percentage=p.max_soc_percentage,
        min_soc_deadline_datetime=settings.min_soc_deadline_datetime,
        min_soc_max_duration_h=settings.min_soc_max_duration_h,
        **settings.power_and_efficiency_fields(p, profile),
    )


def inverter_request(
    settings: InverterCommonSettings,
    operating_parameters: Optional[InverterOperatingParameters] = None,
) -> GeneticInverterRequest:
    """GENETIC parameters of an inverter from its settings.

    Args:
        settings: The inverter settings.
        operating_parameters: Resolved snapshot to convert, e.g. from
            ``resolve_current_operating_parameters``. None resolves at the
            reference operating point.

    Returns:
        The inverter request.

    Raises:
        InverterResolutionError: If the snapshot is resolved here and the
            inverter cannot be resolved.
    """
    p = operating_parameters or settings.resolve_operating_parameters()
    return GeneticInverterRequest(
        device_id=settings.device_id,
        max_power_wh=p.max_power_w,
        battery_id=settings.battery_id,
        ac_to_dc_efficiency=p.ac_to_dc_efficiency,
        dc_to_ac_efficiency=p.dc_to_ac_efficiency,
        dc_to_ac_efficiency_curve=list(p.dc_to_ac_efficiency_curve),
        ac_to_dc_efficiency_curve=list(p.ac_to_dc_efficiency_curve),
        dc_to_ac_efficiency_reference_load_fraction=(
            settings.dc_to_ac_efficiency_reference_load_fraction
        ),
        max_ac_charge_power_w=p.max_ac_charge_power_w,
        ac_charge_limits_total_charge=p.ac_charge_limits_total_charge,
    )


def home_appliance_request(settings: HomeApplianceCommonSettings) -> GeneticHomeApplianceRequest:
    """GENETIC parameters of a home appliance from its settings.

    Args:
        settings: The home appliance settings.

    Returns:
        The home appliance request.
    """
    return GeneticHomeApplianceRequest(
        device_id=settings.device_id,
        consumption_wh=settings.consumption_wh,
        duration_h=settings.duration_h,
        num_cycles=settings.effective_num_cycles,
        min_cycle_gap_h=settings.min_cycle_gap_h,
        time_windows=settings.cycle_time_windows,
        shared_time_windows=settings.time_windows,
        load_profile_power_w=settings.load_profile_power_w,
        load_profile_interval_seconds=settings.load_profile_interval_seconds,
        schedule_mode=settings.schedule_mode,
        earliest_start_datetime=settings.earliest_start_datetime,
        deadline_datetime=settings.deadline_datetime,
        deadline_policy=settings.deadline_policy,
    )
