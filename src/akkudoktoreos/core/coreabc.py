"""Abstract and base classes for EOS core.

This module provides foundational classes and functions to access global EOS resources.
"""

from __future__ import (
    annotations,  # use types lazy as strings, helps to prevent circular dependencies
)

import threading
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional, Type, Union

from loguru import logger

from akkudoktoreos.core.decorators import classproperty
from akkudoktoreos.utils.datetimeutil import DateTime

if TYPE_CHECKING:
    # Prevents circular dependies
    from akkudoktoreos.adapter.adapter import Adapter
    from akkudoktoreos.config.config import ConfigEOS
    from akkudoktoreos.core.database import Database
    from akkudoktoreos.core.ems import EnergyManagement
    from akkudoktoreos.devices.devices import ResourceRegistry
    from akkudoktoreos.measurement.measurement import Measurement
    from akkudoktoreos.prediction.prediction import Prediction


# Module level singleton cache
_adapter_eos: Optional[Adapter] = None
_config_eos: Optional[ConfigEOS] = None
_ems_eos: Optional[EnergyManagement] = None
_database_eos: Optional[Database] = None
_measurement_eos: Optional[Measurement] = None
_prediction_eos: Optional[Prediction] = None
_resource_registry_eos: Optional[ResourceRegistry] = None


def get_adapter(init: bool = False) -> Adapter:
    """Retrieve the singleton EOS Adapter instance.

    This function provides access to the global EOS Adapter instance. The Adapter
    object is created on first access if `init` is True. If the instance is
    accessed before initialization and `init` is False, a RuntimeError is raised.

    Args:
        init (bool): If True, create the Adapter instance if it does not exist.
                     Default is False.

    Returns:
        Adapter: The global EOS Adapter instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            adapter = get_adapter(init=True)  # Initialize and retrieve
            adapter.do_something()
    """
    global _adapter_eos
    if _adapter_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("Adapter access before init.")

        from akkudoktoreos.adapter.adapter import Adapter

        _adapter_eos = Adapter()

    return _adapter_eos


class AdapterMixin:
    """Mixin class for managing EOS adapter.

    This class serves as a foundational component for EOS-related classes requiring access
    to the global EOS adapters. It provides a `adapter` property that dynamically retrieves
    the adapter instance.

    Usage:
        Subclass this base class to gain access to the `adapter` attribute, which retrieves the
        global adapter instance lazily to avoid import-time circular dependencies.

    Attributes:
        adapter (Adapter): Property to access the global EOS adapter.

    Example:
        .. code-block:: python

            class MyEOSClass(AdapterMixin):
                def my_method(self):
                    self.adapter.update_date()

    """

    @classproperty
    def adapter(cls) -> Adapter:
        """Convenience class method/ attribute to retrieve the EOS adapters.

        Returns:
            Adapter: The adapters.
        """
        return get_adapter()


