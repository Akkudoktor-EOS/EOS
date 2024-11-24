"""Abstract and base classes for generic data.

This module provides classes for managing and processing generic data in a flexible, configurable manner.
It includes classes to handle configurations, record structures, sequences, and containers for generic data,
enabling efficient storage, retrieval, and manipulation of data records.

This module is designed for use in predictive modeling workflows, facilitating the organization, serialization,
and manipulation of configuration and generic data in a clear, scalable, and structured manner.
"""

import difflib
import json
from abc import abstractmethod
from collections.abc import MutableMapping, MutableSequence
from itertools import chain
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type, Union, overload

import numpy as np
import pandas as pd
import pendulum
from numpydantic import NDArray, Shape
from pendulum import DateTime, Duration
from pydantic import AwareDatetime, ConfigDict, Field, computed_field, field_validator

from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin, StartMixin
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class DataBase(ConfigMixin, StartMixin, PydanticBaseModel):
    """Base class for handling generic data.

    Enables access to EOS configuration data (attribute `config`).
    """

    pass


class DataRecord(DataBase, MutableMapping):
    """Base class for data records, enabling dynamic access to fields defined in derived classes.

    Fields can be accessed and mutated both using dictionary-style access (`record['field_name']`)
    and attribute-style access (`record.field_name`).

    Attributes:
        date_time (Optional[AwareDatetime]): Aware datetime indicating when the data record applies.

    Configurations:
        - Allows mutation after creation.
        - Supports non-standard data types like `datetime`.
    """

    date_time: Optional[AwareDatetime] = Field(default=None, description="DateTime")

    # Pydantic v2 model configuration
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    @field_validator("date_time", mode="before")
    @classmethod
    def transform_to_datetime(cls, value: Any) -> DateTime:
        """Converts various datetime formats into AwareDatetime."""
        if value is None:
            # Allow to set to default.
            return None
        return to_datetime(value)

    @classmethod
    def record_keys(cls) -> List[str]:
        """Returns the keys of all fields in the data record."""
        key_list = []
        key_list.extend(list(cls.model_fields.keys()))
        key_list.extend(list(cls.__pydantic_decorators__.computed_fields.keys()))
        return key_list

    @classmethod
    def record_keys_writable(cls) -> List[str]:
        """Returns the keys of all fields in the data record that are writable."""
        return list(cls.model_fields.keys())

    def _validate_key_writable(self, key: str) -> None:
        """Verify that a specified key exists and is writable in the current record keys.

        Args:
            key (str): The key to check for in the records.

        Raises:
            KeyError: If the specified key is not in the expected list of keys for the records.
        """
        if key not in self.record_keys_writable():
            raise KeyError(
                f"Key '{key}' is not in writable record keys: {self.record_keys_writable()}"
            )

    def __getitem__(self, key: str) -> Any:
        """Retrieve the value of a field by key name.

        Args:
            key (str): The name of the field to retrieve.

        Returns:
            Any: The value of the requested field.

        Raises:
            KeyError: If the specified key does not exist.
        """
        if key in self.model_fields:
            return getattr(self, key)
        raise KeyError(f"'{key}' not found in the record fields.")

    def __setitem__(self, key: str, value: Any) -> None:
        """Set the value of a field by key name.

        Args:
            key (str): The name of the field to set.
            value (Any): The value to assign to the field.

        Raises:
            KeyError: If the specified key does not exist in the fields.
        """
        if key in self.model_fields:
            setattr(self, key, value)
        else:
            raise KeyError(f"'{key}' is not a recognized field.")

    def __delitem__(self, key: str) -> None:
        """Delete the value of a field by key name by setting it to None.

        Args:
            key (str): The name of the field to delete.

        Raises:
            KeyError: If the specified key does not exist in the fields.
        """
        if key in self.model_fields:
            setattr(self, key, None)  # Optional: set to None instead of deleting
        else:
            raise KeyError(f"'{key}' is not a recognized field.")

    def __iter__(self) -> Iterator[str]:
        """Iterate over the field names in the data record.

        Returns:
            Iterator[str]: An iterator over field names.
        """
        return iter(self.model_fields)

    def __len__(self) -> int:
        """Return the number of fields in the data record.

        Returns:
            int: The number of defined fields.
        """
        return len(self.model_fields)

    def __repr__(self) -> str:
        """Provide a string representation of the data record.

        Returns:
            str: A string representation showing field names and their values.
        """
        field_values = {field: getattr(self, field) for field in self.model_fields}
        return f"{self.__class__.__name__}({field_values})"

    def __getattr__(self, key: str) -> Any:
        """Dynamic attribute access for fields.

        Args:
            key (str): The name of the field to access.

        Returns:
            Any: The value of the requested field.

        Raises:
            AttributeError: If the field does not exist.
        """
        if key in self.model_fields:
            return getattr(self, key)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{key}'")

    def __setattr__(self, key: str, value: Any) -> None:
        """Set attribute values directly if they are recognized fields.

        Args:
            key (str): The name of the attribute/field to set.
            value (Any): The value to assign to the attribute/field.

        Raises:
            AttributeError: If the attribute/field does not exist.
        """
        if key in self.model_fields:
            super().__setattr__(key, value)
        else:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{key}'")

    def __delattr__(self, key: str) -> None:
        """Delete an attribute by setting it to None if it exists as a field.

        Args:
            key (str): The name of the attribute/field to delete.

        Raises:
            AttributeError: If the attribute/field does not exist.
        """
        if key in self.model_fields:
            setattr(self, key, None)  # Optional: set to None instead of deleting
        else:
            super().__delattr__(key)

    @classmethod
    def key_from_description(cls, description: str, threshold: float = 0.8) -> Optional[str]:
        """Returns the attribute key that best matches the provided description.

        Fuzzy matching is used.

        Args:
            description (str): The description text to search for.
            threshold (float): The minimum ratio for a match (0-1). Default is 0.8.

        Returns:
            Optional[str]: The attribute key if a match is found above the threshold, else None.
        """
        if description is None:
            return None

        # Get all descriptions from the fields
        descriptions = {
            field_name: field_info.description
            for field_name, field_info in cls.model_fields.items()
        }

        # Use difflib to get close matches
        matches = difflib.get_close_matches(
            description, descriptions.values(), n=1, cutoff=threshold
        )

        # Check if there is a match
        if matches:
            best_match = matches[0]
            # Return the key that corresponds to the best match
            for key, desc in descriptions.items():
                if desc == best_match:
                    return key
        return None

    @classmethod
    def keys_from_descriptions(
        cls, descriptions: List[str], threshold: float = 0.8
    ) -> List[Optional[str]]:
        """Returns a list of attribute keys that best matches the provided list of descriptions.

        Fuzzy matching is used.

        Args:
            descriptions (List[str]): A list of description texts to search for.
            threshold (float): The minimum ratio for a match (0-1). Default is 0.8.

        Returns:
            List[Optional[str]]: A list of attribute keys matching the descriptions, with None for unmatched descriptions.
        """
        keys = []
        for description in descriptions:
            key = cls.key_from_description(description, threshold)
            keys.append(key)
        return keys


