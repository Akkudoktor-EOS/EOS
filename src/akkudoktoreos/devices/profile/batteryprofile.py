"""Battery profile documents for AkkudoktorEOS device controllers and simulation.

This module defines the schema of a battery profile document: temperature- and
SOC-dependent charge and discharge limit maps, optional C-rate-dependent
efficiency curves, model power limits, technical SOC bounds, an optional
nominal capacity and hard temperature cutoffs. C-rates refer to the effective
nominal capacity: the capacity of the battery device settings if configured,
else the nominal capacity of the profile.

The document models only describe and validate the data. The other concerns
live in their own modules:

- ``batteryprofileruntime``: the ready-to-use ``BatteryProfile`` and the
  algorithms that compile a document into it (reference interpolation and
  precomputation of dense lookup tables).
- ``profilestore``: where documents are stored, loading and saving them,
  and a cache of compiled profiles. ``BatteryProfileSettings`` is the battery
  profile type of the store (kind ``"battery"``).

The classmethods and methods of ``BatteryProfileSettings`` that load, save or
compile documents are kept as thin delegates to these modules. The recommended
entry point is the store::

    from akkudoktoreos.devices.profile.profilestore import get_profile_store

    profile = get_profile_store().get(BatteryProfileSettings, "generic-lfp")
    p_charge = profile.max_charge_crate(temperature_degC=12.0, soc_percent=30.0)
    if p_charge is None:
        ...  # charging must be inhibited at this operating point
"""

from pathlib import Path
from typing import Any, ClassVar, List, Literal, Optional, Union

from pydantic import Field, field_validator, model_validator

from akkudoktoreos.devices.profile.batteryprofileruntime import (
    BatteryProfile as BatteryProfile,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    EfficiencyInterpolation as EfficiencyInterpolation,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    Interpolation as Interpolation,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    OutOfRangeBehavior as OutOfRangeBehavior,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    PrecomputedChargeDischargeLimitMap as PrecomputedChargeDischargeLimitMap,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    PrecomputedEfficiencyCurve as PrecomputedEfficiencyCurve,
)
from akkudoktoreos.devices.profile.batteryprofileruntime import (
    compile_battery_profile,
    precompute_limit_maps,
    reference_efficiency_lookup,
    reference_max_charge_crate,
    reference_max_discharge_crate,
)
from akkudoktoreos.devices.profile.profilestore import (
    ProfileBaseModel as ProfileBaseModel,
)
from akkudoktoreos.devices.profile.profilestore import (
    ProfileDocument,
    get_profile_store,
)


class Units(ProfileBaseModel):
    """Units of the axes and of the limit values in a limit table.

    The units describe how to interpret the numbers in Axes and ChargeDischargeLimitMap.
    They do not affect the interpolation itself; they only tell the controller
    how to convert the resulting limit into power or current before applying
    it.
    """

    temperature: Literal["degC"] = Field(
        default="degC",
        json_schema_extra={
            "description": "Unit of the temperature axis and the hard cutoffs: 'degC'.",
            "examples": ["degC"],
        },
    )
    soc: Literal["percent"] = Field(
        default="percent",
        json_schema_extra={
            "description": "Unit of the SOC axis: 'percent' (0..100).",
            "examples": ["percent"],
        },
    )
    limit: Literal["C-rate", "W", "A"] = Field(
        ...,
        json_schema_extra={
            "description": (
                "Unit of the limit values. 'C-rate' (multiply by nominal capacity for power), "
                "'W' (absolute power) or 'A' (absolute current, needs nominal_voltage_v)."
            ),
            "examples": ["C-rate"],
        },
    )


