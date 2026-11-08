"""Battery and electric vehicle device settings.

Note: Used for the GENETIC and GENETIC0 algorithm.

The technical battery data (capacity, power limits, efficiencies, technical
SOC bounds) comes from a battery profile (see BatteryProfileSettings). The user
selects the profile by name via ``battery_profile`` (default ``generic-lfp``);
there is always a profile.

Responsibilities
----------------

``BatteriesCommonSettings`` holds the configuration fields, their validation and
the measurement keys. The other concerns live in their own modules, the
settings methods delegate to them:

- ``profilestore``: loading (and caching) the compiled battery profile.
- ``batterymeasurement``: reading the operating point from the measurements.
- ``batteryresolution``: resolving the effective parameters from profile,
  profile adjustment and operating point (pure functions).

Configuration fields versus computed fields
-------------------------------------------

Configuration fields are inputs, in three groups:

- The battery profile: ``battery_profile`` names the profile, and
  ``profile_adjustment`` holds how this unit differs from it: its nominal
  capacity (replaces the profile capacity; required for a generic profile) and
  the charge / discharge power limits of the installation (can only lower the
  profile limits), and the periodic full charge interval and detection
  threshold (replace the profile values).
- Operating policy: ``min_soc_percentage`` / ``max_soc_percentage`` (kept within
  the technical SOC bounds of the profile), ``charge_rates``,
  ``min_charge_power_w``, ``levelized_cost_of_storage_amt_kwh``, the minimum SoC
  deadline, the grid export rates and the periodic full charge target
  (``full_charge_soc_percentage``, ``full_charge_allow_grid``).
- Runtime state: ``capacity_estimation`` / ``capacity_estimate``.

Computed fields are outputs, all named ``effective_*``. They are evaluated on
every access (pydantic does not cache them and does not evaluate them while
validating or constructing the model). They depend on the configuration and the
battery profile only, never on measurements, and are evaluated at the reference
operating point (``BatteryOperatingPoint()``: 25 degC, 50 % SoC, no measured
power):

- ``effective_nominal_capacity_wh``: profile adjustment, else profile.
- ``effective_max_charge_power_w`` / ``effective_max_discharge_power_w``: profile
  limits at the reference operating point, capped by the profile adjustment.
- ``effective_charging_efficiency`` / ``effective_discharging_efficiency``: profile
  efficiency curve at the maximum charge / discharge power.
- ``effective_min_soc_percentage`` / ``effective_max_soc_percentage``.
- ``effective_full_charge_interval_days`` / ``effective_full_charge_detect_percentage``:
  profile adjustment, else profile (``resolve_full_charge``).

Because pydantic also evaluates computed fields on every serialization
(``model_dump``, ``model_dump_json``), they never raise: they are None if the
battery cannot be resolved, e.g. the profile does not exist. The strict methods
below raise a ``BatteryResolutionError`` naming the battery instead, and
``check_profile`` reports the problem.

Measurements versus resolution
------------------------------

- ``measure_operating_point`` (async) reads temperature, SoC and charge /
  discharge power (``<device_id>-charge-power-w`` / ``<device_id>-discharge-power-w``)
  from the measurements and returns a ``BatteryOperatingPoint``.
- ``resolve_operating_parameters`` (sync, strict) evaluates the profile at a given
  operating point, the reference operating point by default.
- ``resolve_current_operating_parameters`` (async, strict) combines both.
- The optimizer conversions (``to_*_param``) take a resolved snapshot, so async
  callers resolve once at the measured operating point and convert.
"""

