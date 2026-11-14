import math
from typing import Any, Iterator, Optional, Sequence

import numpy as np
from pydantic import Field, field_validator, model_validator

from akkudoktoreos.config.configabc import TimeWindowSequence
from akkudoktoreos.devices.devicesabc import validate_efficiency_curve
from akkudoktoreos.devices.settings.batteryresolution import (
    BATTERY_SOC_TABLE_POINTS as SOC_TABLE_POINTS,
)
from akkudoktoreos.devices.settings.batterysettings import BATTERY_DEFAULT_CHARGE_RATES
from akkudoktoreos.optimization.genetic.geneticdevices import DeviceParameters
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# Resolution of the dense efficiency tables: one value per power percent.
_EFFICIENCY_TABLE_POINTS = 101


def _dense_efficiency_table(curve: Optional[list[tuple[float, float]]]) -> Optional[list[float]]:
    """Interpolate an efficiency curve at every power percent 0 % ... 100 %."""
    if curve is None:
        return None
    fractions, efficiencies = zip(*curve)
    grid = np.linspace(0.0, 1.0, _EFFICIENCY_TABLE_POINTS)
    return [float(value) for value in np.interp(grid, fractions, efficiencies)]


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    # 0: the battery may not charge, e.g. the profile inhibits it at a low temperature.
    return Field(default=5000, ge=0, json_schema_extra={"description": description})


def initial_soc_percentage_field(description: str) -> int:
    return Field(
        default=0, ge=0, le=100, json_schema_extra={"description": description, "examples": [42]}
    )


def discharging_efficiency_field(default_value: float) -> float:
    return Field(
        default=default_value,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the discharge efficiency of the battery."
        },
    )


