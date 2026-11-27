"""Conservative lumped air/water heat distribution, without a source COP or I/O.

Power entering this module is delivered thermal power in W. Capacities use J/K,
conductances W/K and time seconds. Air mixing and water/emitter parameters must
be identified separately from the building mass; an existing fitted aggregate
mass must not silently acquire the same slab capacity a second time.

The water path is a linear effective model, not a hydraulic network, a domestic
hot-water tank or a nonlinear radiator law. Exact ZOH integration does not by
itself enforce temperature bounds or source supply-temperature constraints.
"""

import math
from collections.abc import Sequence
from typing import Annotated, Literal, Self

import numpy as np
import numpy.typing as npt
from pydantic import Field, model_validator

from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    NonemptyText,
    ThermalBuildingRC2Parameters,
    ThermalModel,
)

FloatArray = npt.NDArray[np.float64]
Positive = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0)]
Nonnegative = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]


class DistributionTemperatureBounds(ThermalModel):
    """Explicit finite domain of an additional modeled temperature node."""

    min_temperature_c: FiniteNumber
    max_temperature_c: FiniteNumber

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        """Reject reversed bounds without inventing a permissible temperature."""
        if self.min_temperature_c > self.max_temperature_c:
            raise ValueError("Distribution temperature minimum must not exceed maximum")
        return self


class DirectDistributionSettings(ThermalModel):
    """Heat enters the room immediately, retaining the two-node building model."""

    kind: Literal["direct"] = "direct"


class AirFanLevel(ThermalModel):
    """Declared mixing conductance and optional continuous fan electricity.

    Conductance is calibrated or measured effective heat exchange. It is not
    automatically inferred from an appliance's nominal airflow or a fan command.
    """

    level_id: NonemptyText
    conductance_w_per_k: Positive
    electric_power_w: Nonnegative | None = None
    electricity_included_in_cop: bool | None = Field(default=None, strict=True)


class AirDistributionSettings(ThermalModel):
    """A near-unit air node exchanging heat with the occupied room.

    Capacity may include explicitly identified local solids. It is not a CFD
    volume, an appliance inlet measurement or a universal manufacturer value.
    The selected fan/mixing profile is exogenous, not optimized here. A native
    unit's fan behavior while its thermostat is OFF is not inferred.
    """

    kind: Literal["air"] = "air"
    air_capacity_j_per_k: Positive
    air_temperature_bounds: DistributionTemperatureBounds
    fan_levels: tuple[AirFanLevel, ...] = Field(min_length=1, max_length=32)
    default_fan_level_id: NonemptyText
    circulation_operation: Literal["fixed_entire_horizon"] | None = None

    @model_validator(mode="after")
    def named_fan_levels(self) -> Self:
        """Require unique levels and an explicitly selected default profile."""
        identifiers = [level.level_id for level in self.fan_levels]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Air fan level IDs must be unique")
        if self.default_fan_level_id not in identifiers:
            raise ValueError("Default fan level must reference a declared level")
        return self


class WaterBufferSettings(ThermalModel):
    """One additional mixed buffer with a known effective transfer to loop water.

    Exactly one transfer conductance or mass-flow/specific-heat pair is required.
    This lumped thermal chain does not imply a particular pipe arrangement,
    stratification, domestic-hot-water demand or interchangeable primary flows.
    """

    capacity_j_per_k: Positive
    temperature_bounds: DistributionTemperatureBounds
    transfer_conductance_w_per_k: Positive | None = None
    transfer_mass_flow_kg_per_s: Nonnegative | None = None
    fluid_specific_heat_j_per_kg_k: Positive | None = None

    @model_validator(mode="after")
    def require_identified_transfer(self) -> Self:
        """Keep the buffer transfer explicit without borrowing an unknown flow."""
        direct = self.transfer_conductance_w_per_k is not None
        flow = self.transfer_mass_flow_kg_per_s is not None
        if direct == flow:
            raise ValueError("Buffer requires exactly one transfer conductance or mass flow")
        if flow and self.fluid_specific_heat_j_per_kg_k is None:
            raise ValueError("Buffer mass flow requires declared fluid specific heat")
        if direct and self.fluid_specific_heat_j_per_kg_k is not None:
            raise ValueError("Specific heat is unused with an explicit buffer conductance")
        return self