class DataSequence(DataBase, MutableSequence):
    """A managed sequence of DataRecord instances with list-like behavior.

    The DataSequence class provides an ordered, mutable collection of DataRecord
    instances, allowing list-style access for adding, deleting, and retrieving records. It also
    supports advanced data operations such as JSON serialization, conversion to Pandas Series,
    and sorting by timestamp.

    Attributes:
        records (List[DataRecord]): A list of DataRecord instances representing
                                          individual generic data points.
        record_keys (Optional[List[str]]): A list of field names (keys) expected in each
                                           DataRecord.

    Note:
        Derived classes have to provide their own records field with correct record type set.

    Usage:
        # Example of creating, adding, and using DataSequence
        class DerivedSequence(DataSquence):
            records: List[DerivedDataRecord] = Field(default_factory=list,
                                                        description="List of data records")

        seq = DerivedSequence()
        seq.insert(DerivedDataRecord(date_time=datetime.now(), temperature=72))
        seq.insert(DerivedDataRecord(date_time=datetime.now(), temperature=75))

        # Convert to JSON and back
        json_data = seq.to_json()
        new_seq = DerivedSequence.from_json(json_data)

        # Convert to Pandas Series
        series = seq.key_to_series('temperature')
    """

    # To be overloaded by derived classes.
    records: List[DataRecord] = Field(default_factory=list, description="List of data records")

    # Derived fields (computed)
    @computed_field  # type: ignore[prop-decorator]
    @property
    def record_keys(self) -> List[str]:
        """Returns the keys of all fields in the data records."""
        key_list = []
        key_list.extend(list(self.record_class().model_fields.keys()))
        key_list.extend(list(self.record_class().__pydantic_decorators__.computed_fields.keys()))
        return key_list

    @computed_field  # type: ignore[prop-decorator]
    @property
    def record_keys_writable(self) -> List[str]:
        """Returns the keys of all fields in the data records that are writable."""
        return list(self.record_class().model_fields.keys())

    @classmethod
    def record_class(cls) -> Type:
        """Returns the class of the data record this data sequence handles."""
        # Access the model field metadata
        field_info = cls.model_fields["records"]
        # Get the list element type from the 'type_' attribute
        list_element_type = field_info.annotation.__args__[0]
        if not isinstance(list_element_type(), DataRecord):
            raise ValueError(
                f"Data record must be an instance of DataRecord: '{list_element_type}'."
            )
        return list_element_type

    def _validate_key(self, key: str) -> None:
        """Verify that a specified key exists in the current record keys.

        Args:
            key (str): The key to check for in the records.

        Raises:
            KeyError: If the specified key is not in the expected list of keys for the records.
        """
        if key not in self.record_keys:
            raise KeyError(f"Key '{key}' is not in record keys: {self.record_keys}")

    def _validate_key_writable(self, key: str) -> None:
        """Verify that a specified key exists and is writable in the current record keys.

        Args:
            key (str): The key to check for in the records.

        Raises:
            KeyError: If the specified key is not in the expected list of keys for the records.
        """
        if key not in self.record_keys_writable:
            raise KeyError(
                f"Key '{key}' is not in writable record keys: {self.record_keys_writable}"
            )

    def _validate_record(self, value: DataRecord) -> None:
        """Check if the provided value is a valid DataRecord with compatible keys.

        Args:
            value (DataRecord): The record to validate.

        Raises:
            ValueError: If the value is not an instance of DataRecord or has an invalid date_time type.
            KeyError: If the value has different keys from those expected in the sequence.
        """
        # Assure value is of correct type
        if value.__class__.__name__ != self.record_class().__name__:
            raise ValueError(f"Value must be an instance of `{self.record_class().__name__}`.")

        # Assure datetime value can be converted to datetime object
        value.date_time = to_datetime(value.date_time)

    @overload
    def __getitem__(self, index: int) -> DataRecord: ...

    @overload
    def __getitem__(self, index: slice) -> list[DataRecord]: ...

    def __getitem__(self, index: Union[int, slice]) -> Union[DataRecord, list[DataRecord]]:
        """Retrieve a DataRecord or list of DataRecords by index or slice.

        Supports both single item and slice-based access to the sequence.

        Args:
            index (int or slice): The index or slice to access.

        Returns:
            DataRecord or list[DataRecord]: A single DataRecord or a list of DataRecords.

        Raises:
            IndexError: If the index is invalid or out of range.
        """
        if isinstance(index, int):
            # Single item access logic
            return self.records[index]
        elif isinstance(index, slice):
            # Slice access logic
            return self.records[index]
        raise IndexError("Invalid index")

    def __setitem__(self, index: Any, value: Any) -> None:
        """Replace a data record or slice of records with new value(s).

        Supports setting a single record at an integer index or
        multiple records using a slice.

        Args:
            index (int or slice): The index or slice to modify.
            value (DataRecord or list[DataRecord]):
                Single record or list of records to set.

        Raises:
            ValueError: If the number of records does not match the slice length.
            IndexError: If the index is out of range.
        """
        if isinstance(index, int):
            if isinstance(value, list):
                raise ValueError("Cannot assign list to single index")
            self._validate_record(value)
            self.records[index] = value
        elif isinstance(index, slice):
            if isinstance(value, DataRecord):
                raise ValueError("Cannot assign single record to slice")
            for record in value:
                self._validate_record(record)
            self.records[index] = value
        else:
            # Should never happen
            raise TypeError("Invalid type for index")

    def __delitem__(self, index: Any) -> None:
        """Remove a single data record or a slice of records.

        Supports deleting a single record by integer index
        or multiple records using a slice.

        Args:
            index (int or slice): The index or slice to delete.

        Raises:
            IndexError: If the index is out of range.
        """
        del self.records[index]

    def __len__(self) -> int:
        """Get the number of DataRecords in the sequence.

        Returns:
            int: The count of records in the sequence.
        """
        return len(self.records)

    def __iter__(self) -> Iterator[DataRecord]:
        """Create an iterator for accessing DataRecords sequentially.

        Returns:
            Iterator[DataRecord]: An iterator for the records.
        """
        return iter(self.records)

    def __repr__(self) -> str:
        """Provide a string representation of the DataSequence.

        Returns:
            str: A string representation of the DataSequence.
        """
        return f"{self.__class__.__name__}([{', '.join(repr(record) for record in self.records)}])"

    def insert(self, index: int, value: DataRecord) -> None:
        """Insert a DataRecord at a specified index in the sequence.

        This method inserts a `DataRecord` at the specified index within the sequence of records,
        shifting subsequent records to the right. If `index` is 0, the record is added at the beginning
        of the sequence, and if `index` is equal to the length of the sequence, the record is appended
        at the end.

        Args:
            index (int): The position before which to insert the new record. An index of 0 inserts
                        the record at the start, while an index equal to the length of the sequence
                        appends it to the end.
            value (DataRecord): The `DataRecord` instance to insert into the sequence.

        Raises:
            ValueError: If `value` is not an instance of `DataRecord`.
        """
        self.records.insert(index, value)

    def insert_by_datetime(self, value: DataRecord) -> None:
        """Insert or merge a DataRecord into the sequence based on its date.

        If a record with the same date exists, merges new data fields with the existing record.
        Otherwise, appends the record and maintains chronological order.

        Args:
            value (DataRecord): The record to add or merge.
        """
        self._validate_record(value)
        # Check if a record with the given date already exists
        for record in self.records:
            if not isinstance(record.date_time, DateTime):
                raise ValueError(
                    f"Record date '{record.date_time}' is not a datetime, but a `{type(record.date_time).__name__}`."
                )
            if compare_datetimes(record.date_time, value.date_time).equal:
                # Merge values, only updating fields where data record has a non-None value
                for field, val in value.model_dump(exclude_unset=True).items():
                    if field in value.record_keys_writable():
                        setattr(record, field, val)
                break
        else:
            # Add data record if the date does not exist
            self.records.append(value)
            # Sort the list by datetime after adding/updating
            self.sort_by_datetime()

    def update_value(self, date: DateTime, key: str, value: Any) -> None:
        """Updates a specific value in the data record for a given date.

        If a record for the date exists, updates the specified attribute with the new value.
        Otherwise, appends a new record with the given value and maintains chronological order.

        Args:
            date (datetime): The date for which the weather value is to be added or updated.
            key (str): The attribute name to be updated.
            value: The new value to set for the specified attribute.
        """
        self._validate_key_writable(key)
        # Ensure datetime objects are normalized
        date = to_datetime(date, to_maxtime=False)
        # Check if a record with the given date already exists
        for record in self.records:
            if not isinstance(record.date_time, DateTime):
                raise ValueError(
                    f"Record date '{record.date_time}' is not a datetime, but a `{type(record.date_time).__name__}`."
                )
            if compare_datetimes(record.date_time, date).equal:
                # Update the DataRecord with the new value for the specified key
                setattr(record, key, value)
                break
        else:
            # Create a new record and append to the list
            record = self.record_class()(date_time=date, **{key: value})
            self.records.append(record)
            # Sort the list by datetime after adding/updating
            self.sort_by_datetime()

    def to_datetimeindex(self) -> pd.DatetimeIndex:
        """Generate a Pandas DatetimeIndex from the date_time fields of all records in the sequence.

        Returns:
            pd.DatetimeIndex: An index of datetime values corresponding to each record's date_time attribute.

        Raises:
            ValueError: If any record does not have a valid date_time attribute.
        """
        date_times = [record.date_time for record in self.records if record.date_time is not None]

        if not date_times:
            raise ValueError("No valid date_time values found in the records.")

        return pd.DatetimeIndex(date_times)

    def key_to_dict(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
    ) -> Dict[DateTime, Any]:
        """Extract a dictionary indexed by the date_time field of the DataRecords.

        The dictionary will contain values extracted from the specified key attribute of each DataRecord,
        using the date_time field as the key.

        Args:
            key (str): The field name in the DataRecord from which to extract values.
            start_datetime (datetime, optional): The start date to filter records (inclusive).
            end_datetime (datetime, optional): The end date to filter records (exclusive).

        Returns:
            Dict[datetime, Any]: A dictionary with the date_time of each record as the key
                                  and the values extracted from the specified key.

        Raises:
            KeyError: If the specified key is not found in any of the DataRecords.
        """
        self._validate_key(key)
        # Ensure datetime objects are normalized
        start_datetime = to_datetime(start_datetime, to_maxtime=False) if start_datetime else None
        end_datetime = to_datetime(end_datetime, to_maxtime=False) if end_datetime else None

        # Create a dictionary to hold date_time and corresponding values
        filtered_data = {
            to_datetime(record.date_time, as_string=True): getattr(record, key, None)
            for record in self.records
            if (start_datetime is None or compare_datetimes(record.date_time, start_datetime).ge)
            and (end_datetime is None or compare_datetimes(record.date_time, end_datetime).lt)
        }

        return filtered_data

    def key_to_lists(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
    ) -> Tuple[List[DateTime], List[Optional[float]]]:
        """Extracts two lists from data records within an optional date range.

        The lists are:
            Dates: List of datetime elements.
            Values: List of values corresponding to the specified key in the data records.

        Args:
            key (str): The key of the attribute in DataRecord to extract.
            start_datetime (datetime, optional): The start date for filtering the records (inclusive).
            end_datetime (datetime, optional): The end date for filtering the records (exclusive).

        Returns:
            tuple: A tuple containing a list of datetime values and a list of extracted values.

        Raises:
            KeyError: If the specified key is not found in any of the DataRecords.
        """
        self._validate_key(key)
        # Ensure datetime objects are normalized
        start_datetime = to_datetime(start_datetime, to_maxtime=False) if start_datetime else None
        end_datetime = to_datetime(end_datetime, to_maxtime=False) if end_datetime else None

        # Create two lists to hold date_time and corresponding values
        filtered_records = []
        for record in self.records:
            if record.date_time is None:
                continue
            if (
                start_datetime is None or compare_datetimes(record.date_time, start_datetime).ge
            ) and (end_datetime is None or compare_datetimes(record.date_time, end_datetime).lt):
                filtered_records.append(record)
        dates = [record.date_time for record in filtered_records]
        values = [getattr(record, key, None) for record in filtered_records]

        return dates, values

    def key_to_series(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
    ) -> pd.Series:
        """Extract a series indexed by the date_time field from data records within an optional date range.

        Args:
            key (str): The field name in the DataRecord from which to extract values.
            start_datetime (datetime, optional): The start date for filtering the records (inclusive).
            end_datetime (datetime, optional): The end date for filtering the records (exclusive).

        Returns:
            pd.Series: A Pandas Series with the index as the date_time of each record
                        and the values extracted from the specified key.

        Raises:
            KeyError: If the specified key is not found in any of the DataRecords.
        """
        dates, values = self.key_to_lists(key, start_datetime, end_datetime)
        return pd.Series(data=values, index=pd.DatetimeIndex(dates), name=key)

    def key_from_series(self, key: str, series: pd.Series) -> None:
        """Update the DataSequence from a Pandas Series.

        The series index should represent the date_time of each DataRecord, and the series values
        should represent the corresponding data values for the specified key.

        Args:
            series (pd.Series): A Pandas Series containing data to update the DataSequence.
            key (str): The field name in the DataRecord that corresponds to the values in the Series.
        """
        self._validate_key_writable(key)

        for date_time, value in series.items():
            # Ensure datetime objects are normalized
            date_time = to_datetime(date_time, to_maxtime=False) if date_time else None
            # Check if there's an existing record for this date_time
            existing_record = next((r for r in self.records if r.date_time == date_time), None)
            if existing_record:
                # Update existing record's specified key
                setattr(existing_record, key, value)
            else:
                # Create a new DataRecord if none exists
                new_record = self.record_class()(date_time=date_time, **{key: value})
                self.records.append(new_record)
        self.sort_by_datetime()

    def key_to_array(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
        interval: Optional[Duration] = None,
        fill_method: Optional[str] = None,
    ) -> NDArray[Shape["*"], Any]:
        """Extract an array indexed by fixed time intervals from data records within an optional date range.

        Args:
            key (str): The field name in the DataRecord from which to extract values.
            start_datetime (datetime, optional): The start date for filtering the records (inclusive).
            end_datetime (datetime, optional): The end date for filtering the records (exclusive).
            interval (duration, optional): The fixed time interval. Defaults to 1 hour.
            fill_method (str): Method to handle missing values during resampling.
                - 'linear': Linearly interpolate missing values (for numeric data only).
                - 'ffill': Forward fill missing values.
                - 'bfill': Backward fill missing values.
                - 'none': Defaults to 'linear' for numeric values, otherwise 'ffill'.

        Returns:
            np.ndarray: A NumPy Array of the values extracted from the specified key.

        Raises:
            KeyError: If the specified key is not found in any of the DataRecords.
        """
        resampled = None
        if interval is None:
            interval = to_duration("1 hour")
        series = self.key_to_series(key)

        # Handle missing values
        if series.dtype in [np.float64, np.float32, np.int64, np.int32]:
            # Numeric types
            if fill_method is None:
                fill_method = "linear"
            # Resample the series to the specified interval
            resampled = series.resample(interval).mean()
            if fill_method == "linear":
                resampled = resampled.interpolate(method="linear")
            elif fill_method == "ffill":
                resampled = resampled.ffill()
            elif fill_method == "bfill":
                resampled = resampled.bfill()
            elif fill_method != "none":
                raise ValueError(f"Unsupported fill method: {fill_method}")
        else:
            # Non-numeric types
            if fill_method is None:
                fill_method = "ffill"
            # Resample the series to the specified interval
            resampled = series.resample(interval).first()
            if fill_method == "ffill":
                resampled = resampled.ffill()
            elif fill_method == "bfill":
                resampled = resampled.bfill()
            elif fill_method != "none":
                raise ValueError(f"Unsupported fill method for non-numeric data: {fill_method}")

        # Convert the resampled series to a NumPy array
        if start_datetime is not None:
            resampled = resampled.truncate(before=start_datetime)
        if end_datetime is not None:
            resampled = resampled.truncate(after=end_datetime.subtract(seconds=1))
        array = resampled.values
        return array

    def sort_by_datetime(self, reverse: bool = False) -> None:
        """Sort the DataRecords in the sequence by their date_time attribute.

        This method modifies the existing list of records in place, arranging them in order
        based on the date_time attribute of each DataRecord.

        Args:
            reverse (bool, optional): If True, sorts in descending order.
                                      If False (default), sorts in ascending order.

        Raises:
            TypeError: If any record's date_time attribute is None or not comparable.
        """
        try:
            # Use a default value (-inf or +inf) for None to make all records comparable
            self.records.sort(
                key=lambda record: record.date_time or pendulum.datetime(1, 1, 1, 0, 0, 0),
                reverse=reverse,
            )
        except TypeError as e:
            # Provide a more informative error message
            none_records = [i for i, record in enumerate(self.records) if record.date_time is None]
            if none_records:
                raise TypeError(
                    f"Cannot sort: {len(none_records)} record(s) have None date_time "
                    f"at indices {none_records}"
                ) from e
            raise

    def delete_by_datetime(
        self, start_datetime: Optional[DateTime] = None, end_datetime: Optional[DateTime] = None
    ) -> None:
        """Delete DataRecords from the sequence within a specified datetime range.

        Removes records with `date_time` attributes that fall between `start_datetime` (inclusive)
        and `end_datetime` (exclusive). If only `start_datetime` is provided, records from that date
        onward will be removed. If only `end_datetime` is provided, records up to that date will be
        removed. If none is given, no record will be deleted.

        Args:
            start_datetime (datetime, optional): The start date to begin deleting records (inclusive).
            end_datetime (datetime, optional): The end date to stop deleting records (exclusive).

        Raises:
            ValueError: If both `start_datetime` and `end_datetime` are None.
        """
        # Ensure datetime objects are normalized
        start_datetime = to_datetime(start_datetime, to_maxtime=False) if start_datetime else None
        end_datetime = to_datetime(end_datetime, to_maxtime=False) if end_datetime else None

        # Retain records that are outside the specified range
        retained_records = []
        for record in self.records:
            if record.date_time is None:
                continue
            if (
                (
                    start_datetime is not None
                    and compare_datetimes(record.date_time, start_datetime).lt
                )
                or (
                    end_datetime is not None
                    and compare_datetimes(record.date_time, end_datetime).ge
                )
                or (start_datetime is None and end_datetime is None)
            ):
                retained_records.append(record)
        self.records = retained_records

    def key_delete_by_datetime(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
    ) -> None:
        """Delete an attribute specified by `key` from records in the sequence within a given datetime range.

        This method removes the attribute identified by `key` from records that have a `date_time` value falling
        within the specified `start_datetime` (inclusive) and `end_datetime` (exclusive) range.

        - If only `start_datetime` is specified, attributes will be removed from records from that date onward.
        - If only `end_datetime` is specified, attributes will be removed from records up to that date.
        - If neither `start_datetime` nor `end_datetime` is given, the attribute will be removed from all records.

        Args:
            key (str): The attribute name to delete from each record.
            start_datetime (datetime, optional): The start datetime to begin attribute deletion (inclusive).
            end_datetime (datetime, optional): The end datetime to stop attribute deletion (exclusive).

        Raises:
            KeyError: If `key` is not a valid attribute of the records.
        """
        self._validate_key_writable(key)
        # Ensure datetime objects are normalized
        start_datetime = to_datetime(start_datetime, to_maxtime=False) if start_datetime else None
        end_datetime = to_datetime(end_datetime, to_maxtime=False) if end_datetime else None

        for record in self.records:
            if (
                start_datetime is None or compare_datetimes(record.date_time, start_datetime).ge
            ) and (end_datetime is None or compare_datetimes(record.date_time, end_datetime).lt):
                del record[key]

    def filter_by_datetime(
        self, start_datetime: Optional[DateTime] = None, end_datetime: Optional[DateTime] = None
    ) -> "DataSequence":
        """Returns a new DataSequence object containing only records within the specified datetime range.

        Args:
            start_datetime (Optional[datetime]): The start of the datetime range (inclusive). If None, no lower limit.
            end_datetime (Optional[datetime]): The end of the datetime range (exclusive). If None, no upper limit.

        Returns:
            DataSequence: A new DataSequence object with filtered records.
        """
        # Ensure datetime objects are normalized
        start_datetime = to_datetime(start_datetime, to_maxtime=False) if start_datetime else None
        end_datetime = to_datetime(end_datetime, to_maxtime=False) if end_datetime else None

        filtered_records = [
            record
            for record in self.records
            if (start_datetime is None or compare_datetimes(record.date_time, start_datetime).ge)
            and (end_datetime is None or compare_datetimes(record.date_time, end_datetime).lt)
        ]
        return self.__class__(records=filtered_records)


