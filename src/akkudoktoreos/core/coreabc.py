"""Abstract and base classes for EOS core.

This module provides foundational classes for handling configuration and prediction functionality
in EOS. It includes base classes that provide convenient access to global
configuration and prediction instances through properties.

Classes:
    - ConfigMixin: Mixin class for managing and accessing global configuration.
    - PredictionMixin: Mixin class for managing and accessing global prediction data.
    - SingletonMixin: Mixin class to create singletons.
"""

import threading
from typing import Any, ClassVar, Dict, Optional, Type

from loguru import logger

from akkudoktoreos.core.decorators import classproperty
from akkudoktoreos.utils.datetimeutil import DateTime

config_eos: Any = None
measurement_eos: Any = None
prediction_eos: Any = None
ems_eos: Any = None


class ConfigMixin:
    """Mixin class for managing EOS configuration data.

    This class serves as a foundational component for EOS-related classes requiring access
    to the global EOS configuration. It provides a `config` property that dynamically retrieves
    the configuration instance, ensuring up-to-date access to configuration settings.

    Usage:
        Subclass this base class to gain access to the `config` attribute, which retrieves the
        global configuration instance lazily to avoid import-time circular dependencies.

    Attributes:
        config (ConfigEOS): Property to access the global EOS configuration.

    Example:
        .. code-block:: python

            class MyEOSClass(ConfigMixin):
                def my_method(self):
                    if self.config.myconfigval:

    """

    @classproperty
    def config(cls) -> Any:
        """Convenience class method/ attribute to retrieve the EOS configuration data.

        Returns:
            ConfigEOS: The configuration.
        """
        # avoid circular dependency at import time
        global config_eos
        if config_eos is None:
            from akkudoktoreos.config.config import get_config

            config_eos = get_config()

        return config_eos


class MeasurementMixin:
    """Mixin class for managing EOS measurement data.

    This class serves as a foundational component for EOS-related classes requiring access
    to global measurement data. It provides a `measurement` property that dynamically retrieves
    the measurement instance, ensuring up-to-date access to measurement results.

    Usage:
        Subclass this base class to gain access to the `measurement` attribute, which retrieves the
        global measurement instance lazily to avoid import-time circular dependencies.

    Attributes:
        measurement (Measurement): Property to access the global EOS measurement data.

    Example:
        .. code-block:: python

            class MyOptimizationClass(MeasurementMixin):
                def analyze_mymeasurement(self):
                    measurement_data = self.measurement.mymeasurement
                    # Perform analysis

    """

    @classproperty
    def measurement(cls) -> Any:
        """Convenience class method/ attribute to retrieve the EOS measurement data.

        Returns:
            Measurement: The measurement.
        """
        # avoid circular dependency at import time
        global measurement_eos
        if measurement_eos is None:
            from akkudoktoreos.measurement.measurement import get_measurement

            measurement_eos = get_measurement()

        return measurement_eos


class PredictionMixin:
    """Mixin class for managing EOS prediction data.

    This class serves as a foundational component for EOS-related classes requiring access
    to global prediction data. It provides a `prediction` property that dynamically retrieves
    the prediction instance, ensuring up-to-date access to prediction results.

    Usage:
        Subclass this base class to gain access to the `prediction` attribute, which retrieves the
        global prediction instance lazily to avoid import-time circular dependencies.

    Attributes:
        prediction (Prediction): Property to access the global EOS prediction data.

    Example:
        .. code-block:: python

            class MyOptimizationClass(PredictionMixin):
                def analyze_myprediction(self):
                    prediction_data = self.prediction.mypredictionresult
                    # Perform analysis

    """

    @classproperty
    def prediction(cls) -> Any:
        """Convenience class method/ attribute to retrieve the EOS prediction data.

        Returns:
            Prediction: The prediction.
        """
        # avoid circular dependency at import time
        global prediction_eos
        if prediction_eos is None:
            from akkudoktoreos.prediction.prediction import get_prediction

            prediction_eos = get_prediction()

        return prediction_eos


