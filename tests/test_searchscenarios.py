"""Scenario set for the device by device search: 8 synthetic scenarios, two plants.

The price, PV and load series are those of the optimizer study (``scenarios.py`` there), 15 minute
slots, 3 days starting at 12:00 so that a rolling run of 96 plannings with a 48 h horizon fits.
The plants follow the study's cases ``fall1`` (plant A: one 10 kWh / 5 kW battery and one vehicle)
and ``fall3a`` (plant B: batteries of 3, 5, 8, 10 and 15 kWh and three vehicles), grid import
limit 7 kW and feed-in limit 6 kW for B. The physics is the one of the new kernel (constant
efficiencies), so the figures are not comparable with those of the study.

Plants C and D add a heat pump with a room model (``HEAT_SCENARIOS`` are the five scenarios of
the study that heat): C is plant A plus an air-to-air heat pump that heats the room directly
(2 states), D is plant B plus the same heat pump behind an air distribution (3 states). The
outdoor temperature is the one of the study (daily means and amplitude per scenario); the comfort
band is 20.5-23 °C by day and 18.5-24 °C from 22:00 to 06:00 (night setback), with an absence of
the second day from 08:00 to 17:00 (16.5 °C minimum, warm-up two hours before the return).
"""

from dataclasses import dataclass, field, replace
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import os

import numpy as np

from akkudoktoreos.devices.heatpump import HeatPumpElectricalSettings, HeatPumpProfile
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.storage import StorageParams
from akkudoktoreos.devices.models import thermal as thm
from akkudoktoreos.devices.models.thermal import ThermalParams
from akkudoktoreos.optimization.kernel import GridConnection, PlanningProblem
from akkudoktoreos.optimization.objective import (
    Objective,
    ObjectiveSettings,
    TerminalValueSettings,
    ThermalObjectiveSettings,
    VehicleTarget,
)
from akkudoktoreos.prediction.presence import PresenceEstimate
from akkudoktoreos.thermal.planninginput import build_thermal_device
from akkudoktoreos.thermal.thermalcomfort import apply_comfort_windows, comfort_bands_from_presence
from akkudoktoreos.thermal.thermaldistribution import (
    AirDistributionSettings,
    AirFanLevel,
    DirectDistributionSettings,
    DistributionTemperatureBounds,
    ThermalDistributionSettings,
)
from akkudoktoreos.thermal.thermalmodels import (
    TemperatureInterval,
    ThermalBuildingRC2Parameters,
    ThermalComfortBand,
)

SLOT_H = 0.25
DAY = 96
HORIZON = 192
N_TOTAL = DAY + HORIZON
STD_RATES = tuple(round(j / 10.0, 10) for j in range(0, 11))
# Price of the energy left in the batteries [EUR/Wh]; fixed so that DP and objective agree.
TERMINAL_EUR_PER_WH = 0.0001