class DataProvider(SingletonMixin, DataSequence):
    """Abstract base class for data providers with singleton thread-safety and configurable data parameters.

    This class serves as a base for managing generic data, providing an interface for derived
    classes to maintain a single instance across threads. It offers attributes for managing
    data and historical data retention.

    Note:
        Derived classes have to provide their own records field with correct record type set.
    """

    update_datetime: Optional[AwareDatetime] = Field(
        None, description="Latest update datetime for generic data"
    )

    @abstractmethod
    def provider_id(self) -> str:
        """Return the unique identifier for the data provider.

        To be implemented by derived classes.
        """
        return "DataProvider"

    @abstractmethod
    def enabled(self) -> bool:
        """Return True if the provider is enabled according to configuration.

        To be implemented by derived classes.
        """
        return self.provider_id() == self.config.abstract_provider

    @abstractmethod
    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Abstract method for custom data update logic, to be implemented by derived classes.

        Args:
            force_update (bool, optional): If True, forces the provider to update the data even if still cached.
        """
        pass

    def update_data(
        self,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Calls the custom update function if enabled or forced.

        Args:
            force_enable (bool, optional): If True, forces the update even if the provider is disabled.
            force_update (bool, optional): If True, forces the provider to update the data even if still cached.
        """
        # Check after configuration is updated.
        if not force_enable and not self.enabled():
            return

        # Call the custom update logic
        self._update_data(force_update=force_update)

        # Assure records are sorted.
        self.sort_by_datetime()


