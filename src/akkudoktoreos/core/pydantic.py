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

import inspect
import json
import uuid
import weakref
from copy import deepcopy
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
    get_args,
    get_origin,
)
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger
from pandas.api.types import is_datetime64_any_dtype
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    RootModel,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration

# Global weakref dictionary to hold external state per model instance
# Used as a workaround for PrivateAttr not working in e.g. Mixin Classes
_model_private_state: "weakref.WeakKeyDictionary[Union[PydanticBaseModel, PydanticModelNestedValueMixin], Dict[str, Any]]" = weakref.WeakKeyDictionary()


def merge_models(source: BaseModel, update_dict: dict[str, Any]) -> dict[str, Any]:
    """Merge a Pydantic model instance with an update dictionary.

    Values in update_dict (including None) override source values.
    Nested dictionaries are merged recursively.
    Lists in update_dict replace source lists entirely.

    Args:
        source (BaseModel): Pydantic model instance serving as the source.
        update_dict (dict[str, Any]): Dictionary with updates to apply.

    Returns:
        dict[str, Any]: Merged dictionary representing combined model data.
    """

    def deep_merge(source_data: Any, update_data: Any) -> Any:
        if isinstance(source_data, dict) and isinstance(update_data, dict):
            merged = dict(source_data)
            for key, update_value in update_data.items():
                if key in merged:
                    merged[key] = deep_merge(merged[key], update_value)
                else:
                    merged[key] = update_value
            return merged

        # If both are lists, replace source list with update list
        if isinstance(source_data, list) and isinstance(update_data, list):
            return update_data

        # For other types or if update_data is None, override source_data
        return update_data

    source_dict = source.model_dump(exclude_unset=True)
    merged_result = deep_merge(source_dict, deepcopy(update_dict))
    return merged_result