class EfficiencyCurve(ProfileBaseModel):
    """Battery charge or discharge efficiency as a function of C-rate.

    C-rate is based on the effective nominal battery capacity: the capacity
    configured on the device if set, else the nominal capacity of the profile.
    Efficiency is expressed as a fraction in the range (0, 1].
    """

    description: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Free-form note about the source or meaning of this curve.",
            "examples": ["Charge efficiency of a typical LFP pack"],
        },
    )
    c_rate: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": "Strictly increasing absolute C-rate breakpoints.",
            "examples": [[0.05, 0.1, 0.25, 0.5, 1.0]],
        },
    )
    efficiency: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": "Efficiency fractions corresponding to each C-rate.",
            "examples": [[0.90, 0.94, 0.96, 0.95, 0.91]],
        },
    )
    interpolation: EfficiencyInterpolation = Field(
        default=EfficiencyInterpolation.LINEAR,
        json_schema_extra={"description": "Interpolation between C-rate breakpoints."},
    )
    out_of_range_behavior: OutOfRangeBehavior = Field(
        default=OutOfRangeBehavior.CLAMP_TO_NEAREST,
        json_schema_extra={
            "description": "Clamp to the nearest endpoint or reject out-of-range C-rates."
        },
    )

    @field_validator("c_rate")
    @classmethod
    def valid_c_rates(cls, values: List[float]) -> List[float]:
        """Validate non-negative, strictly increasing C-rate breakpoints."""
        if any(value < 0 for value in values):
            raise ValueError("C-rate breakpoints must be non-negative")
        if any(right <= left for left, right in zip(values, values[1:])):
            raise ValueError("C-rate breakpoints must be strictly increasing")
        return values

    @field_validator("efficiency")
    @classmethod
    def valid_efficiencies(cls, values: List[float]) -> List[float]:
        """Validate efficiency fractions in the interval (0, 1]."""
        if any(not 0.0 < value <= 1.0 for value in values):
            raise ValueError("efficiency values must be in the range (0, 1]")
        return values

    @model_validator(mode="after")
    def matching_lengths(self) -> "EfficiencyCurve":
        """Ensure each C-rate breakpoint has exactly one efficiency value."""
        if len(self.c_rate) != len(self.efficiency):
            raise ValueError("c_rate and efficiency must contain the same number of values")
        return self

    def lookup(self, c_rate: float) -> Optional[float]:
        """Return the interpolated efficiency at an absolute C-rate.

        Reference implementation, see
        ``batteryprofileruntime.reference_efficiency_lookup``. Runtime queries
        use ``PrecomputedEfficiencyCurve.lookup``.
        """
        return reference_efficiency_lookup(self, c_rate)


class Axes(ProfileBaseModel):
    """Breakpoints of the temperature and SOC axes of a limit table.

    Both axes must be strictly increasing. The rows of a ChargeDischargeLimitMap align with
    temperature_degC and its columns align with soc_percent.
    """

    temperature_degC: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": (
                "Strictly increasing temperature breakpoints in degrees Celsius. "
                "Rows of the limit maps align with these values."
            ),
            "examples": [[-10, -5, 0, 5, 10, 15, 25, 35, 45, 50]],
        },
    )
    soc_percent: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": (
                "Strictly increasing SOC breakpoints in percent (0..100). "
                "Columns of the limit maps align with these values."
            ),
            "examples": [[0, 5, 10, 20, 40, 60, 80, 90, 95, 100]],
        },
    )

    @field_validator("temperature_degC", "soc_percent")
    @classmethod
    def strictly_increasing(cls, v: List[float]) -> List[float]:
        """Validate that an axis is strictly increasing.

        Args:
            v: Axis breakpoints, in order.

        Returns:
            The unchanged breakpoints if the axis is strictly increasing.

        Raises:
            ValueError: If any breakpoint is not greater than its predecessor.
        """
        if any(b <= a for a, b in zip(v, v[1:])):
            raise ValueError("axis values must be strictly increasing")
        return v

    @field_validator("soc_percent")
    @classmethod
    def soc_in_range(cls, v: List[float]) -> List[float]:
        """Validate that the SOC axis lies within [0, 100].

        Args:
            v: SOC breakpoints in percent.

        Returns:
            The unchanged breakpoints if they lie within [0, 100].

        Raises:
            ValueError: If the first breakpoint is below 0 or the last is
                above 100.
        """
        if v[0] < 0 or v[-1] > 100:
            raise ValueError("soc_percent must lie within [0, 100]")
        return v