class DataImportProvider(DataProvider):
    """Abstract base class for data providers that import generic data.

    This class is designed to handle generic data provided in the form of a key-value dictionary.
    - **Keys**: Represent identifiers from the record keys of a specific data.
    - **Values**: Are lists of data values starting at a specified `start_datetime`, where
      each value corresponds to a subsequent time interval (e.g., hourly).

    Subclasses must implement the logic for managing generic data based on the imported records.
    """

    def import_datetimes(self, value_count: int) -> List[Tuple[DateTime, int]]:
        """Generates a list of tuples containing timestamps and their corresponding value indices.

        The function accounts for daylight saving time (DST) transitions:
        - During a spring forward transition (e.g., DST begins), skipped hours are omitted.
        - During a fall back transition (e.g., DST ends), repeated hours are included,
        but they share the same value index.

        Args:
            value_count (int): The number of timestamps to generate.

        Returns:
            List[Tuple[DateTime, int]]:
                A list of tuples, where each tuple contains:
                - A `DateTime` object representing an hourly step from `start_datetime`.
                - An integer value index corresponding to the logical hour.

        Behavior:
            - Skips invalid timestamps during DST spring forward transitions.
            - Includes both instances of repeated timestamps during DST fall back transitions.
            - Ensures the list contains exactly `value_count` entries.

        Example:
            >>> start_datetime = pendulum.datetime(2024, 11, 3, 0, 0, tz="America/New_York")
            >>> import_datetimes(5)
            [(DateTime(2024, 11, 3, 0, 0, tzinfo=Timezone('America/New_York')), 0),
            (DateTime(2024, 11, 3, 1, 0, tzinfo=Timezone('America/New_York')), 1),
            (DateTime(2024, 11, 3, 1, 0, tzinfo=Timezone('America/New_York')), 1),  # Repeated hour
            (DateTime(2024, 11, 3, 2, 0, tzinfo=Timezone('America/New_York')), 2),
            (DateTime(2024, 11, 3, 3, 0, tzinfo=Timezone('America/New_York')), 3)]
        """
        timestamps_with_indices: List[Tuple[DateTime, int]] = []
        value_datetime = self.start_datetime
        value_index = 0

        while value_index < value_count:
            i = len(timestamps_with_indices)
            logger.debug(f"{i}: Insert at {value_datetime} with index {value_index}")
            timestamps_with_indices.append((value_datetime, value_index))

            # Check if there is a DST transition
            next_time = value_datetime.add(hours=1)
            if next_time <= value_datetime:
                # Check if there is a DST transition (i.e., ambiguous time during fall back)
                # Repeat the hour value (reuse value index)
                value_datetime = next_time
                logger.debug(f"{i+1}: Repeat at {value_datetime} with index {value_index}")
                timestamps_with_indices.append((value_datetime, value_index))
            elif next_time.hour != value_datetime.hour + 1 and value_datetime.hour != 23:
                # Skip the hour value (spring forward in value index)
                value_index += 1
                logger.debug(f"{i+1}: Skip   at {next_time} with index {value_index}")

            # Increment value index and value_datetime for new hour
            value_index += 1
            value_datetime = value_datetime.add(hours=1)

        return timestamps_with_indices

    def import_from_json(self, json_str: str, key_prefix: str = "") -> None:
        """Updates generic data by importing it from a JSON string.

        This method reads generic data from a JSON string, matches keys based on the
        record keys and the provided `key_prefix`, and updates the data values sequentially,
        starting from the `start_datetime`. Each data value is associated with an hourly
        interval.

        Args:
            json_str (str): The JSON string containing the generic data.
            key_prefix (str, optional): A prefix to filter relevant keys from the generic data.
                Only keys starting with this prefix will be considered. Defaults to an empty string.

        Raises:
            JSONDecodeError: If the file content is not valid JSON.

        Example:
            Given a JSON string with the following content:
            ```json
            {
                "load0_mean": [20.5, 21.0, 22.1],
                "load1_mean": [50, 55, 60]
            }
            ```
            and `key_prefix = "load1"`, only the "load1_mean" key will be processed even though
            both keys are in the record.
        """
        import_data = json.loads(json_str)
        for key in self.record_keys_writable:
            if key.startswith(key_prefix) and key in import_data:
                value_list = import_data[key]
                value_datetime_mapping = self.import_datetimes(len(value_list))
                for value_datetime, value_index in value_datetime_mapping:
                    self.update_value(value_datetime, key, value_list[value_index])

    def import_from_file(self, import_file_path: Path, key_prefix: str = "") -> None:
        """Updates generic data by importing it from a file.

        This method reads generic data from a JSON file, matches keys based on the
        record keys and the provided `key_prefix`, and updates the data values sequentially,
        starting from the `start_datetime`. Each data value is associated with an hourly
        interval.

        Args:
            import_file_path (Path): The path to the JSON file containing the generic data.
            key_prefix (str, optional): A prefix to filter relevant keys from the generic data.
                Only keys starting with this prefix will be considered. Defaults to an empty string.

        Raises:
            FileNotFoundError: If the specified file does not exist.
            JSONDecodeError: If the file content is not valid JSON.

        Example:
            Given a JSON file with the following content:
            ```json
            {
                "load0_mean": [20.5, 21.0, 22.1],
                "load1_mean": [50, 55, 60]
            }
            ```
            and `key_prefix = "load1"`, only the "load1_mean" key will be processed even though
            both keys are in the record.
        """
        with import_file_path.open("r") as import_file:
            import_str = import_file.read()
        self.import_from_json(import_str, key_prefix)