import re
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field, field_validator, model_validator

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel
from akkudoktoreos.devices.settings import batterymeasurement
from akkudoktoreos.devices.settings.batterymeasurement import (
    BATTERY_MEASUREMENT_MAX_AGE as BATTERY_MEASUREMENT_MAX_AGE,
)
from akkudoktoreos.devices.settings.batteryresolution import (
    BATTERY_DEFAULT_SOC_FACTOR as BATTERY_DEFAULT_SOC_FACTOR,
)
from akkudoktoreos.devices.settings.batteryresolution import (
    BATTERY_DEFAULT_TEMPERATURE_DEGC as BATTERY_DEFAULT_TEMPERATURE_DEGC,
)
from akkudoktoreos.devices.settings.batteryresolution import (
    BATTERY_FALLBACK_CAPACITY_WH,
    BatteryFullCharge,
    BatteryOperatingParameters,
    BatteryOperatingPoint,
    BatteryOperatingPointError,
    BatteryPowerCurves,
    BatteryResolutionError,
    BatterySocRangeError,
    profile_peak_power_w,
    resolve_battery_parameters,
    resolve_battery_parameters_without_profile,
    resolve_battery_power_curves,
    resolve_full_charge,
    resolve_nominal_capacity_wh,
)
from akkudoktoreos.devices.settings.devicebasesettings import DevicesBaseSettings
from akkudoktoreos.measurement.batterycapacity import (
    BatteryCapacityEstimate,
    BatteryCapacityEstimationSettings,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

if TYPE_CHECKING:
    from akkudoktoreos.devices.genetic0.genetic0battery import (
        Genetic0ElectricVehicleParameters,
        Genetic0SolarPanelBatteryParameters,
    )
    from akkudoktoreos.devices.genetic.battery import (
        ElectricVehicleParameters,
        SolarPanelBatteryParameters,
    )
    from akkudoktoreos.devices.profile.batteryprofileruntime import BatteryProfile


# Default charge rates for battery
BATTERY_DEFAULT_CHARGE_RATES: list[float] = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


BATTERY_DEFAULT_GRID_EXPORT_RATES: list[float] = [0.25, 0.5, 0.75, 1.0]

# Name of the battery profile used when the user selects none.
BATTERY_DEFAULT_PROFILE: str = "generic-lfp"


class BatteryProfileAdjustment(SettingsBaseModel):
    """How a battery unit differs from its battery profile.

    Each field is named after the profile field it adjusts. None keeps the
    profile value.
    """

    nominal_capacity_wh: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Nominal capacity of the installed battery [Wh]. Replaces the capacity "
                "of the battery profile. Required if the profile is generic and "
                "provides no capacity. The effective value is available as "
                "'effective_nominal_capacity_wh'."
            ),
            "examples": [None, 8000],
        },
    )
    max_charge_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum charging power of the installation, e.g. the inverter limit [W]. "
                "Can only lower the limits of the battery profile. The effective value "
                "is available as 'effective_max_charge_power_w'."
            ),
            "examples": [None, 5000],
        },
    )
    max_discharge_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum discharging power of the installation, e.g. the inverter limit "
                "[W]. Can only lower the limits of the battery profile. The effective "
                "value is available as 'effective_max_discharge_power_w'."
            ),
            "examples": [None, 5000],
        },
    )
    full_charge_interval_days: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Maximum number of days between two full charges of a stationary "
                "battery [d]. Replaces the interval of the battery profile. When the "
                "next full charge falls due within the optimization horizon, the "
                "maximum state of charge is raised to 'full_charge_soc_percentage'. "
                "The periodic full charge is disabled if neither this nor the profile "
                "sets an interval. Ignored for electric vehicles. The effective value "
                "is available as 'effective_full_charge_interval_days'."
            ),
            "examples": [None, 21],
        },
    )
    full_charge_detect_percentage: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Measured state of charge from which the battery counts as fully "
                "charged [%]. Replaces the threshold of the battery profile. The "
                "effective value is available as "
                "'effective_full_charge_detect_percentage'."
            ),
            "examples": [None, 98],
        },
    )


