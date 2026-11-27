"""Time grid and forecast series of a planning request.

The planning runs on a grid of 900 or 3600 s slots that starts at the slot containing the run
time (the EMS start) and covers the control horizon, followed by a tail of forecast that only
serves the value of stored energy. Forecasts are read from the prediction providers on the grid
of the local day, in energy per slot (Wh) and price per Wh. Missing values inside the control
horizon are an error; a tail that is not completely covered is shortened to the part that is.
"""

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.optimization.forecast import bounded_forecast_array
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration


@dataclass(frozen=True)
class TimeGrid:
    """The slot grid of one planning.

    Attributes:
        interval_seconds: Slot duration [s], 900 or 3600.
        start: Start of slot 0 (the run time rounded down to the grid).
        observation: Moment of the run (within slot 0, or equal to its start).
        origin: Start of the local day of ``start``; forecasts are read from there.
        first: Index of ``start`` on the grid that begins at ``origin``.
        control_slots: Number of slots of the control horizon.
        requested_tail_slots: Tail the configuration asks for, bounded by the forecast budget.
        prediction_hours: Forecast duration of the configuration [h].
    """

    interval_seconds: int
    start: DateTime
    observation: DateTime
    origin: DateTime
    first: int
    control_slots: int
    requested_tail_slots: int
    prediction_hours: int

    @property
    def slot_duration_h(self) -> float:
        """Slot duration [h]."""
        return self.interval_seconds / 3600

    @property
    def slots_per_hour(self) -> int:
        """Slots per hour."""
        return 3600 // self.interval_seconds

    @property
    def first_slot_fraction(self) -> float:
        """Part of slot 0 that is still ahead of the run (1.0 on a slot boundary).

        The run start is rounded down to the slot while measurements and plan apply from the
        unrounded run time: a run at 10:45 with hourly slots has a quarter of slot 0 left.
        """
        elapsed = (self.observation - self.start).total_seconds()
        if elapsed <= 0.0:
            return 1.0
        if elapsed >= self.interval_seconds:
            logger.debug(
                "Run time {} is not within slot 0 starting {}; planning a full first slot.",
                self.observation,
                self.start,
            )
            return 1.0
        return (self.interval_seconds - elapsed) / self.interval_seconds

    def slot_start(self, slot: int) -> DateTime:
        """Return the start of a slot."""
        return self.start.add(seconds=slot * self.interval_seconds)

    def slot_of(self, moment: DateTime, *, round_up: bool = False) -> int:
        """Return the slot index of a moment relative to slot 0 (may be negative)."""
        timezone = self.start.timezone
        local = moment.in_timezone(timezone) if timezone is not None else moment
        exact = (local - self.start).total_seconds() / self.interval_seconds
        return math.ceil(exact - 1e-9) if round_up else math.floor(exact + 1e-9)


def build_time_grid(
    *,
    interval_seconds: int,
    horizon_hours: int,
    tail_horizon_hours: int,
    prediction_hours: Optional[int],
    start: DateTime,
    observation: DateTime,
) -> TimeGrid:
    """Build the grid of a planning from the configured sizes and the EMS times.

    Raises:
        ValueError: For an unsupported interval, a nonpositive horizon or a missing
            prediction horizon.
    """
    if interval_seconds not in (900, 3600):
        raise ValueError(
            f"The optimization interval must be 900 or 3600 s, not {interval_seconds}."
        )
    if prediction_hours is None or prediction_hours <= 0:
        raise ValueError("Configure a positive prediction horizon.")
    per_hour = 3600 // interval_seconds
    control = horizon_hours * per_hour
    if control <= 0:
        raise ValueError("Forecast does not cover the positive control horizon.")
    budget = max(0, int(prediction_hours * per_hour) - control)
    origin = start.start_of("day")
    return TimeGrid(
        interval_seconds=interval_seconds,
        start=start,
        observation=observation,
        origin=origin,
        first=int((start - origin).total_seconds() / interval_seconds),
        control_slots=control,
        requested_tail_slots=min(tail_horizon_hours * per_hour, budget),
        prediction_hours=int(prediction_hours),
    )