def get_config(init: Union[bool, dict[str, bool]] = False) -> ConfigEOS:
    """Retrieve the singleton EOS configuration instance.

    This function provides controlled access to the global EOS configuration
    singleton (`ConfigEOS`). The configuration is created lazily on first
    access and can be initialized with a configurable set of settings sources.

    By default, accessing the configuration without prior initialization
    raises a `RuntimeError`. Passing `init=True` or an initialization
    configuration dictionary enables creation of the singleton.

    Args:
        init (Union[bool, dict[str, bool]]):
            Controls initialization of the configuration.

            - ``False`` (default): Do not initialize. Raises ``RuntimeError``
              if the configuration does not yet exist.
            - ``True``: Initialize the configuration using default
              initialization behavior (all settings sources enabled).
            - ``dict[str, bool]``: Initialize the configuration with fine-grained
              control over which settings sources are enabled. Missing keys
              default to ``True``.

            Supported keys include:
                - ``with_init_settings``
                - ``with_env_settings``
                - ``with_dotenv_settings``
                - ``with_file_settings``
                - ``with_file_secret_settings``

    Returns:
        ConfigEOS: The global EOS configuration singleton instance.

    Raises:
        RuntimeError:
            If the configuration has not been initialized and ``init`` is
            ``False``.

    Usage:
        .. code-block:: python

            # Initialize with default behavior (all sources enabled)
            config = get_config(init=True)

            # Initialize with explicit source control
            config = get_config(init={
                "with_init_settings": True,
                "with_env_settings": True,
                "with_dotenv_settings": True,
                "with_file_settings": False,
                "with_file_secret_settings": False,
            })

            # Access existing configuration
            host = get_config().server.host
    """
    global _config_eos
    if _config_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("Config access before init.")

        if isinstance(init, dict):
            ConfigEOS._init_config_eos = init

        _config_eos = ConfigEOS()

    return _config_eos


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
    def config(cls) -> ConfigEOS:
        """Convenience class method/ attribute to retrieve the EOS configuration data.

        Returns:
            ConfigEOS: The configuration.
        """
        return get_config()


def get_measurement(init: bool = False) -> Measurement:
    """Retrieve the singleton EOS Measurement instance.

    This function provides access to the global EOS Measurement object. The
    Measurement instance is created on first access if `init` is True. If the
    instance is accessed before initialization and `init` is False, a RuntimeError
    is raised.

    Args:
        init (bool): If True, create the Measurement instance if it does not exist.
                     Default is False.

    Returns:
        Measurement: The global EOS Measurement instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            measurement = get_measurement(init=True)  # Initialize and retrieve
            measurement.read_sensor_data()
    """
    global _measurement_eos
    if _measurement_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("Measurement access before init.")

        from akkudoktoreos.measurement.measurement import Measurement

        _measurement_eos = Measurement()

    return _measurement_eos


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
    def measurement(cls) -> Measurement:
        """Convenience class method/ attribute to retrieve the EOS measurement data.

        Returns:
            Measurement: The measurement.
        """
        return get_measurement()


def get_prediction(init: bool = False) -> Prediction:
    """Retrieve the singleton EOS Prediction instance.

    This function provides access to the global EOS Prediction object. The
    Prediction instance is created on first access if `init` is True. If the
    instance is accessed before initialization and `init` is False, a RuntimeError
    is raised.

    Args:
        init (bool): If True, create the Prediction instance if it does not exist.
                     Default is False.

    Returns:
        Prediction: The global EOS Prediction instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            prediction = get_prediction(init=True)  # Initialize and retrieve
            prediction.forecast_next_hour()
    """
    global _prediction_eos
    if _prediction_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("Prediction access before init.")

        from akkudoktoreos.prediction.prediction import Prediction

        _prediction_eos = Prediction()

    return _prediction_eos


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
    def prediction(cls) -> Prediction:
        """Convenience class method/ attribute to retrieve the EOS prediction data.

        Returns:
            Prediction: The prediction.
        """
        return get_prediction()


