"""Battery and electric vehicle device settings.

Note: Used for the GENETIC and GENETIC0 algorithm.
"""

import re
from typing import TYPE_CHECKING, Any, Literal, Optional

import numpy as np
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field, field_validator, model_validator

from akkudoktoreos.config.configabc import ConfigScope
from akkudoktoreos.devices.devicesabc import validate_soc_value_steps
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


# Default charge rates for battery
BATTERY_DEFAULT_CHARGE_RATES: list[float] = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


BATTERY_DEFAULT_GRID_EXPORT_RATES: list[float] = [0.25, 0.5, 0.75, 1.0]


EV_SOC_VALUE_STEPS_DESCRIPTION = (
    "Electric vehicles only. Value of energy stored in the vehicle above "
    "'min_soc_percentage', as steps [[soc_percentage, amount_per_kwh], ...] "
    "with strictly ascending state of charge in ]0, 100] and finite, "
    "non-negative amounts that do not rise with the state of charge. Every "
    "kWh stored (SoC energy, after charging losses) between the previous step "
    "- at first 'min_soc_percentage' - and a step's state of charge, at most "
    "'max_soc_percentage', is credited with that step's amount at the moment "
    "the target is checked: the deadline, or else the end of the control "
    "horizon. The optimizer then charges beyond the target where that costs "
    "less than the value, e.g. in a cheap night or from PV surplus. Energy "
    "below the target earns nothing, so the target keeps its penalty. None or "
    "an empty list: energy above the target earns no credit, as before. Used "
    "by GENETIC."
)


