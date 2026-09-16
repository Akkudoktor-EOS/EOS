"""Estimate effective model capacity from independent SoC anchors and DC power."""

from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.measurement.quality import SampleQuality

if TYPE_CHECKING:
    from akkudoktoreos.measurement.measurement import MeasurementChannelSettings


class BatteryCapacityEstimationSettings(SettingsBaseModel):
    """Only a battery-terminal DC power channel is supported, not inverter AC."""

    power_key: str = Field(min_length=1)
    positive_power: Literal["charging", "discharging"]
    measurement_boundary: Literal["battery_dc"] = "battery_dc"
    min_soc_change_percentage: float = Field(default=20, gt=0, le=100)
    max_duration_hours: float = Field(default=168, gt=0, le=744)


class CapacityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BatteryCapacityRequest(CapacityModel):
    """Caller attests both anchors are independent of the capacity being fitted.

    End is the first confirmed full point when using the default end SoC.
    Voltage/current anchors must be independently established for the chemistry.
    """

    start: AwareDatetime
    end: AwareDatetime
    start_soc_percentage: float = Field(ge=0, le=100)
    end_soc_percentage: float = Field(default=100, ge=0, le=100)
    soc_reference: Literal["bms", "external_calibration", "voltage_current_anchor"]
    store_estimate: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.end.timestamp() <= self.start.timestamp():
            raise ValueError("end must be after start.")
        return self


class BatteryCapacityEstimate(CapacityModel):
    battery_id: str
    start: AwareDatetime
    end: AwareDatetime
    soc_reference: str
    start_soc_percentage: float
    end_soc_percentage: float
    estimated_capacity_wh: float
    configured_capacity_wh: float
    capacity_change_percentage: float
    model_end_soc_percentage_unclipped: float
    model_soc_error_percentage_points: float
    charge_energy_wh: float
    discharge_energy_wh: float
    stored_energy_change_wh: float
    charging_efficiency: float
    discharging_efficiency: float
    power_key: str
    positive_power: str
    integration_method: str
    coverage_seconds: float
    samples_used: int
    warnings: list[str]


