"""Planning request: configuration, measurements and forecasts become a planning task.

``PlanningRequest.resolve`` reads the configured devices, the measurements and the forecast
providers and returns a ``PlanningTask``: the ``PlanningProblem`` with the objective and search
settings that belong to it. Runtime values (state of charge, forecast series) may replace what
would be read; everything else comes from the configuration.

"""

from datetime import datetime
from typing import Annotated, Optional

import numpy as np
from pydantic import ConfigDict, Field

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    get_ems,
)
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.appliancescheduling import estimate_daily_load
from akkudoktoreos.optimization.kernel.problem import GridConnection, PlanningProblem
from akkudoktoreos.optimization.objective import (
    ObjectiveSettings,
    TerminalValueSettings,
    ThermalObjectiveSettings,
)
from akkudoktoreos.optimization.request.devices import DeviceResolver, ElectricDevices
from akkudoktoreos.optimization.request.grid import (
    Forecasts,
    TimeGrid,
    build_time_grid,
    read_series,
    slice_forecasts,
)
from akkudoktoreos.optimization.request.task import PlanningTask
from akkudoktoreos.optimization.request.thermal import ThermalDeviceInfo, ThermalResolver
from akkudoktoreos.optimization.search.settings import (
    ApplianceSchedule,
    SearchSettings,
    SearchSpace,
)
from akkudoktoreos.optimization.searchsettings import SearchEffort
from akkudoktoreos.optimization.terminalvalue import trailing_window
from akkudoktoreos.prediction.presence import PresenceAbsence

# Effort levels of the search: transitions per single device planning and passes over the devices.
EFFORT_DP_TRANSITIONS = {
    SearchEffort.LOW: 10_000,
    SearchEffort.NORMAL: 30_000,
    SearchEffort.HIGH: 100_000,
}
EFFORT_MAX_PASSES = {SearchEffort.LOW: 4, SearchEffort.NORMAL: 8, SearchEffort.HIGH: 12}


class RuntimeForecasts(PydanticBaseModel):
    """External series from local midnight on the optimization grid, in slot Wh and EUR per Wh.

    The grid interval is configured in the optimization settings. Omitted series are read from
    the configured providers, preserving their raw coverage.
    """

    model_config = ConfigDict(extra="forbid")

    pv_forecast_wh: Optional[list[float]] = None
    total_load: Optional[list[float]] = None
    electricity_price_per_wh: Optional[list[float]] = None
    feed_in_tariff_per_wh: Optional[list[float]] = None