_SCEN: list[dict[str, Any]] = [
    dict(
        name="winter_dark",
        sr=8.2,
        ss=16.7,
        peak=1800.0,
        clouds=(0.25, 0.20, 0.30),
        cvar=0.10,
        load_kwh=14.0,
        spot=dict(base=0.09, am=0.07, pm=0.12, solar=0.00, noise=0.015, offs=(0.0, 0.01, -0.01)),
        fees=0.14,
    ),
    dict(
        name="winter_clear",
        sr=7.6,
        ss=17.3,
        peak=4200.0,
        clouds=(0.95, 0.9, 0.95),
        cvar=0.04,
        load_kwh=15.0,
        spot=dict(base=0.10, am=0.08, pm=0.16, solar=0.01, noise=0.015, offs=(0.0, 0.02, 0.0)),
        fees=0.14,
    ),
    dict(
        name="summer_sunny",
        sr=5.2,
        ss=21.5,
        peak=8500.0,
        clouds=(1.0, 1.0, 0.97),
        cvar=0.03,
        load_kwh=9.5,
        spot=dict(base=0.07, am=0.04, pm=0.09, solar=0.07, noise=0.01, offs=(0.0, 0.0, 0.0)),
        fees=0.14,
    ),
    dict(
        name="summer_clouds",
        sr=5.4,
        ss=21.2,
        peak=8500.0,
        clouds=(0.55, 0.45, 0.6),
        cvar=0.30,
        load_kwh=10.0,
        spot=dict(base=0.08, am=0.05, pm=0.10, solar=0.04, noise=0.02, offs=(0.0, 0.01, 0.0)),
        fees=0.14,
    ),
    dict(
        name="shoulder_mixed",
        sr=6.4,
        ss=20.0,
        peak=6000.0,
        clouds=(0.7, 0.5, 0.75),
        cvar=0.20,
        load_kwh=11.5,
        spot=dict(base=0.085, am=0.06, pm=0.11, solar=0.04, noise=0.015, offs=(0.0, -0.01, 0.01)),
        fees=0.14,
    ),
    dict(
        name="price_spike",
        sr=7.0,
        ss=18.2,
        peak=4500.0,
        clouds=(0.6, 0.6, 0.6),
        cvar=0.15,
        load_kwh=12.5,
        spot=dict(
            base=0.09,
            am=0.06,
            pm=0.10,
            solar=0.03,
            noise=0.02,
            offs=(0.0, 0.0, 0.0),
            spikes=((17.0, 21.0, 0.55), (31.5, 33.5, 0.50)),
        ),
        fees=0.14,
    ),
    dict(
        name="negative_prices",
        sr=5.8,
        ss=20.8,
        peak=7500.0,
        clouds=(1.0, 0.95, 1.0),
        cvar=0.05,
        load_kwh=9.0,
        spot=dict(base=0.07, am=0.03, pm=0.08, solar=0.19, noise=0.015, offs=(0.0, -0.01, 0.0)),
        fees=0.0,
    ),
    dict(
        name="flat_tariff",
        sr=6.9,
        ss=19.3,
        peak=5500.0,
        clouds=(0.75, 0.7, 0.8),
        cvar=0.12,
        load_kwh=11.0,
        spot=dict(base=0.085, am=0.004, pm=0.006, solar=0.002, noise=0.002, offs=(0.0, 0.0, 0.0)),
        fees=0.14,
    ),
]
SCENARIO_NAMES = [str(s["name"]) for s in _SCEN]
# Another draw of the noise of every scenario (other days of the same weather types), for the
# check on cases not used to tune the search: ``EOS_SCENARIO_VARIANT=1``. 0 is the study's set.
VARIANT = int(os.environ.get("EOS_SCENARIO_VARIANT", "0"))
# The five scenarios of the study with a heating load, and their outdoor temperature (daily mean
# of each of the three days, daily amplitude) from the study's ``scenarios.py``.
HEAT_SCENARIOS = ("winter_dark", "winter_clear", "price_spike", "shoulder_mixed", "flat_tariff")
_TOUT = {
    "winter_dark": ((0.0, 1.0, -1.0), 3.0),
    "winter_clear": ((-6.0, -4.0, -7.0), 6.0),
    "summer_sunny": ((22.0, 23.0, 21.0), 7.0),
    "summer_clouds": ((17.0, 15.0, 18.0), 4.0),
    "shoulder_mixed": ((8.0, 6.0, 9.0), 5.0),
    "price_spike": ((2.0, 0.0, 3.0), 4.0),
    "negative_prices": ((13.0, 12.0, 14.0), 6.0),
    "flat_tariff": ((11.0, 10.0, 12.0), 5.0),
}
START_TIME = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)


def _gauss(h: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * ((h - mu) / sigma) ** 2)


def _ar1(rng: np.random.RandomState, n: int, phi: float, sigma: float) -> np.ndarray:
    out = np.zeros(n)
    e = rng.normal(0.0, sigma, n)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + e[i]
    return out


@dataclass(frozen=True, eq=False)
class Series:
    """Forecast series of a scenario: ``N_TOTAL`` slots from 12:00 of day 0.

    Attributes:
        name: Scenario name.
        pv_wh: PV energy per slot [Wh].
        load_wh: Base load per slot [Wh].
        import_eur_per_wh: Import price [EUR/Wh].
        export_eur_per_wh: Feed-in tariff [EUR/Wh].
        t_out_c: Outdoor temperature [°C] (heating scenarios).
    """

    name: str
    pv_wh: np.ndarray
    load_wh: np.ndarray
    import_eur_per_wh: np.ndarray
    export_eur_per_wh: np.ndarray
    t_out_c: np.ndarray = field(default_factory=lambda: np.zeros(0))