class PydanticModelNestedValueMixin:
    """A mixin providing methods to get, set and track nested values within a Pydantic model.

    The methods use a '/'-separated path to denote the nested values.
    Supports handling `Optional`, `List`, and `Dict` types, ensuring correct initialization of
    missing attributes.


    Example:
        class Address(PydanticBaseModel):
            city: str

        class User(PydanticBaseModel):
            name: str
            address: Address

        def on_city_change(old, new, path):
            print(f"{path}: {old} -> {new}")

        user = User(name="Alice", address=Address(city="NY"))
        user.track_nested_value("address/city", on_city_change)
        user.set_nested_value("address/city", "LA")  # triggers callback

    """

    def track_nested_value(self, path: str, callback: Callable[[Any, str, Any, Any], None]) -> None:
        """Register a callback for a specific path (or subtree).

        Callback triggers if set path is equal or deeper.

        Args:
            path (str): '/'-separated path to track.
            callback (callable): Function called as callback(model_instance, set_path, old_value, new_value).
        """
        try:
            self._validate_path_structure(path)
            pass
        except:
            raise ValueError(f"Path '{path}' is invalid")
        path = path.strip("/")
        # Use private data workaround
        # Should be:
        #  _nested_value_callbacks: dict[str, list[Callable[[str, Any, Any], None]]]
        #   = PrivateAttr(default_factory=dict)
        nested_value_callbacks = get_private_attr(self, "nested_value_callbacks", dict())
        if path not in nested_value_callbacks:
            nested_value_callbacks[path] = []
        nested_value_callbacks[path].append(callback)
        set_private_attr(self, "nested_value_callbacks", nested_value_callbacks)

        logger.debug("Nested value callbacks {}", nested_value_callbacks)

    def _validate_path_structure(self, path: str) -> None:
        """Validate that a '/'-separated path is structurally valid for this model.

        Checks that each segment of the path corresponds to a field or index in the model's type structure,
        without requiring that all intermediate values are currently initialized. This method is intended
        to ensure that the path could be valid for nested access or assignment, according to the model's
        class definition.

        Args:
            path (str): The '/'-separated attribute/index path to validate (e.g., "address/city" or "items/0/value").

        Raises:
            ValueError: If any segment of the path does not correspond to a valid field in the model,
                or an invalid transition is made (such as an attribute on a non-model).

        Example:
            class Address(PydanticBaseModel):
                city: str

            class User(PydanticBaseModel):
                name: str
                address: Address

            user = User(name="Alice", address=Address(city="NY"))
            user._validate_path_structure("address/city")  # OK
            user._validate_path_structure("address/zipcode")  # Raises ValueError
        """
        path_elements = path.strip("/").split("/")
        # The model we are currently working on
        model: Any = self
        # The model we get the type information from. It is a pydantic BaseModel
        parent: BaseModel = model
        # The field that provides type information for the current key
        # Fields may have nested types that translates to a sequence of keys, not just one
        # - my_field: Optional[list[OtherModel]] -> e.g. "myfield/0" for index 0
        #   parent_key = ["myfield",] ... ["myfield", "0"]
        #   parent_key_types = [list, OtherModel]
        parent_key: list[str] = []
        parent_key_types: list = []

        for i, key in enumerate(path_elements):
            is_final_key = i == len(path_elements) - 1
            # Add current key to parent key to enable nested type tracking
            parent_key.append(key)

            # Get next value
            next_value = None
            if isinstance(model, BaseModel):
                # Track parent and key for possible assignment later
                parent = model
                parent_key = [
                    key,
                ]
                parent_key_types = self._get_key_types(model.__class__, key)

                # If this is the final key, set the value
                if is_final_key:
                    return

                # Attempt to access the next attribute, handling None values
                next_value = getattr(model, key, None)

                # Handle missing values (initialize dict/list/model if necessary)
                if next_value is None:
                    next_type = parent_key_types[len(parent_key) - 1]
                    next_value = self._initialize_value(next_type)

            elif isinstance(model, list):
                # Handle lists
                try:
                    idx = int(key)
                except Exception as e:
                    raise IndexError(
                        f"Invalid list index '{key}' at '{path}': key = '{key}'; parent = '{parent}', parent_key = '{parent_key}'; model = '{model}'; {e}"
                    )

                # Get next type from parent key type information
                next_type = parent_key_types[len(parent_key) - 1]

                if len(model) > idx:
                    next_value = model[idx]
                else:
                    return

                if is_final_key:
                    return

            elif isinstance(model, dict):
                # Handle dictionaries (auto-create missing keys)

                # Get next type from parent key type information
                next_type = parent_key_types[len(parent_key) - 1]

                if is_final_key:
                    return

                if key not in model:
                    return
                else:
                    next_value = model[key]

            else:
                raise KeyError(f"Key '{key}' not found in model.")

            # Move deeper
            model = next_value

    def get_nested_value(self, path: str) -> Any:
        """Retrieve a nested value from the model using a '/'-separated path.

        Supports accessing nested attributes and list indices.

        Args:
            path (str): A '/'-separated path to the nested attribute (e.g., "key1/key2/0").

        Returns:
            Any: The retrieved value.

        Raises:
            KeyError: If a key is not found in the model.
            IndexError: If a list index is out of bounds or invalid.

        Example:
            ```python
            class Address(PydanticBaseModel):
                city: str

            class User(PydanticBaseModel):
                name: str
                address: Address

            user = User(name="Alice", address=Address(city="New York"))
            city = user.get_nested_value("address/city")
            print(city)  # Output: "New York"
            ```
        """
        path_elements = path.strip("/").split("/")
        model: Any = self

        for key in path_elements:
            if isinstance(model, list):
                try:
                    model = model[int(key)]
                except (ValueError, IndexError) as e:
                    raise IndexError(f"Invalid list index at '{path}': {key}; {e}")
            elif isinstance(model, dict):
                try:
                    model = model[key]
                except Exception as e:
                    raise KeyError(f"Invalid dict key at '{path}': {key}; {e}")
            elif isinstance(model, BaseModel):
                model = getattr(model, key)
            else:
                raise KeyError(f"Key '{key}' not found in model.")

        return model

    def set_nested_value(self, path: str, value: Any) -> None:
        """Set a nested value in the model using a '/'-separated path.

        Supports modifying nested attributes and list indices while preserving Pydantic validation.
        Automatically initializes missing `Optional`, `Union`, `dict`, and `list` fields if necessary.
        If a missing field cannot be initialized, raises an exception.

        Triggers the callbacks registered by track_nested_value().

        Args:
            path (str): A '/'-separated path to the nested attribute (e.g., "key1/key2/0").
            value (Any): The new value to set.

        Raises:
            KeyError: If a key is not found in the model.
            IndexError: If a list index is out of bounds or invalid.
            ValueError: If a validation error occurs.
            TypeError: If a missing field cannot be initialized.

        Example:
            ```python
            class Address(PydanticBaseModel):
                city: Optional[str]

            class User(PydanticBaseModel):
                name: str
                address: Optional[Address]
                settings: Optional[Dict[str, Any]]

            user = User(name="Alice", address=None, settings=None)
            user.set_nested_value("address/city", "Los Angeles")
            user.set_nested_value("settings/theme", "dark")

            print(user.address.city)  # Output: "Los Angeles"
            print(user.settings)  # Output: {'theme': 'dark'}
            ```
        """
        path = path.strip("/")
        # Store old value (if possible)
        try:
            old_value = self.get_nested_value(path)
        except Exception as e:
            # We can not get the old value
            # raise ValueError(f"Can not get old (current) value of '{path}': {e}") from e
            old_value = None

        # Proceed with core logic
        self._set_nested_value(path, value)

        # Trigger all callbacks whose path is a prefix of set path
        triggered = set()
        nested_value_callbacks = get_private_attr(self, "nested_value_callbacks", dict())
        for cb_path, callbacks in nested_value_callbacks.items():
            # Match: cb_path == path, or cb_path is a prefix (parent) of path
            pass
            if path == cb_path or path.startswith(cb_path + "/"):
                for cb in callbacks:
                    # Prevent duplicate calls
                    if (cb_path, id(cb)) not in triggered:
                        cb(self, path, old_value, value)
                        triggered.add((cb_path, id(cb)))

    def _set_nested_value(self, path: str, value: Any) -> None:
        """Set a nested value core logic.

        Args:
            path (str): A '/'-separated path to the nested attribute (e.g., "key1/key2/0").
            value (Any): The new value to set.

        Raises:
            KeyError: If a key is not found in the model.
            IndexError: If a list index is out of bounds or invalid.
            ValueError: If a validation error occurs.
            TypeError: If a missing field cannot be initialized.
        """
        path_elements = path.strip("/").split("/")
        # The model we are currently working on
        model: Any = self
        # The model we get the type information from. It is a pydantic BaseModel
        parent: BaseModel = model
        # The field that provides type information for the current key
        # Fields may have nested types that translates to a sequence of keys, not just one
        # - my_field: Optional[list[OtherModel]] -> e.g. "myfield/0" for index 0
        #   parent_key = ["myfield",] ... ["myfield", "0"]
        #   parent_key_types = [list, OtherModel]
        parent_key: list[str] = []
        parent_key_types: list = []

        for i, key in enumerate(path_elements):
            is_final_key = i == len(path_elements) - 1
            # Add current key to parent key to enable nested type tracking
            parent_key.append(key)

            # Get next value
            next_value = None
            if isinstance(model, BaseModel):
                # Track parent and key for possible assignment later
                parent = model
                parent_key = [
                    key,
                ]
                parent_key_types = self._get_key_types(model.__class__, key)

                # If this is the final key, set the value
                if is_final_key:
                    try:
                        model.__pydantic_validator__.validate_assignment(model, key, value)
                    except ValidationError as e:
                        raise ValueError(f"Error updating model: {e}") from e
                    return

                # Attempt to access the next attribute, handling None values
                next_value = getattr(model, key, None)

                # Handle missing values (initialize dict/list/model if necessary)
                if next_value is None:
                    next_type = parent_key_types[len(parent_key) - 1]
                    next_value = self._initialize_value(next_type)
                    if next_value is None:
                        raise TypeError(
                            f"Unable to initialize missing value for key '{key}' in path '{path}' with type {next_type} of {parent_key}:{parent_key_types}."
                        )
                    setattr(parent, key, next_value)
                    # pydantic may copy on validation assignment - reread to get the copied model
                    next_value = getattr(model, key, None)

            elif isinstance(model, list):
                # Handle lists (ensure index exists and modify safely)
                try:
                    idx = int(key)
                except Exception as e:
                    raise IndexError(
                        f"Invalid list index '{key}' at '{path}': key = '{key}'; parent = '{parent}', parent_key = '{parent_key}'; model = '{model}'; {e}"
                    )

                # Get next type from parent key type information
                next_type = parent_key_types[len(parent_key) - 1]

                if len(model) > idx:
                    next_value = model[idx]
                else:
                    # Extend the list with default values if index is out of range
                    while len(model) <= idx:
                        next_value = self._initialize_value(next_type)
                        if next_value is None:
                            raise TypeError(
                                f"Unable to initialize missing value for key '{key}' in path '{path}' with type {next_type} of {parent_key}:{parent_key_types}."
                            )
                        model.append(next_value)

                if is_final_key:
                    if (
                        (isinstance(next_type, type) and not isinstance(value, next_type))
                        or (next_type is dict and not isinstance(value, dict))
                        or (next_type is list and not isinstance(value, list))
                    ):
                        raise TypeError(
                            f"Expected type {next_type} for key '{key}' in path '{path}', but got {type(value)}: {value}"
                        )
                    model[idx] = value
                    return

            elif isinstance(model, dict):
                # Handle dictionaries (auto-create missing keys)

                # Get next type from parent key type information
                next_type = parent_key_types[len(parent_key) - 1]

                if is_final_key:
                    if (
                        (isinstance(next_type, type) and not isinstance(value, next_type))
                        or (next_type is dict and not isinstance(value, dict))
                        or (next_type is list and not isinstance(value, list))
                    ):
                        raise TypeError(
                            f"Expected type {next_type} for key '{key}' in path '{path}', but got {type(value)}: {value}"
                        )
                    model[key] = value
                    return

                if key not in model:
                    next_value = self._initialize_value(next_type)
                    if next_value is None:
                        raise TypeError(
                            f"Unable to initialize missing value for key '{key}' in path '{path}' with type {next_type} of {parent_key}:{parent_key_types}."
                        )
                    model[key] = next_value
                else:
                    next_value = model[key]

            else:
                raise KeyError(f"Key '{key}' not found in model.")

            # Move deeper
            model = next_value

    @staticmethod
    def _get_key_types(model: Type[BaseModel], key: str) -> List[Union[Type[Any], list, dict]]:
        """Returns a list of nested types for a given Pydantic model key.

        - Skips `Optional` and `Union`, using only the first non-None type.
        - Skips dictionary keys and only adds value types.
        - Keeps `list` and `dict` as origins.

        Args:
            model (Type[BaseModel]): The Pydantic model class to inspect.
            key (str): The attribute name in the model.

        Returns:
            List[Union[Type[Any], list, dict]]: A list of extracted types, preserving `list` and `dict` origins.

        Raises:
            TypeError: If the key does not exist or lacks a valid type annotation.
        """
        if not inspect.isclass(model):
            raise TypeError(f"Model '{model}' is not of class type.")

        if key not in model.model_fields:
            raise TypeError(f"Field '{key}' does not exist in model '{model.__name__}'.")

        field_annotation = model.model_fields[key].annotation
        if not field_annotation:
            raise TypeError(
                f"Missing type annotation for field '{key}' in model '{model.__name__}'."
            )

        nested_types: list[Union[Type[Any], list, dict]] = []
        queue: list[Any] = [field_annotation]

        while queue:
            annotation = queue.pop(0)
            origin = get_origin(annotation)
            args = get_args(annotation)

            # Handle Union (Optional[X] is treated as Union[X, None])
            if origin is Union:
                queue.extend(arg for arg in args if arg is not type(None))
                continue

            # Handle lists and dictionaries
            if origin is list:
                nested_types.append(list)
                if args:
                    queue.append(args[0])  # Extract value type for list[T]
                continue

            if origin is dict:
                nested_types.append(dict)
                if len(args) == 2:
                    queue.append(args[1])  # Extract only the value type for dict[K, V]
                continue

            # If it's a BaseModel, add it to the list
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                nested_types.append(annotation)
                continue

            # Otherwise, it's a standard type (e.g., str, int, bool, float, etc.)
            nested_types.append(annotation)

        return nested_types

    @staticmethod
    def _initialize_value(type_hint: Type[Any] | None | list[Any] | dict[Any, Any]) -> Any:
        """Initialize a missing value based on the provided type hint.

        Args:
            type_hint (Type[Any] | None | list[Any] | dict[Any, Any]): The type hint that determines
                how the missing value should be initialized.

        Returns:
            Any: An instance of the expected type (e.g., list, dict, or Pydantic model), or `None`
                if initialization is not possible.

        Raises:
            TypeError: If instantiation fails.

        Example:
            - For `list[str]`, returns `[]`
            - For `dict[str, Any]`, returns `{}`
            - For `Address` (a Pydantic model), returns a new `Address()` instance.
        """
        if type_hint is None:
            return None

        # Handle direct instances of list or dict
        if isinstance(type_hint, list):
            return []
        if isinstance(type_hint, dict):
            return {}

        origin = get_origin(type_hint)

        # Handle generic list and dictionary
        if origin is list:
            return []
        if origin is dict:
            return {}

        # Handle Pydantic models
        if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
            try:
                return type_hint.model_construct()
            except Exception as e:
                raise TypeError(f"Failed to initialize model '{type_hint.__name__}': {e}")

        # Handle standard built-in types (int, float, str, bool, etc.)
        if isinstance(type_hint, type):
            try:
                return type_hint()
            except Exception as e:
                raise TypeError(f"Failed to initialize instance of '{type_hint.__name__}': {e}")

        raise TypeError(f"Unsupported type hint '{type_hint}' for initialization.")