class EnergyManagementSystemMixin:
    """Mixin class for managing EOS energy management system.

    This class serves as a foundational component for EOS-related classes requiring access
    to global energy management system. It provides a `ems` property that dynamically retrieves
    the energy management system instance, ensuring up-to-date access to energy management system
    control.

    Usage:
        Subclass this base class to gain access to the `ems` attribute, which retrieves the
        global EnergyManagementSystem instance lazily to avoid import-time circular dependencies.

    Attributes:
        ems (EnergyManagementSystem): Property to access the global EOS energy management system.

    Example:
        .. code-block:: python

            class MyOptimizationClass(EnergyManagementSystemMixin):
                def analyze_myprediction(self):
                    ems_data = self.ems.the_ems_method()
                    # Perform analysis

    """

    @classproperty
    def ems(cls) -> Any:
        """Convenience class method/ attribute to retrieve the EOS energy management system.

        Returns:
            EnergyManagementSystem: The energy management system.
        """
        # avoid circular dependency at import time
        global ems_eos
        if ems_eos is None:
            from akkudoktoreos.core.ems import get_ems

            ems_eos = get_ems()

        return ems_eos


class StartMixin(EnergyManagementSystemMixin):
    """A mixin to manage the start datetime for energy management.

    Provides property:
        - `start_datetime`: The starting datetime of the current or latest energy management.
    """

    @classproperty
    def ems_start_datetime(cls) -> Optional[DateTime]:
        """Convenience class method/ attribute to retrieve the start datetime of the current or latest energy management.

        Returns:
            DateTime: The starting datetime of the current or latest energy management, or None.
        """
        # avoid circular dependency at import time
        global ems_eos
        if ems_eos is None:
            from akkudoktoreos.core.ems import get_ems

            ems_eos = get_ems()

        return ems_eos.start_datetime


class SingletonMixin:
    """A thread-safe singleton mixin class.

    Ensures that only one instance of the derived class is created, even when accessed from multiple
    threads. This mixin is intended to be combined with other classes, such as Pydantic models,
    to make them singletons.

    Attributes:
        _instances (Dict[Type, Any]): A dictionary holding instances of each singleton class.
        _lock (threading.Lock): A lock to synchronize access to singleton instance creation.

    Usage:
        - Inherit from `SingletonMixin` alongside other classes to make them singletons.
        - Avoid using `__init__` to reinitialize the singleton instance after it has been created.

    Example:
        .. code-block:: python

            class MySingletonModel(SingletonMixin, PydanticBaseModel):
                name: str

                # implement __init__ to avoid re-initialization of parent classes:
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    if hasattr(self, "_initialized"):
                        return
                    # Your initialisation here
                    ...
                    super().__init__(*args, **kwargs)

            instance1 = MySingletonModel(name="Instance 1")
            instance2 = MySingletonModel(name="Instance 2")

            assert instance1 is instance2  # True
            print(instance1.name)          # Output: "Instance 1"

    """

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _instances: ClassVar[Dict[Type, Any]] = {}

    def __new__(cls: Type["SingletonMixin"], *args: Any, **kwargs: Any) -> "SingletonMixin":
        """Creates or returns the singleton instance of the class.

        Ensures thread-safe instance creation by locking during the first instantiation.

        Args:
            *args: Positional arguments for instance creation (ignored if instance exists).
            **kwargs: Keyword arguments for instance creation (ignored if instance exists).

        Returns:
            SingletonMixin: The singleton instance of the derived class.
        """
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    instance = super().__new__(cls)
                    cls._instances[cls] = instance
        return cls._instances[cls]

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance, forcing it to be recreated on next access."""
        with cls._lock:
            if cls in cls._instances:
                del cls._instances[cls]
                logger.debug(f"{cls.__name__} singleton instance has been reset.")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the singleton instance if it has not been initialized previously.

        Further calls to `__init__` are ignored for the singleton instance.

        Args:
            *args: Positional arguments for initialization.
            **kwargs: Keyword arguments for initialization.
        """
        if not hasattr(self, "_initialized"):
            super().__init__(*args, **kwargs)
            self._initialized = True