class BatteriesCommonSettings(DevicesBaseSettings):
    """Battery and electric vehicle device settings.

    Used for both stationary batteries and EV battery packs.

    Note: Used for the GENETIC and GENETIC0 algorithm.
    """

    capacity_estimation: Optional[BatteryCapacityEstimationSettings] = None
    capacity_estimate: Optional[BatteryCapacityEstimate] = Field(
        default=None,
        description=(
            "Last explicitly stored capacity estimate; does not override the "
            "effective nominal capacity."
        ),
    )

    battery_profile: str = Field(
        default=BATTERY_DEFAULT_PROFILE,
        min_length=1,
        json_schema_extra={
            "description": (
                "Name of the battery profile (without '.json') providing the nominal "
                "capacity, power limits, efficiency curves and technical SOC bounds. "
                "Differences of this unit go into 'profile_adjustment'."
            ),
            "examples": [BATTERY_DEFAULT_PROFILE],
        },
    )
    profile_adjustment: BatteryProfileAdjustment = Field(
        default_factory=BatteryProfileAdjustment,
        json_schema_extra={
            "description": (
                "How this battery unit differs from its battery profile: nominal "
                "capacity, installation power limits and periodic full charge."
            ),
            "examples": [{"nominal_capacity_wh": 8000, "max_charge_power_w": 5000}],
        },
    )
    levelized_cost_of_storage_amt_kwh: float = Field(
        default=0.0,
        json_schema_extra={
            "description": (
                "Levelized cost of storage (LCOS), the average lifetime cost "
                "of delivering one kWh [€/kWh]."
            ),
            "examples": [0.12],
        },
    )
    min_charge_power_w: Optional[float] = Field(
        default=50,
        gt=0,
        json_schema_extra={
            "description": "Minimum charging power [W].",
            "examples": [50],
        },
    )
    charge_rates: Optional[list[float]] = Field(
        default=BATTERY_DEFAULT_CHARGE_RATES,
        json_schema_extra={
            "description": (
                "Charge rates as factor of maximum charging power [0.00 ... 1.00]. "
                "None triggers fallback to default charge-rates."
            ),
            "examples": [[0.0, 0.25, 0.5, 0.75, 1.0], None],
        },
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        json_schema_extra={
            "description": (
                "Minimum state of charge (SOC) as percentage of capacity [%]. "
                "This is the target SoC for charging. Raised to the technical minimum "
                "SOC of the battery profile if that is higher."
            ),
            "examples": [10],
        },
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        json_schema_extra={
            "description": (
                "Maximum state of charge (SOC) as percentage of capacity [%]. "
                "Lowered to the technical maximum SOC of the battery profile if that "
                "is lower."
            ),
            "examples": [100],
        },
    )

    # ------------------------------------------------------------------
    # Battery profile loading (see profilestore)
    # ------------------------------------------------------------------

    def get_battery_profile(self) -> "BatteryProfile":
        """Return the compiled battery profile for ``battery_profile``.

        The profile is loaded lazily (not at validation time, so configuration
        loading does not depend on the data directories). The profile store
        caches the compiled profile and recompiles it when the file changes.

        Returns:
            The ready-to-use profile.

        Raises:
            FileNotFoundError: If the configured battery profile does not exist.
            ValueError: If the profile document fails validation.
        """
        from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings
        from akkudoktoreos.devices.profile.profilestore import get_profile_store

        return get_profile_store().get(BatteryProfileSettings, self.battery_profile)

    def _load_battery_profile(self) -> "BatteryProfile":
        """Return the compiled battery profile, or raise an error naming the battery.

        Raises:
            BatteryResolutionError: If the profile does not exist or fails
                validation.
        """
        try:
            return self.get_battery_profile()
        except BatteryResolutionError:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise BatteryResolutionError(
                f"Battery '{self.device_id}': battery profile '{self.battery_profile}' "
                f"is not usable: {exc}"
            ) from exc

    def resolve_power_curves(
        self, operating_parameters: BatteryOperatingParameters
    ) -> Optional[BatteryPowerCurves]:
        """Evaluate the battery profile over the whole SoC range.

        The profile is evaluated at the temperature of the operating parameters,
        i.e. the measured battery temperature or the default temperature if
        there is no measurement.

        Args:
            operating_parameters: Resolved snapshot providing temperature and
                capacity.

        Returns:
            The power curves, or None if the profile allows no charging at any
            SoC at this temperature.

        Raises:
            BatteryResolutionError: If the profile cannot be loaded or its
                limits cannot be converted to power.
        """
        return self._power_curves(operating_parameters, self._load_battery_profile())

    def _power_curves(
        self, operating_parameters: BatteryOperatingParameters, profile: "BatteryProfile"
    ) -> Optional[BatteryPowerCurves]:
        """Evaluate a loaded profile like ``resolve_power_curves``."""
        try:
            return resolve_battery_power_curves(
                self,
                profile,
                operating_parameters.capacity_wh,
                operating_parameters.temperature_degC,
            )
        except BatteryResolutionError:
            raise
        except ValueError as exc:
            raise BatteryResolutionError(f"Battery '{self.device_id}': {exc}") from exc

    def _genetic_snapshot(
        self, operating_parameters: Optional[BatteryOperatingParameters], use_profile_tables: bool
    ) -> tuple[BatteryOperatingParameters, Optional["BatteryProfile"]]:
        """Return the snapshot to convert and the profile, loading the profile once.

        Without profile tables, the given snapshot is converted as is and no
        profile is loaded.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        if not use_profile_tables:
            if operating_parameters is None:
                raise ValueError("Converting without profile tables requires a resolved snapshot.")
            return operating_parameters, None
        profile = self._load_battery_profile()
        if operating_parameters is not None:
            return operating_parameters, profile
        capacity_wh = resolve_nominal_capacity_wh(self, profile)
        return (
            resolve_battery_parameters(self, profile, capacity_wh, BatteryOperatingPoint()),
            profile,
        )

    def _genetic_power_parameters(
        self, p: BatteryOperatingParameters, profile: Optional["BatteryProfile"]
    ) -> dict[str, Any]:
        """Return the power and efficiency fields of the GENETIC battery parameters.

        With power curves from the profile, the rated powers are the highest
        powers over SoC; the per-SoC tables limit them in the simulation.
        Without (no profile, or the profile allows no charging at this
        temperature), the powers resolved at the operating point apply.
        """
        curves = self._power_curves(p, profile) if profile is not None else None
        if curves is None:
            return {
                "max_charge_power_w": p.max_charge_power_w,
                "max_discharge_power_w": p.max_discharge_power_w,
            }
        return {
            "max_charge_power_w": curves.max_charge_power_w,
            "max_discharge_power_w": curves.max_discharge_power_w,
            "charge_power_limit_w_by_soc": curves.charge_power_limit_w_by_soc,
            "discharge_power_limit_w_by_soc": curves.discharge_power_limit_w_by_soc,
            "charging_efficiency_curve": curves.charging_efficiency_curve,
            "discharging_efficiency_curve": curves.discharging_efficiency_curve,
        }

    # ------------------------------------------------------------------
    # Measurement access (see batterymeasurement)
    # ------------------------------------------------------------------

    async def measure_operating_point(
        self, target_datetime: Optional[DateTime] = None
    ) -> BatteryOperatingPoint:
        """Read the current operating point from the battery measurements.

        Missing, non-finite or unavailable measurements fall back to the
        reference operating point (25 degC, SoC 0.5, no power); the SoC factor
        is clamped to [0.0 ... 1.0]. This method never raises for missing
        measurements.

        Args:
            target_datetime: Datetime the measurements are looked up for, now if None.

        Returns:
            The measured operating point.
        """
        return await batterymeasurement.measure_operating_point(self, target_datetime)

    # ------------------------------------------------------------------
    # Resolution (see batteryresolution)
    # ------------------------------------------------------------------

    def resolve_nominal_capacity_wh(self) -> int:
        """Return the effective nominal capacity [Wh].

        Order: ``profile_adjustment.nominal_capacity_wh``, then the capacity of
        the battery profile.

        Raises:
            BatteryResolutionError: If the profile cannot be loaded, or it is
                generic and the profile adjustment provides no capacity.
        """
        return resolve_nominal_capacity_wh(self, self._load_battery_profile())

    def resolve_operating_parameters(
        self, operating_point: Optional[BatteryOperatingPoint] = None
    ) -> BatteryOperatingParameters:
        """Resolve the effective battery parameters as one snapshot.

        Synchronous and deterministic: measurements are not read here. Use
        ``resolve_current_operating_parameters`` for the measured operating point.
        The efficiencies are taken at the charge / discharge power of the
        operating point, or at the maximum power if there is none.

        Args:
            operating_point: Operating point to evaluate the profile at, the
                reference operating point ``BatteryOperatingPoint()`` if None.

        Returns:
            The effective parameters.

        Raises:
            BatteryResolutionError: If the profile cannot be loaded, provides no
                capacity, inhibits charging at the operating point, yields a
                non-positive charge power, or the technical SOC bounds leave an
                empty SOC range.
        """
        if operating_point is None:
            operating_point = BatteryOperatingPoint()
        profile = self._load_battery_profile()
        capacity_wh = resolve_nominal_capacity_wh(self, profile)
        return resolve_battery_parameters(self, profile, capacity_wh, operating_point)

    async def resolve_current_operating_parameters(
        self, target_datetime: Optional[DateTime] = None
    ) -> BatteryOperatingParameters:
        """Resolve the effective battery parameters at the measured operating point.

        Reads the operating point once (see ``measure_operating_point``) and
        resolves all parameters from it, so the snapshot is consistent even if
        measurements change meanwhile.

        Args:
            target_datetime: Datetime the measurements are looked up for, now if None.

        Returns:
            The effective parameters.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        operating_point = await self.measure_operating_point(target_datetime)
        return self.resolve_operating_parameters(operating_point)

    def resolve_operating_parameters_or_defaults(
        self, operating_point: Optional[BatteryOperatingPoint] = None, report: bool = True
    ) -> tuple[BatteryOperatingParameters, bool]:
        """Resolve the parameters for the optimization, with defaults for configuration errors.

        The parameter preparation of the optimizations always succeeds for a
        configuration error. Whatever the configuration allows is resolved from
        the battery profile and the profile adjustment; only the values that
        cannot be resolved are filled with the former defaults:

        - The profile cannot be used (missing, invalid, limits not convertible
          to power): the profile adjustment where set, else 8000 Wh, 5000 W
          charge and discharge power, efficiencies 0.88, no SoC dependent power
          limits.
        - A generic profile without capacity in the profile adjustment: the
          capacity 8000 Wh.
        - A configured SoC range outside the technical SoC bounds of the profile:
          the technical SoC range of the profile.

        Args:
            operating_point: Operating point to evaluate the profile at, the
                reference operating point if None.
            report: Whether to log an error that names the battery, the problem
                and the default values used.

        Returns:
            ``(parameters, use_profile_tables)``. ``use_profile_tables`` is
            False if the parameters were resolved without profile; pass it to
            the GENETIC conversions.

        Raises:
            BatteryOperatingPointError: If the profile inhibits charging at the
                operating point. This is a state of the battery, not a
                configuration error.
        """
        if operating_point is None:
            operating_point = BatteryOperatingPoint()
        problems: list[str] = []
        defaults: list[str] = []
        parameters: Optional[BatteryOperatingParameters] = None
        use_profile_tables = True
        try:
            profile = self._load_battery_profile()
            try:
                capacity_wh = resolve_nominal_capacity_wh(self, profile)
            except BatteryResolutionError as exc:
                problems.append(str(exc))
                capacity_wh = BATTERY_FALLBACK_CAPACITY_WH
                defaults.append(f"nominal capacity {capacity_wh} Wh")
            try:
                parameters = resolve_battery_parameters(self, profile, capacity_wh, operating_point)
            except BatterySocRangeError as exc:
                problems.append(str(exc))
                full_range = self.model_copy(
                    update={"min_soc_percentage": 0, "max_soc_percentage": 100}
                )
                parameters = resolve_battery_parameters(
                    full_range, profile, capacity_wh, operating_point
                )
                defaults.append(
                    f"SoC range {parameters.min_soc_percentage} ... "
                    f"{parameters.max_soc_percentage} % (technical bounds of the profile)"
                )
        except BatteryOperatingPointError:
            raise
        except BatteryResolutionError as exc:
            problems.append(str(exc))
            parameters, defaults = resolve_battery_parameters_without_profile(self, operating_point)
            use_profile_tables = False
        if problems and report:
            logger.error(
                "{} Using defaults for the optimization: {}.",
                " ".join(problems),
                ", ".join(defaults),
            )
        return parameters, use_profile_tables

    async def resolve_current_operating_parameters_or_defaults(
        self, target_datetime: Optional[DateTime] = None, report: bool = True
    ) -> tuple[BatteryOperatingParameters, bool]:
        """Resolve like ``resolve_operating_parameters_or_defaults`` at the measured operating point.

        Args:
            target_datetime: Datetime the measurements are looked up for, now if None.
            report: Whether to log the configuration error and the defaults used.

        Returns:
            ``(parameters, use_profile_tables)``.

        Raises:
            BatteryOperatingPointError: If the profile inhibits charging at the
                measured operating point.
        """
        operating_point = await self.measure_operating_point(target_datetime)
        return self.resolve_operating_parameters_or_defaults(operating_point, report)

    def check_profile(self) -> tuple[list[str], list[str]]:
        """Check that the battery can be resolved from its profile and adjustment.

        Resolves the battery at the reference operating point. Also checks
        that every power limit of the profile adjustment lowers the profile
        limit; a higher one has no effect.

        Returns:
            ``(errors, warnings)``: messages naming the battery. No errors means
            the battery can be resolved.
        """
        try:
            profile = self._load_battery_profile()
            capacity_wh = resolve_nominal_capacity_wh(self, profile)
            resolve_battery_parameters(self, profile, capacity_wh, BatteryOperatingPoint())
            peak_charge_w, peak_discharge_w = profile_peak_power_w(
                profile, capacity_wh, BatteryOperatingPoint().temperature_degC
            )
        except ValueError as exc:
            return [str(exc)], []
        warnings: list[str] = []
        for name, limit_w, peak_w in (
            ("max_charge_power_w", self.profile_adjustment.max_charge_power_w, peak_charge_w),
            (
                "max_discharge_power_w",
                self.profile_adjustment.max_discharge_power_w,
                peak_discharge_w,
            ),
        ):
            if limit_w is not None and limit_w > peak_w:
                warnings.append(
                    f"Battery '{self.device_id}': 'profile_adjustment.{name}' {limit_w} W is "
                    f"above the {peak_w} W of battery profile '{self.battery_profile}' and "
                    "has no effect; the profile value applies."
                )
        return [], warnings

    def _effective_parameters(self) -> Optional[BatteryOperatingParameters]:
        """Resolve at the reference operating point, None if that is not possible."""
        try:
            return self.resolve_operating_parameters()
        except ValueError as exc:
            logger.debug(f"Battery '{self.device_id}' not resolvable: {exc}")
            return None

    # ------------------------------------------------------------------
    # Computed fields, evaluated on every access at the reference operating point
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_nominal_capacity_wh(self) -> Optional[int]:
        """Effective nominal capacity [Wh]: profile adjustment, else profile.

        None if the battery cannot be resolved.
        """
        p = self._effective_parameters()
        return p.capacity_wh if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_charge_power_w(self) -> Optional[float]:
        """Maximum charging power [W] at the reference operating point."""
        p = self._effective_parameters()
        return p.max_charge_power_w if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_discharge_power_w(self) -> Optional[float]:
        """Maximum discharging power [W] at the reference operating point."""
        p = self._effective_parameters()
        return p.max_discharge_power_w if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_charging_efficiency(self) -> Optional[float]:
        """Charging efficiency [0.01 ... 1.00] at the maximum charge power."""
        p = self._effective_parameters()
        return p.charging_efficiency if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_discharging_efficiency(self) -> Optional[float]:
        """Discharging efficiency [0.01 ... 1.00] at the maximum discharge power."""
        p = self._effective_parameters()
        return p.discharging_efficiency if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_min_soc_percentage(self) -> Optional[int]:
        """Effective minimum SoC [%], within the technical bounds of the profile."""
        p = self._effective_parameters()
        return p.min_soc_percentage if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_soc_percentage(self) -> Optional[int]:
        """Effective maximum SoC [%], within the technical bounds of the profile."""
        p = self._effective_parameters()
        return p.max_soc_percentage if p is not None else None

    # ------------------------------------------------------------------
    # GENETIC domain conversion
    # ------------------------------------------------------------------

    grid_export_rates: Optional[list[float]] = Field(
        default=BATTERY_DEFAULT_GRID_EXPORT_RATES,
        json_schema_extra={
            "description": (
                "Battery-to-grid export rates as factor of maximum discharge "
                "power ]0.00 ... 1.00]. Available to algorithms that explicitly "
                "enable battery-to-grid export; configuring rates alone does not "
                "enable export. [1.0] selects full-power export. None uses the "
                "default export rates."
            ),
            "examples": [[0.25, 0.5, 0.75, 1.0], [1.0], None],
        },
    )

    @field_validator("grid_export_rates", mode="before")
    def validate_and_sort_grid_export_rates(cls, v: Any) -> list[float]:
        """Normalize export rates to a finite, sorted, duplicate-free list in ]0, 1]."""
        # None means fallback to default values
        if v is None:
            return BATTERY_DEFAULT_GRID_EXPORT_RATES.copy()

        if isinstance(v, str):
            numbers = re.split(r"[,\s]+", v.strip("[]"))
            arr = np.array([float(x) for x in numbers if x])
        else:
            arr = np.array(v, dtype=float)

        if arr.ndim != 1 or arr.size == 0:
            raise ValueError("grid_export_rates must be a nonempty one-dimensional list.")
        if not np.isfinite(arr).all():
            raise ValueError("grid_export_rates must contain finite values.")

        # A rate of 0.0 is not an export level - "no export" is expressed by the
        # other battery states - so the lower bound is exclusive.
        if (arr <= 0.0).any() or (arr > 1.0).any():
            raise ValueError("grid_export_rates must be within ]0.0, 1.0].")

        arr = np.unique(arr)
        arr.sort()

        return arr.tolist()

    min_soc_deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
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
            "x-scope": [str(ConfigScope.GENETIC)],
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

    # ------------------------------------------------------------------
    # Periodic full charge (stationary batteries, GENETIC only)
    # ------------------------------------------------------------------
    #
    # The interval and the detection threshold are battery model properties:
    # they come from the battery profile, see ``resolve_full_charge``.

    full_charge_soc_percentage: int = Field(
        default=100,
        ge=1,
        le=100,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "State of charge a periodic full charge has to reach [%]. May be "
                "above 'max_soc_percentage', which then applies on the other days. "
                "Capped by the technical maximum SOC of the battery profile. The "
                "interval comes from the battery profile or "
                "'profile_adjustment.full_charge_interval_days'."
            ),
            "examples": [100],
        },
    )
    full_charge_allow_grid: bool = Field(
        default=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Enforce a due full charge, charging from the grid if needed. "
                "False only raises the maximum state of charge, so that PV surplus "
                "can fill the battery, without enforcing the target."
            ),
            "examples": [False, True],
        },
    )

    def resolve_full_charge(self) -> BatteryFullCharge:
        """Return the effective periodic full charge policy of this battery.

        Never raises: if the battery profile is not usable, only the profile
        adjustment and the defaults apply.
        """
        try:
            profile: Optional["BatteryProfile"] = self._load_battery_profile()
        except BatteryResolutionError as exc:
            logger.debug(f"Battery '{self.device_id}': full charge without profile: {exc}")
            profile = None
        return resolve_full_charge(self, profile)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_full_charge_interval_days(self) -> Optional[float]:
        """Maximum days between two full charges [d]: profile adjustment, else profile.

        None if the periodic full charge is disabled.
        """
        return self.resolve_full_charge().interval_days

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_full_charge_detect_percentage(self) -> int:
        """Measured SoC from which the battery counts as fully charged [%]."""
        return self.resolve_full_charge().detect_percentage

    def to_genetic_pv_bat_param(
        self,
        operating_parameters: Optional[BatteryOperatingParameters] = None,
        use_profile_tables: bool = True,
    ) -> "SolarPanelBatteryParameters":
        """Return SolarPanelBatteryParameters for the GENETIC optimizer.

        Args:
            operating_parameters: Resolved snapshot to convert, e.g. from
                ``resolve_current_operating_parameters``. None resolves at the
                reference operating point.
            use_profile_tables: Whether to add the SoC dependent power limits
                and efficiency curves of the profile. False for a snapshot
                resolved without profile (see
                ``resolve_operating_parameters_or_defaults``); requires
                ``operating_parameters``.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        from akkudoktoreos.devices.genetic.battery import SolarPanelBatteryParameters

        p, profile = self._genetic_snapshot(operating_parameters, use_profile_tables)
        return SolarPanelBatteryParameters(
            device_id=self.device_id,
            capacity_wh=p.capacity_wh,
            charging_efficiency=p.charging_efficiency,
            discharging_efficiency=p.discharging_efficiency,
            min_soc_percentage=p.min_soc_percentage,
            max_soc_percentage=p.max_soc_percentage,
            charge_rates=self.charge_rates,
            grid_export_rates=self.grid_export_rates,
            **self._genetic_power_parameters(p, profile),
            levelized_cost_of_storage_kwh=self.levelized_cost_of_storage_amt_kwh,
        )

    def to_genetic_ev_bat_param(
        self,
        operating_parameters: Optional[BatteryOperatingParameters] = None,
        use_profile_tables: bool = True,
    ) -> "ElectricVehicleParameters":
        """Return ElectricVehicleParameters for the GENETIC optimizer.

        Args:
            operating_parameters: Resolved snapshot to convert, e.g. from
                ``resolve_current_operating_parameters``. None resolves at the
                reference operating point.
            use_profile_tables: Whether to add the SoC dependent power limits
                and efficiency curves of the profile. False for a snapshot
                resolved without profile (see
                ``resolve_operating_parameters_or_defaults``); requires
                ``operating_parameters``.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        from akkudoktoreos.devices.genetic.battery import ElectricVehicleParameters

        p, profile = self._genetic_snapshot(operating_parameters, use_profile_tables)
        return ElectricVehicleParameters(
            device_id=self.device_id,
            capacity_wh=p.capacity_wh,
            charging_efficiency=p.charging_efficiency,
            discharging_efficiency=p.discharging_efficiency,
            charge_rates=self.charge_rates,
            min_soc_percentage=p.min_soc_percentage,
            max_soc_percentage=p.max_soc_percentage,
            min_soc_deadline_datetime=self.min_soc_deadline_datetime,
            min_soc_max_duration_h=self.min_soc_max_duration_h,
            **self._genetic_power_parameters(p, profile),
        )

    # ------------------------------------------------------------------
    # GENETIC0 domain conversion
    # ------------------------------------------------------------------

    def to_genetic0_pv_bat_param(
        self, operating_parameters: Optional[BatteryOperatingParameters] = None
    ) -> "Genetic0SolarPanelBatteryParameters":
        """Return Genetic0SolarPanelBatteryParameters for the GENETIC0 optimizer.

        Args:
            operating_parameters: Resolved snapshot to convert, e.g. from
                ``resolve_current_operating_parameters``. None resolves at the
                reference operating point.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        from akkudoktoreos.devices.genetic0.genetic0battery import (
            Genetic0SolarPanelBatteryParameters,
        )

        p = (
            operating_parameters
            if operating_parameters is not None
            else self.resolve_operating_parameters()
        )
        return Genetic0SolarPanelBatteryParameters(
            device_id=self.device_id,
            capacity_wh=p.capacity_wh,
            charging_efficiency=p.charging_efficiency,
            discharging_efficiency=p.discharging_efficiency,
            max_charge_power_w=p.max_charge_power_w,
            min_soc_percentage=p.min_soc_percentage,
            max_soc_percentage=p.max_soc_percentage,
        )

    def to_genetic0_ev_bat_param(
        self, operating_parameters: Optional[BatteryOperatingParameters] = None
    ) -> "Genetic0ElectricVehicleParameters":
        """Return Genetic0ElectricVehicleParameters for the GENETIC0 optimizer.

        Args:
            operating_parameters: Resolved snapshot to convert, e.g. from
                ``resolve_current_operating_parameters``. None resolves at the
                reference operating point.

        Raises:
            BatteryResolutionError: See ``resolve_operating_parameters``.
        """
        from akkudoktoreos.devices.genetic0.genetic0battery import (
            Genetic0ElectricVehicleParameters,
        )

        p = (
            operating_parameters
            if operating_parameters is not None
            else self.resolve_operating_parameters()
        )
        return Genetic0ElectricVehicleParameters(
            device_id=self.device_id,
            capacity_wh=p.capacity_wh,
            charging_efficiency=p.charging_efficiency,
            discharging_efficiency=p.discharging_efficiency,
            charge_rates=self.charge_rates,
            max_charge_power_w=p.max_charge_power_w,
            min_soc_percentage=p.min_soc_percentage,
            max_soc_percentage=p.max_soc_percentage,
        )

    @field_validator("charge_rates", mode="before")
    def validate_and_sort_charge_rates(cls, v: Any) -> NDArray[Shape["*"], float]:
        # None means fallback to default values
        if v is None:
            return np.asarray(BATTERY_DEFAULT_CHARGE_RATES, dtype=float).copy()

        # Convert to numpy array
        if isinstance(v, str):
            # Remove brackets and split by comma or whitespace
            numbers = re.split(r"[,\s]+", v.strip("[]"))

            # Filter out any empty strings and convert to floats
            arr = np.array([float(x) for x in numbers if x])
        else:
            arr = np.array(v, dtype=float)

        # Must not be empty
        if arr.size == 0:
            raise ValueError("charge_rates must contain at least one value.")

        # Enforce bounds: 0.0 ≤ x ≤ 1.0
        if (arr < 0.0).any() or (arr > 1.0).any():
            raise ValueError("charge_rates must be within [0.0, 1.0].")

        # Remove duplicates + sort
        arr = np.unique(arr)
        arr.sort()

        return arr

    @model_validator(mode="after")
    def _validate_soc_range(self) -> "BatteriesCommonSettings":
        if self.min_soc_percentage > self.max_soc_percentage:
            raise ValueError("min_soc_percentage must be <= max_soc_percentage")
        detect_percentage = self.profile_adjustment.full_charge_detect_percentage
        if detect_percentage is not None and detect_percentage > self.full_charge_soc_percentage:
            raise ValueError(
                "profile_adjustment.full_charge_detect_percentage must be <= "
                "full_charge_soc_percentage"
            )
        max_charge_power_w = self.profile_adjustment.max_charge_power_w
        if (
            self.min_charge_power_w is not None
            and max_charge_power_w is not None
            and self.min_charge_power_w > max_charge_power_w
        ):
            raise ValueError("min_charge_power_w must be <= profile_adjustment.max_charge_power_w")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_soc_factor(self) -> str:
        """Measurement key for SoC as factor of total capacity [0.0 ... 1.0]."""
        return f"{self.device_id}-soc-factor"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_temperature_degc(self) -> str:
        """Measurement key for the battery temperature [degC]."""
        return f"{self.device_id}-temperature-degc"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_charge_power_w(self) -> str:
        """Measurement key for the charge power [W], used to compute the charging efficiency."""
        return f"{self.device_id}-charge-power-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_discharge_power_w(self) -> str:
        """Measurement key for the discharge power [W], used for the discharging efficiency."""
        return f"{self.device_id}-discharge-power-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l1_w(self) -> str:
        """Measurement key for L1 power [W]."""
        return f"{self.device_id}-power-l1-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l2_w(self) -> str:
        """Measurement key for L2 power [W]."""
        return f"{self.device_id}-power-l2-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l3_w(self) -> str:
        """Measurement key for L3 power [W]."""
        return f"{self.device_id}-power-l3-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_3_phase_sym_w(self) -> str:
        """Measurement key for symmetric 3-phase power [W]."""
        return f"{self.device_id}-power-3-phase-sym-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> list[str]:
        """All measurement keys for this battery."""
        return [
            self.measurement_key_soc_factor,
            self.measurement_key_temperature_degc,
            self.measurement_key_charge_power_w,
            self.measurement_key_discharge_power_w,
            self.measurement_key_power_l1_w,
            self.measurement_key_power_l2_w,
            self.measurement_key_power_l3_w,
            self.measurement_key_power_3_phase_sym_w,
        ]