class PydanticBaseModel(PydanticModelNestedValueMixin, BaseModel):
    """Base model with pendulum datetime support, nested value utilities, and stable hashing.

    This class provides:
    - ISO 8601 serialization/deserialization of `pendulum.DateTime` fields.
    - Nested attribute access and mutation via `PydanticModelNestedValueMixin`.
    - A consistent hash using a UUID for use in sets and as dictionary keys
    """

    # Enable custom serialization globally in config
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        use_enum_values=True,
        validate_assignment=True,
    )

    _uuid: str = PrivateAttr(default_factory=lambda: str(uuid.uuid4()))
    """str: A private UUID string generated on instantiation, used for hashing."""

    def __hash__(self) -> int:
        """Returns a stable hash based on the instance's UUID.

        Returns:
            int: Hash value derived from the model's UUID.
        """
        return hash(self._uuid)

    def reset_to_defaults(self) -> "PydanticBaseModel":
        """Resets the fields to their default values."""
        for field_name, field_info in self.__class__.model_fields.items():
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

    # Override Pydantic’s serialization to include computed fields by default
    def model_dump(
        self, *args: Any, include_computed_fields: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        """Custom dump method to serialize computed fields by default."""
        result = super().model_dump(*args, **kwargs)

        if not include_computed_fields:
            for computed_field_name in self.__class__.model_computed_fields:
                result.pop(computed_field_name, None)

        return result

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

        # Allowed exact dtypes
        valid_base_dtypes = {"int64", "float64", "bool", "object", "string"}

        def is_valid_dtype(dtype: str) -> bool:
            # Allow timezone-aware or naive datetime64
            if dtype.startswith("datetime64[ns"):
                return True
            return dtype in valid_base_dtypes

        invalid_dtypes = [dtype for dtype in v.values() if not is_valid_dtype(dtype)]
        if invalid_dtypes:
            raise ValueError(f"Unsupported dtypes: {set(invalid_dtypes)}")

        # Cross-check with data column existence
        data = info.data.get("data", {})
        if data:
            columns = set(next(iter(data.values())).keys())
            missing_columns = set(v.keys()) - columns
            if missing_columns:
                raise ValueError(f"dtype columns must exist in data columns: {missing_columns}")

        return v

    def to_dataframe(self) -> pd.DataFrame:
        """Convert the validated model data to a pandas DataFrame."""
        df = pd.DataFrame.from_dict(self.data, orient="index")

        # Convert index to datetime
        # index = pd.Index([to_datetime(dt, in_timezone=self.tz) for dt in df.index])
        index = [to_datetime(dt, in_timezone=self.tz) for dt in df.index]
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
            if dtype.startswith("datetime64[ns"):
                df[col] = pd.to_datetime(df[col], utc=True)
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

    # --- Direct Manipulation Methods ---

    def _normalize_index(self, index: str | DateTime) -> str:
        """Normalize index into timezone-aware datetime string.

        Args:
            index (str | DateTime): A datetime-like value.

        Returns:
            str: Normalized datetime string based on model timezone.
        """
        return to_datetime(index, as_string=True, in_timezone=self.tz)

    def add_row(self, index: str | DateTime, row: Dict[str, Any]) -> None:
        """Add a new row to the dataset.

        Args:
            index (str | DateTime): Timestamp of the new row.
            row (Dict[str, Any]): Dictionary of column values. Must match existing columns.

        Raises:
            ValueError: If row does not contain the exact same columns as existing rows.
        """
        idx = self._normalize_index(index)

        if self.data:
            existing_cols = set(next(iter(self.data.values())).keys())
            if set(row.keys()) != existing_cols:
                raise ValueError(f"Row must have exactly these columns: {existing_cols}")
        self.data[idx] = row

    def update_row(self, index: str | DateTime, updates: Dict[str, Any]) -> None:
        """Update values for an existing row.

        Args:
            index (str | DateTime): Timestamp of the row to modify.
            updates (Dict[str, Any]): Key/value pairs of columns to update.

        Raises:
            KeyError: If row or column does not exist.
        """
        idx = self._normalize_index(index)
        if idx not in self.data:
            raise KeyError(f"Row {idx} not found")
        for col, value in updates.items():
            if col not in self.data[idx]:
                raise KeyError(f"Column {col} does not exist")
            self.data[idx][col] = value

    def delete_row(self, index: str | DateTime) -> None:
        """Delete a row from the dataset.

        Args:
            index (str | DateTime): Timestamp of the row to delete.
        """
        idx = self._normalize_index(index)
        if idx in self.data:
            del self.data[idx]

    def set_value(self, index: str | DateTime, column: str, value: Any) -> None:
        """Set a single cell value.

        Args:
            index (str | datetime): Timestamp of the row.
            column (str): Column name.
            value (Any): New value.
        """
        self.update_row(index, {column: value})

    def get_value(self, index: str | DateTime, column: str) -> Any:
        """Retrieve a single cell value.

        Args:
            index (str | DateTime): Timestamp of the row.
            column (str): Column name.

        Returns:
            Any: Value stored at the given location.
        """
        idx = self._normalize_index(index)
        return self.data[idx][column]

    def add_column(self, name: str, default: Any = None, dtype: Optional[str] = None) -> None:
        """Add a new column to all rows.

        Args:
            name (str): Name of the column to add.
            default (Any, optional): Default value for all rows. Defaults to None.
            dtype (Optional[str], optional): Declared data type. Defaults to None.
        """
        for row in self.data.values():
            row[name] = default
        if dtype:
            self.dtypes[name] = dtype

    def rename_column(self, old: str, new: str) -> None:
        """Rename a column across all rows.

        Args:
            old (str): Existing column name.
            new (str): New column name.

        Raises:
            KeyError: If column does not exist.
        """
        for row in self.data.values():
            if old not in row:
                raise KeyError(f"Column {old} does not exist")
            row[new] = row.pop(old)
        if old in self.dtypes:
            self.dtypes[new] = self.dtypes.pop(old)
        if old in self.datetime_columns:
            self.datetime_columns = [new if c == old else c for c in self.datetime_columns]

    def drop_column(self, name: str) -> None:
        """Remove a column from all rows.

        Args:
            name (str): Column to remove.
        """
        for row in self.data.values():
            if name in row:
                del row[name]
        self.dtypes.pop(name, None)
        self.datetime_columns = [c for c in self.datetime_columns if c != name]


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


def set_private_attr(
    model: Union[PydanticBaseModel, PydanticModelNestedValueMixin], key: str, value: Any
) -> None:
    """Set a private attribute for a model instance (not stored in model itself)."""
    if model not in _model_private_state:
        _model_private_state[model] = {}
    _model_private_state[model][key] = value


def get_private_attr(
    model: Union[PydanticBaseModel, PydanticModelNestedValueMixin], key: str, default: Any = None
) -> Any:
    """Get a private attribute or return default."""
    return _model_private_state.get(model, {}).get(key, default)


def del_private_attr(
    model: Union[PydanticBaseModel, PydanticModelNestedValueMixin], key: str
) -> None:
    """Delete a private attribute."""
    if model in _model_private_state and key in _model_private_state[model]:
        del _model_private_state[model][key]
