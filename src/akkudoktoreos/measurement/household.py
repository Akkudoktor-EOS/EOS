"""AC household balances on the intersection of actual measurement support."""

from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from akkudoktoreos.measurement.energy import EnergyInterval


class HouseholdInput(BaseModel):
    """A non-overlapping AC branch; polarity normalizes the sensor's sign."""

    model_config = ConfigDict(extra="forbid")
    key: str
    branch: str = Field(min_length=1)
    role: Literal["site", "grid", "pv", "battery", "inverter", "ev", "device"]
    polarity: Literal[-1, 1] = 1


class HouseholdSettings(BaseModel):
    """Fixed topology, never user-supplied executable balance expressions.

    Grid import, PV production and battery/inverter discharge are positive.
    EV/device inputs are positive consumption, subtracted only from derived loads.
    """

    model_config = ConfigDict(extra="forbid")
    topology: Literal["direct", "separate_ac", "hybrid_ac"]
    inputs: list[HouseholdInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_topology(self) -> "HouseholdSettings":
        keys = [item.key for item in self.inputs]
        branches = [item.branch for item in self.inputs]
        if len(keys) != len(set(keys)) or len(branches) != len(set(branches)):
            raise ValueError("Duplicate measurement key or physical branch in household balance.")
        roles = {item.role for item in self.inputs} - {"ev", "device"}
        allowed = {
            "direct": {"site"},
            "separate_ac": {"grid", "pv", "battery"},
            # Additional AC PV branches are independent of the hybrid's net output.
            # Its DC PV/battery must not be added again as separate inputs.
            "hybrid_ac": {"grid", "inverter", "pv"},
        }[self.topology]
        required = {"site"} if self.topology == "direct" else {"grid"}
        if self.topology == "hybrid_ac":
            required.add("inverter")
        if not required <= roles or not roles <= allowed:
            raise ValueError("Inputs do not match the selected AC topology.")
        return self


def household_intervals(
    settings: HouseholdSettings,
    convert: Callable[[str, datetime, datetime, int], list[EnergyInterval]],
    start: datetime,
    end: datetime,
    interval_seconds: int = 900,
) -> dict[str, list[EnergyInterval]]:
    """Integrate each source again over shared support, never prorate partial sums."""
    series = {item.key: convert(item.key, start, end, interval_seconds) for item in settings.inputs}
    output: dict[str, list[EnergyInterval]] = {}
    for name, excluded in (
        ("site", {"ev", "device"}),
        ("household", {"device"}),
        ("base", set()),
    ):
        inputs = [item for item in settings.inputs if item.role not in excluded]
        result = []
        for index, template in enumerate(series[inputs[0].key]):
            rows = [series[item.key][index] for item in inputs]
            # Partition at every coverage boundary; keep only the intersection.
            boundaries = sorted({t for row in rows for pair in row.coverage_ranges for t in pair})
            support = [
                (a, b)
                for a, b in zip(boundaries, boundaries[1:])
                if all(any(lo <= a and b <= hi for lo, hi in row.coverage_ranges) for row in rows)
            ]
            total = 0.0
            methods = {method for row in rows for method in row.methods}
            flags = {flag for row in rows for flag in row.flags}
            for a, b in support:
                for item in inputs:
                    # The range is at most one target interval, but may be fractional seconds.
                    parts = convert(item.key, a, b, interval_seconds)
                    value = parts[0].energy_wh
                    if value is None:
                        raise ValueError("Inconsistent measurement support during balance.")
                    sign = -1 if item.role in ("ev", "device") else 1
                    total += sign * item.polarity * value
                    methods.update(parts[0].methods)
                    flags.update(parts[0].flags)
            coverage = sum((b - a).total_seconds() for a, b in support)
            complete = abs(coverage - (template.end - template.start).total_seconds()) < 1e-6
            if coverage and total < -1e-6:
                flags.add("negative_balance")
            result.append(
                EnergyInterval(
                    template.start,
                    template.end,
                    total if complete else None,
                    total if coverage else None,
                    coverage,
                    "complete"
                    if complete
                    else "partial"
                    if coverage
                    else "invalid"
                    if flags
                    else "missing",
                    tuple(sorted(methods | {"ac_balance"})),
                    tuple(sorted(flags)),
                    tuple(support),
                )
            )
        output[name] = result
    return output