class BatteriesCommonSettings(DevicesBaseSettings):
    """Battery and electric vehicle device settings.

    Used for both stationary batteries and EV battery packs.

    Note: Used for the GENETIC and GENETIC0 algorithm.
    """

    capacity_estimation: Optional[BatteryCapacityEstimationSettings] = None
    capacity_estimate: Optional[BatteryCapacityEstimate] = Field(
        default=None,
        description="Last explicitly stored capacity estimate; does not override capacity_wh.",
    )

    capacity_wh: int = Field(
        default=8000,
        gt=0,
        json_schema_extra={"description": "Capacity [Wh].", "examples": [8000]},
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "Charging efficiency [0.01 ... 1.00].",
            "examples": [0.88],
        },
    )
    discharging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "Discharge efficiency [0.01 ... 1.00].",
            "examples": [0.88],
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
    max_charge_power_w: Optional[float] = Field(
        default=5000,
        gt=0,
        json_schema_extra={
            "description": "Maximum charging power [W].",
            "examples": [5000],
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
                "This is the target SoC for charging."
            ),
            "examples": [10],
        },
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "Maximum state of charge (SOC) as percentage of capacity [%].",
            "examples": [100],
        },
    )

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
                "as local time. None means end of the optimization horizon. GENETIC "
                "treats a deadline that has passed like None, with a warning while "
                "the target is not reached."
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

    charge_start_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Earliest absolute moment from which the electric vehicle may "
                "charge (electric vehicles only; ignored for stationary "
                "batteries). 'min_soc_deadline_datetime' only sets when the target "
                "state of charge has to be reached; charging may continue after "
                "it. Use it for a vehicle that is not connected yet "
                "(e.g. its expected arrival or plug-in time) or for a connected "
                "vehicle that may only charge from a given time on. Before it "
                "the vehicle neither charges nor discharges, and its state of "
                "charge stays at the initial state of charge (for a vehicle that "
                "is not connected yet: the state of charge expected at the "
                "charge start). A slot that begins before this moment is not "
                "used for charging. A date time without timezone is read as "
                "local time. None means the vehicle can charge from the start of "
                "the optimization."
            ),
            "examples": [None, "2026-07-15T20:00:00+02:00"],
        },
    )

    soc_value_steps: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": EV_SOC_VALUE_STEPS_DESCRIPTION,
            "examples": [None, [[80, 0.25]], [[60, 0.3], [80, 0.22]]],
        },
    )

    @field_validator("soc_value_steps", mode="after")
    @classmethod
    def _validate_soc_value_steps(
        cls, value: Optional[list[tuple[float, float]]]
    ) -> Optional[list[tuple[float, float]]]:
        """Ascending SoC, non-increasing finite amounts; an empty list means no value."""
        return validate_soc_value_steps(value)

    @field_validator("min_soc_deadline_datetime", "charge_start_datetime", mode="before")
    @classmethod
    def transform_deadline_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        return to_datetime(value)

    # ------------------------------------------------------------------
    # Periodic full charge (stationary batteries, GENETIC only)
    # ------------------------------------------------------------------

    cell_chemistry: Optional[Literal["LFP", "NMC", "NCA", "LTO"]] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Cell chemistry of the battery. Informational only, it does not "
                "enable anything. LFP batteries usually need a periodic full charge "
                "to recalibrate their state of charge, see "
                "'full_charge_interval_days'."
            ),
            "examples": [None, "LFP"],
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
                "battery [d]. The last full charge is taken from the measured state "
                "of charge. When the next one falls due within the optimization "
                "horizon, the maximum state of charge is raised to "
                "'full_charge_soc_percentage'. None disables the periodic full "
                "charge. Ignored for electric vehicles."
            ),
            "examples": [None, 21],
        },
    )
    full_charge_soc_percentage: int = Field(
        default=100,
        ge=1,
        le=100,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "State of charge a periodic full charge has to reach [%]. May be "
                "above 'max_soc_percentage', which then applies on the other days."
            ),
            "examples": [100],
        },
    )
    full_charge_detect_percentage: int = Field(
        default=99,
        ge=1,
        le=100,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Measured state of charge from which the battery counts as fully "
                "charged [%]. Many battery management systems never report exactly "
                "100 %."
            ),
            "examples": [99],
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

    def to_genetic_pv_bat_param(self) -> "SolarPanelBatteryParameters":
        """Return SolarPanelBatteryParameters for the GENETIC optimizer."""
        from akkudoktoreos.devices.genetic.battery import SolarPanelBatteryParameters

        return SolarPanelBatteryParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            max_charge_power_w=self.max_charge_power_w,
            min_soc_percentage=self.min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
            charge_rates=self.charge_rates,
            grid_export_rates=self.grid_export_rates,
            levelized_cost_of_storage_kwh=self.levelized_cost_of_storage_amt_kwh,
        )

    def to_genetic_ev_bat_param(self) -> "ElectricVehicleParameters":
        """Return ElectricVehicleParameters for the GENETIC optimizer."""
        from akkudoktoreos.devices.genetic.battery import ElectricVehicleParameters

        return ElectricVehicleParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            charge_rates=self.charge_rates,
            max_charge_power_w=self.max_charge_power_w,
            min_soc_percentage=self.min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
            min_soc_deadline_datetime=self.min_soc_deadline_datetime,
            min_soc_max_duration_h=self.min_soc_max_duration_h,
            charge_start_datetime=self.charge_start_datetime,
            soc_value_steps=self.soc_value_steps,
        )

    # ------------------------------------------------------------------
    # GENETIC0 domain conversion
    # ------------------------------------------------------------------

    def to_genetic0_pv_bat_param(self) -> "Genetic0SolarPanelBatteryParameters":
        """Return Genetic0SolarPanelBatteryParameters for the GENETIC0 optimizer."""
        from akkudoktoreos.devices.genetic0.genetic0battery import (
            Genetic0SolarPanelBatteryParameters,
        )

        return Genetic0SolarPanelBatteryParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            max_charge_power_w=self.max_charge_power_w,
            min_soc_percentage=self.min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
        )

    def to_genetic0_ev_bat_param(self) -> "Genetic0ElectricVehicleParameters":
        """Return Genetic0ElectricVehicleParameters for the GENETI0C optimizer."""
        from akkudoktoreos.devices.genetic0.genetic0battery import (
            Genetic0ElectricVehicleParameters,
        )

        return Genetic0ElectricVehicleParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            charge_rates=self.charge_rates,
            max_charge_power_w=self.max_charge_power_w,
            min_soc_percentage=self.min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
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
        if self.full_charge_detect_percentage > self.full_charge_soc_percentage:
            raise ValueError("full_charge_detect_percentage must be <= full_charge_soc_percentage")
        if (
            self.min_charge_power_w is not None
            and self.max_charge_power_w is not None
            and self.min_charge_power_w > self.max_charge_power_w
        ):
            raise ValueError("min_charge_power_w must be <= max_charge_power_w")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_soc_factor(self) -> str:
        """Measurement key for SoC as factor of total capacity [0.0 ... 1.0]."""
        return f"{self.device_id}-soc-factor"

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
            self.measurement_key_power_l1_w,
            self.measurement_key_power_l2_w,
            self.measurement_key_power_l3_w,
            self.measurement_key_power_3_phase_sym_w,
        ]
