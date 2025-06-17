"""Utility module for string-to-boolean conversion."""

from typing import Any


def str2bool(value: Any) -> bool:
    """Convert a string or boolean value to a boolean.

    This function normalizes common textual representations of truthy and
    falsy values (case-insensitive). It also accepts an existing boolean
    and returns it unchanged.

    Accepted truthy values:
        - "yes", "y", "true", "t", "1", "on"

    Accepted falsy values:
        - "no", "n", "false", "f", "0", "off"

    Args:
        value (Union[str, bool]): The input value to convert. Can be a string
            (e.g., "true", "no", "on") or a boolean.

    Returns:
        bool: The corresponding boolean value.

    Raises:
        ValueError: If the input cannot be interpreted as a boolean.

    Examples:
        >>> str2bool("yes")
        True
        >>> str2bool("OFF")
        False
        >>> str2bool(True)
        True
        >>> str2bool("n")
        False
        >>> str2bool("maybe")
        Traceback (most recent call last):
            ...
        ValueError: Invalid boolean value: maybe
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        val = value.strip().lower()
        if val in ("yes", "y", "true", "t", "1", "on"):
            return True
        if val in ("no", "n", "false", "f", "0", "off"):
            return False

    raise ValueError(f"Invalid boolean value: {value}")