class WaterDistributionSettings(ThermalModel):
    """Separate loop-water and metal/slab nodes with linear heat conductances.

    ``water_temperature_c`` is a declared well-mixed loop-return proxy. The
    emitter state describes metal/slab storage and must not be used as water
    return temperature. Linear UAs are local/calibrated approximations; flow
    dependence, mixing valves and hydraulic delays are not identified here.
    Primary source flow and buffer-to-water transfer are separate declarations.
    A scalar runtime flow override changes only the primary source flow.
    """

    kind: Literal["water"] = "water"
    emitter_type: Literal["radiator", "underfloor"]
    water_capacity_j_per_k: Positive
    emitter_capacity_j_per_k: Positive
    water_to_emitter_conductance_w_per_k: Positive
    emitter_to_room_conductance_w_per_k: Positive
    water_temperature_bounds: DistributionTemperatureBounds
    emitter_temperature_bounds: DistributionTemperatureBounds
    buffer: WaterBufferSettings | None = None
    source_mass_flow_kg_per_s: Nonnegative | None = None
    fluid_specific_heat_j_per_kg_k: Positive | None = None
    pump_electric_power_w: Nonnegative | None = None
    pump_electricity_included_in_cop: bool | None = Field(default=None, strict=True)
    circulation_operation: Literal["fixed_entire_horizon"] | None = None

    @model_validator(mode="after")
    def require_known_fluid_heat(self) -> Self:
        """A mass flow is not a heat conductance without the declared fluid Cp."""
        if (
            self.source_mass_flow_kg_per_s is not None
            and self.fluid_specific_heat_j_per_kg_k is None
        ):
            raise ValueError("Source mass flow requires declared fluid specific heat")
        return self


ThermalDistributionSettings = Annotated[
    DirectDistributionSettings | AirDistributionSettings | WaterDistributionSettings,
    Field(discriminator="kind"),
]


def distribution_state_names(distribution: ThermalDistributionSettings) -> tuple[str, ...]:
    """Return FULL state order, including room and building mass first."""
    names: tuple[str, ...] = ("room_temperature_c", "mass_temperature_c")
    if isinstance(distribution, AirDistributionSettings):
        return (*names, "air_temperature_c")
    if isinstance(distribution, WaterDistributionSettings):
        names = (*names, "water_temperature_c", "emitter_temperature_c")
        return (*names, "buffer_temperature_c") if distribution.buffer is not None else names
    if not isinstance(distribution, DirectDistributionSettings):
        raise TypeError("A typed thermal distribution setting is required")
    return names


def distribution_additional_state_names(
    distribution: ThermalDistributionSettings,
) -> tuple[str, ...]:
    """Return only extra state names, in the same order as FULL names after index 1."""
    return distribution_state_names(distribution)[2:]


def distribution_temperature_bounds(
    distribution: ThermalDistributionSettings,
) -> tuple[tuple[float, float], ...]:
    """Return bounds for extra nodes only, matching additional state names."""
    bounds: tuple[DistributionTemperatureBounds, ...]
    if isinstance(distribution, AirDistributionSettings):
        bounds = (distribution.air_temperature_bounds,)
    elif isinstance(distribution, WaterDistributionSettings):
        bounds = (distribution.water_temperature_bounds, distribution.emitter_temperature_bounds)
        if distribution.buffer:
            bounds = (*bounds, distribution.buffer.temperature_bounds)
    else:
        distribution_state_names(distribution)
        return ()
    return tuple((bound.min_temperature_c, bound.max_temperature_c) for bound in bounds)


def _fan(distribution: AirDistributionSettings, selector: str | float | None) -> AirFanLevel:
    if selector is not None and not isinstance(selector, str):
        raise ValueError("Air distribution requires a declared fan level ID")
    wanted = distribution.default_fan_level_id if selector is None else selector
    for level in distribution.fan_levels:
        if level.level_id == wanted:
            return level
    raise ValueError("Unknown air fan level ID")


