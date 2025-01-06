"""Abstract and base classes for predictions.

This module provides classes for managing and processing prediction data in a flexible, configurable manner.
It includes classes to handle configurations, record structures, sequences, and containers for prediction data,
enabling efficient storage, retrieval, and manipulation of prediction records.

This module is designed for use in predictive modeling workflows, facilitating the organization, serialization,
and manipulation of configuration and prediction data in a clear, scalable, and structured manner.
"""

from typing import List, Optional

from pendulum import DateTime
from pydantic import Field, computed_field

from akkudoktoreos.core.coreabc import MeasurementMixin
from akkudoktoreos.core.dataabc import (
    DataBase,
    DataContainer,
    DataImportProvider,
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.utils.datetimeutil import to_duration

logger = get_logger(__name__)


class PredictionBase(DataBase, MeasurementMixin):
    """Base class for handling prediction data.

    Enables access to EOS configuration data (attribute `config`) and EOS measurement data
    (attribute `measurement`).
    """

    pass


class PredictionRecord(DataRecord):
    """Base class for prediction records, enabling dynamic access to fields defined in derived classes.

    Fields can be accessed and mutated both using dictionary-style access (`record['field_name']`)
    and attribute-style access (`record.field_name`).

    Attributes:
        date_time (Optional[AwareDatetime]): Aware datetime indicating when the prediction record applies.

    Configurations:
        - Allows mutation after creation.
        - Supports non-standard data types like `datetime`.
    """

    pass


class PredictionSequence(DataSequence):
    """A managed sequence of PredictionRecord instances with list-like behavior.

    The PredictionSequence class provides an ordered, mutable collection of PredictionRecord
    instances, allowing list-style access for adding, deleting, and retrieving records. It also
    supports advanced data operations such as JSON serialization, conversion to Pandas Series,
    and sorting by timestamp.

    Attributes:
        records (List[PredictionRecord]): A list of PredictionRecord instances representing
                                          individual prediction data points.
        record_keys (Optional[List[str]]): A list of field names (keys) expected in each
                                           PredictionRecord.

    Note:
        Derived classes have to provide their own records field with correct record type set.

    Usage:
        # Example of creating, adding, and using PredictionSequence
        class DerivedSequence(PredictionSquence):
            records: List[DerivedPredictionRecord] = Field(default_factory=list,
                                                        description="List of prediction records")

        seq = DerivedSequence()
        seq.insert(DerivedPredictionRecord(date_time=datetime.now(), temperature=72))
        seq.insert(DerivedPredictionRecord(date_time=datetime.now(), temperature=75))

        # Convert to JSON and back
        json_data = seq.to_json()
        new_seq = DerivedSequence.from_json(json_data)

        # Convert to Pandas Series
        series = seq.key_to_series('temperature')
    """

    # To be overloaded by derived classes.
    records: List[PredictionRecord] = Field(
        default_factory=list, description="List of prediction records"
    )


class PredictionStartEndKeepMixin(PredictionBase):
    """A mixin to manage start, end, and historical retention datetimes for prediction data.

    The starting datetime for prediction data generation is provided by the energy management
    system. Predictions cannot be computed if this value is `None`.
    """

    def historic_hours_min(self) -> int:
        """Return the minimum historic prediction hours for specific data.

        To be implemented by derived classes if default 0 is not appropriate.
        """
        return 0

    # Computed field for end_datetime and keep_datetime
    @computed_field  # type: ignore[prop-decorator]
    @property
    def end_datetime(self) -> Optional[DateTime]:
        """Compute the end datetime based on the `start_datetime` and `prediction_hours`.

        Ajusts the calculated end time if DST transitions occur within the prediction window.

        Returns:
            Optional[DateTime]: The calculated end datetime, or `None` if inputs are missing.
        """
        if self.start_datetime and self.config.prediction_hours:
            end_datetime = self.start_datetime + to_duration(
                f"{self.config.prediction_hours} hours"
            )
            dst_change = end_datetime.offset_hours - self.start_datetime.offset_hours
            logger.debug(f"Pre: {self.start_datetime}..{end_datetime}: DST change: {dst_change}")
            if dst_change < 0:
                end_datetime = end_datetime + to_duration(f"{abs(int(dst_change))} hours")
            elif dst_change > 0:
                end_datetime = end_datetime - to_duration(f"{abs(int(dst_change))} hours")
            logger.debug(f"Pst: {self.start_datetime}..{end_datetime}: DST change: {dst_change}")
            return end_datetime
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def keep_datetime(self) -> Optional[DateTime]:
        """Compute the keep datetime for historical data retention.

        Returns:
            Optional[DateTime]: The calculated retention cutoff datetime, or `None` if inputs are missing.
        """
        if self.start_datetime is None:
            return None
        historic_hours = self.historic_hours_min()
        if (
            self.config.prediction_historic_hours
            and self.config.prediction_historic_hours > historic_hours
        ):
            historic_hours = int(self.config.prediction_historic_hours)
        return self.start_datetime - to_duration(f"{historic_hours} hours")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_hours(self) -> Optional[int]:
        """Compute the hours from `start_datetime` to `end_datetime`.

        Returns:
            Optional[pendulum.period]: The duration hours, or `None` if either datetime is unavailable.
        """
        end_dt = self.end_datetime
        if end_dt is None:
            return None
        duration = end_dt - self.start_datetime
        return int(duration.total_hours())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def keep_hours(self) -> Optional[int]:
        """Compute the hours from `keep_datetime` to `start_datetime`.

        Returns:
            Optional[pendulum.period]: The duration hours, or `None` if either datetime is unavailable.
        """
        keep_dt = self.keep_datetime
        if keep_dt is None:
            return None
        duration = self.start_datetime - keep_dt
        return int(duration.total_hours())


class PredictionProvider(PredictionStartEndKeepMixin, DataProvider):
    """Abstract base class for prediction providers with singleton thread-safety and configurable prediction parameters.

    This class serves as a base for managing prediction data, providing an interface for derived
    classes to maintain a single instance across threads. It offers attributes for managing
    prediction and historical data retention.

    Note:
        Derived classes have to provide their own records field with correct record type set.
    """

    def update_data(
        self,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Update prediction parameters and call the custom update function.

        Updates the configuration, deletes outdated records, and performs the custom update logic.

        Args:
            force_enable (bool, optional): If True, forces the update even if the provider is disabled.
            force_update (bool, optional): If True, forces the provider to update the data even if still cached.
        """
        # Update prediction configuration
        self.config.update()

        # Check after configuration is updated.
        if not force_enable and not self.enabled():
            return

        # Delete outdated records before updating
        self.delete_by_datetime(end_datetime=self.keep_datetime)

        # Call the custom update logic
        self._update_data(force_update=force_update)

        # Assure records are sorted.
        self.sort_by_datetime()


class PredictionImportProvider(PredictionProvider, DataImportProvider):
    """Abstract base class for prediction providers that import prediction data.

    This class is designed to handle prediction data provided in the form of a key-value dictionary.
    - **Keys**: Represent identifiers from the record keys of a specific prediction.
    - **Values**: Are lists of prediction values starting at a specified `start_datetime`, where
      each value corresponds to a subsequent time interval (e.g., hourly).

    Subclasses must implement the logic for managing prediction data based on the imported records.
    """

    pass


class PredictionContainer(PredictionStartEndKeepMixin, DataContainer):
    """A container for managing multiple PredictionProvider instances.

    This class enables access to data from multiple prediction providers, supporting retrieval and
    aggregation of their data as Pandas Series objects. It acts as a dictionary-like structure
    where each key represents a specific data field, and the value is a Pandas Series containing
    combined data from all PredictionProvider instances for that key.

    Note:
        Derived classes have to provide their own providers field with correct provider type set.
    """

    # To be overloaded by derived classes.
    providers: List[PredictionProvider] = Field(
        default_factory=list, description="List of prediction providers"
    )