def make_series(name: str) -> Series:
    """Build the series of a scenario (fixed seed, perfect forecast)."""
    spec = next(s for s in _SCEN if s["name"] == name)
    rng = np.random.RandomState(1000 + SCENARIO_NAMES.index(name) * 17 + VARIANT * 100_003)
    t = np.arange(N_TOTAL)
    abs_h = 12.0 + (t + 0.5) * SLOT_H
    day = (abs_h // 24).astype(int)
    h = abs_h % 24
    daylight = (h > spec["sr"]) & (h < spec["ss"])
    clear = np.where(
        daylight,
        np.sin(np.pi * (h - spec["sr"]) / (spec["ss"] - spec["sr"])).clip(0, None) ** 1.25,
        0.0,
    )
    cloud = np.clip(
        np.array(spec["clouds"])[day.clip(0, 2)] + _ar1(rng, N_TOTAL, 0.93, spec["cvar"] * 0.55),
        0.04,
        1.0,
    )
    pv_w = spec["peak"] * clear * cloud
    shape = (
        0.32 + 0.55 * _gauss(h, 7.4, 0.9) + 0.22 * _gauss(h, 12.5, 1.2) + 1.0 * _gauss(h, 19.0, 1.5)
    )
    noise = np.exp(_ar1(rng, N_TOTAL, 0.6, 0.12) + 0.25 * rng.standard_normal(N_TOTAL).clip(-1, 3))
    load = shape * noise
    load *= spec["load_kwh"] * 1000.0 / (load[:DAY].sum() * SLOT_H)
    load_w = np.clip(load, 80.0, None)
    sp = spec["spot"]
    hours_total = N_TOTAL // 4 + 1
    hh_abs = 12.0 + np.arange(hours_total) + 0.5
    hh_day = (hh_abs // 24).astype(int)
    hh = hh_abs % 24
    sun_day = np.array(spec["clouds"])[hh_day.clip(0, 2)]
    spot_h = (
        sp["base"]
        + sp["am"] * _gauss(hh, 8.0, 1.4)
        + sp["pm"] * _gauss(hh, 19.0, 1.6)
        - sp["solar"] * _gauss(hh, 13.2, 2.4) * sun_day
        + np.array(sp["offs"])[hh_day.clip(0, 2)]
        + _ar1(rng, hours_total, 0.7, sp["noise"])
    )
    for lo, hi, add in sp.get("spikes", ()):
        spot_h += np.where((hh_abs >= lo) & (hh_abs < hi), add, 0.0)
    spot = np.repeat(spot_h, 4)[:N_TOTAL]
    imp = 1.19 * (spot + spec["fees"])
    exp = np.clip(spot - 0.012, 0.0, None)
    # Drawn after every other series so that the series of the plants A and B stay as they were.
    tmean, tamp = _TOUT[name]
    t_out = (
        np.array(tmean)[day.clip(0, 2)]
        - tamp * np.cos(2 * np.pi * (h - 3.0) / 24.0)
        + _ar1(rng, N_TOTAL, 0.97, 0.12)
    )
    return Series(name, pv_w * SLOT_H, load_w * SLOT_H, imp / 1000.0, exp / 1000.0, t_out)


@dataclass(frozen=True)
class Vehicle:
    """A vehicle of a plant: capacity, start charge, power, plug-in window and target."""

    capacity_wh: float
    soc_pct: float
    power_w: float
    arrive: int
    depart: int
    target_pct: float


PLANT_A_BATTERIES = ((10000.0, 50.0, 10.0, 5000.0),)
PLANT_A_VEHICLES = (Vehicle(40000.0, 30.0, 11000.0, 0, 76, 80.0),)
PLANT_B_BATTERIES = (
    (3000.0, 50.0, 10.0, 1500.0),
    (5000.0, 40.0, 10.0, 2500.0),
    (8000.0, 60.0, 10.0, 3000.0),
    (10000.0, 30.0, 10.0, 5000.0),
    (15000.0, 50.0, 15.0, 7500.0),
)
PLANT_B_VEHICLES = (
    Vehicle(40000.0, 30.0, 11000.0, 0, 76, 80.0),
    Vehicle(60000.0, 20.0, 7400.0, 24, 92, 90.0),
    Vehicle(40000.0, 35.0, 3700.0, 40, 140, 75.0),
)


def battery_params(
    capacity_wh: float, soc_pct: float, min_pct: float, power_w: float, rates: tuple[float, ...]
) -> StorageParams:
    """Return a house battery with constant efficiencies."""
    return StorageParams(
        capacity_wh=capacity_wh,
        initial_soc_pct=soc_pct,
        min_soc_pct=min_pct,
        charging_efficiency=0.96,
        discharging_efficiency=0.96,
        max_charge_power_w=power_w,
        max_discharge_power_w=power_w,
        charge_rates=rates,
    )


def vehicle_params(vehicle: Vehicle, slots: int, offset: int = 0) -> StorageParams:
    """Return a vehicle plugged in for ``arrive <= slot < depart`` (counted from ``offset``)."""
    window = tuple(vehicle.arrive <= offset + s < vehicle.depart for s in range(slots))
    rates = tuple(r for r in STD_RATES if r == 0.0 or r * vehicle.power_w >= 1380.0 - 1e-9)
    return StorageParams(
        capacity_wh=vehicle.capacity_wh,
        initial_soc_pct=vehicle.soc_pct,
        charging_efficiency=0.95,
        discharging_efficiency=1.0,
        max_charge_power_w=vehicle.power_w,
        max_discharge_power_w=0.0,
        charge_rates=rates,
        is_vehicle=True,
        available=window,
    )


HEAT_PROFILE = HeatPumpProfile.model_validate(
    {
        "profile_id": "scenario_air_to_air",
        "heat_pump_type": "air_to_air",
        "cop_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "cop": c}
            for t, c in [(-20, 2.0), (-7, 2.6), (2, 3.3), (7, 4.0), (20, 5.0)]
        ],
        "thermal_max_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "thermal_power_w": q}
            for t, q in [(-20, 4000.0), (-7, 5000.0), (2, 5800.0), (7, 6300.0), (20, 7000.0)]
        ],
        "thermal_min_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "thermal_power_w": 1200.0}
            for t in (-20, 20)
        ],
        "provenance": {
            "source": "scenario",
            "quality": "user_supplied",
            "electric_power_basis": "total_system",
        },
    }
)
# Room model of the tests of the thermal package: 0.12/h loss, 0.6/h exchange with the building
# mass of 4 times the room capacity, 1.8 K per thermal kWh (556 Wh/K room, 2.2 kWh/K mass).
HEAT_BUILDING = ThermalBuildingRC2Parameters(
    room_loss_per_hour=0.12,
    room_mass_exchange_per_hour=0.6,
    mass_capacity_ratio=4.0,
    heat_gain_kelvin_per_kwh=1.8,
)
HEAT_ELECTRICAL = HeatPumpElectricalSettings(
    standby_power_w=5.0,
    running_auxiliary_power_w=20.0,
    startup_energy_wh=30.0,
    extras_are_outside_cop=True,
)
HEAT_AIR = AirDistributionSettings(
    air_capacity_j_per_k=200_000.0,
    air_temperature_bounds=DistributionTemperatureBounds(
        min_temperature_c=5.0, max_temperature_c=45.0
    ),
    fan_levels=(
        AirFanLevel(
            level_id="mid",
            conductance_w_per_k=400.0,
            electric_power_w=30.0,
            electricity_included_in_cop=False,
        ),
    ),
    default_fan_level_id="mid",
    circulation_operation="fixed_entire_horizon",
)
OCCUPIED = ThermalComfortBand(
    min_temperature_c=20.5, target_temperature_c=21.5, max_temperature_c=23.0
)
NIGHT = (18.5, 19.5, 24.0)
ABSENT = ThermalComfortBand(
    min_temperature_c=16.5, target_temperature_c=17.5, max_temperature_c=23.0
)
ABSENCE_SLOTS = (80, 116)  # day 1, 08:00-17:00
PREHEAT_S = 7200