def _number(value: float, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and numeric")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return float(value)


def source_temperature_state_index(distribution: ThermalDistributionSettings) -> int | None:
    """Return the water source recipient; air rating is not near-unit air temperature."""
    if isinstance(distribution, WaterDistributionSettings):
        return 4 if distribution.buffer is not None else 2
    distribution_state_names(distribution)
    return None


def source_flow_conductance_w_per_k(
    distribution: ThermalDistributionSettings, flow_or_fan: str | float | None = None
) -> float | None:
    """Return known primary mass-flow times fluid Cp, or None when not identified.

    A caller can impose Q <= G*(supply-source_node) only while the source is ON.
    A warm stored node may remain above a lowered OFF supply target. This helper
    neither constrains source output nor changes the separate buffer circuit.
    """
    if not isinstance(distribution, WaterDistributionSettings):
        if isinstance(distribution, AirDistributionSettings):
            _fan(distribution, flow_or_fan)
        elif flow_or_fan is not None:
            raise ValueError("Direct distribution does not accept a flow/fan signal")
        return None
    if isinstance(flow_or_fan, str):
        raise ValueError("Water source flow override must be in kg/s")
    mass_flow = (
        distribution.source_mass_flow_kg_per_s
        if flow_or_fan is None
        else _number(flow_or_fan, "Source mass flow", nonnegative=True)
    )
    if mass_flow is None:
        return None
    cp = distribution.fluid_specific_heat_j_per_kg_k
    if cp is None:
        raise ValueError("A source flow override requires declared fluid specific heat")
    return _number(mass_flow * cp, "Source flow heat conductance", nonnegative=True)


def distribution_electric_power_w(
    distribution: ThermalDistributionSettings, flow_or_fan: str | float | None = None
) -> tuple[float | None, tuple[str, ...]]:
    """Return declared continuous electricity outside COP, never useful thermal heat.

    The fixed-circulation assumption must be declared. Known electricity already
    included in the selected COP contributes zero additional grid load. Missing
    power/boundary/operation remains unknown and must not qualify cost claims.
    """
    if isinstance(distribution, DirectDistributionSettings):
        if flow_or_fan is not None:
            raise ValueError("Direct distribution does not accept a flow/fan signal")
        return 0.0, ()
    if isinstance(distribution, AirDistributionSettings):
        level = _fan(distribution, flow_or_fan)
        power, included = level.electric_power_w, level.electricity_included_in_cop
    else:
        source_flow_conductance_w_per_k(distribution, flow_or_fan)
        power = distribution.pump_electric_power_w
        included = distribution.pump_electricity_included_in_cop
    if distribution.circulation_operation is None:
        return None, ("distribution_circulation_operation_unknown",)
    flags = ("distribution_circulation_fixed_entire_horizon",)
    if included is True:
        return 0.0, (*flags, "distribution_electricity_already_in_cop")
    if power is None or included is None:
        return None, (*flags, "distribution_electricity_power_or_boundary_unknown")
    return power, (*flags, "distribution_electricity_additional_outside_cop")


def distribution_quality_flags(
    distribution: ThermalDistributionSettings, flow_or_fan: str | float | None = None
) -> tuple[str, ...]:
    """Describe physical approximations and unidentified source consistency."""
    _, flags = distribution_electric_power_w(distribution, flow_or_fan)
    if isinstance(distribution, DirectDistributionSettings):
        return flags
    if isinstance(distribution, AirDistributionSettings):
        return (*flags, "lumped_air_mixing_not_cfd_or_verified_native_fan_behavior")
    extra = [
        "linear_water_emitter_ua_not_full_hydraulics",
        "return_temperature_is_mixed_model_proxy",
    ]
    if source_flow_conductance_w_per_k(distribution, flow_or_fan) is None:
        extra.append("supply_output_consistency_unknown")
    if distribution.emitter_type == "underfloor":
        extra.append("slab_capacity_must_not_duplicate_building_mass")
    return (*flags, *extra)


def distribution_capacities_j_per_k(
    building: ThermalBuildingRC2Parameters, distribution: ThermalDistributionSettings
) -> FloatArray:
    """Derive absolute building capacities from thermal gain, then append declared nodes."""
    if not isinstance(building, ThermalBuildingRC2Parameters):
        raise TypeError("Distribution requires a thermal building model, not electrical gain")
    room = 3_600_000 / building.heat_gain_kelvin_per_kwh
    values = [room, room * building.mass_capacity_ratio]
    if isinstance(distribution, AirDistributionSettings):
        values.append(distribution.air_capacity_j_per_k)
    elif isinstance(distribution, WaterDistributionSettings):
        values.extend((distribution.water_capacity_j_per_k, distribution.emitter_capacity_j_per_k))
        if distribution.buffer is not None:
            values.append(distribution.buffer.capacity_j_per_k)
    else:
        distribution_state_names(distribution)
    capacities = np.asarray(values, dtype=float)
    if not np.isfinite(capacities).all() or np.any(capacities <= 0):
        raise ValueError("Absolute thermal capacities lie outside numerical support")
    return capacities


def _buffer_transfer(buffer: WaterBufferSettings) -> float:
    if buffer.transfer_conductance_w_per_k is not None:
        return buffer.transfer_conductance_w_per_k
    if buffer.transfer_mass_flow_kg_per_s is None or buffer.fluid_specific_heat_j_per_kg_k is None:
        raise ValueError("Buffer transfer is not identified")
    return _number(
        buffer.transfer_mass_flow_kg_per_s * buffer.fluid_specific_heat_j_per_kg_k,
        "Buffer transfer heat conductance",
        nonnegative=True,
    )


def _continuous_dynamics(
    building: ThermalBuildingRC2Parameters,
    distribution: ThermalDistributionSettings,
    flow_or_fan: str | float | None,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, float]:
    capacities = distribution_capacities_j_per_k(building, distribution)
    n = len(capacities)
    a, b, e = np.zeros((n, n)), np.zeros(n), np.zeros(n)
    loss = building.room_loss_per_hour * capacities[0] / 3600
    exchange = building.room_mass_exchange_per_hour * capacities[0] / 3600

    def connect(i: int, j: int, conductance: float) -> None:
        a[i, i] -= conductance / capacities[i]
        a[i, j] += conductance / capacities[i]
        a[j, j] -= conductance / capacities[j]
        a[j, i] += conductance / capacities[j]

    connect(0, 1, exchange)
    a[0, 0] -= loss / capacities[0]
    e[0] = loss / capacities[0]
    source = 0
    if isinstance(distribution, AirDistributionSettings):
        connect(0, 2, _fan(distribution, flow_or_fan).conductance_w_per_k)
        source = 2
    elif isinstance(distribution, WaterDistributionSettings):
        source_flow_conductance_w_per_k(distribution, flow_or_fan)
        connect(0, 3, distribution.emitter_to_room_conductance_w_per_k)
        connect(2, 3, distribution.water_to_emitter_conductance_w_per_k)
        source = 2
        if distribution.buffer is not None:
            connect(2, 4, _buffer_transfer(distribution.buffer))
            source = 4
    elif flow_or_fan is not None:
        raise ValueError("Direct distribution does not accept a flow/fan signal")
    b[source] = 1 / capacities[source]
    if not all(np.isfinite(value).all() for value in (a, b, e)) or not math.isfinite(loss):
        raise ValueError("Distribution coefficients lie outside numerical support")
    return a, b, e, capacities, loss


def build_distribution_dynamics(
    building: ThermalBuildingRC2Parameters,
    distribution: ThermalDistributionSettings,
    *,
    seconds: float,
    flow_or_fan: str | float | None = None,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Build exact ZOH A/B/E: next_T = A*T + B*Qthermal_W + E*Tout_C.

    The first two states are always room and mass. Seconds may be fractional;
    zero returns identity and zero input vectors. Inputs and the selected
    conductances remain constant during this interval. No COP is applied here.
    """
    from scipy.linalg import expm

    dt = _number(seconds, "Step duration", nonnegative=True)
    a, b, e, _, _ = _continuous_dynamics(building, distribution, flow_or_fan)
    n = len(b)
    augmented = np.zeros((n + 2, n + 2))
    augmented[:n, :n], augmented[:n, n], augmented[:n, n + 1] = a, b, e
    transition = expm(augmented * dt)
    if not np.isfinite(transition).all():
        raise ValueError("Distribution transition is nonfinite")
    return transition[:n, :n], transition[:n, n], transition[:n, n + 1]


class DistributionStepResult(ThermalModel):
    """Exact model-integrated heat accounting, never new measured source data."""

    state_names: tuple[str, ...]
    temperatures_c: tuple[FiniteNumber, ...]
    source_thermal_energy_j: FiniteNumber
    delivered_to_room_energy_j: FiniteNumber
    outdoor_heat_loss_j: FiniteNumber
    stored_energy_change_j: FiniteNumber
    building_stored_energy_change_j: FiniteNumber
    distribution_stored_energy_change_j: FiniteNumber
    total_energy_balance_error_j: FiniteNumber
    building_energy_balance_error_j: FiniteNumber
    buffer_to_water_energy_j: FiniteNumber | None = None
    modeled_return_temperature_c: FiniteNumber | None = None
    return_temperature_is_measured: Literal[False] = False
    source_flow_conductance_w_per_k: FiniteNumber | None = None
    endpoint_temperature_bound_violation_c: Nonnegative
    quality_flags: tuple[str, ...]


def step_distribution(
    building: ThermalBuildingRC2Parameters,
    distribution: ThermalDistributionSettings,
    temperatures_c: Sequence[float],
    *,
    thermal_power_w: float,
    outdoor_temperature_c: float,
    seconds: float,
    flow_or_fan: str | float | None = None,
) -> DistributionStepResult:
    """Integrate one interval and audit source/building/distribution heat balances.

    Delivered-to-room and outdoor-loss energies are signed. A warmer room can
    heat a colder emitter; outdoor heat gain is negative loss. Temperature-domain
    diagnostics cover endpoints only, not all continuous extrema. The caller
    must enforce source supply-temperature/flow limits and temperature bounds.
    Pump/fan electrical power is deliberately absent from useful thermal heat.
    """
    from scipy.linalg import expm

    dt = _number(seconds, "Step duration", nonnegative=True)
    power = _number(thermal_power_w, "Delivered thermal power", nonnegative=True)
    outside = _number(outdoor_temperature_c, "Outdoor temperature")
    a, b, e, capacities, loss = _continuous_dynamics(building, distribution, flow_or_fan)
    n = len(b)
    if len(temperatures_c) != n:
        raise ValueError("Distribution temperature state length does not match its nodes")
    initial = np.array([_number(value, "State temperature") for value in temperatures_c])
    # State integrals are extra passive accumulators; this also works for an
    # isolated buffer or zero duration without inverting a singular state matrix.
    augmented = np.zeros((2 * n + 2, 2 * n + 2))
    augmented[:n, :n], augmented[:n, n], augmented[:n, n + 1] = a, b, e
    augmented[n + 2 :, :n] = np.eye(n)
    value = expm(augmented * dt) @ np.r_[initial, power, outside, np.zeros(n)]
    if not np.isfinite(value).all():
        raise ValueError("Distribution step produced nonfinite temperatures or integrals")
    final, integral = value[:n], value[n + 2 :]
    source_energy = power * dt
    delivered = source_energy
    buffer_energy = None
    if isinstance(distribution, AirDistributionSettings):
        delivered = _fan(distribution, flow_or_fan).conductance_w_per_k * (
            integral[2] - integral[0]
        )
    elif isinstance(distribution, WaterDistributionSettings):
        delivered = distribution.emitter_to_room_conductance_w_per_k * (integral[3] - integral[0])
        if distribution.buffer is not None:
            buffer_energy = _buffer_transfer(distribution.buffer) * (integral[4] - integral[2])
    outdoors = loss * (integral[0] - outside * dt)
    delta = capacities * (final - initial)
    bound_error = 0.0
    for k, (lower, upper) in enumerate(distribution_temperature_bounds(distribution), start=2):
        bound_error = max(
            bound_error,
            lower - min(initial[k], final[k]),
            max(initial[k], final[k]) - upper,
        )
    return DistributionStepResult(
        state_names=distribution_state_names(distribution),
        temperatures_c=tuple(final),
        source_thermal_energy_j=source_energy,
        delivered_to_room_energy_j=float(delivered),
        outdoor_heat_loss_j=float(outdoors),
        stored_energy_change_j=float(sum(delta)),
        building_stored_energy_change_j=float(sum(delta[:2])),
        distribution_stored_energy_change_j=float(sum(delta[2:])),
        total_energy_balance_error_j=float(sum(delta) - source_energy + outdoors),
        building_energy_balance_error_j=float(sum(delta[:2]) - delivered + outdoors),
        buffer_to_water_energy_j=buffer_energy,
        modeled_return_temperature_c=float(final[2])
        if isinstance(distribution, WaterDistributionSettings)
        else None,
        source_flow_conductance_w_per_k=source_flow_conductance_w_per_k(distribution, flow_or_fan),
        endpoint_temperature_bound_violation_c=max(0.0, bound_error),
        quality_flags=distribution_quality_flags(distribution, flow_or_fan),
    )