class BlockedInterval(PydanticBaseModel):
    """A period in which a heat source must not run (a block by the user or the grid operator)."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime


class PlanningRequest(ConfigMixin, MeasurementMixin, PredictionMixin, PydanticBaseModel):
    """Supply observations while hardware, tariffs and scheduling remain in the configuration.

    An explicit SoC takes precedence over a fresh measurement. Without a usable measurement a
    storage starts at 0 % and a warning says so.
    """

    model_config = ConfigDict(extra="forbid")

    soc: dict[str, Annotated[float, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: RuntimeForecasts = Field(default_factory=RuntimeForecasts)
    vehicle_available: dict[str, list[bool]] = Field(
        default_factory=dict,
        description=(
            "Per vehicle id whether it is plugged in, one value per slot from the run on. "
            "A vehicle without an entry is plugged in all the time."
        ),
    )

    heat_source_blocks: dict[str, list[BlockedInterval]] = Field(
        default_factory=dict,
        description=(
            "Per heat source id the periods in which it must not run. A slot that overlaps "
            "a period is blocked."
        ),
    )
    absences: list[PresenceAbsence] = Field(
        default_factory=list,
        description=(
            "Reported absences of this run, added to presence.absences of the configuration "
            "for the presence forecast of the thermal zones."
        ),
    )

    async def resolve(self) -> PlanningTask:
        """Resolve the planning task inside the EMS lock, after its slot start has been set.

        Raises:
            ValueError: For incomplete or inconsistent configuration, devices, forecasts or
                measurements; the message names the cause.
        """
        config = self.config
        config.validate_optimization_horizons()
        ems = get_ems()
        settings = config.optimization
        grid = build_time_grid(
            interval_seconds=settings.interval_sec,
            horizon_hours=settings.horizon_hours,
            tail_horizon_hours=settings.tail_horizon_hours,
            prediction_hours=config.prediction.hours,
            start=ems.start_datetime,
            observation=ems.observation_datetime,
        )
        devices = await DeviceResolver(
            grid, dict(self.soc), self.vehicle_available, settings.measurement_max_age_seconds
        ).resolve()
        forecasts = await self._forecasts(grid)
        thermal, warnings = await ThermalResolver(
            grid, self.heat_source_blocks, self.absences
        ).resolve_available()
        problem = self._problem(grid, devices, forecasts, thermal)
        objective = self._objective(grid, devices, forecasts, thermal, problem)
        return PlanningTask(
            problem=problem,
            objective=objective,
            search=self._search(devices),
            space=SearchSpace(
                tuple(
                    ApplianceSchedule(
                        tuple(np.asarray(run.allowed_starts, dtype=np.int64) for run in item.runs)
                        or (np.zeros(0, dtype=np.int64),),
                        item.min_gap_slots,
                    )
                    for item in devices.appliance_inputs
                )
            ),
            grid=grid,
            forecasts=forecasts,
            battery_ids=tuple(devices.battery_ids),
            vehicle_ids=tuple(devices.vehicle_ids),
            inverter_ids=tuple(devices.inverter_ids),
            appliance_ids=tuple(devices.appliance_ids),
            appliance_pinned_end_slots=tuple(
                item.model.pinned_end_slot for item in devices.appliance_inputs
            ),
            thermal=tuple(thermal),
            appliance_inputs=tuple(devices.appliance_inputs),
            warnings=tuple(devices.warnings) + tuple(warnings),
        )

    async def _forecasts(self, grid: TimeGrid) -> Forecasts:
        """Read or take the four forecast series and cut them to the grid."""
        supplied = self.forecasts
        updated = [False]

        async def series(
            given: Optional[list[float]], key: str, energy: bool = False
        ) -> np.ndarray:
            return await read_series(
                self.prediction, grid, key, energy=energy, supplied=given, updated=updated
            )

        pv = await series(supplied.pv_forecast_wh, "pvforecast_ac_power", energy=True)
        load = await series(supplied.total_load, "loadforecast_power_w", energy=True)
        prices = await series(supplied.electricity_price_per_wh, "elecprice_marketprice_wh")
        tariffs = await series(supplied.feed_in_tariff_per_wh, "feed_in_tariff_wh")
        return slice_forecasts(grid, pv, load, prices, tariffs)

    def _problem(
        self,
        grid: TimeGrid,
        devices: ElectricDevices,
        forecasts: Forecasts,
        thermal: list[ThermalDeviceInfo],
    ) -> PlanningProblem:
        """Assemble the planning problem; slot 0 of PV and load is reduced to the part ahead."""
        control = forecasts.control_slots
        fraction = grid.first_slot_fraction
        pv = forecasts.pv_wh[:control].copy()
        load = forecasts.load_wh[:control].copy()
        if fraction < 1.0:
            pv[0] *= fraction
            load[0] *= fraction
        limits = self.config.devices.grid_connection
        return PlanningProblem(
            slot_duration_h=grid.slot_duration_h,
            pv_wh=pv,
            load_wh=load,
            import_price_per_wh=forecasts.price_per_wh[:control],
            feed_in_tariff_per_wh=forecasts.tariff_per_wh[:control],
            batteries=tuple(devices.batteries),
            vehicles=tuple(devices.vehicles),
            inverters=tuple(devices.inverters),
            appliances=tuple(devices.appliances),
            appliance_cycles=tuple(max(len(item.runs), 1) for item in devices.appliance_inputs),
            thermal=tuple(info.params for info in thermal),
            grid=GridConnection(limits.import_limit_w, limits.export_limit_w),
            first_slot_fraction=fraction,
            direct_marketing=bool(self.config.feedintariff.direct_marketing_enabled),
            tail_pv_wh=forecasts.pv_wh[control:],
            tail_load_wh=forecasts.load_wh[control:],
            tail_import_price_per_wh=forecasts.price_per_wh[control:],
            tail_feed_in_tariff_per_wh=forecasts.tariff_per_wh[control:],
        )

    def _objective(
        self,
        grid: TimeGrid,
        devices: ElectricDevices,
        forecasts: Forecasts,
        thermal: list[ThermalDeviceInfo],
        problem: PlanningProblem,
    ) -> ObjectiveSettings:
        """Build the objective settings: terminal value, targets and penalty prices."""
        settings = self.config.optimization
        penalties = settings.penalties
        auto = str(settings.terminal_value_mode) == "AUTO"
        window_slots = max(int(settings.terminal_value_window_hours) * grid.slots_per_hour, 1)
        window_estimate: Optional[np.ndarray] = None
        tail_estimate: Optional[np.ndarray] = None
        if auto and devices.batteries:
            # Daily appliances keep running after the horizon: their expected load is part of
            # the demand the tail and the continuation curve evaluate.
            control = forecasts.control_slots
            end_slot = control + forecasts.tail_slots
            base = trailing_window(forecasts.load_wh, end_slot, window_slots)
            inputs = devices.appliance_inputs
            window_estimate = estimate_daily_load(
                inputs, grid.start, end_slot - len(base), end_slot
            )
            if grid.requested_tail_slots > 0:
                tail_estimate = estimate_daily_load(
                    inputs, grid.start, control, end_slot, skip_planned_days=True
                )
        terminal = TerminalValueSettings(
            mode="AUTO" if auto else "FIXED",
            fixed_price_per_wh=settings.terminal_value_euro_per_kwh / 1000,
            lookahead=grid.requested_tail_slots > 0,
            window_slots=window_slots,
            tail_charge_rates=devices.charge_rates,
            tail_export_rates=devices.export_rates,
            appliance_estimate_window_wh=window_estimate,
            appliance_estimate_tail_wh=tail_estimate,
        )
        limits = self.config.devices.grid_connection
        thermal_prices = ThermalObjectiveSettings()
        if thermal:
            thermal_prices = ThermalObjectiveSettings(
                comfort_eur_per_kh=max(info.comfort_eur_per_kh for info in thermal),
                protection_eur_per_kh=max(info.protection_eur_per_kh for info in thermal),
            )
        return ObjectiveSettings(
            terminal_value=terminal,
            vehicle_targets=tuple(devices.vehicle_targets),
            full_charge_targets=tuple(devices.full_charge_targets),
            vehicle_miss_eur_per_pct=float(penalties.get("ev_soc_miss", 10)),
            full_charge_miss_eur_per_pct=float(penalties.get("battery_full_charge_miss", 10)),
            ac_break_even_factor=float(penalties.get("ac_charge_break_even", 1.0)),
            import_limit_eur_per_kwh=limits.import_limit_penalty_eur_per_kwh,
            thermal=thermal_prices,
        )

    def _search(self, devices: ElectricDevices) -> SearchSettings:
        """Build the search settings from the device settings and the search block."""
        common = self.config.optimization.search
        discharge = tuple(
            (tuple(sorted(device.discharge_rates or ())) or (1.0,))
            if device.adapter_supports_discharge_limit
            else (1.0,)
            for device in devices.battery_settings
        )
        export = tuple(
            tuple(sorted(device.grid_export_rates))
            if device.grid_export_rates is not None
            else (1.0,)
            for device in devices.battery_settings
        )
        return SearchSettings(
            battery_discharge_levels=discharge,
            battery_export_levels=export,
            battery_pv_switchable=tuple(
                bool(device.pv_charge_switchable) for device in devices.battery_settings
            ),
            dp_transitions=EFFORT_DP_TRANSITIONS[common.effort],
            max_passes=EFFORT_MAX_PASSES[common.effort],
            time_budget_s=common.time_budget_seconds,
        )


class EnergyManagementRunRequest(PlanningRequest):
    """Body of a run with the stored configuration: runtime values and changed devices."""

    changed_devices: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of devices an event concerns (a vehicle that leaves earlier, a battery that "
            "fails, a block of the heat pump). They are planned first, starting from the plan "
            "of the last run. An unknown id is an error."
        ),
    )
