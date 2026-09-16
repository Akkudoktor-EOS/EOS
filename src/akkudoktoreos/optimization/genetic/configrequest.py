"""Configuration-owned GENETIC requests with fresh runtime observations."""

import math
from typing import Annotated, Any, Optional

from pydantic import AliasChoices, Field, field_validator

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    get_ems,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration


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
    """Supply observations while hardware, tariffs and scheduling remain in config."""

    soc: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: RuntimeForecasts = Field(default_factory=RuntimeForecasts)
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def validate_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Parse a previous-plan timestamp while retaining its explicit timezone."""
        return GeneticOptimizationParameters.transform_start_solution_datetime(value)

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
                samples = [
                    (date, value)
                    for date, value in zip(dates, values)
                    if date.timestamp() <= observation_time.timestamp()
                ]
                date, value = max(samples, key=lambda sample: sample[0].timestamp())
                if (
                    observation_time.timestamp() - date.timestamp()
                    > settings.measurement_max_age_seconds
                    or value is None
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Invalid or stale SoC factor")
                return int(value * 100)
            except (ValueError, KeyError) as exc:
                raise ValueError(
                    f"Fresh SoC missing for {device.device_id}; supply soc or a measurement."
                ) from exc

        battery = None
        if groups["batteries"]:
            device = groups["batteries"][0]
            battery = device.to_genetic_pv_bat_param()
            battery.initial_soc_percentage = await state(device)
        ev = None
        if groups["electric_vehicles"]:
            device = groups["electric_vehicles"][0]
            ev = device.to_genetic_ev_bat_param()
            ev.initial_soc_percentage = await state(device)
        inverter = None
        if groups["inverters"]:
            device = groups["inverters"][0]
            if device.battery_id != (battery.device_id if battery else None):
                raise ValueError("Inverter battery_id must match the configured battery.")
            inverter = device.to_genetic_param()
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
            samples = [
                (date, count)
                for date, count in zip(dates, counts)
                if date.timestamp() <= observation_time.timestamp()
            ]
            if not samples:
                continue
            count = max(samples, key=lambda item: item[0].timestamp())[1]
            if (
                count is None
                or not math.isfinite(count)
                or int(count) != count
                or not 0 <= count <= appliance.num_cycles
            ):
                raise ValueError(f"Invalid completed cycle count for {device.device_id}.")
            appliance.completed_cycles = int(count)

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
            if not all(math.isfinite(value) for value in values[first:last]):
                raise ValueError(f"Missing or invalid {key} within the control horizon.")
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
