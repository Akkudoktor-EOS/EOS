"""Resolve fleet inputs from EOS configuration, measurements and forecasts."""

import math
from typing import Annotated, Any

import pandas as pd
from pydantic import Field

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    get_ems,
)
from akkudoktoreos.optimization.fleet import FleetDevice, FleetPowerGroup, FleetRequest
from akkudoktoreos.optimization.fleetsettings import FleetDevicePolicy
from akkudoktoreos.optimization.genetic.configrequest import RuntimeForecasts
from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.utils.datetimeutil import to_duration


class FleetConfigRequest(
    ConfigMixin, MeasurementMixin, PredictionMixin, GeneticParametersBaseModel
):
    """Runtime inputs only; forecasts use the existing midnight Wh contract.

    Availability is run-relative. Missing fresh SoC is an error rather than an
    invented state. Fractional percentages are retained.
    """

    soc: dict[str, Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]] = Field(
        default_factory=dict
    )
    forecasts: RuntimeForecasts = Field(default_factory=RuntimeForecasts)
    available: dict[str, list[bool]] = Field(default_factory=dict)

    async def resolve(self) -> FleetRequest:
        """Resolve under the EMS lock after its start time has been aligned."""
        config = self.config
        settings = config.optimization.fleet
        ems = get_ems()
        start = ems.start_datetime
        observation = ems.observation_datetime
        seconds = settings.interval_sec
        count = settings.horizon_hours * 3600 // seconds
        if count > 384:
            raise ValueError("FLEET supports at most 384 slots")
        if settings.import_limit_w is None or settings.export_limit_w is None:
            raise ValueError("Configure optimization.fleet import_limit_w and export_limit_w")
        groups: dict[str, list[Any]] = {}
        for name in ("batteries", "electric_vehicles", "inverters", "home_appliances"):
            entries = list((getattr(config.devices, name) or {}).values())
            maximum = getattr(config.devices, "max_" + name)
            if maximum is not None and len(entries) > maximum:
                raise ValueError(f"devices.{name} exceeds configured maximum")
            groups[name] = entries
        if groups["home_appliances"]:
            raise ValueError(
                "FLEET requires appliance consumption in the load forecast; shiftable appliances are not supported"
            )
        ids = [d.device_id for entries in groups.values() for d in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Device IDs must be unique across groups")
        storage = groups["batteries"] + groups["electric_vehicles"]
        storage_ids = {d.device_id for d in storage}
        if (set(self.soc) | set(self.available) | set(settings.devices)) - storage_ids:
            raise ValueError("Unknown device ID in fleet runtime inputs or policies")
        inverters = {}
        for inverter in groups["inverters"]:
            if inverter.battery_id is None:
                continue  # PV AC forecasts already include their own conversion.
            if inverter.battery_id not in {d.device_id for d in groups["batteries"]}:
                raise ValueError("Inverter references an unknown stationary battery")
            if inverter.battery_id in inverters:
                raise ValueError(
                    "Multiple inverters for one battery need an explicit equivalent model"
                )
            if inverter.dc_to_ac_efficiency_curve is not None:
                raise ValueError("FLEET requires constant inverter efficiencies")
            inverters[inverter.battery_id] = inverter

        resolved = []
        for device in storage:
            is_ev = device in groups["electric_vehicles"]
            policy = settings.devices.get(device.device_id, FleetDevicePolicy())
            if device.device_id in self.soc:
                soc = self.soc[device.device_id]
            else:
                earliest = observation.subtract(seconds=settings.measurement_max_age_seconds)
                dates, values = await self.measurement.key_to_lists(
                    key=device.measurement_key_soc_factor,
                    start_datetime=earliest,
                    end_datetime=observation.add(seconds=1),
                    dropna=False,
                )
                samples = [
                    (date, value)
                    for date, value in zip(dates, values)
                    if earliest <= date <= observation and value is not None and value is not pd.NA
                ]
                if not samples:
                    raise ValueError(f"No fresh SoC for {device.device_id}")
                value = max(samples, key=lambda pair: pair[0].timestamp())[1]
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"Invalid SoC for {device.device_id}")
                soc = value * 100
            if device.max_charge_power_w is None:
                raise ValueError(f"Configure max_charge_power_w for {device.device_id}")
            charge_power = device.max_charge_power_w
            minimum = device.min_charge_power_w or 0.0
            discharge_power = policy.max_discharge_power_w
            if discharge_power is None:
                discharge_power = charge_power
            charge_eff = device.charging_efficiency
            discharge_eff = device.discharging_efficiency
            inverter = inverters.get(device.device_id)
            if inverter is not None:
                if inverter.max_power_w is None:
                    raise ValueError("Configure inverter max_power_w")
                discharge_power = min(
                    discharge_power * inverter.dc_to_ac_efficiency, inverter.max_power_w
                )
                discharge_eff *= inverter.dc_to_ac_efficiency
                if inverter.ac_to_dc_efficiency == 0:
                    charge_power = 0
                else:
                    charge_power /= inverter.ac_to_dc_efficiency
                    minimum /= inverter.ac_to_dc_efficiency
                    charge_eff *= inverter.ac_to_dc_efficiency
                if inverter.max_ac_charge_power_w is not None:
                    charge_power = min(charge_power, inverter.max_ac_charge_power_w)
            if charge_power < minimum:
                charge_power = 0
                minimum = 0
            deadline = count
            if is_ev:
                deadlines = [settings.horizon_hours * 3600.0]
                if device.min_soc_deadline_datetime is not None:
                    deadlines.append(
                        device.min_soc_deadline_datetime.timestamp() - start.timestamp()
                    )
                if device.min_soc_max_duration_h is not None:
                    deadlines.append(device.min_soc_max_duration_h * 3600)
                remaining = min(deadlines)
                deadline = math.floor(remaining / seconds)
                if deadline <= 0:
                    if soc < device.min_soc_percentage:
                        raise ValueError(f"Unreachable deadline for {device.device_id}")
                    deadline = 1
            levels = None
            if is_ev:
                levels = sorted(
                    {0.0}
                    | {
                        float(rate) * charge_power
                        for rate in (device.charge_rates or [])
                        if float(rate) * charge_power >= minimum and float(rate) > 0
                    }
                )
                if len(levels) == 1 and charge_power:
                    raise ValueError(f"No permitted charging rates for {device.device_id}")
            resolved.append(
                FleetDevice(
                    device_id=device.device_id,
                    kind="ev" if is_ev else "battery",
                    capacity_wh=device.capacity_wh,
                    max_energy_wh=device.capacity_wh * device.max_soc_percentage / 100,
                    initial_energy_wh=device.capacity_wh * soc / 100,
                    reserve_wh=0 if is_ev else device.capacity_wh * device.min_soc_percentage / 100,
                    max_charge_power_w=charge_power,
                    min_charge_power_w=minimum,
                    max_discharge_power_w=0 if is_ev else discharge_power,
                    charge_efficiency=charge_eff,
                    discharge_efficiency=discharge_eff,
                    target_energy_wh=device.capacity_wh * device.min_soc_percentage / 100
                    if is_ev
                    else 0,
                    target_slot=deadline,
                    charge_levels_w=levels,
                    available=self.available.get(device.device_id),
                    power_group=policy.power_group,
                    terminal_value_eur_per_kwh=0 if is_ev else settings.terminal_value_eur_per_kwh,
                    discharge_cost_eur_per_kwh=device.levelized_cost_of_storage_amt_kwh,
                )
            )
        origin = start.start_of("day")
        end = start.add(seconds=count * seconds)
        first = int((start.timestamp() - origin.timestamp()) // seconds)
        updated = False

        async def series(
            supplied: list[float] | None, key: str, energy: bool = False
        ) -> list[float]:
            nonlocal updated
            if supplied is not None:
                values = supplied[first : first + count]
            else:
                if not updated:
                    await self.prediction.update_data()
                    updated = True
                array = await bounded_forecast_array(
                    self.prediction,
                    key=key,
                    start_datetime=start,
                    end_datetime=end,
                    interval=to_duration(seconds),
                )
                values = array.tolist()
                if energy:
                    # Providers return W, explicit runtime series are Wh.
                    values = [v * seconds / 3600 for v in values]
            if len(values) != count or not all(math.isfinite(v) for v in values):
                raise ValueError(f"Missing or invalid {key} within FLEET horizon")
            return [v * (3600 / seconds if energy else 1000) for v in values]

        forecasts = self.forecasts
        return FleetRequest(
            start_datetime=start,
            interval_seconds=seconds,
            load_w=await series(forecasts.total_load, "loadforecast_power_w", True),
            pv_w=await series(forecasts.pv_forecast_wh, "pvforecast_ac_power", True),
            import_price_eur_per_kwh=await series(
                forecasts.electricity_price_per_wh, "elecprice_marketprice_wh"
            ),
            export_price_eur_per_kwh=await series(
                forecasts.feed_in_tariff_per_wh, "feed_in_tariff_wh"
            ),
            import_limit_w=settings.import_limit_w,
            export_limit_w=settings.export_limit_w,
            devices=resolved,
            allow_battery_export=settings.allow_battery_export,
            power_groups=[
                FleetPowerGroup(group_id=key, **group.model_dump())
                for key, group in settings.power_groups.items()
            ],
            solver_time_limit_seconds=settings.solver_time_limit_seconds,
        )
