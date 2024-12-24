"""Measurement module to provide and store measurements.

This module provides a `Measurement` class to manage and update a sequence of
data records for measurements.

The measurements can be added programmatically or imported from a file or JSON string.
"""

from typing import List, Optional

from pydantic import Field

from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.dataabc import DataImportMixin, DataRecord, DataSequence


class MeasurementDataRecord(DataRecord):
    """Represents a measurement data record containing various measurements at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.
    """

    measurement_total_load: Optional[float] = Field(
        default=None, ge=0, description="Measured total load [W]"
    )


class Measurement(SingletonMixin, DataImportMixin, DataSequence):
    """Singleton class that holds measurement data records.

    Measurements can be provided programmatically or read from JSON string or file.
    """

    records: List[MeasurementDataRecord] = Field(
        default_factory=list, description="List of measurement data records"
    )


def get_measurement() -> Measurement:
    """Gets the EOS measurement data."""
    return Measurement()