@lru_cache(maxsize=16)
def comfort_band_series(
    total: int = N_TOTAL,
    absence: Optional[tuple[int, int]] = ABSENCE_SLOTS,
    shift_k: float = 0.0,
    night_setback: bool = True,
) -> tuple[ThermalComfortBand, ...]:
    """Return the comfort bands of the whole scenario: presence, night setback, absence.

    The bands come from the presence conversion of the thermal package (absence with a warm-up
    lead) and the explicit night windows; ``shift_k`` moves every band (an event "target +1 K").
    """
    estimates = [
        PresenceEstimate(
            date_time=START_TIME + timedelta(minutes=15 * i),
            require_comfort=not (absence is not None and absence[0] <= i < absence[1]),
            source="manual_away",
        )
        for i in range(total)
    ]
    baseline = comfort_bands_from_presence(estimates, OCCUPIED, ABSENT, preheat_seconds=PREHEAT_S)
    if night_setback:
        windows = tuple(
            TemperatureInterval(
                min_temperature_c=NIGHT[0],
                target_temperature_c=NIGHT[1],
                max_temperature_c=NIGHT[2],
                start_datetime=START_TIME + timedelta(hours=start - 12),
                end_datetime=START_TIME + timedelta(hours=end - 12),
            )
            for start, end in ((22, 30), (46, 54), (70, 78))
        )
        baseline = apply_comfort_windows([e.date_time for e in estimates], baseline, windows)
    return tuple(
        ThermalComfortBand(
            min_temperature_c=b.min_temperature_c + shift_k,
            target_temperature_c=b.target_temperature_c + shift_k,
            max_temperature_c=b.max_temperature_c + shift_k,
        )
        for b in baseline
    )


