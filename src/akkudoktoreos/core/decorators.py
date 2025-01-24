from typing import Any, Optional

from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class classproperty(property):
    """A decorator to define a read-only property at the class level.

    This class extends the built-in `property` to allow a method to be accessed
    as a property on the class itself, rather than an instance. This is useful
    when you want a property-like syntax for methods that depend on the class
    rather than any instance of the class.

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
        fget (Callable[[type], Any]): A method that takes the class as an
                                      argument and returns a value.

    Raises:
        AssertionError: If `fget` is not defined when `__get__` is called.
    """

    def __get__(self, _: Any, owner_cls: Optional[type[Any]] = None) -> Any:
        if owner_cls is None:
            return self
        assert self.fget is not None
        return self.fget(owner_cls)
