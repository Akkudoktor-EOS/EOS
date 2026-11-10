"""Inverter device settings.

The technical inverter data (nominal power, efficiency curves, AC charge power
limit of the model, power derating) comes from an inverter profile (see
``InverterProfileSettings``). The user selects the profile by name via
``inverter_profile`` (default ``generic-hybrid``) and describes how the
installed unit differs from it in ``profile_adjustment``.

``InverterCommonSettings`` holds the configuration fields, their validation and
the measurement keys. The other concerns live in their own modules:

- ``profilestore``: loading (and caching) the compiled inverter profile.
- ``invertermeasurement``: reading the ambient temperature from the
  measurements.
- ``inverterresolution``: resolving the effective parameters from profile,
  profile adjustment and operating point (pure functions).

The computed ``effective_*`` fields are evaluated at the reference operating
point (``InverterOperatingPoint()``: 45 degC ambient temperature). They never
raise; they are None if the inverter cannot be resolved.
"""

from typing import TYPE_CHECKING, Optional

from loguru import logger
from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel
from akkudoktoreos.devices.settings import invertermeasurement
from akkudoktoreos.devices.settings.devicebasesettings import (
    DevicesBaseSettings,
)
from akkudoktoreos.devices.settings.inverterresolution import (
    INVERTER_FALLBACK_NOMINAL_POWER_W,
    InverterOperatingParameters,
    InverterOperatingPoint,
    InverterResolutionError,
    check_inverter_adjustment,
    resolve_inverter_parameters,
    resolve_inverter_parameters_without_profile,
    resolve_nominal_power_w,
)
from akkudoktoreos.utils.datetimeutil import DateTime

if TYPE_CHECKING:
    from akkudoktoreos.devices.genetic0.genetic0inverter import (
        Genetic0InverterParameters,
    )
    from akkudoktoreos.devices.genetic.inverter import InverterParameters
    from akkudoktoreos.devices.profile.inverterprofile import InverterProfile


# Name of the inverter profile used when the user selects none.
INVERTER_DEFAULT_PROFILE: str = "generic-hybrid"


class InverterProfileAdjustment(SettingsBaseModel):
    """How an inverter unit differs from its inverter profile.

    None keeps the profile value.
    """

    nominal_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Nominal AC power of the installed inverter [W]. Replaces the nominal "
                "power of the inverter profile. Required if the profile is generic and "
                "provides no nominal power. The effective value is available as "
                "'effective_nominal_power_w'."
            ),
            "examples": [None, 10000],
        },
    )
    max_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Maximum AC power of the installation [W], e.g. a configured output "
                "limit. Can only lower the (derated) nominal power. The effective value "
                "is available as 'effective_max_power_w'."
            ),
            "examples": [None, 8000],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "AC charge power limit of the installation [W]. Can only lower the limit "
                "of the inverter profile. 0 disables AC charging. The effective value is "
                "available as 'effective_max_ac_charge_power_w'."
            ),
            "examples": [None, 0, 5000],
        },
    )