class ChargeDischargeLimitMap(ProfileBaseModel):
    """Two-dimensional grid of non-negative charge or discharge limits.

    The grid is indexed by temperature along rows and by SOC along columns,
    matching the breakpoints of Axes. Values are in the unit declared by
    Units.
    """

    description: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Free-form note about what this map represents.",
            "examples": ["Max charge C-rate; rows = temperature, cols = SOC"],
        },
    )
    values: List[List[float]] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": (
                "2D grid of non-negative limit values. Rows align with "
                "axes.temperature_degC, columns with axes.soc_percent."
            ),
            "examples": [
                [
                    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.10, 0.10, 0.15, 0.20, 0.25, 0.25, 0.15, 0.08, 0.03, 0.0],
                    [0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 0.25, 0.10, 0.0],
                ]
            ],
        },
    )

    @field_validator("values")
    @classmethod
    def non_negative(cls, v: List[List[float]]) -> List[List[float]]:
        """Validate that all limit values are non-negative.

        Args:
            v: Two-dimensional grid of limit values.

        Returns:
            The unchanged grid if every value is >= 0.

        Raises:
            ValueError: If any value in the grid is negative.
        """
        for row in v:
            if any(x < 0 for x in row):
                raise ValueError("limit values must be non-negative")
        return v


class HardCutoffs(ProfileBaseModel):
    """Absolute temperature thresholds that override the limit maps.

    Outside these bounds the controller must inhibit the operation entirely,
    regardless of the interpolated limit map value. Charge and discharge
    bounds are independent and may differ.
    """

    description: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Free-form note about the cutoffs, e.g. their source.",
            "examples": ["Cell manufacturer data sheet"],
        },
    )
    charge_allowed_below_degC: float = Field(
        ...,
        json_schema_extra={
            "description": "Charging is inhibited strictly below this temperature.",
            "examples": [-10.0],
        },
    )
    charge_allowed_above_degC: float = Field(
        ...,
        json_schema_extra={
            "description": "Charging is inhibited strictly above this temperature.",
            "examples": [55.0],
        },
    )
    discharge_allowed_below_degC: float = Field(
        ...,
        json_schema_extra={
            "description": "Discharging is inhibited strictly below this temperature.",
            "examples": [-15.0],
        },
    )
    discharge_allowed_above_degC: float = Field(
        ...,
        json_schema_extra={
            "description": "Discharging is inhibited strictly above this temperature.",
            "examples": [60.0],
        },
    )

    @model_validator(mode="after")
    def ordered(self) -> "HardCutoffs":
        """Validate that each lower cutoff is below its corresponding upper one.

        Returns:
            The validated instance.

        Raises:
            ValueError: If a charge or discharge lower bound is not strictly
                below its upper bound.
        """
        if self.charge_allowed_below_degC >= self.charge_allowed_above_degC:
            raise ValueError("charge cutoff lower bound must be < upper bound")
        if self.discharge_allowed_below_degC >= self.discharge_allowed_above_degC:
            raise ValueError("discharge cutoff lower bound must be < upper bound")
        return self


