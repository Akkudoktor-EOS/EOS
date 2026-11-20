"""Configuration-owned GENETIC requests with fresh runtime observations."""

import math
from typing import Annotated, Any, Optional

import pandas as pd
from loguru import logger
from pydantic import AliasChoices, Field, field_validator

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    get_ems,
)
from akkudoktoreos.devices.settings.batteryresolution import BatteryFullCharge
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.fullcharge import (
    completion_counter_deadline,
    enforced_full_charge_deadline,
    full_charge_deadline,
    full_charge_hold_start,
)
from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration

# How far back the latest "run started" report of a home appliance is looked up.
RUN_STARTED_LOOKBACK_HOURS = 48


class RuntimeForecasts(GeneticParametersBaseModel):
    """External series from local midnight, in slot Wh and currency per Wh.

    The interval is configured in optimization.genetic.interval_sec. Omitted
    series are read from the configured providers, preserving their raw coverage.
    """

    pv_forecast_wh: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("pv_forecast_wh", "pv_prognose_wh")
    )
    total_load: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("total_load", "gesamtlast")
    )
    electricity_price_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("electricity_price_per_wh", "strompreis_euro_pro_wh"),
    )
    feed_in_tariff_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
    )
    temperature_forecast: Optional[list[Optional[float]]] = None


class ConfigOptimizationRequest(
    ConfigMixin, MeasurementMixin, PredictionMixin, GeneticParametersBaseModel
):
    """Supply observations while hardware, tariffs and scheduling remain in config.

    Explicit SoC values take precedence over fresh measurements. Without a usable
    measurement, assume 0% initial SoC for the affected device and continue.
    """

    soc: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: RuntimeForecasts = Field(default_factory=RuntimeForecasts)
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def validate_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Parse a previous-plan timestamp while retaining its explicit timezone."""
        return GeneticOptimizationParameters.transform_start_solution_datetime(value)

    async def _run_started(self, device: Any, observation_time: DateTime) -> Optional[DateTime]:
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
            dates, values = await self.measurement.key_to_lists(
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

    async def resolve(self) -> GeneticOptimizationParameters:
        """Resolve inside the EMS lock after its slot start has been established."""
        config = self.config
        settings = config.optimization.genetic
        config.validate_optimization_horizons()
        ems = get_ems()
        start = ems.start_datetime
        observation_time = ems.observation_datetime
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
        unknown = set(self.soc) - {device.device_id for device in storage}
        if unknown:
            raise ValueError(f"SoC supplied for unconfigured devices: {sorted(unknown)}.")

        async def state(device: BatteriesCommonSettings) -> int:
            if device.device_id in self.soc:
                return self.soc[device.device_id]
            try:
                dates, values = await self.measurement.key_to_lists(
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

        async def history(key: str, start_datetime: DateTime) -> tuple[list[DateTime], list[Any]]:
            """Measured values of ``key`` from ``start_datetime`` up to now, empty if unknown."""
            try:
                return await self.measurement.key_to_lists(
                    key=key,
                    start_datetime=start_datetime,
                    end_datetime=observation_time.add(seconds=1),
                    dropna=False,
                )
            except KeyError:
                return [], []

        async def full_charge(
            device: BatteriesCommonSettings, policy: BatteryFullCharge
        ) -> Optional[DateTime]:
            """Deadline of the next periodic full charge, None if disabled or unknown."""
            interval_days = policy.interval_days
            if interval_days is None:
                return None
            dates, values = await history(
                device.measurement_key_soc_factor,
                observation_time.subtract(days=math.ceil(interval_days)),
            )
            if device.full_charge_completion == "measurement":
                # One extra day, so that an increase right at the start of the
                # interval is still seen as an increase.
                counter_dates, counter_values = await history(
                    device.measurement_key_full_charge_completed,
                    observation_time.subtract(days=math.ceil(interval_days) + 1),
                )
                deadline = completion_counter_deadline(
                    zip(counter_dates, counter_values),
                    now=observation_time,
                    interval_days=interval_days,
                )
                if deadline is None:
                    logger.warning(
                        "No readings of the full charge completion counter {} of {}; "
                        "counting the interval from the oldest state of charge sample.",
                        device.measurement_key_full_charge_completed,
                        device.device_id,
                    )
                    # A threshold above 100 % never counts a sample as full.
                    deadline = full_charge_deadline(
                        zip(dates, values),
                        now=observation_time,
                        interval_days=interval_days,
                        detect_percentage=101,
                    )
            else:
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

        async def full_charge_hold(
            device: BatteriesCommonSettings, policy: BatteryFullCharge, soc_percentage: int
        ) -> tuple[Optional[DateTime], Optional[float]]:
            """Hold of a full charge that waits for its completion counter.

            Returns:
                ``(hold_until, remaining_hold_hours)``. ``hold_until`` is None if
                the battery is not being held. ``remaining_hold_hours`` is the
                planning estimate still to go, None without an estimate.
            """
            estimate = device.full_charge_hold_hours
            if soc_percentage < policy.detect_percentage:
                return None, estimate
            max_hold_hours = device.full_charge_max_hold_hours
            # One hour more than the limit, so that a run of full samples longer
            # than the limit is not cut at the window start.
            dates, values = await history(
                device.measurement_key_soc_factor,
                observation_time.subtract(seconds=round((max_hold_hours + 1) * 3600)),
            )
            started = full_charge_hold_start(
                zip(dates, values),
                now=observation_time,
                detect_percentage=policy.detect_percentage,
            )
            if started is None:
                started = observation_time
            held_hours = (observation_time.timestamp() - started.timestamp()) / 3600
            if held_hours >= max_hold_hours:
                logger.error(
                    "Full charge of {} held for {:.1f} h without an increase of {}; "
                    "no longer holding it (full_charge_max_hold_hours = {} h).",
                    device.device_id,
                    held_hours,
                    device.measurement_key_full_charge_completed,
                    max_hold_hours,
                )
                return None, None
            limit = started.add(seconds=round(max_hold_hours * 3600))
            remaining = None
            hold_until = observation_time
            if estimate is not None:
                remaining = max(estimate - held_hours, 0.0)
                hold_until = observation_time.add(seconds=round(remaining * 3600))
            if hold_until.timestamp() > limit.timestamp():
                hold_until = limit
            return hold_until, remaining

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
                    if device.full_charge_completion == "measurement":
                        hold_until, hold_hours = await full_charge_hold(
                            device, policy, battery.initial_soc_percentage
                        )
                        battery.full_charge_hold_hours = hold_hours
                        battery.full_charge_hold_until_datetime = hold_until
                        if hold_until is not None:
                            logger.info(
                                "Holding the full charge of {} until {} at the earliest; "
                                "waiting for {} to increase.",
                                device.device_id,
                                hold_until,
                                device.measurement_key_full_charge_completed,
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
                dates, counts = await self.measurement.key_to_lists(
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
            appliance.run_started_datetime = await self._run_started(device, observation_time)

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
                await self.prediction.update_data()
                updated = True
            values = await bounded_forecast_array(
                self.prediction, key=key, start_datetime=origin, end_datetime=end, interval=interval
            )
            if energy:
                values = values * (settings.interval_sec / 3600)
            return values.tolist()

        supplied = self.forecasts
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
        previous = ems.genetic_solution()
        warm_start = self.start_solution
        warm_start_time = self.start_solution_datetime
        if warm_start is None and previous is not None:
            warm_start = previous.start_solution
            warm_start_time = previous.start_solution_datetime
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
            start_solution=warm_start,
            start_solution_datetime=warm_start_time,
        )