def heat_device(
    series: Series,
    plant: str,
    offset: int = 0,
    slots: int = HORIZON,
    temperature_c: Optional[np.ndarray] = None,
    running: bool = False,
    blocked: Optional[np.ndarray] = None,
    shift_k: float = 0.0,
    demand_factor: float = 1.0,
    absence: Optional[tuple[int, int]] = ABSENCE_SLOTS,
    night_setback: bool = True,
) -> ThermalParams:
    """Return the thermal device of plant C (direct, 2 states) or D (air distribution, 3 states).

    Args:
        series: The scenario (outdoor temperature).
        plant: ``"C"`` or ``"D"``.
        offset: First slot of the window in the scenario.
        slots: Window length.
        temperature_c: Initial temperatures (room, mass[, air]); default 21 °C everywhere.
        running: Whether the heat pump was running before the window.
        blocked: Slots in which it must not run.
        shift_k: Shift of the comfort band [K] (event).
        demand_factor: Factor on the heat loss of the building (event "heat demand +30 %").
        absence: Absence window of the scenario in slots, None for none.
        night_setback: Whether the night windows apply.
    """
    direct = plant == "C"
    states = 2 if direct else 3
    building = HEAT_BUILDING
    if demand_factor != 1.0:
        building = building.model_copy(
            update={"room_loss_per_hour": building.room_loss_per_hour * demand_factor}
        )
    window = slice(offset, offset + slots)
    bands = comfort_band_series(len(series.t_out_c), absence, shift_k, night_setback)[window]
    distribution: ThermalDistributionSettings = DirectDistributionSettings() if direct else HEAT_AIR
    return build_thermal_device(
        building,
        distribution,
        HEAT_PROFILE,
        outdoor_c=series.t_out_c[window],
        comfort_bands=bands,
        initial_temperature_c=(
            np.full(states, 21.0) if temperature_c is None else np.asarray(temperature_c)
        ),
        delivery_temperature_c=20.0,
        electrical=HEAT_ELECTRICAL,
        initial_running=running,
        blocked=blocked,
        reference_room_c=21.0,
    )