class BatteryProfileSettings(ProfileBaseModel, ProfileDocument[BatteryProfile]):
    """Temperature- and SOC-dependent charge/discharge limits for a battery.

    This profile holds model-specific operating characteristics: charge and
    discharge power-limit maps, efficiency curves, technical SOC bounds, hard
    temperature cutoffs, optional absolute power limits, an optional nominal
    voltage and the nominal capacity. It is the configuration object read by an
    AkkudoktorEOS device controller to constrain a battery or hybrid inverter at
    the current operating point, and by the simulation to model the same limits.

    A profile contains every value the computation uses. The one exception is
    the nominal capacity of a generic profile: its limits are C-rates that scale
    with any capacity, so it may leave the capacity out. The battery device then
    has to provide the capacity in its ``profile_adjustment``. Unknown keys are
    rejected.

    The caller should not query this document directly. Use
    ``get_profile_store().get(BatteryProfileSettings, name)`` (or the delegate
    ``from_profile_name``) to obtain a precomputed BatteryProfile object instead.

    Attributes:
        schema_version: Version of the JSON document format.
        battery_model: Model name of the battery pack.
        chemistry: Cell chemistry, if relevant for interpretation.
        nominal_voltage_v: Nominal voltage, if known.
        nominal_capacity_wh: Nominal capacity of the battery model, None for a
            generic profile.
        max_charge_power_w: Optional absolute model charge-power limit.
        max_discharge_power_w: Optional absolute model discharge-power limit.
        technical_min_soc_percent: Manufacturer-defined lower SOC limit.
        technical_max_soc_percent: Manufacturer-defined upper SOC limit.
        full_charge_interval_days: Maximum number of days between two full
            charges (SoC recalibration), None if not needed or unknown.
        full_charge_detect_percentage: Reported SOC from which the battery
            counts as fully charged.
        charge_efficiency: Charge-efficiency curve versus C-rate.
        discharge_efficiency: Discharge-efficiency curve versus C-rate.
        units: Units of the axes and of the limit values.
        interpolation: Interpolation strategy between grid points.
        out_of_range_behavior: Behavior for queries outside the axis range
            but inside the hard cutoffs.
        axes: Shared axis breakpoints.
        charge_limit_crate: Grid of maximum charge limits.
        discharge_limit_crate: Grid of maximum discharge limits.
        hard_cutoffs: Absolute thresholds that override the maps.
    """

    profile_kind: ClassVar[str] = "battery"

    schema_version: str = Field(
        default="1.1",
        json_schema_extra={
            "description": "Version of this schema, for forward-compatibility.",
            "examples": ["1.1"],
        },
    )
    battery_model: str = Field(
        ...,
        min_length=1,
        json_schema_extra={
            "description": "Model name of the battery pack.",
            "examples": ["BYD Battery-Box Premium HVS"],
        },
    )
    chemistry: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Cell chemistry, if relevant for interpretation.",
            "examples": ["LFP"],
        },
    )
    nominal_voltage_v: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Nominal voltage, used to convert power to current.",
            "examples": [102.4],
        },
    )
    nominal_capacity_wh: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Nominal capacity of this battery model [Wh]. Required for the profile "
                "of a specific battery model. A generic profile, whose limits are "
                "C-rates, leaves it out; the battery device then has to set "
                "'profile_adjustment.nominal_capacity_wh'."
            ),
            "examples": [None, 10240.0],
        },
    )
    max_charge_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Optional absolute maximum charge power for this battery model. "
                "The effective limit should also respect the installed inverter/device limit."
            ),
            "examples": [5000.0],
        },
    )
    max_discharge_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Optional absolute maximum discharge power for this battery model. "
                "The effective limit should also respect the installed inverter/device limit."
            ),
            "examples": [5000.0],
        },
    )
    technical_min_soc_percent: float = Field(
        ...,
        ge=0,
        le=100,
        json_schema_extra={
            "description": (
                "Manufacturer-defined technical minimum SOC [%], not the user's "
                "operational reserve setting."
            ),
            "examples": [5.0],
        },
    )
    technical_max_soc_percent: float = Field(
        ...,
        ge=0,
        le=100,
        json_schema_extra={
            "description": (
                "Manufacturer-defined technical maximum SOC [%], not the user's "
                "operational target setting."
            ),
            "examples": [95.0],
        },
    )
    full_charge_interval_days: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Maximum number of days between two full charges, e.g. the interval "
                "the battery management system needs to recalibrate its state of "
                "charge (typical for LFP) [d]. None if the battery needs no periodic "
                "full charge or the interval is unknown."
            ),
            "examples": [None, 21.0],
        },
    )
    full_charge_detect_percentage: int = Field(
        default=99,
        ge=1,
        le=100,
        json_schema_extra={
            "description": (
                "Reported state of charge from which the battery management system "
                "counts as fully charged [%]. Many never report exactly 100 %."
            ),
            "examples": [99],
        },
    )
    charge_efficiency: EfficiencyCurve = Field(
        ...,
        json_schema_extra={"description": "Charge efficiency curve versus absolute C-rate."},
    )
    discharge_efficiency: EfficiencyCurve = Field(
        ...,
        json_schema_extra={"description": "Discharge efficiency curve versus absolute C-rate."},
    )
    units: Units = Field(
        ...,
        json_schema_extra={
            "description": "Units of axes and limit values.",
        },
    )
    interpolation: Interpolation = Field(
        default=Interpolation.BILINEAR,
        json_schema_extra={
            "description": (
                "How to interpolate between grid points: 'bilinear', 'nearest' "
                "or 'none' (exact grid points only)."
            ),
            "examples": ["bilinear"],
        },
    )
    out_of_range_behavior: OutOfRangeBehavior = Field(
        default=OutOfRangeBehavior.CLAMP_TO_NEAREST,
        json_schema_extra={
            "description": (
                "What to do when a query lies outside the axis range but inside "
                "the hard cutoffs: 'clamp_to_nearest' or 'reject'."
            ),
            "examples": ["clamp_to_nearest"],
        },
    )
    axes: Axes = Field(
        ...,
        json_schema_extra={"description": "Axis breakpoints for both dimensions."},
    )
    charge_limit_crate: ChargeDischargeLimitMap = Field(
        ...,
        json_schema_extra={"description": "Maximum charge limit map."},
    )
    discharge_limit_crate: ChargeDischargeLimitMap = Field(
        ...,
        json_schema_extra={"description": "Maximum discharge limit map."},
    )
    hard_cutoffs: HardCutoffs = Field(
        ...,
        json_schema_extra={"description": "Absolute protection thresholds."},
    )

    @model_validator(mode="after")
    def current_limits_need_voltage(self) -> "BatteryProfileSettings":
        """Validate that current limits ('A') come with the nominal voltage.

        Without it the limits cannot be converted to power.

        Returns:
            The validated instance.

        Raises:
            ValueError: If units.limit is 'A' and nominal_voltage_v is not set.
        """
        if self.units.limit == "A" and self.nominal_voltage_v is None:
            raise ValueError("units.limit 'A' needs nominal_voltage_v to convert currents to power")
        return self

    @model_validator(mode="after")
    def grids_match_axes(self) -> "BatteryProfileSettings":
        """Validate that both limit maps match the shape of the shared axes.

        The number of rows of each map must equal the number of temperature
        breakpoints, and the number of columns must equal the number of SOC
        breakpoints.

        Returns:
            The validated instance.

        Raises:
            ValueError: If either map's shape does not match the axes.
        """
        n_temp = len(self.axes.temperature_degC)
        n_soc = len(self.axes.soc_percent)
        for name, grid in (
            ("charge_limit_crate", self.charge_limit_crate.values),
            ("discharge_limit_crate", self.discharge_limit_crate.values),
        ):
            if len(grid) != n_temp:
                raise ValueError(f"{name}: expected {n_temp} rows (temperature), got {len(grid)}")
            for i, row in enumerate(grid):
                if len(row) != n_soc:
                    raise ValueError(
                        f"{name}: row {i} expected {n_soc} columns (SOC), got {len(row)}"
                    )
        if self.technical_min_soc_percent >= self.technical_max_soc_percent:
            raise ValueError(
                "technical_min_soc_percent must be less than technical_max_soc_percent"
            )
        return self

    # --- storage (delegates to profilestore) ------------------------------

    def save_profile(self, name: str) -> Path:
        """Save this document to the user data directory as JSON.

        Saved as ``<name>.json``, see ``ProfileStore.save``. The profile name is
        independent of ``battery_model``, which is free text.

        Args:
            name: Profile name, see ``ProfileStore`` for the allowed characters.

        Returns:
            The path the profile was written to.

        Raises:
            ValueError: If the name is invalid or reserved.
            OSError: If the file or its parent directories cannot be written.
        """
        return get_profile_store().save(name, self)

    @classmethod
    def load_profile(cls, name: str) -> "BatteryProfileSettings":
        """Load the validated document of a profile by name, not precomputed.

        See ``ProfileStore.load``.

        Args:
            name: Profile name, without the ``.json`` suffix.

        Returns:
            The validated BatteryProfileSettings.

        Raises:
            ValueError: If the name is invalid or the document fails validation.
            FileNotFoundError: If no JSON document exists for the given name.
        """
        return get_profile_store().load(cls, name)

    @classmethod
    def from_profile_name(
        cls,
        name: str,
        t_step: float = 1.0,
        s_step: float = 1.0,
    ) -> BatteryProfile:
        """Return the compiled profile of a profile name.

        See ``ProfileStore.get``; the result is cached.

        Args:
            name: Profile name, without the ``.json`` suffix.
            t_step: Spacing of the precomputed temperature axis [degC].
            s_step: Spacing of the precomputed SOC axis [%].

        Returns:
            A ready-to-use BatteryProfile object.

        Raises:
            FileNotFoundError: If no JSON document exists for the given name.
            ValueError: If the document fails validation, or if t_step or
                s_step is not positive.
        """
        return get_profile_store().get(cls, name, t_step=t_step, s_step=s_step)

    @classmethod
    def from_json_file(
        cls,
        path: Union[str, Path],
        t_step: float = 1.0,
        s_step: float = 1.0,
    ) -> BatteryProfile:
        """Load a JSON document from an explicit path and compile it (not cached).

        Intended for configuration overrides, tests and tooling.

        Args:
            path: Path to the JSON document.
            t_step: Spacing of the precomputed temperature axis [degC].
            s_step: Spacing of the precomputed SOC axis [%].

        Returns:
            A ready-to-use BatteryProfile object.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the document fails validation, or if t_step or
                s_step is not positive.
        """
        document = get_profile_store().load_file(cls, path)
        return compile_battery_profile(document, t_step=t_step, s_step=s_step)

    # --- compilation (delegates to batteryprofileruntime) -----------------

    def compile_profile(
        self, t_step: float = 1.0, s_step: float = 1.0, **options: Any
    ) -> BatteryProfile:
        """Compile this document for the profile store, see ``to_limits``.

        Raises:
            TypeError: If an unknown option is given.
        """
        if options:
            raise TypeError(f"Unknown battery profile options: {sorted(options)}")
        return compile_battery_profile(self, t_step=t_step, s_step=s_step)

    def to_limits(self, t_step: float = 1.0, s_step: float = 1.0) -> BatteryProfile:
        """Compile this document into a ready-to-use BatteryProfile.

        See ``batteryprofileruntime.compile_battery_profile``.
        """
        return compile_battery_profile(self, t_step=t_step, s_step=s_step)

    def precompute_charge_discharge_limit_map(
        self,
        t_step: float = 1.0,
        s_step: float = 1.0,
        apply_cutoffs: bool = True,
    ) -> tuple[PrecomputedChargeDischargeLimitMap, PrecomputedChargeDischargeLimitMap]:
        """Resample both limit maps onto dense, uniformly spaced grids.

        See ``batteryprofileruntime.precompute_limit_maps``.
        """
        return precompute_limit_maps(
            self, t_step=t_step, s_step=s_step, apply_cutoffs=apply_cutoffs
        )

    def max_charge_crate(self, temperature_degC: float, soc_percent: float) -> Optional[float]:
        """Return the maximum charge limit by slow per-query interpolation.

        Reference implementation; controllers use ``BatteryProfile.max_charge_crate``.
        See ``batteryprofileruntime.reference_max_charge_crate``.
        """
        return reference_max_charge_crate(self, temperature_degC, soc_percent)

    def max_discharge_crate(self, temperature_degC: float, soc_percent: float) -> Optional[float]:
        """Return the maximum discharge limit by slow per-query interpolation.

        Reference implementation; controllers use ``BatteryProfile.max_discharge_crate``.
        See ``batteryprofileruntime.reference_max_discharge_crate``.
        """
        return reference_max_discharge_crate(self, temperature_degC, soc_percent)
