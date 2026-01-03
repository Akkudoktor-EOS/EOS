"""Measurement module to provide and store measurements.

This module provides a `Measurement` class to manage and update a sequence of
data records for measurements.

The measurements can be added programmatically or imported from a file or JSON string.
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.dataabc import DataImportMixin, DataRecord, DataSequence
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
)


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
        key_list = []
        for key in self.__class__.model_fields.keys():
            if key.endswith("_keys") and (value := getattr(self, key)):
                key_list.extend(value)
        return sorted(set(key_list))


class MeasurementDataRecord(DataRecord):
    """Represents a measurement data record containing various measurements at a specific datetime."""

    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        """Return the keys for the configured field like data."""
        keys = cls.config.measurement.keys
        # Add measurment keys that are needed/ handled by the resource/ device simulations.
        if cls.config.devices.measurement_keys:
            keys.extend(cls.config.devices.measurement_keys)
        return keys


class Measurement(SingletonMixin, DataImportMixin, DataSequence):
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

    def _energy_from_meter_readings(
        self,
        key: str,
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
    ) -> NDArray[Shape["*"], Any]:
        """Calculate an  energy values array indexed by fixed time intervals from energy metering data within an optional date range.

        Args:
            key: Key for energy meter readings.
            start_datetime (datetime): The start date for filtering the energy data (inclusive).
            end_datetime (datetime): The end date for filtering the energy data (exclusive).
            interval (duration): The fixed time interval.

        Returns:
            np.ndarray: A NumPy Array of the energy [kWh] per interval values calculated from
                        the meter readings.
        """
        size = self._interval_count(start_datetime, end_datetime, interval)

        energy_mr_array = self.key_to_array(
            key=key,
            start_datetime=start_datetime,
            end_datetime=end_datetime + interval,
            interval=interval,
            fill_method="time",
            boundary="context",
        )
        if energy_mr_array.size != size + 1:
            logging_msg = (
                f"'{key}' meter reading array size: {energy_mr_array.size}"
                f" does not fit to expected size: {size + 1}, {energy_mr_array}"
            )
            if energy_mr_array.size != 0:
                logger.error(logging_msg)
                raise ValueError(logging_msg)
            logger.debug(logging_msg)
            energy_array = np.zeros(size)
        elif np.any(energy_mr_array == None):
            # 'key_to_array()' creates None values array if no data records are available.
            # Array contains None value -> ignore
            debug_msg = f"'{key}' meter reading None: {energy_mr_array}"
            logger.debug(debug_msg)
            energy_array = np.zeros(size)
        else:
            # Calculate load per interval
            debug_msg = f"'{key}' meter reading: {energy_mr_array}"
            logger.debug(debug_msg)
            energy_array = np.diff(energy_mr_array)
            debug_msg = f"'{key}' energy calculation: {energy_array}"
            logger.debug(debug_msg)
        return energy_array

    def load_total_kwh(
        self,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
        interval: Optional[Duration] = None,
    ) -> NDArray[Shape["*"], Any]:
        """Calculate a total load energy values array indexed by fixed time intervals from load metering data within an optional date range.

        Args:
            start_datetime (datetime, optional): The start date for filtering the load data (inclusive).
            end_datetime (datetime, optional): The end date for filtering the load data (exclusive).
            interval (duration, optional): The fixed time interval. Defaults to 1 hour.

        Returns:
            np.ndarray: A NumPy Array of the total load energy [kWh] per interval values calculated from
                        the load meter readings.
        """
        if interval is None:
            interval = to_duration("1 hour")

        if len(self) < 1:
            # No data available
            if start_datetime is None or end_datetime is None:
                size = 0
            else:
                size = self._interval_count(start_datetime, end_datetime, interval)
            return np.zeros(size)

        if start_datetime is None:
            start_datetime = self.min_datetime
        if end_datetime is None:
            end_datetime = self.max_datetime.add(seconds=1)
        size = self._interval_count(start_datetime, end_datetime, interval)
        load_total_kwh_array = np.zeros(size)

        # Loop through all loads
        if isinstance(self.config.measurement.load_emr_keys, list):
            for key in self.config.measurement.load_emr_keys:
                # Calculate load per interval
                load_array = self._energy_from_meter_readings(
                    key=key,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=interval,
                )
                # Add calculated load to total load
                load_total_kwh_array += load_array
                debug_msg = f"Total load '{key}' calculation: {load_total_kwh_array}"
                logger.debug(debug_msg)

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

    def save(self) -> bool:
        """Save the measurements to persistent storage.

        Returns:
            True in case the measurements were saved, False otherwise.
        """
        # Use db storage if available
        saved_to_db = DataSequence.save(self)
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

    def load(self) -> bool:
        """Load measurements from persistent storage.

        Returns:
            True in case the measurements were loaded, False otherwise.
        """
        # Use db storage if available
        loaded_from_db = DataSequence.load(self)
        if not loaded_from_db:
            measurement_file_path = self._measurement_file_path()
            if measurement_file_path is None:
                return False
            if not measurement_file_path.exists():
                return False
            try:
                # Validate into a temporary instance
                loaded = self.__class__.model_validate_json(
                    measurement_file_path.read_text(encoding="utf-8")
                )

                # Explicitly add data records to the existing singleton
                for record in loaded.records:
                    self.insert_by_datetime(record)
            except Exception as e:
                logger.exception("Cannot load measurements")
        return True
