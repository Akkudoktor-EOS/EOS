"""Module for managing and serializing Pydantic-based models with custom support.

This module provides classes that extend Pydantic’s functionality to include robust handling
of `pendulum.DateTime` fields, offering seamless serialization and deserialization into ISO 8601 format.
These enhancements facilitate the use of Pydantic models in applications requiring timezone-aware
datetime fields and consistent data serialization.

Key Features:
- Custom type adapter for `pendulum.DateTime` fields with automatic serialization to ISO 8601 strings.
- Utility methods for converting models to and from dictionaries and JSON strings.
- Validation tools for maintaining data consistency, including specialized support for
  pandas DataFrames and Series with datetime indexes.
"""

import json
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Type, Union
from zoneinfo import ZoneInfo

import pandas as pd
import pendulum
from pandas.api.types import is_datetime64_any_dtype
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


def merge_models(source: BaseModel, update_dict: dict[str, Any]) -> dict[str, Any]:
    def deep_update(source_dict: dict[str, Any], update_dict: dict[str, Any]) -> dict[str, Any]:
        for key, value in source_dict.items():
            if isinstance(value, dict) and isinstance(update_dict.get(key), dict):
                update_dict[key] = deep_update(update_dict[key], value)
            else:
                update_dict[key] = value
        return update_dict

    source_dict = source.model_dump(exclude_unset=True)
    merged_dict = deep_update(source_dict, deepcopy(update_dict))

    return merged_dict


def access_nested_value(
    model: BaseModel, path: str, setter: bool, value: Optional[Any] = None
) -> Any:
    """Get or set a nested model value based on the provided path.

    Supports string paths (with '/' separators) or sequence paths (list/tuple).
    Trims leading and trailing '/' from string paths.

    Args:
        model (BaseModel): The model object for partial assignment.
        path (str): The path to the model key (e.g., "key1/key2/key3" or key1/key2/0).
        setter (bool): True to set value at path, False to return value at path.
        value (Optional[Any]): The value to set.

    Returns:
        Any: The retrieved value if acting as a getter, or None if setting a value.
    """
    path_elements = path.strip("/").split("/")

    cfg: Any = model
    parent: BaseModel = model
    model_key: str = ""

    for i, key in enumerate(path_elements):
        is_final_key = i == len(path_elements) - 1

        if isinstance(cfg, list):
            try:
                idx = int(key)
                if is_final_key:
                    if not setter:  # Getter
                        return cfg[idx]
                    else:  # Setter
                        new_list = list(cfg)
                        new_list[idx] = value
                        # Trigger validation
                        setattr(parent, model_key, new_list)
                else:
                    cfg = cfg[idx]
            except ValidationError as e:
                raise ValueError(f"Error updating model: {e}") from e
            except (ValueError, IndexError) as e:
                raise IndexError(f"Invalid list index at {path}: {key}") from e

        elif isinstance(cfg, BaseModel):
            parent = cfg
            model_key = key
            if is_final_key:
                if not setter:  # Getter
                    return getattr(cfg, key)
                else:  # Setter
                    try:
                        # Verification also if nested value is provided opposed to just setattr
                        # Will merge partial assignment
                        cfg = cfg.__pydantic_validator__.validate_assignment(cfg, key, value)
                    except Exception as e:
                        raise ValueError(f"Error updating model: {e}") from e
            else:
                cfg = getattr(cfg, key)

        else:
            raise KeyError(f"Key '{key}' not found in model.")


class PydanticTypeAdapterDateTime(TypeAdapter[pendulum.DateTime]):
    """Custom type adapter for Pendulum DateTime fields."""

    @classmethod
    def serialize(cls, value: Any) -> str:
        """Convert pendulum.DateTime to ISO 8601 string."""
        if isinstance(value, pendulum.DateTime):
            return value.to_iso8601_string()
        raise ValueError(f"Expected pendulum.DateTime, got {type(value)}")

    @classmethod
    def deserialize(cls, value: Any) -> pendulum.DateTime:
        """Convert ISO 8601 string to pendulum.DateTime."""
        if isinstance(value, str) and cls.is_iso8601(value):
            try:
                return pendulum.parse(value)
            except pendulum.parsing.exceptions.ParserError as e:
                raise ValueError(f"Invalid date format: {value}") from e
        elif isinstance(value, pendulum.DateTime):
            return value
        raise ValueError(f"Expected ISO 8601 string or pendulum.DateTime, got {type(value)}")

    @staticmethod
    def is_iso8601(value: str) -> bool:
        """Check if the string is a valid ISO 8601 date string."""
        iso8601_pattern = (
            r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})?)$"
        )
        return bool(re.match(iso8601_pattern, value))