class DataContainer(SingletonMixin, DataBase, MutableMapping):
    """A container for managing multiple DataProvider instances.

    This class enables access to data from multiple data providers, supporting retrieval and
    aggregation of their data as Pandas Series objects. It acts as a dictionary-like structure
    where each key represents a specific data field, and the value is a Pandas Series containing
    combined data from all DataProvider instances for that key.

    Note:
        Derived classes have to provide their own providers field with correct provider type set.
    """

    # To be overloaded by derived classes.
    providers: List[DataProvider] = Field(
        default_factory=list, description="List of data providers"
    )

    @field_validator("providers", mode="after")
    def check_providers(cls, value: List[DataProvider]) -> List[DataProvider]:
        # Check each item in the list
        for item in value:
            if not isinstance(item, DataProvider):
                raise TypeError(
                    f"Each item in the providers list must be a DataProvider, got {type(item).__name__}"
                )
        return value

    def __getitem__(self, key: str) -> pd.Series:
        """Retrieve a Pandas Series for a specified key from the data in each DataProvider.

        Iterates through providers to find and return the first available Series for the specified key.

        Args:
            key (str): The field name to retrieve, representing a data attribute in DataRecords.

        Returns:
            pd.Series: A Pandas Series containing aggregated data for the specified key.

        Raises:
            KeyError: If no provider contains data for the specified key.
        """
        series = None
        for provider in self.providers:
            try:
                series = provider.key_to_series(key)
                break
            except KeyError:
                continue

        if series is None:
            raise KeyError(f"No data found for key '{key}'.")

        return series

    def __setitem__(self, key: str, value: pd.Series) -> None:
        """Add or merge a Pandas Series for a specified key into the records of an appropriate provider.

        Attempts to update or insert the provided Series data in each provider. If no provider supports
        the specified key, an error is raised.

        Args:
            key (str): The field name to update, representing a data attribute in DataRecords.
            value (pd.Series): A Pandas Series containing data for the specified key.

        Raises:
            ValueError: If `value` is not an instance of `pd.Series`.
            KeyError: If no provider supports the specified key.
        """
        if not isinstance(value, pd.Series):
            raise ValueError("Value must be an instance of pd.Series.")

        for provider in self.providers:
            try:
                provider.key_from_series(key, value)
                break
            except KeyError:
                continue
        else:
            raise KeyError(f"Key '{key}' not found in any provider.")

    def __delitem__(self, key: str) -> None:
        """Set the value of the specified key in the data records of each provider to None.

        Args:
            key (str): The field name in DataRecords to clear.

        Raises:
            KeyError: If the key is not found in any provider.
        """
        for provider in self.providers:
            try:
                provider.key_delete_by_datetime(key)
                break
            except KeyError:
                continue
        else:
            raise KeyError(f"Key '{key}' not found in any provider.")

    def __iter__(self) -> Iterator[str]:
        """Return an iterator over all unique keys available across providers.

        Returns:
            Iterator[str]: An iterator over the unique keys from all providers.
        """
        return iter(set(chain.from_iterable(provider.record_keys for provider in self.providers)))

    def __len__(self) -> int:
        """Return the number of keys in the container.

        Returns:
            int: The total number of keys in this container.
        """
        return len(list(chain.from_iterable(provider.record_keys for provider in self.providers)))

    def __repr__(self) -> str:
        """Provide a string representation of the DataContainer instance.

        Returns:
            str: A string representing the container and its contained providers.
        """
        return f"{self.__class__.__name__}({self.providers})"

    def update_data(
        self,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Update data.

        Args:
            force_enable (bool, optional): If True, forces the update even if a provider is disabled.
            force_update (bool, optional): If True, forces the providers to update the data even if still cached.
        """
        for provider in self.providers:
            provider.update_data(force_enable=force_enable, force_update=force_update)

    def key_to_array(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
        interval: Optional[Duration] = None,
        fill_method: Optional[str] = None,
    ) -> NDArray[Shape["*"], Any]:
        """Retrieve an array indexed by fixed time intervals for a specified key from the data in each DataProvider.

        Iterates through providers to find and return the first available array for the specified key.

        Args:
            key (str): The field name to retrieve, representing a data attribute in DataRecords.
            start_datetime (datetime, optional): The start date for filtering the records (inclusive).
            end_datetime (datetime, optional): The end date for filtering the records (exclusive).
            interval (duration, optional): The fixed time interval. Defaults to 1 hour.
            fill_method (str): Method to handle missing values during resampling.
                - 'linear': Linearly interpolate missing values (for numeric data only).
                - 'ffill': Forward fill missing values.
                - 'bfill': Backward fill missing values.
                - 'none': Defaults to 'linear' for numeric values, otherwise 'ffill'.

        Returns:
            np.ndarray: A NumPy array containing aggregated data for the specified key.

        Raises:
            KeyError: If no provider contains data for the specified key.

        Todo:
            Cache the result in memory until the next `update_data` call.
        """
        array = None
        for provider in self.providers:
            try:
                array = provider.key_to_array(
                    key,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=interval,
                    fill_method=fill_method,
                )
                break
            except KeyError:
                continue

        if array is None:
            raise KeyError(f"No data found for key '{key}'.")

        return array

    def provider_by_id(self, provider_id: str) -> DataProvider:
        """Retrieves a data provider by its unique identifier.

        This method searches through the list of available providers and
        returns the first provider whose `provider_id` matches the given
        `provider_id`. If no matching provider is found, the method returns `None`.

        Args:
            provider_id (str): The unique identifier of the desired data provider.

        Returns:
            DataProvider: The data provider matching the given `provider_id`.

        Raises:
            ValueError if provider id is unknown.

        Example:
            provider = data.provider_by_id("WeatherImport")
        """
        providers = {provider.provider_id(): provider for provider in self.providers}
        if provider_id not in providers:
            error_msg = f"Unknown provider id: '{provider_id}' of '{providers.keys()}'."
            logger.error(error_msg)
            raise ValueError(error_msg)
        return providers[provider_id]