class BaseBatteryParameters(DeviceParameters):
    """Battery Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of battery", "examples": ["battery1"]}
    )
    capacity_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the capacity of the battery in watt-hours.",
            "examples": [8000],
        },
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the charging efficiency of the battery."
        },
    )
    discharging_efficiency: float = discharging_efficiency_field(0.88)
    max_charge_power_w: Optional[float] = max_charging_power_field()
    max_discharge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum discharging power in watts. 0 means discharging is inhibited. "
                "None uses max_charge_power_w."
            ),
            "examples": [None, 4000],
        },
    )
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the minimum state of charge (SOC) of the battery in percentage.",
            "examples": [10],
        },
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the maximum state of charge (SOC) of the battery in percentage."
        },
    )
    charge_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "Charge rates as factor of maximum charging power [0.00 ... 1.00]. None denotes all charge rates are available.",
            "examples": [[0.0, 0.25, 0.5, 0.75, 1.0], None],
        },
    )
    grid_export_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Battery-to-grid export rates as factor of maximum discharge "
                "power ]0.00 ... 1.00]. These levels are available to algorithms "
                "that explicitly enable battery-to-grid export. None leaves the "
                "choice of export levels to the caller."
            ),
            "examples": [[0.25, 0.5, 0.75, 1.0], [1.0], None],
        },
    )
    charge_power_limit_w_by_soc: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Maximum charging power [W] per state of charge, e.g. from a battery "
                f"profile. {SOC_TABLE_POINTS} values, index i is the limit at SoC i %. "
                "Further limits max_charge_power_w. None means no SoC dependent limit."
            ),
            "examples": [None],
        },
    )
    discharge_power_limit_w_by_soc: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Maximum discharging power [W] per state of charge, e.g. from a battery "
                f"profile. {SOC_TABLE_POINTS} values, index i is the limit at SoC i %. "
                "Further limits max_discharge_power_w. None means no SoC dependent limit."
            ),
            "examples": [None],
        },
    )
    charging_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Power dependent charging efficiency as a list of (power_fraction, "
                "efficiency) points. The power fraction is the charging power relative "
                "to max_charge_power_w [0.0 ... 1.0]; points must be strictly increasing "
                "in power fraction, efficiencies in (0, 1]. Values are interpolated "
                "linearly and clamped to the first/last point. When set, the curve "
                "replaces charging_efficiency in the simulation. None means constant "
                "charging_efficiency."
            ),
            "examples": [None, [[0.0, 0.975], [0.5, 0.96], [1.0, 0.94]]],
        },
    )
    discharging_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Power dependent discharging efficiency as a list of (power_fraction, "
                "efficiency) points. The power fraction is the discharging power "
                "relative to max_discharge_power_w [0.0 ... 1.0]; same rules as "
                "charging_efficiency_curve. When set, the curve replaces "
                "discharging_efficiency in the simulation. None means constant "
                "discharging_efficiency."
            ),
            "examples": [None, [[0.0, 0.975], [1.0, 0.96]]],
        },
    )

    @field_validator("charge_power_limit_w_by_soc", "discharge_power_limit_w_by_soc")
    @classmethod
    def _validate_power_limit_by_soc(cls, value: Optional[list[float]]) -> Optional[list[float]]:
        if value is None:
            return None
        if len(value) != SOC_TABLE_POINTS:
            raise ValueError(
                f"A power limit table needs {SOC_TABLE_POINTS} values (SoC 0 % ... 100 %), "
                f"got {len(value)}."
            )
        for power_w in value:
            if not math.isfinite(power_w) or power_w < 0:
                raise ValueError(f"Power limits must be finite and >= 0 W, got {power_w}.")
        return value

    @field_validator("charging_efficiency_curve", "discharging_efficiency_curve")
    @classmethod
    def _validate_efficiency_curve(
        cls, value: Optional[list[tuple[float, float]]]
    ) -> Optional[list[tuple[float, float]]]:
        return validate_efficiency_curve(value)


class SolarPanelBatteryParameters(BaseBatteryParameters):
    """PV battery device simulation configuration."""

    levelized_cost_of_storage_kwh: float = Field(
        default=0.0,
        ge=0.0,
        json_schema_extra={
            "description": (
                "Levelized cost of storage applied once to each kWh delivered "
                "by the battery [EUR/kWh]."
            ),
            "examples": [0.12],
        },
    )
    max_charge_power_w: Optional[float] = max_charging_power_field()
    full_charge_soc_percentage: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        json_schema_extra={
            "description": (
                "State of charge a due full charge has to reach at least once "
                "before 'full_charge_deadline_datetime' [%]. Must not exceed "
                "'max_soc_percentage'. None means no full charge is due."
            ),
            "examples": [None, 100],
        },
    )
    full_charge_deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Moment by which 'full_charge_soc_percentage' has to be reached. "
                "A date time without timezone is read as local time. None means "
                "end of the optimization horizon."
            ),
            "examples": [None, "2026-11-20T07:00:00+01:00"],
        },
    )

    reserve_soc_percentage: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        json_schema_extra={
            "description": (
                "Reserve for unplanned consumption [%]. Within "
                "'reserve_soc_time_windows' the plan keeps at least this state of "
                "charge for the planned consumption and reaches it by each window "
                "start, from the grid if needed. The battery may still discharge "
                "into the reserve for unplanned consumption. Between "
                "'min_soc_percentage' and 'max_soc_percentage'. None means no reserve."
            ),
            "examples": [None, 40],
        },
    )
    reserve_soc_time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Time windows in local time in which the reserve has to be "
                "available. None means 06:00-22:00 every day."
            ),
            "examples": [
                None,
                {"windows": [{"start_time": "06:00", "duration": "16 hours"}]},
            ],
        },
    )

    @field_validator("full_charge_deadline_datetime", mode="before")
    @classmethod
    def transform_full_charge_deadline(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        return to_datetime(value)

    @model_validator(mode="after")
    def validate_full_charge_target(self) -> "SolarPanelBatteryParameters":
        """A full charge target above the maximum SoC could never be reached."""
        if (
            self.full_charge_soc_percentage is not None
            and self.full_charge_soc_percentage > self.max_soc_percentage
        ):
            raise ValueError("full_charge_soc_percentage must be <= max_soc_percentage")
        return self

    @model_validator(mode="after")
    def validate_reserve(self) -> "SolarPanelBatteryParameters":
        """The reserve has to lie within the SoC range of the battery."""
        reserve = self.reserve_soc_percentage
        if reserve is not None and not (
            self.min_soc_percentage <= reserve <= self.max_soc_percentage
        ):
            raise ValueError(
                "reserve_soc_percentage must be between min_soc_percentage and max_soc_percentage"
            )
        return self


class ElectricVehicleParameters(BaseBatteryParameters):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )

    min_soc_deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute moment by which 'min_soc_percentage' has to be "
                "reached (departure time). A date time without timezone is read "
                "as local time. None means end of the optimization horizon."
            ),
            "examples": [None, "2026-07-16T07:00:00+02:00"],
        },
    )
    min_soc_max_duration_h: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Maximum time from the start of the optimization until "
                "'min_soc_percentage' has to be reached [h]. Combined with "
                "'min_soc_deadline_datetime' the earlier of the two applies."
            ),
            "examples": [None, 6.0],
        },
    )

    @field_validator("min_soc_deadline_datetime", mode="before")
    @classmethod
    def transform_deadline_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        return to_datetime(value)


class _PowerCurves:
    """Battery profile data of a simulated battery, prepared for fast access.

    All values are precomputed for the slot duration of the battery, so the
    simulation only scales and indexes per call. Every table has 101 entries
    (index 0 ... 100); the hot paths rely on that bound.

    Attributes:
        charge_limit_wh_by_soc: Raw charge energy of one slot [Wh] per SoC
            percent, capped at the rated charge power. None without limit table.
        discharge_limit_wh_by_soc: Raw discharge energy of one slot [Wh] per
            SoC percent, capped at the rated discharge power. None without table.
        charging_efficiency_by_power: Charging efficiency per power percent of
            the rated charge power. None without curve.
        discharging_efficiency_by_power: Discharging efficiency per power
            percent of the rated discharge power. None without curve.
        soc_index_per_wh: Factor from stored energy [Wh] to SoC percent.
        charge_index_per_wh: Factor from raw charge energy of one slot [Wh] to
            power percent of the rated charge power.
        discharge_index_per_wh: Factor from raw discharge energy of one slot
            [Wh] to power percent of the rated discharge power.
        discharged_dc_wh_per_slot: Energy delivered by the battery per slot [Wh].
        charged_stored_wh_per_slot: Energy stored into the battery per slot [Wh].
    """

    __slots__ = (
        "charge_limit_wh_by_soc",
        "discharge_limit_wh_by_soc",
        "charging_efficiency_by_power",
        "discharging_efficiency_by_power",
        "soc_index_per_wh",
        "charge_index_per_wh",
        "discharge_index_per_wh",
        "discharged_dc_wh_per_slot",
        "charged_stored_wh_per_slot",
    )

    def __init__(
        self,
        parameters: "BaseBatteryParameters",
        capacity_wh: float,
        max_charge_power_w: float,
        max_discharge_power_w: float,
        slot_duration_h: float,
        slots: int,
    ) -> None:
        rated_charge_wh = max_charge_power_w * slot_duration_h
        rated_discharge_wh = max_discharge_power_w * slot_duration_h
        self.charge_limit_wh_by_soc: Optional[list[float]] = (
            [min(limit_w * slot_duration_h, rated_charge_wh) for limit_w in limits]
            if (limits := parameters.charge_power_limit_w_by_soc) is not None
            else None
        )
        self.discharge_limit_wh_by_soc: Optional[list[float]] = (
            [min(limit_w * slot_duration_h, rated_discharge_wh) for limit_w in limits]
            if (limits := parameters.discharge_power_limit_w_by_soc) is not None
            else None
        )
        self.charging_efficiency_by_power = _dense_efficiency_table(
            parameters.charging_efficiency_curve
        )
        self.discharging_efficiency_by_power = _dense_efficiency_table(
            parameters.discharging_efficiency_curve
        )
        self.soc_index_per_wh = 100.0 / capacity_wh
        # A rated power of 0 W maps every energy to the 0 % entry.
        self.charge_index_per_wh = 100.0 / rated_charge_wh if rated_charge_wh > 0 else 0.0
        self.discharge_index_per_wh = 100.0 / rated_discharge_wh if rated_discharge_wh > 0 else 0.0
        self.discharged_dc_wh_per_slot = [0.0] * slots
        self.charged_stored_wh_per_slot = [0.0] * slots

    def reset(self, slots: int) -> None:
        """Clear the delivered and stored energy of all slots."""
        self.discharged_dc_wh_per_slot = [0.0] * slots
        self.charged_stored_wh_per_slot = [0.0] * slots


def _table_entry(table: list[float], index: int) -> float:
    """Return a table entry, with the index clamped to the table."""
    if index <= 0:
        return table[0]
    if index >= len(table):
        return table[-1]
    return table[index]


class Battery:
    """Represents a battery device with methods to simulate energy charging and discharging."""

    # Battery profile data, set per instance only if the parameters provide it.
    # A class default keeps plain batteries small: the simulation reads their
    # attributes for every slot of every candidate plan, and CPython accesses
    # attributes of instances with 30 or more of them noticeably slower.
    _curves: Optional[_PowerCurves] = None

    @property
    def has_power_curves(self) -> bool:
        """Whether charging or discharging depends on SoC or power (battery profile)."""
        return self._curves is not None

    def __init__(
        self,
        parameters: BaseBatteryParameters,
        prediction_hours: int,
        slot_duration_h: float = 1.0,
    ):
        # `prediction_hours` is the number of optimization slots, not hours. At
        # the default optimization interval of 3600 s, slot_duration_h is 1.0 and
        # the slot count equals the hour count, so existing callers are
        # unaffected. At 900 s (15 min) slot_duration_h is 0.25 and there are 4x
        # as many slots, each able to move a quarter of the hourly energy.
        self.parameters = parameters
        self.prediction_hours = prediction_hours
        self.slot_duration_h = slot_duration_h
        self._setup()

    def _setup(self) -> None:
        """Sets up the battery parameters based on provided parameters."""
        self.capacity_wh = self.parameters.capacity_wh
        self.initial_soc_percentage = self.parameters.initial_soc_percentage
        self.charging_efficiency = self.parameters.charging_efficiency
        self.discharging_efficiency = self.parameters.discharging_efficiency
        self.levelized_cost_of_storage_kwh = (
            self.parameters.levelized_cost_of_storage_kwh
            if isinstance(self.parameters, SolarPanelBatteryParameters)
            else 0.0
        )

        # Charge rates, in case of None use default
        self.charge_rates = np.array(BATTERY_DEFAULT_CHARGE_RATES, dtype=float)
        if self.parameters.charge_rates:
            charge_rates = np.array(self.parameters.charge_rates, dtype=float)
            charge_rates = np.unique(charge_rates)
            charge_rates.sort()
            self.charge_rates = charge_rates

        # Only assign for storage battery
        self.min_soc_percentage = (
            self.parameters.min_soc_percentage
            if isinstance(self.parameters, SolarPanelBatteryParameters)
            else 0
        )
        self.max_soc_percentage = self.parameters.max_soc_percentage

        # Initialize state of charge
        if self.parameters.max_charge_power_w is not None:
            self.max_charge_power_w = self.parameters.max_charge_power_w
        else:
            self.max_charge_power_w = self.capacity_wh  # TODO this should not be equal capacity_wh
        if self.parameters.max_discharge_power_w is not None:
            self.max_discharge_power_w = self.parameters.max_discharge_power_w
        else:
            self.max_discharge_power_w = self.max_charge_power_w

        # Optional battery profile data: power limits per SoC percent and
        # efficiencies per power percent.
        parameters = self.parameters
        if (
            parameters.charge_power_limit_w_by_soc is not None
            or parameters.discharge_power_limit_w_by_soc is not None
            or parameters.charging_efficiency_curve is not None
            or parameters.discharging_efficiency_curve is not None
        ):
            self._curves = _PowerCurves(
                parameters,
                self.capacity_wh,
                self.max_charge_power_w,
                self.max_discharge_power_w,
                self.slot_duration_h,
                self.prediction_hours,
            )

        self.discharge_array = np.full(self.prediction_hours, 0)
        self.charge_array = np.full(self.prediction_hours, 0)
        # Discharged energy per slot, a plain list: it is read and written one
        # slot at a time for every candidate plan, where single NumPy elements
        # (and the NumPy scalars they return) are slow.
        self._discharged_raw_wh_per_slot: list[float] = [0.0] * self.prediction_hours
        self._charged_raw_wh_per_slot: list[float] = [0.0] * self.prediction_hours
        # Optional per-slot cap on the raw charge energy from all sources. It is
        # unbounded unless an inverter restricts a slot (see limit_slot_charge).
        # Like the charged energy it is kept in a plain list: both are read and
        # written one slot at a time for every candidate plan, where single
        # NumPy elements (and the NumPy scalars they return) are slow.
        self._charge_limit_raw_wh_per_slot: list[float] = [float("inf")] * self.prediction_hours
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.min_soc_wh = (self.min_soc_percentage / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percentage / 100) * self.capacity_wh
        # Stored energy planned discharge must not go below, per slot. The
        # minimum SoC unless a reserve raises it (see set_discharge_floor). A
        # plain list for the same reason as the per-slot energies above.
        self._discharge_floor_wh_per_slot: list[float] = [self.min_soc_wh] * self.prediction_hours

    def _lower_charge_rates_desc(self, start_rate: float) -> Iterator[float]:
        """Yield all charge rates lower than a given rate in descending order.

        Args:
            charge_rates (np.ndarray): Sorted 1D array of available charge rates.
            start_rate (float): The reference charge rate.

        Yields:
            float: Charge rates lower than `start_rate`, in descending order.
        """
        charge_rates_fast = self.charge_rates

        # Find the insertion index for start_rate (left-most position)
        idx = np.searchsorted(charge_rates_fast, start_rate, side="left")

        # Yield values before idx in reverse (descending)
        return (charge_rates_fast[j] for j in range(idx - 1, -1, -1))

    def _charge_rates_descending(self) -> tuple[float, ...]:
        """Return the charge rates from highest to lowest as plain numbers.

        Built once per ``charge_rates`` array instead of searching and slicing
        the array for every charge request that does not fit.
        """
        rates = self.charge_rates
        if rates is not getattr(self, "_charge_rates_desc_source", None):
            self._charge_rates_desc = tuple(float(rate) for rate in rates[::-1])
            self._charge_rates_desc_source = rates
        return self._charge_rates_desc

    def to_dict(self) -> dict[str, Any]:
        """Converts the object to a dictionary representation."""
        return {
            "device_id": self.parameters.device_id,
            "capacity_wh": self.capacity_wh,
            "initial_soc_percentage": self.initial_soc_percentage,
            "soc_wh": self.soc_wh,
            "hours": self.prediction_hours,
            "discharge_array": self.discharge_array,
            "charge_array": self.charge_array,
            "charging_efficiency": self.charging_efficiency,
            "discharging_efficiency": self.discharging_efficiency,
            "max_charge_power_w": self.max_charge_power_w,
            "max_discharge_power_w": self.max_discharge_power_w,
        }

    def reset(self) -> None:
        """Resets the battery state to its initial values."""
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)  # Only clamp to max
        self.discharge_array = np.zeros(self.prediction_hours, dtype=np.int_)
        self.charge_array = np.zeros(self.prediction_hours, dtype=np.int_)
        self._discharged_raw_wh_per_slot = [0.0] * self.prediction_hours
        self._charged_raw_wh_per_slot = [0.0] * self.prediction_hours
        if self._curves is not None:
            self._curves.reset(self.prediction_hours)
        self._charge_limit_raw_wh_per_slot = [float("inf")] * self.prediction_hours

    def _discharge_slot_cap_wh(self) -> float:
        """Return the raw discharge energy of one slot [Wh] at the current SoC."""
        curves = self._curves
        if curves is None or curves.discharge_limit_wh_by_soc is None:
            return self.max_discharge_power_w * self.slot_duration_h
        return _table_entry(
            curves.discharge_limit_wh_by_soc,
            int(self.soc_wh * curves.soc_index_per_wh + 0.5),
        )

    def _discharging_efficiency_at(self, slot_raw_wh: float) -> float:
        """Return the discharging efficiency at the slot power of ``slot_raw_wh``."""
        curves = self._curves
        if curves is None or curves.discharging_efficiency_by_power is None:
            return self.discharging_efficiency
        return _table_entry(
            curves.discharging_efficiency_by_power,
            int(slot_raw_wh * curves.discharge_index_per_wh + 0.5),
        )

    def limit_slot_charge(self, hour: int, raw_wh: float) -> None:
        """Cap the raw energy the battery may take in one slot, from all sources.

        The cap covers PV and grid charging together and is not raised by later
        calls within the same slot. It is lifted again by ``reset()``.

        Args:
            hour (int): Slot index.
            raw_wh (float): Maximum raw charge energy [Wh] before charging efficiency.
        """
        self._charge_limit_raw_wh_per_slot[hour] = min(
            self._charge_limit_raw_wh_per_slot[hour], max(float(raw_wh), 0.0)
        )

    def set_discharge_floor(self, floor_percent: Sequence[float], start_slot: int = 0) -> None:
        """Raise the SoC planned discharge stops at, per slot, e.g. for a reserve.

        The floor never goes below the minimum SoC. It is kept by ``reset()``:
        it belongs to the run, not to a candidate plan.

        Args:
            floor_percent: Floor per slot from ``start_slot`` on [%]. Slots it
                does not cover keep the minimum SoC.
            start_slot: Slot the first value applies to.
        """
        floors = [self.min_soc_wh] * self.prediction_hours
        for offset, percent in enumerate(floor_percent):
            slot = start_slot + offset
            if 0 <= slot < self.prediction_hours:
                floors[slot] = max(self.min_soc_wh, percent / 100.0 * self.capacity_wh)
        self._discharge_floor_wh_per_slot = floors

    def discharge_floor_wh(self, hour: int) -> float:
        """Return the stored energy planned discharge stops at in a slot [Wh]."""
        return self._discharge_floor_wh_per_slot[hour]

    def rated_discharge_energy_wh(self) -> float:
        """Return the DC energy one full-power discharge slot delivers.

        This is the reference a grid-export rate is applied to: a rate of 0.5
        exports at most half the battery's rated discharge power, independent of
        how much of the slot budget self-consumption already used.
        """
        rated_slot_wh = self.max_discharge_power_w * self.slot_duration_h
        return rated_slot_wh * self._discharging_efficiency_at(rated_slot_wh)

    def remaining_discharge_energy_wh(self, hour: int) -> float:
        """Return DC energy still deliverable within one optimization slot."""
        discharged_raw_wh = self._discharged_raw_wh_per_slot[hour]
        raw_power_remaining_wh = max(self._discharge_slot_cap_wh() - discharged_raw_wh, 0.0)
        raw_soc_available_wh = max(self.soc_wh - self._discharge_floor_wh_per_slot[hour], 0.0)
        raw_remaining_wh = min(raw_power_remaining_wh, raw_soc_available_wh)
        return raw_remaining_wh * self._discharging_efficiency_at(
            discharged_raw_wh + raw_remaining_wh
        )

    def discharge_released(self, hour: int) -> bool:
        """Whether the plan lets the battery discharge in this slot."""
        return self.discharge_array[hour] != 0

    def discharged_energy_wh(self, hour: int) -> float:
        """Return DC energy delivered by the battery in one optimization slot."""
        if self._curves is not None:
            return self._curves.discharged_dc_wh_per_slot[hour]
        return self._discharged_raw_wh_per_slot[hour] * self.discharging_efficiency

    def charged_energy_wh(self, hour: int) -> float:
        """Return the energy stored into the battery in one optimization slot."""
        if self._curves is not None:
            return self._curves.charged_stored_wh_per_slot[hour]
        return self._charged_raw_wh_per_slot[hour] * self.charging_efficiency

    def set_discharge_per_hour(self, discharge_array: np.ndarray) -> None:
        """Sets the discharge values for each hour."""
        if len(discharge_array) != self.prediction_hours:
            raise ValueError(
                f"Discharge array must have exactly {self.prediction_hours} elements. Got {len(discharge_array)} elements."
            )
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array: np.ndarray) -> None:
        """Sets the charge values for each hour."""
        if len(charge_array) != self.prediction_hours:
            raise ValueError(
                f"Charge array must have exactly {self.prediction_hours} elements. Got {len(charge_array)} elements."
            )
        self.charge_array = np.array(charge_array)

    def current_soc_percentage(self) -> float:
        """Calculates the current state of charge in percentage."""
        return (self.soc_wh / self.capacity_wh) * 100

    def discharge_energy(self, wh: float, hour: int) -> tuple[float, float]:
        """Discharge energy from the battery.

        Discharge is limited by:

        * Requested delivered energy
        * Remaining energy above minimum SoC
        * Maximum discharge power, and the power limit at the current SoC if
          ``discharge_power_limit_w_by_soc`` is given
        * Discharge efficiency, at the slot power if
          ``discharging_efficiency_curve`` is given

        Args:
            wh (float): Requested delivered energy in watt-hours.
            hour (int): Time index. If `self.discharge_array[hour] == 0`,
                no discharge occurs.

        Returns:
            tuple[float, float]:
                delivered_wh (float): Actual delivered energy [Wh].
                losses_wh (float): Conversion losses [Wh].

        """
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0

        # Raw extractable energy above the floor (minimum SoC, or a reserve)
        floor_wh = self._discharge_floor_wh_per_slot[hour]
        raw_available_wh = max(self.soc_wh - floor_wh, 0.0)
        if raw_available_wh == 0.0:
            # A measured SoC below the reserve must not be raised by discharging.
            return 0.0, 0.0

        # Maximum raw discharge due to power limit, scaled to the slot duration.
        # The power limit is a power [W]; energy movable in one slot is
        # power x slot_duration_h. With a battery profile the limit and the
        # efficiency are looked up inline (no calls): this runs for every slot
        # of every candidate plan.
        discharged_raw_wh = self._discharged_raw_wh_per_slot[hour]
        discharging_efficiency = self.discharging_efficiency
        curves = self._curves
        if curves is None:
            slot_cap_wh = self.max_discharge_power_w * self.slot_duration_h
            raw_withdrawal_wh = min(raw_available_wh, max(slot_cap_wh - discharged_raw_wh, 0.0))
        else:
            limits = curves.discharge_limit_wh_by_soc
            if limits is None:
                slot_cap_wh = self.max_discharge_power_w * self.slot_duration_h
            else:
                index = int(self.soc_wh * curves.soc_index_per_wh + 0.5)
                slot_cap_wh = limits[index] if 0 <= index <= 100 else _table_entry(limits, index)
            raw_withdrawal_wh = slot_cap_wh - discharged_raw_wh
            if raw_withdrawal_wh < 0.0:
                raw_withdrawal_wh = 0.0
            if raw_available_wh < raw_withdrawal_wh:
                raw_withdrawal_wh = raw_available_wh
            efficiencies = curves.discharging_efficiency_by_power
            if efficiencies is not None:
                # Efficiency at the slot power: the energy already discharged
                # in this slot plus this request (estimated with the constant
                # efficiency), within the possible withdrawal.
                raw_estimate_wh = wh / discharging_efficiency
                if raw_estimate_wh > raw_withdrawal_wh:
                    raw_estimate_wh = raw_withdrawal_wh
                index = int(
                    (discharged_raw_wh + raw_estimate_wh) * curves.discharge_index_per_wh + 0.5
                )
                discharging_efficiency = efficiencies[index] if index <= 100 else efficiencies[-1]

        # Convert raw to delivered
        max_deliverable_wh = raw_withdrawal_wh * discharging_efficiency

        # Cap by requested delivered energy
        delivered_wh = min(wh, max_deliverable_wh)

        # Effective raw withdrawal based on what is delivered
        raw_used_wh = delivered_wh / discharging_efficiency

        # Update SoC
        self.soc_wh -= raw_used_wh
        self.soc_wh = max(self.soc_wh, floor_wh)
        self._discharged_raw_wh_per_slot[hour] = discharged_raw_wh + raw_used_wh
        if curves is not None:
            curves.discharged_dc_wh_per_slot[hour] += delivered_wh

        # Losses
        losses_wh = raw_used_wh - delivered_wh

        return delivered_wh, losses_wh

    def charge_energy(
        self,
        wh: Optional[float],
        hour: int,
        charge_factor: float = 0.0,
    ) -> tuple[float, float]:
        """Charge energy into the battery.

        Two **exclusive** modes:

        **Mode 1:**

        - `wh is not None` and `charge_factor == 0`
        - The raw requested charge energy is `wh` (pre-efficiency).
        - If remaining capacity is insufficient, charging is automatically limited.
        - No exception is raised due to capacity limits.

        **Mode 2:**

        - `wh is None` and `charge_factor > 0`
        - The raw requested energy is `max_charge_power_w * charge_factor`.
        - An electric vehicle may finish within a slot at the requested current;
          its accepted energy is capped at the remaining capacity.
        - For stationary batteries, requests above the remaining capacity try a lower
          compatible `charge_factor`, replacing this slot's factor.
        - If no charge factor can accommodate charging, the request is ignored (``(0.0, 0.0)`` is
          returned) and a penalty is applied elsewhere.

        Charging is constrained by:

        - Available SoC headroom (``max_soc_wh − soc_wh``)
        - ``max_charge_power_w``
        - The power limit at the current SoC, if ``charge_power_limit_w_by_soc``
          is given. It caps the accepted energy like a battery management system
          does; it does not scale the charge factor.
        - A slot limit set by ``limit_slot_charge()``, if any
        - ``charging_efficiency``, or the efficiency at the slot power if
          ``charging_efficiency_curve`` is given

        Args:
            wh (float | None):
                Requested raw energy [Wh] before efficiency.
                Must be provided only for Mode 1 (charge_factor must be 0).

            hour (int):
                Time index. If charging is disabled at this hour (charge_array[hour] == 0),
                returns `(0.0, 0.0)`.

            charge_factor (float):
                Fraction (0–1) of max charge power.
                Must be >0 only in Mode 2 (`wh is None`).

        Returns:
            tuple[float, float]:
                stored_wh : float
                    Energy stored after efficiency [Wh].
                losses_wh : float
                    Conversion losses [Wh].

        Raises:
            ValueError:
                - If the mode is ambiguous (neither Mode 1 nor Mode 2).
                - If the final new SoC would exceed capacity_wh.

        Notes:
            stored_wh = raw_input_wh * charging_efficiency
            losses_wh = raw_input_wh − stored_wh
        """
        # Charging allowed in this hour?
        if hour is not None and self.charge_array[hour] == 0:
            return 0.0, 0.0

        # Provide fast (3x..5x) local read access (vs. self.xxx) for repetitive read access
        soc_wh_fast = self.soc_wh
        charged_raw_wh = self._charged_raw_wh_per_slot[hour]
        # Scale the power cap [W] to a per-slot energy cap [Wh] (W x slot hours).
        # At slot_duration_h=1.0 (hourly) this equals the legacy power value.
        max_charge_per_slot_wh_fast = max(
            min(
                self.max_charge_power_w * self.slot_duration_h,
                self._charge_limit_raw_wh_per_slot[hour],
            )
            - charged_raw_wh,
            0.0,
        )
        charging_efficiency_fast = self.charging_efficiency
        # Power limit at the current SoC (battery profile), as raw energy still
        # acceptable in this slot. None without SoC dependent limit. Looked up
        # inline (no calls): this runs for every slot of every candidate plan.
        soc_limit_raw_wh: Optional[float] = None
        curves = self._curves
        if curves is not None:
            limits = curves.charge_limit_wh_by_soc
            if limits is not None:
                index = int(soc_wh_fast * curves.soc_index_per_wh + 0.5)
                soc_limit_raw_wh = (
                    limits[index] if 0 <= index <= 100 else _table_entry(limits, index)
                ) - charged_raw_wh
                if soc_limit_raw_wh < 0.0:
                    soc_limit_raw_wh = 0.0
            efficiencies = curves.charging_efficiency_by_power
            if efficiencies is not None:
                # Efficiency at the slot power: the energy already charged in
                # this slot plus the intended request within the limits.
                intended_raw_wh = (
                    wh if wh is not None else max_charge_per_slot_wh_fast * charge_factor
                )
                if intended_raw_wh > max_charge_per_slot_wh_fast:
                    intended_raw_wh = max_charge_per_slot_wh_fast
                if soc_limit_raw_wh is not None and soc_limit_raw_wh < intended_raw_wh:
                    intended_raw_wh = soc_limit_raw_wh
                if intended_raw_wh < 0.0:
                    intended_raw_wh = 0.0
                index = int((charged_raw_wh + intended_raw_wh) * curves.charge_index_per_wh + 0.5)
                charging_efficiency_fast = efficiencies[index] if index <= 100 else efficiencies[-1]

        # Decide mode & determine raw_request_wh and raw_charge_wh
        if wh is not None and charge_factor == 0.0:  # mode 1
            raw_request_wh = wh
            raw_charge_wh = max(self.max_soc_wh - soc_wh_fast, 0.0) / charging_efficiency_fast
        elif wh is None and charge_factor > 0.0:  # mode 2
            raw_request_wh = max_charge_per_slot_wh_fast * charge_factor
            raw_charge_wh = max(self.max_soc_wh - soc_wh_fast, 0.0) / charging_efficiency_fast
            if isinstance(self.parameters, ElectricVehicleParameters):
                # The vehicle BMS ends charging when full, even within a slot.
                # Keep the feasible pilot/current setting; cap only the energy
                # below instead of inventing a current below the EV minimum.
                if raw_charge_wh <= 0.0:
                    self.charge_array[hour] = 0
                    return 0.0, 0.0
            elif raw_request_wh > raw_charge_wh:
                # Use a lower charge factor: the rates below the requested
                # one, highest first (as _lower_charge_rates_desc yields them).
                requested_charge_factor = charge_factor
                for lower_charge_factor in self._charge_rates_descending():
                    if lower_charge_factor >= requested_charge_factor:
                        continue
                    charge_factor = lower_charge_factor
                    raw_request_wh = max_charge_per_slot_wh_fast * charge_factor
                    if raw_request_wh <= raw_charge_wh:
                        self.charge_array[hour] = charge_factor
                        break
                if raw_request_wh > raw_charge_wh:
                    # ignore request - penalty for missing SoC will be applied
                    self.charge_array[hour] = 0
                    return 0.0, 0.0
        else:
            raise ValueError(
                f"{self.parameters.device_id}: charge_energy must be called either "
                "with wh != None and charge_factor == 0, or with wh == None and charge_factor > 0."
            )

        # Remaining capacity
        max_raw_wh = min(raw_charge_wh, max_charge_per_slot_wh_fast)
        if soc_limit_raw_wh is not None and soc_limit_raw_wh < max_raw_wh:
            max_raw_wh = soc_limit_raw_wh

        # Actual raw intake
        raw_input_wh = raw_request_wh if raw_request_wh < max_raw_wh else max_raw_wh

        # Apply efficiency
        stored_wh = raw_input_wh * charging_efficiency_fast
        new_soc = soc_wh_fast + stored_wh

        if new_soc > self.capacity_wh:
            raise ValueError(
                f"{self.parameters.device_id}: SoC {new_soc} Wh exceeds capacity {self.capacity_wh} Wh"
            )

        self.soc_wh = new_soc
        self._charged_raw_wh_per_slot[hour] = charged_raw_wh + raw_input_wh
        if curves is not None:
            curves.charged_stored_wh_per_slot[hour] += stored_wh
        losses_wh = raw_input_wh - stored_wh

        return stored_wh, losses_wh

    def current_energy_content(self) -> float:
        """Returns the current usable energy in the battery."""
        usable_energy = (self.soc_wh - self.min_soc_wh) * self.discharging_efficiency
        return max(usable_energy, 0.0)