def build(
    series: Series,
    plant: str,
    offset: int = 0,
    slots: int = HORIZON,
    battery_rates: tuple[float, ...] = STD_RATES,
    batteries: Optional[tuple[tuple[float, float, float, float], ...]] = None,
    vehicles: Optional[tuple[Vehicle, ...]] = None,
    heat: Optional[ThermalParams] = None,
    heat_settings: Optional[ThermalObjectiveSettings] = None,
) -> tuple[PlanningProblem, Objective]:
    """Build the problem and the objective of a plant on a window of a scenario.

    Args:
        series: The scenario.
        plant: ``"A"``, ``"B"``, ``"C"`` (A plus heat pump) or ``"D"`` (B plus heat pump with an
            air distribution).
        offset: First slot of the window in the scenario (vehicle times count from slot 0).
        slots: Window length.
        battery_rates: Grid charge levels of the batteries.
        batteries: Override of the batteries ``(capacity, soc %, min %, power)``.
        vehicles: Override of the vehicles.
        heat: Thermal device of plants C and D (default: ``heat_device`` of the window).
        heat_settings: Prices of the thermal parts of the objective.
    """
    if plant not in ("A", "B", "C", "D"):
        raise ValueError("plant must be 'A', 'B', 'C' or 'D'")
    big = plant in ("B", "D")
    battery_spec = (
        batteries if batteries is not None else (PLANT_B_BATTERIES if big else PLANT_A_BATTERIES)
    )
    vehicle_spec = (
        vehicles if vehicles is not None else (PLANT_B_VEHICLES if big else PLANT_A_VEHICLES)
    )
    thermal: tuple[ThermalParams, ...] = ()
    if plant in ("C", "D"):
        thermal = (heat if heat is not None else heat_device(series, plant, offset, slots),)
    window = slice(offset, offset + slots)
    units = tuple(battery_params(c, s, m, p, battery_rates) for c, s, m, p in battery_spec)
    inverters = tuple(
        InverterParams(
            max_power_w=2.0 * p, battery_index=i, dc_to_ac_efficiency=0.96, ac_to_dc_efficiency=0.96
        )
        for i, (_, _, _, p) in enumerate(battery_spec)
    ) or (InverterParams(max_power_w=10000.0, battery_index=None),)
    problem = PlanningProblem(
        slot_duration_h=SLOT_H,
        pv_wh=series.pv_wh[window],
        load_wh=series.load_wh[window],
        import_price_per_wh=series.import_eur_per_wh[window],
        feed_in_tariff_per_wh=series.export_eur_per_wh[window],
        batteries=units,
        vehicles=tuple(vehicle_params(v, slots, offset) for v in vehicle_spec),
        inverters=inverters,
        thermal=thermal,
        grid=GridConnection(import_limit_w=7000.0, export_limit_w=6000.0)
        if big
        else GridConnection(),
    )
    targets = tuple(
        VehicleTarget(v.target_pct, max(v.depart - offset, 1))
        if v.depart - offset <= slots
        else VehicleTarget(v.target_pct, None)
        for v in vehicle_spec
    )
    settings = ObjectiveSettings(
        terminal_value=TerminalValueSettings(mode="FIXED", fixed_price_per_wh=TERMINAL_EUR_PER_WH),
        vehicle_targets=targets,
        thermal=heat_settings or ThermalObjectiveSettings(),
    )
    return problem, Objective(problem, settings)


def with_state(
    problem: PlanningProblem, objective: Objective, soc_wh: np.ndarray, vehicle_soc_wh: np.ndarray
) -> tuple[PlanningProblem, Objective]:
    """Return the problem with other initial states of charge (rolling planning)."""
    batteries = tuple(
        replace(b, initial_soc_pct=float(wh / b.capacity_wh * 100.0))
        for b, wh in zip(problem.batteries, soc_wh)
    )
    vehicles = tuple(
        replace(v, initial_soc_pct=float(wh / v.capacity_wh * 100.0))
        for v, wh in zip(problem.vehicles, vehicle_soc_wh)
    )
    new = replace(problem, batteries=batteries, vehicles=vehicles)
    return new, Objective(new, objective.settings)


def thermostat_heat(unit: ThermalParams, target_c: Any) -> np.ndarray:
    """Return the heat plan of a simple thermostat rule.

    In every slot the rule chooses the lowest heat (0 included) that brings the room temperature
    at the start of the next slot to the target, the highest heat if none does, 0 where the
    device must not run. It uses no forecast of prices and knows no pre-heating.

    Args:
        unit: The thermal device.
        target_c: Target room temperature, one value or one per slot.
    """
    target = np.broadcast_to(np.asarray(target_c, dtype=float), (unit.slots,))
    data = unit.arrays()
    limit = unit.heat_limit_w
    heat = np.zeros(unit.slots)
    temperature = np.array(unit.initial_temperature_c, dtype=float)
    following = np.empty_like(temperature)
    previous = 1.0 if unit.initial_running else 0.0
    for slot in range(unit.slots):
        goal = target[min(slot + 1, unit.slots - 1)]
        thm.thermal_step(data, slot, temperature, 0.0, previous, following)
        needed = (goal - following[0]) / unit.dynamics_b[slot, 0]
        heat[slot] = min(max(needed, 0.0), limit[slot])
        thm.thermal_step(data, slot, temperature, float(heat[slot]), previous, following)
        temperature, following = following.copy(), temperature
        previous = float(thm.heat_share(data, slot, float(heat[slot])))
    return heat


def with_heat_state(
    problem: PlanningProblem,
    objective: Objective,
    temperature_c: np.ndarray,
    running: bool,
) -> tuple[PlanningProblem, Objective]:
    """Return the problem with another initial state of the heat pump (rolling planning)."""
    unit = replace(
        problem.thermal[0],
        initial_temperature_c=np.asarray(temperature_c, dtype=float),
        initial_running=running,
    )
    new = replace(problem, thermal=(unit,))
    return new, Objective(new, objective.settings)
