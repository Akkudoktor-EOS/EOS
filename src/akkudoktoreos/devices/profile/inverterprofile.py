"""Inverter profile documents and their runtime form.

An inverter profile describes the technical data of an inverter model: its
nominal AC power, the efficiency of the DC→AC conversion (battery discharge)
and of the AC→DC conversion (battery charge from the grid) over the load
fraction, the AC charge power limit of the model and an optional power derating
over the ambient temperature.

The load fraction of a conversion is its AC power relative to the nominal power
of the inverter. Efficiency curves and the derating curve are read linearly
between their breakpoints and held at their end values beyond them, so every
lookup yields a value.

``InverterProfileSettings`` is the inverter profile type of the profile store
(kind ``"inverter"``). The store compiles a document into the immutable
``InverterProfile`` that the device settings and the simulation query::

    from akkudoktoreos.devices.profile.profilestore import get_profile_store

    profile = get_profile_store().get(InverterProfileSettings, "generic-hybrid")
    efficiency = profile.dc_to_ac_efficiency_at(load_fraction=0.3)
    power_fraction = profile.power_fraction_at(temperature_degC=50.0)
"""

from dataclasses import dataclass
from typing import Any, ClassVar, List, Optional

from pydantic import Field, field_validator, model_validator

from akkudoktoreos.devices.profile.profilecurve import (
    PrecomputedCurve,
    validate_curve_points,
)
from akkudoktoreos.devices.profile.profilestore import (
    ProfileBaseModel,
    ProfileDocument,
)

# ---------------------------------------------------------------------------
# Runtime form
# ---------------------------------------------------------------------------


def _curve_value(curve: PrecomputedCurve, query: float) -> float:
    """Return the value of a linear, clamped curve, which always exists."""
    value = curve.lookup(query)
    if value is None:  # Only possible for REJECT or NONE curves, never built here.
        raise ValueError(f"Curve has no value at {query}.")
    return value


@dataclass(frozen=True)
class InverterProfile:
    """Ready-to-use technical data of an inverter model.

    Instances are produced by ``compile_inverter_profile``, usually through the
    profile store.

    Attributes:
        inverter_model: Model name of the inverter.
        nominal_power_w: Nominal AC power of the model [W], None for a generic
            profile.
        max_ac_charge_power_w: AC charge power limit of the model [W], None if
            only the nominal power limits AC charging.
        ac_charge_limits_total_charge: Whether the AC charge power limit also
            limits PV charging of the battery.
        dc_to_ac_efficiency: DC→AC efficiency over the load fraction.
        ac_to_dc_efficiency: AC→DC efficiency over the load fraction.
        power_derating: Allowed fraction of the nominal power over the ambient
            temperature [degC], None without derating.
    """

    inverter_model: str
    nominal_power_w: Optional[float]
    max_ac_charge_power_w: Optional[float]
    ac_charge_limits_total_charge: bool
    dc_to_ac_efficiency: PrecomputedCurve
    ac_to_dc_efficiency: PrecomputedCurve
    power_derating: Optional[PrecomputedCurve] = None

    def dc_to_ac_efficiency_at(self, load_fraction: float) -> float:
        """Return the DC→AC efficiency at a load fraction."""
        return _curve_value(self.dc_to_ac_efficiency, load_fraction)

    def ac_to_dc_efficiency_at(self, load_fraction: float) -> float:
        """Return the AC→DC efficiency at a load fraction."""
        return _curve_value(self.ac_to_dc_efficiency, load_fraction)

    def power_fraction_at(self, temperature_degC: float) -> float:
        """Return the allowed fraction of the nominal power at an ambient temperature.

        1.0 without a derating curve.
        """
        if self.power_derating is None:
            return 1.0
        return _curve_value(self.power_derating, temperature_degC)


def compile_inverter_profile(document: "InverterProfileSettings") -> InverterProfile:
    """Precompute an inverter profile document into an ``InverterProfile``."""
    derating = document.power_derating
    return InverterProfile(
        inverter_model=document.inverter_model,
        nominal_power_w=document.nominal_power_w,
        max_ac_charge_power_w=document.max_ac_charge_power_w,
        ac_charge_limits_total_charge=document.ac_charge_limits_total_charge,
        dc_to_ac_efficiency=document.dc_to_ac_efficiency.to_curve(),
        ac_to_dc_efficiency=document.ac_to_dc_efficiency.to_curve(),
        power_derating=derating.to_curve() if derating is not None else None,
    )


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class InverterEfficiencyCurve(ProfileBaseModel):
    """Conversion efficiency of an inverter over its load fraction.

    The load fraction is the AC power of the conversion relative to the nominal
    power of the inverter. The efficiency is read linearly between the
    breakpoints and held at the end values beyond them.
    """

    description: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Free-form note about the source or meaning of this curve.",
            "examples": ["Datasheet efficiency curve at nominal DC voltage"],
        },
    )
    load_fraction: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": (
                "Strictly increasing load fraction breakpoints: AC power relative to "
                "the nominal power [0.0 ... ]."
            ),
            "examples": [[0.05, 0.1, 0.2, 0.5, 1.0]],
        },
    )
    efficiency: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": "Efficiency at each load fraction breakpoint (0.0 ... 1.0].",
            "examples": [[0.88, 0.93, 0.96, 0.97, 0.96]],
        },
    )

    @field_validator("load_fraction")
    @classmethod
    def _valid_load_fractions(cls, values: List[float]) -> List[float]:
        if any(value < 0 for value in values):
            raise ValueError("load fraction breakpoints must be non-negative")
        return values

    @field_validator("efficiency")
    @classmethod
    def _valid_efficiencies(cls, values: List[float]) -> List[float]:
        if any(not 0.0 < value <= 1.0 for value in values):
            raise ValueError("efficiency values must be in the range (0, 1]")
        return values

    @model_validator(mode="after")
    def _valid_points(self) -> "InverterEfficiencyCurve":
        validate_curve_points(self.load_fraction, self.efficiency, "load_fraction", "efficiency")
        return self

    def to_curve(self) -> PrecomputedCurve:
        """Return the runtime curve."""
        return PrecomputedCurve.from_points(self.load_fraction, self.efficiency)