class InverterCommonSettings(DevicesBaseSettings):
    """Inverter device settings.

    An inverter bridges a DC bus (PV / battery) and an AC bus (grid /
    household). It must therefore have at least one DC port and one AC
    port.
    """

    # ------------------------------------------------------------------
    # Shared fields (GENETIC + GENETIC0)
    # ------------------------------------------------------------------

    inverter_profile: str = Field(
        default=INVERTER_DEFAULT_PROFILE,
        min_length=1,
        json_schema_extra={
            "description": (
                "Name of the inverter profile (without '.json') providing the nominal "
                "power, efficiency curves, AC charge power limit and power derating. "
                "Differences of this unit go into 'profile_adjustment'."
            ),
            "examples": [INVERTER_DEFAULT_PROFILE],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    profile_adjustment: InverterProfileAdjustment = Field(
        default_factory=InverterProfileAdjustment,
        json_schema_extra={
            "description": (
                "How this inverter unit differs from its inverter profile: nominal power "
                "and installation limits."
            ),
            "examples": [{"nominal_power_w": 10000}],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    dc_to_ac_efficiency_reference_load_fraction: float = Field(
        default=0.06,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Load fraction of the effective maximum power (0–1) at which the DC→AC "
                "efficiency curve is evaluated to value stored battery energy without a "
                "specific conversion (remaining energy at the end of the horizon, AC "
                "charge break-even). Should represent the typical load served by the "
                "battery, e.g. the night-time base load. Default 0.06."
            ),
            "examples": [0.06, 0.1],
            "x-scope": [str(ConfigScope.GENETIC)],
        },
    )
    battery_id: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": ("Device ID of the battery."),
            "examples": [None],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    # ------------------------------------------------------------------
    # UNUSED-only fields
    # ------------------------------------------------------------------

    # Auxiliary power consumption
    off_state_power_consumption_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Standby power consumed when the inverter is fully idle "
                "(battery=0 and PV=0) [W]. Default 0.0."
            ),
            "examples": [5.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    on_state_power_consumption_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Auxiliary power consumed whenever the inverter is active "
                "(non-zero AC power) [W]. Default 0.0."
            ),
            "examples": [10.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # PV parameters (used for SOLAR and HYBRID inverter types)
    pv_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of PV DC→AC conversion (0–1). "
                "Required when pv_power_w_key is set (SOLAR or HYBRID). Default 1.0."
            ),
            "examples": [0.97, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_to_battery_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of PV DC→battery charging path (0–1). "
                "Used for HYBRID inverters only. Default 1.0."
            ),
            "examples": [0.98, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_max_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum DC PV power fed into the inverter [W]. "
                "Required when pv_power_w_key is set (SOLAR or HYBRID). "
                "Values from pv_power_w_key are clipped to this limit."
            ),
            "examples": [8000.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_min_power_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Minimum DC PV power threshold [W]. Steps with available PV "
                "below this value are treated as zero. Default 0.0."
            ),
            "examples": [50.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_power_w_key: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "SimulationContext prediction key resolving to a per-step PV "
                "power forecast array [W] of shape (horizon,). "
                "Set for SOLAR and HYBRID inverter types; leave None for BATTERY."
            ),
            "examples": ["pv_forecast_w", None],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # Battery parameters (used for BATTERY and HYBRID inverter types)
    battery_capacity_wh: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Usable battery capacity [Wh]. Required for BATTERY and HYBRID inverter types."
            ),
            "examples": [10000.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_charge_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional list of discrete charge rate fractions (each in (0, 1]). "
                "When set, the battery is constrained to these specific fractions "
                "of battery_max_charge_rate. null means continuous charging. "
                "All values must be in (0, 1]."
            ),
            "examples": [None, [0.25, 0.5, 1.0]],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_charge_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Minimum non-zero charge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "Charge commands below this threshold are rounded to zero. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_charge_rate: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum charge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "bat_factor=+1 maps to this rate. Default 1.0."
            ),
            "examples": [0.5, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_discharge_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Minimum discharge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "Discharge commands below this threshold are rounded to zero. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_discharge_rate: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum discharge rate as a fraction of the 1C rate. "
                "(1C = battery_capacity_wh W). "
                "bat_factor=−1 maps to this rate. Default 1.0."
            ),
            "examples": [0.5, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_soc_factor: float = Field(
        default=0.0,
        ge=0,
        lt=1,
        json_schema_extra={
            "description": (
                "Minimum allowed state of charge as a fraction of battery_capacity_wh. "
                "Must be < battery_max_soc_factor. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_soc_factor: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum allowed state of charge as a fraction of battery_capacity_wh. "
                "Must be > battery_min_soc_factor. Default 1.0."
            ),
            "examples": [0.9, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_initial_soc_factor_key: str = Field(
        default="",
        json_schema_extra={
            "description": (
                "SimulationContext measurement key resolving to the initial battery "
                "SoC as a fraction of battery_capacity_wh, in [min_soc_factor, max_soc_factor]. "
                "An empty string means the device uses battery_min_soc_factor as the "
                "initial SoC (fully depleted to the minimum)."
            ),
            "examples": ["battery1_soc_factor", ""],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_lcos_amt_kwh: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Levelized cost of battery storage [Amt./kWh cycled]. "
                "Penalises unnecessary charging/discharging so the GA avoids "
                "grid-charge→discharge cycles with no price-spread benefit. "
                "Typical residential Li-ion value: 0.05 Amt./kWh. "
                "Set to 0.0 to encourage the optimizer to use the battery. "
                "Defaults to 0.0."
            ),
            "examples": [0.05, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_discharge_reward_amt_kwh: float = Field(
        default=0.02,
        ge=0,
        json_schema_extra={
            "description": (
                "Shadow price rewarding battery discharge [Amt./kWh discharged AC]. "
                "Adds a direct fitness benefit per kWh the battery delivers, on top of "
                "the grid import cost reduction already captured by GridConnectionDevice. "
                "Helps the GA discover discharge when the load-matching rate is small "
                "relative to mutation noise. "
                "Suggested value: import_price - export_price - lcos "
                "(e.g. 0.30 - 0.08 - 0.05 = 0.17). Set to 0.0 to disable."
            ),
            "examples": [0.0, 0.17],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_soc_factors(self) -> "InverterCommonSettings":
        if self.battery_min_soc_factor >= self.battery_max_soc_factor:
            raise ValueError(
                "battery_min_soc_factor must be strictly less than battery_max_soc_factor"
            )
        return self

    # ------------------------------------------------------------------
    # Inverter profile loading (see profilestore)
    # ------------------------------------------------------------------

    def get_inverter_profile(self) -> "InverterProfile":
        """Return the compiled inverter profile for ``inverter_profile``.

        Raises:
            FileNotFoundError: If the configured inverter profile does not exist.
            ValueError: If the profile document fails validation.
        """
        from akkudoktoreos.devices.profile.inverterprofile import (
            InverterProfileSettings,
        )
        from akkudoktoreos.devices.profile.profilestore import get_profile_store

        return get_profile_store().get(InverterProfileSettings, self.inverter_profile)

    def _load_inverter_profile(self) -> "InverterProfile":
        """Return the compiled inverter profile, or raise an error naming the inverter.

        Raises:
            InverterResolutionError: If the profile does not exist, cannot be
                read or fails validation.
        """
        try:
            return self.get_inverter_profile()
        except InverterResolutionError:
            raise
        except (OSError, ValueError) as exc:
            raise InverterResolutionError(
                f"Inverter '{self.device_id}': inverter profile '{self.inverter_profile}' "
                f"is not usable: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Measurement access (see invertermeasurement)
    # ------------------------------------------------------------------

    async def measure_operating_point(
        self, target_datetime: Optional[DateTime] = None
    ) -> InverterOperatingPoint:
        """Read the ambient temperature from the measurements, 45 degC if unavailable."""
        return await invertermeasurement.measure_operating_point(self, target_datetime)

    # ------------------------------------------------------------------
    # Parameter resolution (see inverterresolution)
    # ------------------------------------------------------------------

    def resolve_operating_parameters(
        self, operating_point: Optional[InverterOperatingPoint] = None
    ) -> InverterOperatingParameters:
        """Resolve the effective parameters at an operating point.

        Args:
            operating_point: Conditions to evaluate the profile at, the reference
                operating point (45 degC) if None.

        Raises:
            InverterResolutionError: If the profile is not usable or the
                parameters cannot be resolved.
        """
        return resolve_inverter_parameters(
            self, self._load_inverter_profile(), operating_point or InverterOperatingPoint()
        )

    async def resolve_current_operating_parameters(
        self, target_datetime: Optional[DateTime] = None
    ) -> InverterOperatingParameters:
        """Resolve the effective parameters at the measured ambient temperature.

        Raises:
            InverterResolutionError: See ``resolve_operating_parameters``.
        """
        operating_point = await self.measure_operating_point(target_datetime)
        return self.resolve_operating_parameters(operating_point)

    def resolve_operating_parameters_or_defaults(
        self, operating_point: Optional[InverterOperatingPoint] = None, report: bool = True
    ) -> InverterOperatingParameters:
        """Resolve for the optimizations; never raises.

        Optimization parameters must always be valid, so a configuration that
        cannot be resolved gives defaults and an error log:

        - an unusable inverter profile is replaced by the packaged
          ``generic-hybrid`` profile with this unit's profile adjustment,
        - a missing nominal power, or no usable profile at all, gives the
          fallback values (``INVERTER_FALLBACK_*``),
        - an inverter derated to zero power at the operating point is resolved
          at the reference temperature instead.

        Args:
            operating_point: Operating point to evaluate the profile at, the
                reference operating point if None.
            report: Whether to log an error naming the inverter, the problem
                and the defaults used.

        Returns:
            The parameters.
        """
        if operating_point is None:
            operating_point = InverterOperatingPoint()
        problems: list[str] = []
        defaults: list[str] = []
        profile: Optional["InverterProfile"] = None
        try:
            profile = self._load_inverter_profile()
        except InverterResolutionError as exc:
            problems.append(str(exc))
            if self.inverter_profile != INVERTER_DEFAULT_PROFILE:
                try:
                    from akkudoktoreos.devices.profile.inverterprofile import (
                        InverterProfileSettings,
                    )
                    from akkudoktoreos.devices.profile.profilestore import (
                        get_profile_store,
                    )

                    profile = get_profile_store().get(
                        InverterProfileSettings, INVERTER_DEFAULT_PROFILE
                    )
                    defaults.append(f"inverter profile '{INVERTER_DEFAULT_PROFILE}'")
                except (OSError, ValueError) as default_exc:
                    problems.append(str(default_exc))

        parameters: Optional[InverterOperatingParameters] = None
        if profile is not None:
            settings: InverterCommonSettings = self
            try:
                resolve_nominal_power_w(settings, profile)
            except InverterResolutionError as exc:
                problems.append(str(exc))
                defaults.append(f"nominal power {INVERTER_FALLBACK_NOMINAL_POWER_W:g} W")
                settings = self.model_copy(
                    update={
                        "profile_adjustment": self.profile_adjustment.model_copy(
                            update={"nominal_power_w": INVERTER_FALLBACK_NOMINAL_POWER_W}
                        )
                    }
                )
            try:
                parameters = resolve_inverter_parameters(settings, profile, operating_point)
            except InverterResolutionError as exc:
                # Derated to zero power at the operating point.
                problems.append(str(exc))
                reference = InverterOperatingPoint()
                defaults.append(f"the parameters at {reference.temperature_degC:g} degC")
                try:
                    parameters = resolve_inverter_parameters(settings, profile, reference)
                except InverterResolutionError as reference_exc:
                    problems.append(str(reference_exc))
        if parameters is None:
            parameters, fallback_defaults = resolve_inverter_parameters_without_profile(
                self, operating_point
            )
            defaults.extend(fallback_defaults)
        if problems and report:
            logger.error(
                "{} Using defaults for the optimization: {}.",
                " ".join(problems),
                ", ".join(defaults),
            )
        return parameters

    async def resolve_current_operating_parameters_or_defaults(
        self, target_datetime: Optional[DateTime] = None, report: bool = True
    ) -> InverterOperatingParameters:
        """Resolve like ``resolve_operating_parameters_or_defaults`` at the measured temperature.

        Args:
            target_datetime: Datetime the measurement is looked up for, now if None.
            report: Whether to log the configuration error and the defaults used.

        Returns:
            The parameters.
        """
        operating_point = await self.measure_operating_point(target_datetime)
        parameters = self.resolve_operating_parameters_or_defaults(operating_point, report)
        logger.debug(
            f"Inverter '{self.device_id}' at {parameters.temperature_degC} degC: "
            f"maximum power {parameters.max_power_w} W."
        )
        return parameters

    def uses_profile(self, kind: str, name: str) -> bool:
        """Return whether this inverter uses the profile of the given kind and name."""
        from akkudoktoreos.devices.profile.inverterprofile import (
            InverterProfileSettings,
        )

        return kind == InverterProfileSettings.profile_kind and name == self.inverter_profile

    def check_profile(self) -> tuple[list[str], list[str]]:
        """Check that the inverter can be resolved from its profile.

        Returns:
            ``(errors, warnings)``: an error if the inverter cannot be resolved,
            warnings for installation limits without effect.
        """
        try:
            parameters = self.resolve_operating_parameters()
        except InverterResolutionError as exc:
            return [str(exc)], []
        return [], check_inverter_adjustment(self, parameters)

    def _effective_parameters(self) -> Optional[InverterOperatingParameters]:
        """Resolve at the reference operating point, None if that is not possible."""
        try:
            return self.resolve_operating_parameters()
        except ValueError as exc:
            logger.debug(f"Inverter '{self.device_id}' not resolvable: {exc}")
            return None

    # ------------------------------------------------------------------
    # Computed fields, evaluated on every access at the reference operating point
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_nominal_power_w(self) -> Optional[float]:
        """Effective nominal AC power [W]: profile adjustment, else profile."""
        p = self._effective_parameters()
        return p.nominal_power_w if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_power_w(self) -> Optional[float]:
        """Maximum AC power [W] at the reference temperature, derated and capped."""
        p = self._effective_parameters()
        return p.max_power_w if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_ac_charge_power_w(self) -> Optional[float]:
        """AC charge power limit [W] at the reference temperature; 0 disables AC charging."""
        p = self._effective_parameters()
        return p.max_ac_charge_power_w if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_dc_to_ac_efficiency(self) -> Optional[float]:
        """DC→AC efficiency at the reference load fraction."""
        p = self._effective_parameters()
        return p.dc_to_ac_efficiency if p is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_ac_to_dc_efficiency(self) -> Optional[float]:
        """AC→DC efficiency at the AC charge power limit."""
        p = self._effective_parameters()
        return p.ac_to_dc_efficiency if p is not None else None

    # ------------------------------------------------------------------
    # GENETIC domain conversion
    # ------------------------------------------------------------------

    def to_genetic_param(
        self, operating_parameters: Optional[InverterOperatingParameters] = None
    ) -> "InverterParameters":
        """Return InverterParameters for the GENETIC optimizer.

        Args:
            operating_parameters: Resolved snapshot to convert, e.g. from
                ``resolve_current_operating_parameters``. None resolves at the
                reference operating point.

        Raises:
            InverterResolutionError: If the snapshot is resolved here and the
                inverter cannot be resolved.
        """
        from akkudoktoreos.devices.genetic.inverter import InverterParameters

        p = operating_parameters or self.resolve_operating_parameters()
        return InverterParameters(
            device_id=self.device_id,
            max_power_wh=p.max_power_w,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=p.ac_to_dc_efficiency,
            dc_to_ac_efficiency=p.dc_to_ac_efficiency,
            dc_to_ac_efficiency_curve=list(p.dc_to_ac_efficiency_curve),
            ac_to_dc_efficiency_curve=list(p.ac_to_dc_efficiency_curve),
            dc_to_ac_efficiency_reference_load_fraction=(
                self.dc_to_ac_efficiency_reference_load_fraction
            ),
            max_ac_charge_power_w=p.max_ac_charge_power_w,
            ac_charge_limits_total_charge=p.ac_charge_limits_total_charge,
        )

    # ------------------------------------------------------------------
    # GENETIC0 domain conversion
    # ------------------------------------------------------------------

    def to_genetic0_param(
        self, operating_parameters: Optional[InverterOperatingParameters] = None
    ) -> "Genetic0InverterParameters":
        """Return Genetic0InverterParameters for the GENETIC0 optimizer.

        GENETIC0 uses constant efficiencies: the DC→AC efficiency at the
        reference load fraction and the AC→DC efficiency at the AC charge limit.

        Raises:
            InverterResolutionError: See ``to_genetic_param``.
        """
        from akkudoktoreos.devices.genetic0.genetic0inverter import (
            Genetic0InverterParameters,
        )

        p = operating_parameters or self.resolve_operating_parameters()
        return Genetic0InverterParameters(
            device_id=self.device_id,
            max_power_wh=p.max_power_w,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=p.ac_to_dc_efficiency,
            dc_to_ac_efficiency=p.dc_to_ac_efficiency,
            max_ac_charge_power_w=p.max_ac_charge_power_w,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_temperature_degc(self) -> str:
        """Measurement key for the ambient temperature of the inverter [degC]."""
        return f"{self.device_id}-temperature-degc"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> list[str]:
        """Measurement keys for this inverter.

        The ambient temperature, and the ``battery_initial_soc_factor_key`` if
        non-empty, so the EMS measurement store knows to watch for them.
        """
        keys = [self.measurement_key_temperature_degc]
        if self.battery_initial_soc_factor_key:
            keys.append(self.battery_initial_soc_factor_key)
        return keys