class PydanticBaseModel(BaseModel):
    """Base model class with automatic serialization and deserialization of `pendulum.DateTime` fields.

    This model serializes pendulum.DateTime objects to ISO 8601 strings and
    deserializes ISO 8601 strings to pendulum.DateTime objects.
    """

    # Enable custom serialization globally in config
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        use_enum_values=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    def validate_and_convert_pendulum(cls, value: Any, info: ValidationInfo) -> Any:
        """Validator to convert fields of type `pendulum.DateTime`.

        Converts fields to proper `pendulum.DateTime` objects, ensuring correct input types.

        This method is invoked for every field before the field value is set. If the field's type
        is `pendulum.DateTime`, it tries to convert string or timestamp values to `pendulum.DateTime`
        objects. If the value cannot be converted, a validation error is raised.

        Args:
            value: The value to be assigned to the field.
            info: Validation information for the field.

        Returns:
            The converted value, if successful.

        Raises:
            ValidationError: If the value cannot be converted to `pendulum.DateTime`.
        """
        # Get the field name and expected type
        field_name = info.field_name
        expected_type = cls.model_fields[field_name].annotation

        # Convert
        if expected_type is pendulum.DateTime or expected_type is AwareDatetime:
            try:
                value = to_datetime(value)
            except:
                pass
        return value

    # Override Pydantic’s serialization for all DateTime fields
    def model_dump(
        self, *args: Any, include_computed_fields: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        """Custom dump method to handle serialization for DateTime fields."""
        result = super().model_dump(*args, **kwargs)

        if not include_computed_fields:
            for computed_field_name in self.model_computed_fields:
                result.pop(computed_field_name, None)

        for key, value in result.items():
            if isinstance(value, pendulum.DateTime):
                result[key] = PydanticTypeAdapterDateTime.serialize(value)
        return result

    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> "PydanticBaseModel":
        """Custom constructor to handle deserialization for DateTime fields."""
        for key, value in values.items():
            if isinstance(value, str) and PydanticTypeAdapterDateTime.is_iso8601(value):
                values[key] = PydanticTypeAdapterDateTime.deserialize(value)
        return super().model_construct(_fields_set, **values)

    def reset_to_defaults(self) -> "PydanticBaseModel":
        """Resets the fields to their default values."""
        for field_name, field_info in self.model_fields.items():
            if field_info.default_factory is not None:  # Handle fields with default_factory
                default_value = field_info.default_factory()
            else:
                default_value = field_info.default
            try:
                setattr(self, field_name, default_value)
            except (AttributeError, TypeError, ValidationError):
                # Skip fields that are read-only or dynamically computed or can not be set to default
                pass
        return self

    def to_dict(self) -> dict:
        """Convert this PredictionRecord instance to a dictionary representation.

        Returns:
            dict: A dictionary where the keys are the field names of the PydanticBaseModel,
                and the values are the corresponding field values.
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls: Type["PydanticBaseModel"], data: dict) -> "PydanticBaseModel":
        """Create a PydanticBaseModel instance from a dictionary.

        Args:
            data (dict): A dictionary containing data to initialize the PydanticBaseModel.
                        Keys should match the field names defined in the model.

        Returns:
            PydanticBaseModel: An instance of the PydanticBaseModel populated with the data.

        Notes:
            Works with derived classes by ensuring the `cls` argument is used to instantiate the object.
        """
        return cls.model_validate(data)

    def model_dump_json(self, *args: Any, indent: Optional[int] = None, **kwargs: Any) -> str:
        data = self.model_dump(*args, **kwargs)
        return json.dumps(data, indent=indent, default=str)

    def to_json(self) -> str:
        """Convert the PydanticBaseModel instance to a JSON string.

        Returns:
            str: The JSON representation of the instance.
        """
        return self.model_dump_json()

    @classmethod
    def from_json(cls: Type["PydanticBaseModel"], json_str: str) -> "PydanticBaseModel":
        """Create an instance of the PydanticBaseModel class or its subclass from a JSON string.

        Args:
            json_str (str): JSON string to parse and convert into a PydanticBaseModel instance.

        Returns:
            PydanticBaseModel: A new instance of the class, populated with data from the JSON string.

        Notes:
            Works with derived classes by ensuring the `cls` argument is used to instantiate the object.
        """
        data = json.loads(json_str)
        return cls.model_validate(data)


class PydanticDateTimeData(RootModel):
    """Pydantic model for time series data with consistent value lengths.

    This model validates a dictionary where:
    - Keys are strings representing data series names
    - Values are lists of numeric or string values
    - Special keys 'start_datetime' and 'interval' can contain string values
    for time series indexing
    - All value lists must have the same length

    Example:
        {
            "start_datetime": "2024-01-01 00:00:00",  # optional
            "interval": "1 Hour",                     # optional
            "load_mean": [20.5, 21.0, 22.1],
            "load_min": [18.5, 19.0, 20.1]
        }
    """

    root: Dict[str, Union[str, List[Union[float, int, str, None]]]]

    @field_validator("root", mode="after")
    @classmethod
    def validate_root(
        cls, value: Dict[str, Union[str, List[Union[float, int, str, None]]]]
    ) -> Dict[str, Union[str, List[Union[float, int, str, None]]]]:
        # Validate that all keys are strings
        if not all(isinstance(k, str) for k in value.keys()):
            raise ValueError("All keys in the dictionary must be strings.")

        # Validate that no lists contain only None values
        for v in value.values():
            if isinstance(v, list) and all(item is None for item in v):
                raise ValueError("Lists cannot contain only None values.")

        # Validate that all lists have consistent lengths (if they are lists)
        list_lengths = [len(v) for v in value.values() if isinstance(v, list)]
        if len(set(list_lengths)) > 1:
            raise ValueError("All lists in the dictionary must have the same length.")

        # Validate special keys
        if "start_datetime" in value.keys():
            value["start_datetime"] = to_datetime(value["start_datetime"])
        if "interval" in value.keys():
            value["interval"] = to_duration(value["interval"])

        return value

    def to_dict(self) -> Dict[str, Union[str, List[Union[float, int, str, None]]]]:
        """Convert the model to a plain dictionary.

        Returns:
            Dict containing the validated data.
        """
        return self.root

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PydanticDateTimeData":
        """Create a PydanticDateTimeData instance from a dictionary.

        Args:
            data: Input dictionary

        Returns:
            PydanticDateTimeData instance
        """
        return cls(root=data)


class PydanticDateTimeDataFrame(PydanticBaseModel):
    """Pydantic model for validating pandas DataFrame data with datetime index."""

    data: Dict[str, Dict[str, Any]]
    dtypes: Dict[str, str] = Field(default_factory=dict)
    tz: Optional[str] = Field(default=None, description="Timezone for datetime values")
    datetime_columns: list[str] = Field(
        default_factory=lambda: ["date_time"], description="Columns to be treated as datetime"
    )

    @field_validator("tz")
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        """Validate that the timezone is valid."""
        if v is not None:
            try:
                ZoneInfo(v)
            except KeyError:
                raise ValueError(f"Invalid timezone: {v}")
        return v

    @field_validator("data", mode="before")
    @classmethod
    def validate_data(cls, v: Dict[str, Any], info: ValidationInfo) -> Dict[str, Any]:
        if not v:
            return v

        # Validate consistent columns
        columns = set(next(iter(v.values())).keys())
        if not all(set(row.keys()) == columns for row in v.values()):
            raise ValueError("All rows must have the same columns")

        # Convert index datetime strings
        try:
            d = {
                to_datetime(dt, as_string=True, in_timezone=info.data.get("tz")): value
                for dt, value in v.items()
            }
            v = d
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid datetime string in index: {e}")

        # Convert datetime columns
        datetime_cols = info.data.get("datetime_columns", [])
        try:
            for dt_str, value in v.items():
                for column_name, column_value in value.items():
                    if column_name in datetime_cols and column_value is not None:
                        v[dt_str][column_name] = to_datetime(
                            column_value, in_timezone=info.data.get("tz")
                        )
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid datetime value in column: {e}")

        return v

    @field_validator("dtypes")
    @classmethod
    def validate_dtypes(cls, v: Dict[str, str], info: ValidationInfo) -> Dict[str, str]:
        if not v:
            return v

        valid_dtypes = {"int64", "float64", "bool", "datetime64[ns]", "object", "string"}
        invalid_dtypes = set(v.values()) - valid_dtypes
        if invalid_dtypes:
            raise ValueError(f"Unsupported dtypes: {invalid_dtypes}")

        data = info.data.get("data", {})
        if data:
            columns = set(next(iter(data.values())).keys())
            if not all(col in columns for col in v.keys()):
                raise ValueError("dtype columns must exist in data columns")
        return v

    def to_dataframe(self) -> pd.DataFrame:
        """Convert the validated model data to a pandas DataFrame."""
        df = pd.DataFrame.from_dict(self.data, orient="index")

        # Convert index to datetime
        index = pd.Index([to_datetime(dt, in_timezone=self.tz) for dt in df.index])
        df.index = index

        # Check if 'date_time' column exists, if not, create it
        if "date_time" not in df.columns:
            df["date_time"] = df.index

        dtype_mapping = {
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
        }

        # Apply dtypes
        for col, dtype in self.dtypes.items():
            if dtype == "datetime64[ns]":
                df[col] = pd.to_datetime(to_datetime(df[col], in_timezone=self.tz))
            elif dtype in dtype_mapping.keys():
                df[col] = df[col].astype(dtype_mapping[dtype])
            else:
                pass

        return df

    @classmethod
    def from_dataframe(
        cls, df: pd.DataFrame, tz: Optional[str] = None
    ) -> "PydanticDateTimeDataFrame":
        """Create a PydanticDateTimeDataFrame instance from a pandas DataFrame."""
        index = pd.Index([to_datetime(dt, as_string=True, in_timezone=tz) for dt in df.index])
        df.index = index

        datetime_columns = [col for col in df.columns if is_datetime64_any_dtype(df[col])]

        return cls(
            data=df.to_dict(orient="index"),
            dtypes={col: str(dtype) for col, dtype in df.dtypes.items()},
            tz=tz,
            datetime_columns=datetime_columns,
        )


class PydanticDateTimeSeries(PydanticBaseModel):
    """Pydantic model for validating pandas Series with datetime index in JSON format.

    This model handles Series data serialized with orient='index', where the keys are
    datetime strings and values are the series values. Provides validation and
    conversion between JSON and pandas Series with datetime index.

    Attributes:
        data (Dict[str, Any]): Dictionary mapping datetime strings to values.
        dtype (str): The data type of the series values.
        tz (str | None): Timezone name if the datetime index is timezone-aware.
    """

    data: Dict[str, Any]
    dtype: str = Field(default="float64")
    tz: Optional[str] = Field(default=None)

    @field_validator("data", mode="after")
    @classmethod
    def validate_datetime_index(cls, v: Dict[str, Any], info: ValidationInfo) -> Dict[str, Any]:
        """Validate that all keys can be parsed as datetime strings.

        Args:
            v: Dictionary with datetime string keys and series values.

        Returns:
            The validated data dictionary.

        Raises:
            ValueError: If any key cannot be parsed as a datetime.
        """
        tz = info.data.get("tz")
        if tz is not None:
            try:
                ZoneInfo(tz)
            except KeyError:
                tz = None
        try:
            # Attempt to parse each key as datetime
            d = dict()
            for dt_str, value in v.items():
                d[to_datetime(dt_str, as_string=True, in_timezone=tz)] = value
            return d
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid datetime string in index: {e}")

    @field_validator("tz")
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        """Validate that the timezone is valid."""
        if v is not None:
            try:
                ZoneInfo(v)
            except KeyError:
                raise ValueError(f"Invalid timezone: {v}")
        return v

    def to_series(self) -> pd.Series:
        """Convert the validated model data to a pandas Series.

        Returns:
            A pandas Series with datetime index constructed from the model data.
        """
        index = [to_datetime(dt, in_timezone=self.tz) for dt in list(self.data.keys())]

        series = pd.Series(data=list(self.data.values()), index=index, dtype=self.dtype)
        return series

    @classmethod
    def from_series(cls, series: pd.Series, tz: Optional[str] = None) -> "PydanticDateTimeSeries":
        """Create a PydanticDateTimeSeries instance from a pandas Series.

        Args:
            series: The pandas Series with datetime index to convert.

        Returns:
            A new instance containing the Series data.

        Raises:
            ValueError: If series index is not datetime type.

        Example:
            >>> dates = pd.date_range('2024-01-01', periods=3)
            >>> s = pd.Series([1.1, 2.2, 3.3], index=dates)
            >>> model = PydanticDateTimeSeries.from_series(s)
        """
        index = pd.Index([to_datetime(dt, as_string=True, in_timezone=tz) for dt in series.index])
        series.index = index

        if len(index) > 0:
            tz = to_datetime(series.index[0]).timezone.name

        return cls(
            data=series.to_dict(),
            dtype=str(series.dtype),
            tz=tz,
        )


class ParametersBaseModel(PydanticBaseModel):
    model_config = ConfigDict(extra="forbid")