def get_ems(init: bool = False) -> EnergyManagement:
    """Retrieve the singleton EOS Energy Management System (EMS) instance.

    This function provides access to the global EOS EMS instance. The instance
    is created on first access if `init` is True. If the instance is accessed
    before initialization and `init` is False, a RuntimeError is raised.

    Args:
        init (bool): If True, create the EMS instance if it does not exist.
                     Default is False.

    Returns:
        EnergyManagement: The global EOS EMS instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            ems = get_ems(init=True)  # Initialize and retrieve
            ems.start_energy_management_loop()
    """
    global _ems_eos
    if _ems_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("EMS access before init.")

        from akkudoktoreos.core.ems import EnergyManagement

        _ems_eos = EnergyManagement()

    return _ems_eos


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
        ems (EnergyManagement): Property to access the global EOS energy management system.

    Example:
        .. code-block:: python

            class MyOptimizationClass(EnergyManagementSystemMixin):
                def analyze_myprediction(self):
                    ems_data = self.ems.the_ems_method()
                    # Perform analysis

    """

    @classproperty
    def ems(cls) -> EnergyManagement:
        """Convenience class method/ attribute to retrieve the EOS energy management system.

        Returns:
            EnergyManagementSystem: The energy management system.
        """
        return get_ems()


def get_database(init: bool = False) -> Database:
    """Retrieve the singleton EOS database instance.

    This function provides access to the global EOS Database instance. The
    instance is created on first access if `init` is True. If the instance is
    accessed before initialization and `init` is False, a RuntimeError is raised.

    Args:
        init (bool): If True, create the Database instance if it does not exist.
                     Default is False.

    Returns:
        Database: The global EOS database instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            db = get_database(init=True)  # Initialize and retrieve
            db.insert_measurement(...)
    """
    global _database_eos
    if _database_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("Database access before init.")

        from akkudoktoreos.core.database import Database

        _database_eos = Database()

    return _database_eos


class DatabaseMixin:
    """Mixin class for managing EOS database access.

    This class serves as a foundational component for EOS-related classes requiring access
    to the EOS database. It provides a `database` property that dynamically retrieves
    the database instance.

    Usage:
        Subclass this base class to gain access to the `database` attribute, which retrieves the
        global database instance lazily to avoid import-time circular dependencies.

    Attributes:
        database (Database): Property to access the global EOS database.

    Example:
        .. code-block:: python

            class MyOptimizationClass(PredictionMixin):
                def store something(self):
                    db = self.database

    """

    @classproperty
    def database(cls) -> Database:
        """Convenience class method/ attribute to retrieve the EOS database.

        Returns:
            Database: The database.
        """
        return get_database()


def get_resource_registry(init: bool = False) -> ResourceRegistry:
    """Retrieve the singleton EOS Resource Registry instance.

    This function provides access to the global EOS ResourceRegistry instance.
    The instance is created on first access if `init` is True. If the instance
    is accessed before initialization and `init` is False, a RuntimeError is raised.

    Args:
        init (bool): If True, create the ResourceRegistry instance if it does not exist.
                     Default is False.

    Returns:
        ResourceRegistry: The global EOS Resource Registry instance.

    Raises:
        RuntimeError: If accessed before initialization with `init=False`.

    Usage:
        .. code-block:: python

            registry = get_resource_registry(init=True)  # Initialize and retrieve
            registry.register_device(my_device)
    """
    global _resource_registry_eos
    if _resource_registry_eos is None:
        from akkudoktoreos.config.config import ConfigEOS

        if not init and not ConfigEOS.documentation_mode():
            raise RuntimeError("ResourceRegistry access before init.")

        from akkudoktoreos.devices.devices import ResourceRegistry

        _resource_registry_eos = ResourceRegistry()

    return _resource_registry_eos


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
        return get_ems().start_datetime


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


_singletons_init_running: bool = False


def singletons_init() -> None:
    """Initialize the singletons for adapter, config, measurement, prediction, database, resource registry."""
    # Prevent recursive calling
    global \
        _singletons_init_running, \
        _adapter_eos, \
        _config_eos, \
        _database_eos, \
        _measurement_eos, \
        _prediction_eos, \
        _ems_eos, \
        _resource_registry_eos

    if _singletons_init_running:
        return

    _singletons_init_running = True

    try:
        if _config_eos is None:
            get_config(init=True)
        if _adapter_eos is None:
            get_adapter(init=True)
        if _database_eos is None:
            get_database(init=True)
        if _ems_eos is None:
            get_ems(init=True)
        if _measurement_eos is None:
            get_measurement(init=True)
        if _prediction_eos is None:
            get_prediction(init=True)
        if _resource_registry_eos is None:
            get_resource_registry(init=True)
    finally:
        _singletons_init_running = False
