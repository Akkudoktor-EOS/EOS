"""Measurement module to provide and store measurements.

This module provides a `Measurement` class to manage and update a sequence of
data records for measurements.

The measurements can be added programmatically or imported from a file or JSON string.
"""

import json
import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import numpy as np
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.dataabc import DataImportMixin, DataRecord, DataSequence
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.measurement.batterycapacity import (
    BatteryCapacityEstimate,
    BatteryCapacityRequest,
    estimate_capacity,
)
from akkudoktoreos.measurement.energy import EnergyInterval
from akkudoktoreos.measurement.household import HouseholdSettings, household_intervals
from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    compare_datetimes,
    to_datetime,
    to_duration,
)

# Limits of one quality-aware energy query (`Measurement.energy_intervals`).
ENERGY_QUERY_MAX_SECONDS = 31 * 86400
ENERGY_QUERY_MAX_INTERVALS = 10000


class MeasurementChannelSettings(SettingsBaseModel):
    """Meaning of a raw measurement channel; no conversion is performed on storage."""

    quantity: Literal["power", "cumulative_energy", "interval_energy"]
    unit: Literal["W", "kW", "Wh", "kWh"]
    integration_method: Optional[Literal["hold", "linear"]] = None
    max_gap_seconds: Optional[int] = Field(default=None, gt=0, strict=True)
    interval_seconds: Optional[int] = Field(default=None, gt=0, strict=True)
    timestamp_reference: Optional[Literal["start", "end"]] = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "MeasurementChannelSettings":
        """Reject ambiguous units and time semantics before accepting a channel."""
        if self.quantity == "power":
            if self.unit not in ("W", "kW"):
                raise ValueError("Power channels require W or kW.")
            if self.integration_method is None or self.max_gap_seconds is None:
                raise ValueError("Power channels require integration_method and max_gap_seconds.")
        else:
            if self.unit not in ("Wh", "kWh"):
                raise ValueError("Energy channels require Wh or kWh.")
            if self.integration_method is not None:
                raise ValueError("integration_method is only applicable to power channels.")
        if self.quantity == "interval_energy":
            if self.interval_seconds is None or self.timestamp_reference is None:
                raise ValueError(
                    "Interval energy requires interval_seconds and timestamp_reference."
                )
            if self.max_gap_seconds is not None:
                raise ValueError("Interval energy uses explicit intervals, not max_gap_seconds.")
        elif self.interval_seconds is not None or self.timestamp_reference is not None:
            raise ValueError("Interval metadata is only applicable to interval_energy channels.")
        return self


class MeasurementCommonSettings(SettingsBaseModel):
    """Measurement Configuration."""

    historic_hours: Optional[int] = Field(
        default=2 * 365 * 24,
        ge=0,
        json_schema_extra={
            "description": "Number of hours into the past for measurement data",
            "examples": [2 * 365 * 24],
        },
    )

    channels: dict[str, MeasurementChannelSettings] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Typed raw measurement channels keyed by measurement key."
        },
    )

    household: Optional[HouseholdSettings] = Field(
        default=None,
        json_schema_extra={
            "description": "Optional household energy balance definition.",
            "examples": [None],
        },
    )
    energy_context_seconds: int = Field(default=86400, gt=0, le=604800, strict=True)

    @model_validator(mode="after")
    def validate_channels(self) -> "MeasurementCommonSettings":
        """Preserve the kWh meter contract of legacy keys and avoid record collisions."""
        for key, channel in self.channels.items():
            if (
                not key
                or key != key.strip()
                or key.startswith("_")
                or hasattr(DataRecord, key)
                or key in DataRecord.model_fields
                or key == "sample_quality"
            ):
                raise ValueError(f"Invalid or reserved measurement channel key: {key!r}")
            for name in type(self).model_fields:
                if name.endswith("_emr_keys") and key in (getattr(self, name) or []):
                    if channel.quantity != "cumulative_energy" or channel.unit != "kWh":
                        raise ValueError(
                            f"Legacy meter key {key!r} must remain cumulative_energy in kWh."
                        )
        if self.household is not None:
            for item in self.household.inputs:
                if item.key not in self.keys:
                    raise ValueError(f"Unknown household measurement key: {item.key!r}")
        return self

    load_emr_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "The keys of the measurements that are energy meter readings of a load [kWh].",
            "examples": [["load0_emr"]],
        },
    )

    grid_export_emr_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "The keys of the measurements that are energy meter readings of energy export to grid [kWh].",
            "examples": [["grid_export_emr"]],
        },
    )

    grid_import_emr_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "The keys of the measurements that are energy meter readings of energy import from grid [kWh].",
            "examples": [["grid_import_emr"]],
        },
    )

    pv_production_emr_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "The keys of the measurements that are PV production energy meter readings [kWh].",
            "examples": [["pv1_emr"]],
        },
    )

    ## Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def keys(self) -> list[str]:
        """The keys of the measurements that can be stored."""
        key_list = list(self.channels)
        for key in self.__class__.model_fields.keys():
            if key.endswith("_keys") and (value := getattr(self, key)):
                key_list.extend(value)
        return sorted(set(key_list))


