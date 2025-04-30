from collections.abc import Callable
from typing import Any, Optional

from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class classproperty:
    """A decorator to define a read-only property at the class level.

    This class replaces the built-in `property` which is no longer available in
    combination with @classmethod since Python 3.13 to allow a method to be
    accessed as a property on the class itself, rather than an instance. This
    is useful when you want a property-like syntax for methods that depend on
    the class rather than any instance of the class.

    Example:
        class MyClass:
            _value = 42

            @classproperty
            def value(cls):
                return cls._value

        print(MyClass.value)  # Outputs: 42

    Methods:
        __get__: Retrieves the value of the class property by calling the
                 decorated method on the class.

    Parameters:
        fget (Callable[[Any], Any]): A method that takes the class as an
                                      argument and returns a value.

    Raises:
        RuntimeError: If `fget` is not defined when `__get__` is called.
    """

    def __init__(self, fget: Callable[[Any], Any]) -> None:
        self.fget = fget

    def __get__(self, _: Any, owner_cls: Optional[type[Any]] = None) -> Any:
        if owner_cls is None:
            return self
        if self.fget is None:
            raise RuntimeError("'fget' not defined when `__get__` is called")
        return self.fget(owner_cls)