@dataclass(frozen=True)
class Forecasts:
    """Forecast series on the grid, starting at slot 0, control horizon plus effective tail.

    Slot 0 is not reduced to the part still ahead; see ``Forecasts.control``.

    Attributes:
        pv_wh: PV generation per slot [Wh].
        load_wh: Base load per slot [Wh].
        price_per_wh: Import price per slot [EUR/Wh].
        tariff_per_wh: Feed-in tariff per slot [EUR/Wh].
        control_slots: Length of the control part.
        midnight_series: Original common-length series from midnight for compatibility responses.
    """

    pv_wh: np.ndarray
    load_wh: np.ndarray
    price_per_wh: np.ndarray
    tariff_per_wh: np.ndarray
    control_slots: int
    midnight_series: Optional[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = None

    @property
    def tail_slots(self) -> int:
        """Slots of the effective tail."""
        return len(self.pv_wh) - self.control_slots


async def read_series(
    prediction: Any,
    grid: TimeGrid,
    key: str,
    *,
    energy: bool = False,
    supplied: Optional[list[float]] = None,
    updated: Optional[list[bool]] = None,
) -> np.ndarray:
    """Read one forecast series on the local-day grid, without filling gaps.

    Args:
        prediction: The prediction container.
        grid: Time grid of the planning.
        key: Prediction key of the series.
        energy: The series is power [W]; convert to energy per slot [Wh].
        supplied: Values the caller provides instead (from local midnight on the grid).
        updated: One element list remembering whether the providers were updated already.

    Returns:
        The series from the local midnight; missing intervals are NaN.
    """
    if supplied is not None:
        return np.asarray(supplied, dtype=float)
    if updated is not None and not updated[0]:
        await prediction.update_data()
        updated[0] = True
    end = grid.start.add(hours=grid.prediction_hours)
    values = await bounded_forecast_array(
        prediction,
        key=key,
        start_datetime=grid.origin,
        end_datetime=end,
        interval=to_duration(grid.interval_seconds),
    )
    if energy:
        values = values * (grid.interval_seconds / 3600)
    return values


def finite_prefix(values: np.ndarray, start: int, limit: int) -> int:
    """Return the end of the contiguous finite stretch of ``values[start:]`` capped at ``limit``."""
    end = min(len(values), limit)
    missing = np.flatnonzero(~np.isfinite(values[start:end]))
    if missing.size:
        end = start + int(missing[0])
    return end


def slice_forecasts(
    grid: TimeGrid,
    pv: np.ndarray,
    load: np.ndarray,
    prices: np.ndarray,
    tariffs: np.ndarray,
) -> Forecasts:
    """Check the series and cut them to the control horizon plus the effective tail.

    Raises:
        ValueError: For an empty forecast, one that does not cover the control horizon,
            missing or invalid values in it, or negative PV or load energy.
    """
    length = min(len(pv), len(load), len(prices), len(tariffs))
    if not length:
        raise ValueError("Forecast series must be nonempty.")
    pv, load, prices, tariffs = (
        np.asarray(v[:length], dtype=float) for v in (pv, load, prices, tariffs)
    )
    first = grid.first
    last = first + grid.control_slots
    if length < last:
        raise ValueError("Forecast does not cover the positive control horizon.")
    for name, values in (
        ("PV", pv),
        ("load", load),
        ("prices", prices),
        ("feed-in tariff", tariffs),
    ):
        missing = [index for index in range(first, last) if not math.isfinite(values[index])]
        if missing:
            left = grid.origin.add(seconds=missing[0] * grid.interval_seconds)
            right = left.add(seconds=grid.interval_seconds)
            raise ValueError(
                f"Missing or invalid {name} within the control horizon. "
                f"First invalid interval: [{left.isoformat()}, {right.isoformat()}); "
                f"{len(missing)} invalid interval(s)."
            )
    if any(value < 0 for values in (pv, load) for value in values[first:last]):
        raise ValueError("PV and load energy must be nonnegative.")
    requested = grid.control_slots + grid.requested_tail_slots
    available = requested
    limiting = []
    for name, values in (
        ("load", load),
        ("pv", pv),
        ("import price", prices),
        ("feed-in tariff", tariffs),
    ):
        end = finite_prefix(values, first, first + requested) - first
        if end < requested:
            limiting.append(name)
        available = min(available, end)
    tail = max(0, available - grid.control_slots)
    if available < requested:
        logger.warning(
            "Tail forecast shortened: requested {} h, effective {:g} h; limited by {}. "
            "Continuation starts at slot {}.",
            grid.requested_tail_slots / grid.slots_per_hour,
            tail * grid.slot_duration_h,
            ", ".join(limiting),
            available,
        )
    stop = first + grid.control_slots + tail
    return Forecasts(
        pv_wh=pv[first:stop].copy(),
        load_wh=load[first:stop].copy(),
        price_per_wh=prices[first:stop].copy(),
        tariff_per_wh=tariffs[first:stop].copy(),
        control_slots=grid.control_slots,
        midnight_series=(pv, load, prices, tariffs),
    )


def without_missing(series: pd.Series) -> pd.Series:
    """Drop records without a value (an adapter's meter reading, pandas NA)."""
    return series[series.notna()]