class MeasurementDataRecord(DataRecord):
    """Represents a measurement data record containing various measurements at a specific datetime."""

    sample_quality: dict[str, SampleQuality] = Field(default_factory=dict)

    @classmethod
    def record_keys(cls) -> list[str]:
        """Quality is stored alongside values, not exposed as a numeric channel."""
        return [key for key in super().record_keys() if key != "sample_quality"]

    @classmethod
    def record_keys_writable(cls) -> list[str]:
        """Only the typed sample path writes quality, never numeric import paths."""
        return [key for key in super().record_keys_writable() if key != "sample_quality"]

    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        """Return the keys for the configured field like data."""
        keys = cls.config.measurement.keys
        # Add measurment keys that are needed/ handled by the resource/ device simulations.
        if cls.config.devices.measurement_keys:
            keys.extend(cls.config.devices.measurement_keys)
        return keys


class Measurement(SingletonMixin, DataImportMixin, DataSequence[MeasurementDataRecord]):
    """Singleton class that holds measurement data records.

    Measurements can be provided programmatically or read from JSON string or file.
    """

    records: list[MeasurementDataRecord] = Field(
        default_factory=list, json_schema_extra={"description": "list of measurement data records"}
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if hasattr(self, "_initialized"):
            return
        super().__init__(*args, **kwargs)

    @property
    def _silent_load_meters_reported(self) -> set[str]:
        """Load meters already reported as silent; warned once until they report again.

        Created lazily and cached on the instance, like the record locks.
        """
        try:
            return object.__getattribute__(self, "_silent_load_meters_reported_instance")
        except AttributeError:
            reported: set[str] = set()
            object.__setattr__(self, "_silent_load_meters_reported_instance", reported)
            return reported

    def _measurement_file_path(self) -> Optional[Path]:
        """Path to measurements file (may be used optional to database)."""
        try:
            return self.config.general.data_folder_path / "measurement.json"
        except Exception:
            logger.error(
                "Path for measurements is missing. Please configure data folder path or database!"
            )
        return None

    def _interval_count(
        self, start_datetime: DateTime, end_datetime: DateTime, interval: Duration
    ) -> int:
        """Calculate number of intervals between two datetimes.

        Args:
            start_datetime: Starting datetime
            end_datetime: Ending datetime
            interval: Time duration for each interval

        Returns:
            Number of intervals as integer

        Raises:
            ValueError: If end_datetime is before start_datetime
            ValueError: If interval is zero or negative
        """
        if end_datetime < start_datetime:
            raise ValueError("end_datetime must be after start_datetime")

        if interval.total_seconds() <= 0:
            raise ValueError("interval must be positive")

        # Calculate difference in seconds
        diff_seconds = end_datetime.diff(start_datetime).total_seconds()
        interval_seconds = interval.total_seconds()

        # Return ceiling of division to include partial intervals
        return int(np.ceil(diff_seconds / interval_seconds))

    async def import_samples(self, samples: list[MeasurementSample]) -> None:
        """Validate the whole batch before replacing samples; omitted keys stay untouched."""
        for sample in samples:
            self._energy_channel(sample.key)
        for sample in samples:
            dt = to_datetime(sample.date_time)
            await self.update_value(dt, sample.key, sample.value)
            record = await self.db_get_record(DatabaseTimestamp.from_datetime(dt))
            if not isinstance(record, MeasurementDataRecord):
                raise RuntimeError("Measurement sample was not stored.")
            record.sample_quality[sample.key] = sample.quality.model_copy(deep=True)
            await self.db_mark_dirty_record(record)

    async def insert_by_datetime(self, record: MeasurementDataRecord) -> None:
        """Merge quality by channel as well as the ordinary measurement fields."""
        await super().insert_by_datetime(record)
        if (
            isinstance(record, MeasurementDataRecord)
            and record.sample_quality
            and record.date_time is not None
        ):
            stored = await self.db_get_record(DatabaseTimestamp.from_datetime(record.date_time))
            if not isinstance(stored, MeasurementDataRecord):
                raise RuntimeError("Measurement record was not stored.")
            stored.sample_quality = stored.sample_quality | record.sample_quality
            await self.db_mark_dirty_record(stored)

    def _energy_channel(self, key: str) -> MeasurementChannelSettings:
        channel = self.config.measurement.channels.get(key)
        if channel is not None:
            return channel
        for name in type(self.config.measurement).model_fields:
            if name.endswith("_emr_keys") and key in (getattr(self.config.measurement, name) or []):
                return MeasurementChannelSettings(quantity="cumulative_energy", unit="kWh")
        raise ValueError(f"No energy channel definition for {key!r}.")

    async def _energy_converter(
        self,
        keys: list[str],
        start: datetime,
        end: datetime,
    ) -> Callable[[str, datetime, datetime, int], list[EnergyInterval]]:
        """Load a window once, including configured boundary context.

        The query limits that protect the API are checked by the public wrappers
        (`_check_energy_query`), not here.

        Args:
            keys: Energy channels to convert.
            start: Start of the window.
            end: End of the window.

        Returns:
            Converter from a channel and a sub-window to energy intervals.
        """
        from akkudoktoreos.measurement.energy import energy_intervals

        if any(dt.tzinfo is None or dt.utcoffset() is None for dt in (start, end)):
            raise ValueError("Explicit timezone required.")
        if compare_datetimes(to_datetime(end), to_datetime(start)).le:
            raise ValueError("Energy queries require a positive range.")
        channels = {key: self._energy_channel(key) for key in keys}
        context = self.config.measurement.energy_context_seconds
        records = [
            record
            async for record in self.db_iterate_records(
                DatabaseTimestamp.from_datetime(to_datetime(start) - timedelta(seconds=context)),
                DatabaseTimestamp.from_datetime(
                    to_datetime(end) + timedelta(seconds=context, microseconds=1)
                ),
            )
        ]
        samples = {
            key: [
                (record.date_time, record.configured_data[key])
                for record in records
                if key in record.configured_data and record.date_time is not None
            ]
            for key in keys
        }
        quality = {
            key: {
                record.date_time.timestamp(): record.sample_quality[key]
                for record in records
                if key in record.sample_quality and record.date_time is not None
            }
            for key in keys
        }
        timestamps = {key: [dt.timestamp() for dt, _ in values] for key, values in samples.items()}
        for key, channel in channels.items():
            duration = channel.interval_seconds
            if (
                channel.quantity == "interval_energy"
                and duration is not None
                and any(b - a < duration for a, b in zip(timestamps[key], timestamps[key][1:]))
            ):
                raise ValueError(f"Overlapping source energy intervals for {key!r}.")

        def convert(
            key: str, left: datetime, right: datetime, interval_seconds: int
        ) -> list[EnergyInterval]:
            lo = max(0, bisect_left(timestamps[key], left.timestamp()) - 1)
            hi = bisect_right(timestamps[key], right.timestamp()) + 1
            return energy_intervals(
                samples[key][lo:hi], channels[key], left, right, interval_seconds, quality[key]
            )

        return convert

    @staticmethod
    def _check_energy_query(start: datetime, end: datetime, interval_seconds: int) -> None:
        """Enforce the limits of one public energy query (31 days, 10000 intervals).

        Raises:
            ValueError: If the query exceeds a limit.
        """
        seconds = to_datetime(start).diff(to_datetime(end), False).total_seconds()
        if not 0 < seconds <= ENERGY_QUERY_MAX_SECONDS:
            raise ValueError("Energy queries require a positive range of at most 31 days.")
        if type(interval_seconds) is not int or interval_seconds <= 0:
            raise ValueError("Positive integer interval_seconds required.")
        if seconds / interval_seconds > ENERGY_QUERY_MAX_INTERVALS:
            raise ValueError("At most 10000 output intervals per query.")

    async def household_intervals(
        self, start_datetime: datetime, end_datetime: datetime, interval_seconds: int = 900
    ) -> dict[str, list[EnergyInterval]]:
        """Return site, household without EV, and base without configured devices."""
        settings = self.config.measurement.household
        if settings is None:
            raise ValueError("No household balance configured.")
        # Revalidate references even after an in-place configuration mutation.
        settings = HouseholdSettings.model_validate(settings.model_dump())
        self._check_energy_query(start_datetime, end_datetime, interval_seconds)
        convert = await self._energy_converter(
            [item.key for item in settings.inputs], start_datetime, end_datetime
        )
        return household_intervals(
            settings, convert, start_datetime, end_datetime, interval_seconds
        )

    async def energy_intervals(
        self,
        key: str,
        start_datetime: datetime,
        end_datetime: datetime,
        interval_seconds: int = 900,
    ) -> list[EnergyInterval]:
        """Convert a typed channel; load and PV energy use the same conversion.

        Read only explicitly stored values for this key: another channel's timestamp
        must not introduce a synthetic outage. Explicit null values remain barriers.
        """
        self._check_energy_query(start_datetime, end_datetime, interval_seconds)
        convert = await self._energy_converter([key], start_datetime, end_datetime)
        return convert(key, start_datetime, end_datetime, interval_seconds)

    async def meter_energy_kwh(
        self,
        keys: list[str],
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
    ) -> dict[str, NDArray[Shape["*"], float]]:
        """Energy per interval of energy channels, read from the store once.

        Uses the quality-aware conversion (`energy_intervals`): a cumulative meter is
        interpolated at the interval boundaries, so a meter reporting at arbitrary
        stamps yields exactly the energy of each interval. The records of the whole
        period are loaded once for all channels, so a gap converts the same way
        wherever it lies in the period.

        Args:
            keys: Energy channels, e.g. configured meter reading keys.
            start_datetime: Start of the first interval; intervals start here.
            end_datetime: End of the period. A last partial interval is extended to a
                full interval.
            interval: Interval length, a positive whole number of seconds.

        Returns:
            Energy in kWh per interval for each key, NaN where the channel does not
            fully cover the interval (before its first or after its last reading, a
            reset, or a gap longer than the channel's ``max_gap_seconds``).

        Raises:
            ValueError: If the period or interval is invalid or a key has no energy
                channel definition.
        """
        interval_seconds = int(interval.total_seconds())
        if interval_seconds <= 0 or interval_seconds != interval.total_seconds():
            raise ValueError("Interval must be a positive whole number of seconds.")
        size = self._interval_count(start_datetime, end_datetime, interval)
        if size == 0:
            return {key: np.zeros(0) for key in keys}
        period_end = start_datetime.add(seconds=size * interval_seconds)
        convert = await self._energy_converter(keys, start_datetime, period_end)
        return {
            key: np.asarray(
                [
                    np.nan if slot.energy_wh is None else slot.energy_wh / 1000.0
                    for slot in convert(key, start_datetime, period_end, interval_seconds)
                ],
                dtype=float,
            )
            for key in keys
        }

    def _valid_reading_keys(self, record: MeasurementDataRecord, keys: Iterable[str]) -> list[str]:
        """Keys of ``record`` that carry a valid reading.

        A reading counts when it is a finite number whose sample quality is neither
        invalid nor unavailable, as for the energy conversion.
        """
        if record.date_time is None:
            return []
        found = []
        for key in keys:
            value = record.configured_data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(value):
                continue
            quality = record.sample_quality.get(key)
            if quality is not None and quality.status in ("invalid", "unavailable"):
                continue
            found.append(key)
        return found

    async def keys_first_datetime(self, keys: list[str]) -> dict[str, DateTime]:
        """First stamp with a valid reading per key, in one forward pass.

        Scans forward from the oldest record in windows that double in size and stops
        once every key is found, instead of reading the keys' whole history.

        Args:
            keys: Record keys, e.g. meter reading keys.

        Returns:
            First stamp per key, in the configured timezone like `min_datetime`; keys
            without a valid reading are missing.
        """
        timezone = self.config.general.timezone
        wanted = set(keys)
        firsts: dict[str, DateTime] = {}
        oldest = await self.min_datetime()
        newest = await self.max_datetime()
        if not wanted or oldest is None or newest is None:
            return firsts
        end_of_store = newest.add(seconds=1)
        step_seconds = 86400
        window_start = oldest
        while len(firsts) < len(wanted) and compare_datetimes(window_start, end_of_store).lt:
            window_end = window_start.add(seconds=step_seconds)
            async for record in self.db_iterate_records(
                DatabaseTimestamp.from_datetime(window_start),
                DatabaseTimestamp.from_datetime(window_end),
            ):
                for key in self._valid_reading_keys(record, wanted - firsts.keys()):
                    firsts[key] = to_datetime(record.date_time, in_timezone=timezone)
            window_start = window_end
            step_seconds *= 2
        return firsts

    async def keys_last_datetime(self, keys: list[str]) -> dict[str, DateTime]:
        """Last stamp with a valid reading per key, in one backward pass.

        Scans backward from the newest record in windows that double in size and
        stops once every key is found.

        Args:
            keys: Record keys, e.g. meter reading keys.

        Returns:
            Last stamp per key, in the configured timezone like `max_datetime`; keys
            without a valid reading are missing.
        """
        timezone = self.config.general.timezone
        wanted = set(keys)
        lasts: dict[str, DateTime] = {}
        oldest = await self.min_datetime()
        newest = await self.max_datetime()
        if not wanted or oldest is None or newest is None:
            return lasts
        step_seconds = 86400
        window_end = newest.add(seconds=1)
        while len(lasts) < len(wanted) and compare_datetimes(window_end, oldest).gt:
            window_start = window_end.subtract(seconds=step_seconds)
            # Newest valid reading per key in this window; keys found in a newer
            # window keep their stamp.
            window_lasts: dict[str, DateTime] = {}
            async for record in self.db_iterate_records(
                DatabaseTimestamp.from_datetime(window_start),
                DatabaseTimestamp.from_datetime(window_end),
            ):
                for key in self._valid_reading_keys(record, wanted - lasts.keys()):
                    window_lasts[key] = to_datetime(record.date_time, in_timezone=timezone)
            lasts.update(window_lasts)
            window_end = window_start
            step_seconds *= 2
        return lasts

    async def estimate_battery_capacity(
        self, battery_id: str, request: BatteryCapacityRequest
    ) -> BatteryCapacityEstimate:
        """Read signed DC samples without mutating raw data or the active capacity."""
        batteries = [
            b for b in (self.config.devices.batteries or {}).values() if b.device_id == battery_id
        ]
        if len(batteries) != 1:
            raise ValueError("Require exactly one configured battery with this device_id.")
        battery = batteries[0]
        settings = battery.capacity_estimation
        if settings is None:
            raise ValueError("Configure devices.batteries[device_id].capacity_estimation first.")
        if request.end.timestamp() - request.start.timestamp() > settings.max_duration_hours * 3600:
            raise ValueError("Requested period exceeds capacity_estimation.max_duration_hours.")
        channel = self._energy_channel(settings.power_key)
        if channel.quantity != "power":
            raise ValueError("Capacity estimation requires a battery DC power channel.")
        context = channel.max_gap_seconds
        if context is None:
            raise ValueError("Capacity estimation requires a bounded power channel gap.")
        samples: list[tuple[datetime, Optional[float], SampleQuality]] = []
        async for record in self.db_iterate_records(
            DatabaseTimestamp.from_datetime(
                to_datetime(request.start) - timedelta(seconds=context)
            ),
            DatabaseTimestamp.from_datetime(
                to_datetime(request.end) + timedelta(seconds=context, microseconds=1)
            ),
        ):
            if record.date_time is not None and settings.power_key in record.configured_data:
                samples.append(
                    (
                        record.date_time,
                        record.configured_data[settings.power_key],
                        record.sample_quality.get(settings.power_key, SampleQuality()),
                    )
                )
                if len(samples) > 250000:
                    raise ValueError("More than 250000 power samples; request a shorter period.")
        # Raises a BatteryResolutionError (a ValueError) naming the battery if
        # its profile cannot be resolved.
        parameters = battery.resolve_operating_parameters()
        return estimate_capacity(
            request,
            settings,
            channel,
            samples,
            battery_id=battery.device_id,
            capacity_wh=parameters.capacity_wh,
            charging_efficiency=parameters.charging_efficiency,
            discharging_efficiency=parameters.discharging_efficiency,
        )

    async def load_total_kwh(
        self,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
        interval: Optional[Duration] = None,
    ) -> NDArray[Shape["*"], float]:
        """Total load energy per interval from the configured load meters.

        Each meter is converted with `meter_energy_kwh`: the cumulative reading is
        interpolated at the interval boundaries, and an interval a meter does not
        fully cover is unknown (NaN), never zero load.

        Args:
            start_datetime (datetime, optional): Start of the first interval. Defaults
                to the first measurement.
            end_datetime (datetime, optional): End of the period (exclusive). Defaults to
                just after the last measurement.
            interval (duration, optional): The fixed time interval. Defaults to 1 hour.

        Returns:
            np.ndarray: Total load energy [kWh] per interval; NaN where any load meter
                lacks full coverage or no load meter is configured.

        Raises:
            ValueError: If the period cannot be determined or is invalid.
        """
        if interval is None:
            interval = to_duration("1 hour")
        if len(self) < 1 and (start_datetime is None or end_datetime is None):
            # No data and no period: nothing to report.
            return np.zeros(0)
        if start_datetime is None:
            start_datetime = await self.min_datetime()
        if end_datetime is None:
            end_datetime = await self.max_datetime()
            if end_datetime:
                end_datetime = end_datetime.add(seconds=1)
        if start_datetime is None or end_datetime is None:
            raise ValueError("Start and end datetimes are required for energy calculation")
        size = self._interval_count(start_datetime, end_datetime, interval)
        if size == 0:
            return np.zeros(0)
        load_keys = self.config.measurement.load_emr_keys or []
        if not load_keys:
            return np.full(size, np.nan)

        load_total_kwh_array = np.zeros(size)
        meters_kwh = await self.meter_energy_kwh(load_keys, start_datetime, end_datetime, interval)
        for key, meter_kwh in meters_kwh.items():
            if np.isnan(meter_kwh).all():
                message = (
                    f"Load meter '{key}' gives no usable energy for {start_datetime} .. "
                    f"{end_datetime} (no readings, resets or gaps longer than its "
                    "max_gap_seconds): the total load of this period is unknown."
                )
                if key in self._silent_load_meters_reported:
                    logger.debug(message)
                else:
                    self._silent_load_meters_reported.add(key)
                    logger.warning(message)
            else:
                self._silent_load_meters_reported.discard(key)
            load_total_kwh_array += meter_kwh
        logger.debug(f"Total load calculation: {load_total_kwh_array}")
        return load_total_kwh_array

    # ----------------------- Measurement Database Protocol ---------------------

    def db_namespace(self) -> str:
        return "Measurement"

    def db_keep_datetime(self) -> Optional[DateTime]:
        """Earliest datetime from which database records should be retained.

        Used when removing old records from database to free space.

        Returns:
            Datetime or None.
        """
        return to_datetime().subtract(hours=self.config.measurement.historic_hours)

    async def save(self) -> bool:
        """Save the measurements to persistent storage.

        Returns:
            True in case the measurements were saved, False otherwise.
        """
        # Use db storage if available
        saved_to_db = await DataSequence.save(self)
        if not saved_to_db:
            measurement_file_path = self._measurement_file_path()
            if measurement_file_path is None:
                return False
            try:
                measurement_file_path.write_text(
                    self.model_dump_json(indent=4),
                    encoding="utf-8",
                    newline="\n",
                )
            except Exception as e:
                logger.exception("Cannot save measurements")
        return True

    async def load(self) -> bool:
        """Load measurements from persistent storage.

        Returns:
            True in case the measurements were loaded, False otherwise.
        """
        # Use db storage if available
        loaded_from_db = await DataSequence.load(self)
        if not loaded_from_db:
            measurement_file_path = self._measurement_file_path()
            if measurement_file_path is None:
                return False
            if not measurement_file_path.exists():
                return False
            try:
                # Measurement is a singleton; validating another Measurement
                # returns this instance instead of restoring serialized records.
                payload = json.loads(measurement_file_path.read_text(encoding="utf-8"))
                # Validate the complete file before modifying the live records.
                records = [
                    MeasurementDataRecord.model_validate(data)
                    for data in payload.get("records", [])
                ]
                for record in records:
                    await self.insert_by_datetime(record)
            except Exception as e:
                logger.exception("Cannot load measurements")
                return False
        return True
