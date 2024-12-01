"""Module for managing and serializing Pydantic-based models with custom support.

This module introduces the `PydanticBaseModel` class, which extends Pydantic’s `BaseModel` to facilitate
custom serialization and deserialization for `pendulum.DateTime` objects. The main features include
automatic handling of `pendulum.DateTime` fields, custom serialization to ISO 8601 format, and utility
methods for converting model instances to and from dictionary and JSON formats.

Key Classes:
    - PendulumDateTime: A custom type adapter that provides serialization and deserialization
        functionality for `pendulum.DateTime` objects, converting them to ISO 8601 strings and back.
    - PydanticBaseModel: A base model class for handling prediction records or configuration data
        with automatic Pendulum DateTime handling and additional methods for JSON and dictionary
        conversion.

Classes:
    PendulumDateTime(TypeAdapter[pendulum.DateTime]): Type adapter for `pendulum.DateTime` fields
        with ISO 8601 serialization. Includes:
        - serialize: Converts `pendulum.DateTime` instances to ISO 8601 string.
        - deserialize: Converts ISO 8601 strings to `pendulum.DateTime` instances.
        - is_iso8601: Validates if a string matches the ISO 8601 date format.

    PydanticBaseModel(BaseModel): Extends `pydantic.BaseModel` to handle `pendulum.DateTime` fields
        and adds convenience methods for dictionary and JSON serialization. Key methods:
        - model_dump: Dumps the model, converting `pendulum.DateTime` fields to ISO 8601.
        - model_construct: Constructs a model instance with automatic deserialization of
            `pendulum.DateTime` fields from ISO 8601.
        - to_dict: Serializes the model instance to a dictionary.
        - from_dict: Constructs a model instance from a dictionary.
        - to_json: Converts the model instance to a JSON string.
        - from_json: Creates a model instance from a JSON string.

Usage Example:
    # Define custom settings in a model using PydanticBaseModel
    class PredictionCommonSettings(PydanticBaseModel):
        prediction_start: pendulum.DateTime = Field(...)

    # Serialize a model instance to a dictionary or JSON
    config = PredictionCommonSettings(prediction_start=pendulum.now())
    config_dict = config.to_dict()
    config_json = config.to_json()

    # Deserialize from dictionary or JSON
    new_config = PredictionCommonSettings.from_dict(config_dict)
    restored_config = PredictionCommonSettings.from_json(config_json)

Dependencies:
    - `pendulum`: Required for handling timezone-aware datetime fields.
    - `pydantic`: Required for model and validation functionality.

Notes:
    - This module enables custom handling of Pendulum DateTime fields within Pydantic models,
      which is particularly useful for applications requiring consistent ISO 8601 datetime formatting
      and robust timezone-aware datetime support.
"""

import json
import re
from typing import Any, Type

import pendulum
from pydantic import BaseModel, ConfigDict, TypeAdapter


# Custom type adapter for Pendulum DateTime fields
class PendulumDateTime(TypeAdapter[pendulum.DateTime]):
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

    # Override Pydantic’s serialization for all DateTime fields
    def model_dump(self, *args: Any, **kwargs: Any) -> dict:
        """Custom dump method to handle serialization for DateTime fields."""
        result = super().model_dump(*args, **kwargs)
        for key, value in result.items():
            if isinstance(value, pendulum.DateTime):
                result[key] = PendulumDateTime.serialize(value)
        return result

    @classmethod
    def model_construct(cls, data: dict) -> "PydanticBaseModel":
        """Custom constructor to handle deserialization for DateTime fields."""
        for key, value in data.items():
            if isinstance(value, str) and PendulumDateTime.is_iso8601(value):
                data[key] = PendulumDateTime.deserialize(value)
        return super().model_construct(data)

    def reset_optional(self) -> "PydanticBaseModel":
        """Resets all optional fields in the model to None.

        Iterates through all model fields and sets any optional (non-required)
        fields to None. The modification is done in-place on the current instance.

        Returns:
            PydanticBaseModel: The current instance with all optional fields
                reset to None.

        Example:
            >>> settings = PydanticBaseModel(name="test", optional_field="value")
            >>> settings.reset_optional()
            >>> assert settings.optional_field is None
        """
        for field_name, field in self.model_fields.items():
            if field.is_required is False:  # Check if field is optional
                setattr(self, field_name, None)
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

    @classmethod
    def from_dict_with_reset(cls, data: dict | None = None) -> "PydanticBaseModel":
        """Creates a new instance with reset optional fields, then updates from dict.

        First creates an instance with default values, resets all optional fields
        to None, then updates the instance with the provided dictionary data if any.

        Args:
            data (dict | None): Dictionary containing field values to initialize
                the instance with. Defaults to None.

        Returns:
            PydanticBaseModel: A new instance with all optional fields initially
                reset to None and then updated with provided data.

        Example:
            >>> data = {"name": "test", "optional_field": "value"}
            >>> settings = PydanticBaseModel.from_dict_with_reset(data)
            >>> # All non-specified optional fields will be None
        """
        # Create instance with model defaults
        instance = cls()

        # Reset all optional fields to None
        instance.reset_optional()

        # Update with provided data if any
        if data:
            # Use model_validate to ensure proper type conversion and validation
            updated_instance = instance.model_validate({**instance.model_dump(), **data})
            return updated_instance

        return instance

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