def estimate_capacity(
    request: BatteryCapacityRequest,
    settings: BatteryCapacityEstimationSettings,
    channel: "MeasurementChannelSettings",
    samples: list[tuple[datetime, float | None, SampleQuality]],
    *,
    battery_id: str,
    capacity_wh: float,
    charging_efficiency: float,
    discharging_efficiency: float,
) -> BatteryCapacityEstimate:
    """Fit C in C * delta_soc = eta_c * E_charge - E_discharge / eta_d.

    No clipping at 0/100%, extrapolation, gap filling or efficiency fitting.
    Linear segments are split at zero before applying directional efficiencies.
    """
    left, right = request.start.timestamp(), request.end.timestamp()
    if right - left > settings.max_duration_hours * 3600:
        raise ValueError("Requested period exceeds capacity_estimation.max_duration_hours.")
    delta_soc = (request.end_soc_percentage - request.start_soc_percentage) / 100
    if abs(delta_soc) * 100 + 1e-9 < settings.min_soc_change_percentage:
        raise ValueError(
            "Independent SoC change is too small; full-to-full cannot identify capacity."
        )
    if channel.quantity != "power":
        raise ValueError("Capacity estimation requires signed battery DC power in W or kW.")
    if not isfinite(capacity_wh) or capacity_wh <= 0:
        raise ValueError("Configured capacity must be positive and finite.")
    if not all(isfinite(e) and 0 < e <= 1 for e in (charging_efficiency, discharging_efficiency)):
        raise ValueError("Battery efficiencies must be finite and in (0, 1].")
    points = []
    for time, value, quality in samples:
        if time.tzinfo is None or time.utcoffset() is None:
            raise ValueError("Explicit sample timezone required.")
        points.append((time.timestamp(), value, quality))
    points.sort(key=lambda row: row[0])
    if any(a[0] == b[0] for a, b in zip(points, points[1:])):
        raise ValueError("Duplicate power sample timestamps.")
    factor = (1000 if channel.unit == "kW" else 1) * (
        1 if settings.positive_power == "charging" else -1
    )
    charge = discharge = coverage = 0.0
    cumulative_net = 0.0
    minimum_net = maximum_net = 0.0
    used = set()
    cursor = left
    for (a, va, qa), (b, vb, qb) in zip(points, points[1:]):
        lo, hi = max(left, a), min(right, b)
        if hi <= lo:
            continue
        if lo > cursor + 1e-6 or b - a > channel.max_gap_seconds:
            raise ValueError("Incomplete power coverage or sample gap exceeds max_gap_seconds.")
        if (
            any(v is None or isinstance(v, bool) or not isfinite(v) for v in (va, vb))
            or qa.status != "measured"
            or qb.status != "measured"
        ):
            raise ValueError(
                "Capacity estimation requires finite measured power samples throughout."
            )
        if qb.reset or qa.generation != qb.generation:
            raise ValueError("Power sensor reset or generation change within the period.")
        p, q = va * factor, vb * factor
        if channel.integration_method == "linear":
            slope = (q - p) / (b - a)
            p, q = p + slope * (lo - a), p + slope * (hi - a)
        else:
            q = p
        duration = hi - lo
        if p * q < 0:
            first = duration * abs(p) / (abs(p) + abs(q))
            parts = [(p * first / 7200), (q * (duration - first) / 7200)]
        else:
            parts = [(p + q) * duration / 7200]
        charge += sum(max(0, energy) for energy in parts)
        discharge += sum(max(0, -energy) for energy in parts)
        for energy in parts:
            cumulative_net += (
                energy * charging_efficiency if energy >= 0 else energy / discharging_efficiency
            )
            minimum_net = min(minimum_net, cumulative_net)
            maximum_net = max(maximum_net, cumulative_net)
        coverage += duration
        cursor = hi
        used.update((a, b))
    if cursor < right - 1e-6 or abs(coverage - (right - left)) > 1e-6:
        raise ValueError("Incomplete power coverage; no extrapolation to the SoC anchors.")
    net = charge * charging_efficiency - discharge / discharging_efficiency
    capacity = net / delta_soc
    if not isfinite(capacity) or capacity <= 0:
        raise ValueError("Energy flow disagrees with SoC change; check polarity and anchors.")
    if (
        request.start_soc_percentage + minimum_net / capacity * 100 < -1e-6
        or request.start_soc_percentage + maximum_net / capacity * 100 > 100 + 1e-6
    ):
        raise ValueError(
            "Fitted SoC leaves 0..100% within the period; check anchors and use the first full point."
        )
    model_end = request.start_soc_percentage + net / capacity_wh * 100
    warnings = [
        "Conditional estimate: SoC anchors, DC measurement boundary and configured efficiencies must be correct.",
        "A single interval cannot independently identify both capacity and efficiencies.",
        "The active capacity_wh is unchanged.",
    ]
    if request.soc_reference == "voltage_current_anchor":
        warnings.append(
            "Voltage/current anchors depend on chemistry, temperature and operating conditions."
        )
    return BatteryCapacityEstimate(
        battery_id=battery_id,
        start=request.start,
        end=request.end,
        soc_reference=request.soc_reference,
        start_soc_percentage=request.start_soc_percentage,
        end_soc_percentage=request.end_soc_percentage,
        estimated_capacity_wh=capacity,
        configured_capacity_wh=capacity_wh,
        capacity_change_percentage=(capacity / capacity_wh - 1) * 100,
        model_end_soc_percentage_unclipped=model_end,
        model_soc_error_percentage_points=model_end - request.end_soc_percentage,
        charge_energy_wh=charge,
        discharge_energy_wh=discharge,
        stored_energy_change_wh=net,
        charging_efficiency=charging_efficiency,
        discharging_efficiency=discharging_efficiency,
        power_key=settings.power_key,
        positive_power=settings.positive_power,
        integration_method=channel.integration_method,
        coverage_seconds=coverage,
        samples_used=len(used),
        warnings=warnings,
    )