class PowerDeratingCurve(ProfileBaseModel):
    """Allowed fraction of the nominal power of an inverter over the ambient temperature.

    The fraction is read linearly between the breakpoints and held at the end
    values beyond them.
    """

    description: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Free-form note about the source or meaning of this curve.",
            "examples": ["Datasheet derating at nominal DC voltage"],
        },
    )
    temperature_degC: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": "Strictly increasing ambient temperature breakpoints [degC].",
            "examples": [[45.0, 60.0]],
        },
    )
    power_fraction: List[float] = Field(
        ...,
        min_length=2,
        json_schema_extra={
            "description": (
                "Allowed fraction of the nominal power at each temperature breakpoint "
                "[0.0 ... 1.0]."
            ),
            "examples": [[1.0, 0.5]],
        },
    )

    @field_validator("power_fraction")
    @classmethod
    def _valid_power_fractions(cls, values: List[float]) -> List[float]:
        if any(not 0.0 <= value <= 1.0 for value in values):
            raise ValueError("power fraction values must be in the range [0, 1]")
        return values

    @model_validator(mode="after")
    def _valid_points(self) -> "PowerDeratingCurve":
        validate_curve_points(
            self.temperature_degC, self.power_fraction, "temperature_degC", "power_fraction"
        )
        return self

    def to_curve(self) -> PrecomputedCurve:
        """Return the runtime curve."""
        return PrecomputedCurve.from_points(self.temperature_degC, self.power_fraction)


class InverterProfileSettings(ProfileBaseModel, ProfileDocument[InverterProfile]):
    """Technical data of an inverter model.

    A profile contains every value the computation uses. The one exception is
    the nominal power of a generic profile: its efficiency curves are given over
    the load fraction, which scales with any nominal power, so it may leave the
    nominal power out. The inverter device then has to provide it in its
    ``profile_adjustment``. Unknown keys are rejected.

    Use ``get_profile_store().get(InverterProfileSettings, name)`` to obtain the
    compiled ``InverterProfile``.

    Attributes:
        schema_version: Version of the JSON document format.
        inverter_model: Model name of the inverter.
        nominal_power_w: Nominal AC power of the model, None for a generic
            profile.
        max_ac_charge_power_w: AC charge power limit of the model.
        ac_charge_limits_total_charge: Whether the AC charge power limit also
            limits PV charging of the battery.
        dc_to_ac_efficiency: DC→AC efficiency curve over the load fraction.
        ac_to_dc_efficiency: AC→DC efficiency curve over the load fraction.
        power_derating: Optional power derating over the ambient temperature.
    """

    profile_kind: ClassVar[str] = "inverter"

    schema_version: str = Field(
        default="1.0",
        json_schema_extra={
            "description": "Version of this schema, for forward-compatibility.",
            "examples": ["1.0"],
        },
    )
    inverter_model: str = Field(
        ...,
        min_length=1,
        json_schema_extra={
            "description": "Model name of the inverter.",
            "examples": ["Generic hybrid inverter"],
        },
    )
    nominal_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Nominal AC power of this inverter model [W]. Required for the profile "
                "of a specific model. A generic profile leaves it out; the inverter "
                "device then has to set 'profile_adjustment.nominal_power_w'."
            ),
            "examples": [None, 10000.0],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "AC charge power limit of this inverter model [W]: the maximum AC power "
                "drawn from the grid to charge the battery. None if only the nominal "
                "power limits AC charging."
            ),
            "examples": [None, 5000.0],
        },
    )
    ac_charge_limits_total_charge: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Whether the AC charge power limit also limits PV charging of the "
                "battery in the same slot. False if PV can charge the battery on top "
                "of the AC charge power."
            ),
            "examples": [False],
        },
    )
    dc_to_ac_efficiency: InverterEfficiencyCurve = Field(
        ...,
        json_schema_extra={
            "description": "DC→AC efficiency (battery discharge) over the load fraction."
        },
    )
    ac_to_dc_efficiency: InverterEfficiencyCurve = Field(
        ...,
        json_schema_extra={
            "description": "AC→DC efficiency (battery charge from the grid) over the load fraction."
        },
    )
    power_derating: Optional[PowerDeratingCurve] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Allowed fraction of the nominal power over the ambient temperature. "
                "None if the inverter is not derated."
            )
        },
    )

    def compile_profile(self, **options: Any) -> InverterProfile:
        """Compile this document for the profile store.

        Raises:
            TypeError: If an option is given; inverter profiles take none.
        """
        if options:
            raise TypeError(f"Unknown inverter profile options: {sorted(options)}")
        return compile_inverter_profile(self)
