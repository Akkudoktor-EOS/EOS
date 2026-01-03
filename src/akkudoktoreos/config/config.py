"""This module provides functionality to manage and handle configuration for the EOS.

The module including loading, merging, and validating JSON configuration files.
It also provides utility functions for working directory setup and date handling.

Key features:
- Loading and merging configurations from default or custom JSON files
- Validating configurations using Pydantic models
- Managing directory setups for the application
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Optional, Type, Union

import pydantic_settings
from loguru import logger
from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, computed_field, field_validator

# settings
from akkudoktoreos.adapter.adapter import AdapterCommonSettings
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.config.configmigrate import migrate_config_data, migrate_config_file
from akkudoktoreos.core.cachesettings import CacheCommonSettings
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.database import DatabaseCommonSettings
from akkudoktoreos.core.decorators import classproperty
from akkudoktoreos.core.emsettings import (
    EnergyManagementCommonSettings,
)
from akkudoktoreos.core.logsettings import LoggingCommonSettings
from akkudoktoreos.core.pydantic import PydanticModelNestedValueMixin, merge_models
from akkudoktoreos.core.version import __version__
from akkudoktoreos.devices.devices import DevicesCommonSettings
from akkudoktoreos.measurement.measurement import MeasurementCommonSettings
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.feedintariff import FeedInTariffCommonSettings
from akkudoktoreos.prediction.load import LoadCommonSettings
from akkudoktoreos.prediction.prediction import PredictionCommonSettings
from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings
from akkudoktoreos.prediction.weather import WeatherCommonSettings
from akkudoktoreos.server.server import ServerCommonSettings
from akkudoktoreos.utils.datetimeutil import to_datetime, to_timezone
from akkudoktoreos.utils.utils import UtilsCommonSettings


def get_absolute_path(
    basepath: Optional[Path | str], subpath: Optional[Path | str]
) -> Optional[Path]:
    """Get path based on base path."""
    if isinstance(basepath, str):
        basepath = Path(basepath)
    if subpath is None:
        return basepath

    if isinstance(subpath, str):
        subpath = Path(subpath)
    if subpath.is_absolute():
        return subpath
    if basepath is not None:
        return basepath.joinpath(subpath)
    return None


def is_home_assistant_addon() -> bool:
    """Detect Home Assistant add-on environment.

    Home Assistant sets this environment variable automatically.
    """
    return "HASSIO_TOKEN" in os.environ or "SUPERVISOR_TOKEN" in os.environ


def default_data_folder_path() -> Path:
    """Provide default data folder path.

    1. From EOS_DATA_DIR env
    2. From EOS_DIR env
    3. From platform specific default path
    4. Current working directory

    Note:
        When running as Home Assistant add-on the path is fixed to /data.
    """
    if is_home_assistant_addon():
        return Path("/data")

    # 1. From EOS_DATA_DIR env
    if env_dir := os.getenv(ConfigEOS.EOS_DATA_DIR):
        try:
            data_dir = Path(env_dir).resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir
        except Exception as e:
            logger.warning(f"Could not setup data folder {data_dir}: {e}")

    # 2. From EOS_DIR env
    if env_dir := os.getenv(ConfigEOS.EOS_DIR):
        try:
            data_dir = Path(env_dir).resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir
        except Exception as e:
            logger.warning(f"Could not setup data folder {data_dir}: {e}")

    # 3. From platform specific default path
    try:
        data_dir = Path(user_data_dir(ConfigEOS.APP_NAME, ConfigEOS.APP_AUTHOR))
        if data_dir is not None:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir
    except Exception as e:
        logger.warning(f"Could not setup data folder {data_dir}: {e}")

    # 4. Current working directory
    return Path.cwd()


class GeneralSettings(SettingsBaseModel):
    """General settings."""

    home_assistant_addon: bool = Field(
        default_factory=is_home_assistant_addon,
        json_schema_extra={"description": "EOS is running as home assistant add-on."},
        exclude=True,
    )

    version: str = Field(
        default=__version__,
        json_schema_extra={
            "description": "Configuration file version. Used to check compatibility."
        },
    )

    data_folder_path: Path = Field(
        default_factory=default_data_folder_path,
        json_schema_extra={
            "description": "Path to EOS data folder.",
        },
    )

    data_output_subpath: Optional[Path] = Field(
        default="output",
        json_schema_extra={"description": "Sub-path for the EOS output data folder."},
    )

    latitude: Optional[float] = Field(
        default=52.52,
        ge=-90.0,
        le=90.0,
        json_schema_extra={
            "description": "Latitude in decimal degrees between -90 and 90. North is positive (ISO 19115) (°)"
        },
    )
    longitude: Optional[float] = Field(
        default=13.405,
        ge=-180.0,
        le=180.0,
        json_schema_extra={"description": "Longitude in decimal degrees within -180 to 180 (°)"},
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def timezone(self) -> Optional[str]:
        """Computed timezone based on latitude and longitude."""
        if self.latitude and self.longitude:
            return to_timezone(location=(self.latitude, self.longitude), as_string=True)
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_output_path(self) -> Optional[Path]:
        """Computed data_output_path based on data_folder_path."""
        if self.home_assistant_addon:
            # Only /data is persistent for home assistant add-on
            return Path("/data/output")
        return get_absolute_path(self.data_folder_path, self.data_output_subpath)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_folder_path(self) -> Optional[Path]:
        """Path to EOS configuration directory."""
        return self.config._config_file_path.parent

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_file_path(self) -> Optional[Path]:
        """Path to EOS configuration file."""
        return self.config._config_file_path

    compatible_versions: ClassVar[list[str]] = [__version__]

    @field_validator("version")
    @classmethod
    def check_version(cls, v: str) -> str:
        if v not in cls.compatible_versions:
            error = (
                f"Incompatible configuration version '{v}'. "
                f"Expected: {', '.join(cls.compatible_versions)}."
            )
            logger.error(error)
            raise ValueError(error)
        return v

    @field_validator("data_folder_path", mode="after")
    @classmethod
    def validate_data_folder_path(cls, value: Optional[Union[str, Path]]) -> Path:
        """Ensure dir is available."""
        if is_home_assistant_addon():
            # Force to home assistant add-on /data directory
            return Path("/data")
        if value is None:
            return default_data_folder_path()
        if isinstance(value, str):
            value = Path(value)
        try:
            value.resolve()
            value.mkdir(parents=True, exist_ok=True)
        except Exception:
            raise ValueError(f"Data folder path '{value}' is not a directory.")
        return value


class SettingsEOS(pydantic_settings.BaseSettings, PydanticModelNestedValueMixin):
    """Settings for all EOS.

    Only used to update the configuration with specific settings.
    """

    general: Optional[GeneralSettings] = Field(
        default=None, json_schema_extra={"description": "General Settings"}
    )
    cache: Optional[CacheCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Cache Settings"}
    )
    database: Optional[DatabaseCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Database Settings"}
    )
    ems: Optional[EnergyManagementCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Energy Management Settings"}
    )
    logging: Optional[LoggingCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Logging Settings"}
    )
    devices: Optional[DevicesCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Devices Settings"}
    )
    measurement: Optional[MeasurementCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Measurement Settings"}
    )
    optimization: Optional[OptimizationCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Optimization Settings"}
    )
    prediction: Optional[PredictionCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Prediction Settings"}
    )
    elecprice: Optional[ElecPriceCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Electricity Price Settings"}
    )
    feedintariff: Optional[FeedInTariffCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Feed In Tariff Settings"}
    )
    load: Optional[LoadCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Load Settings"}
    )
    pvforecast: Optional[PVForecastCommonSettings] = Field(
        default=None, json_schema_extra={"description": "PV Forecast Settings"}
    )
    weather: Optional[WeatherCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Weather Settings"}
    )
    server: Optional[ServerCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Server Settings"}
    )
    utils: Optional[UtilsCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Utilities Settings"}
    )
    adapter: Optional[AdapterCommonSettings] = Field(
        default=None, json_schema_extra={"description": "Adapter Settings"}
    )

    model_config = pydantic_settings.SettingsConfigDict(
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        env_prefix="EOS_",
        ignored_types=(classproperty,),
    )


class SettingsEOSDefaults(SettingsEOS):
    """Settings for all of EOS with defaults.

    Used by ConfigEOS instance to make all fields available.
    """

    general: GeneralSettings = Field(default_factory=GeneralSettings)
    cache: CacheCommonSettings = Field(default_factory=CacheCommonSettings)
    database: DatabaseCommonSettings = Field(default_factory=DatabaseCommonSettings)
    ems: EnergyManagementCommonSettings = Field(default_factory=EnergyManagementCommonSettings)
    logging: LoggingCommonSettings = Field(default_factory=LoggingCommonSettings)
    devices: DevicesCommonSettings = Field(default_factory=DevicesCommonSettings)
    measurement: MeasurementCommonSettings = Field(default_factory=MeasurementCommonSettings)
    optimization: OptimizationCommonSettings = Field(default_factory=OptimizationCommonSettings)
    prediction: PredictionCommonSettings = Field(default_factory=PredictionCommonSettings)
    elecprice: ElecPriceCommonSettings = Field(default_factory=ElecPriceCommonSettings)
    feedintariff: FeedInTariffCommonSettings = Field(default_factory=FeedInTariffCommonSettings)
    load: LoadCommonSettings = Field(default_factory=LoadCommonSettings)
    pvforecast: PVForecastCommonSettings = Field(default_factory=PVForecastCommonSettings)
    weather: WeatherCommonSettings = Field(default_factory=WeatherCommonSettings)
    server: ServerCommonSettings = Field(default_factory=ServerCommonSettings)
    utils: UtilsCommonSettings = Field(default_factory=UtilsCommonSettings)
    adapter: AdapterCommonSettings = Field(default_factory=AdapterCommonSettings)

    def __hash__(self) -> int:
        # Just for usage in configmigrate, finally overwritten when used by ConfigEOS.
        # This is mutable, so pydantic does not set a hash.
        return id(self)


class ConfigEOS(SingletonMixin, SettingsEOSDefaults):
    """Singleton configuration handler for the EOS application.

    ConfigEOS extends `SettingsEOS` with support for  default configuration paths and automatic
    initialization.

    `ConfigEOS` ensures that only one instance of the class is created throughout the application,
    allowing consistent access to EOS configuration settings. This singleton instance loads
    configuration data from a predefined set of directories or creates a default configuration if
    none is found.

    Initialization Process:
      - Upon instantiation, the singleton instance attempts to load a configuration file in this order:
        1. The directory specified by the `EOS_CONFIG_DIR` environment variable
        2. The directory specified by the `EOS_DIR` environment variable.
        3. A platform specific default directory for EOS.
        4. The current working directory.
      - The first available configuration file found in these directories is loaded.
      - If no configuration file is found, a default configuration file is created in the platform
        specific default directory, and default settings are loaded into it.

    Attributes from the loaded configuration are accessible directly as instance attributes of
    `ConfigEOS`, providing a centralized, shared configuration object for EOS.

    Singleton Behavior:
      - This class uses the `SingletonMixin` to ensure that all requests for `ConfigEOS` return
        the same instance, which contains the most up-to-date configuration. Modifying the configuration
        in one part of the application reflects across all references to this class.

    Raises:
        FileNotFoundError: If no configuration file is found, and creating a default configuration fails.

    Example:
        To initialize and access configuration attributes (only one instance is created):
        .. code-block:: python

            config_eos = ConfigEOS()  # Always returns the same instance
                print(config_eos.prediction.hours)  # Access a setting from the loaded configuration

    """

    APP_NAME: ClassVar[str] = "net.akkudoktor.eos"  # reverse order
    APP_AUTHOR: ClassVar[str] = "akkudoktor"
    EOS_DIR: ClassVar[str] = "EOS_DIR"
    EOS_DATA_DIR: ClassVar[str] = "EOS_DATA_DIR"
    EOS_CONFIG_DIR: ClassVar[str] = "EOS_CONFIG_DIR"
    ENCODING: ClassVar[str] = "UTF-8"
    CONFIG_FILE_NAME: ClassVar[str] = "EOS.config.json"
    _init_config_eos: ClassVar[dict[str, bool]] = {
        "with_init_settings": True,
        "with_env_settings": True,
        "with_dotenv_settings": True,
        "with_file_settings": True,
        "with_file_secret_settings": True,
    }
    _config_file_path: ClassVar[Optional[Path]] = None
    _force_documentation_mode = False

    def __hash__(self) -> int:
        # ConfigEOS is a singleton
        return hash("config_eos")

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ConfigEOS):
            return False
        # ConfigEOS is a singleton
        return True

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[pydantic_settings.BaseSettings],
        init_settings: pydantic_settings.PydanticBaseSettingsSource,
        env_settings: pydantic_settings.PydanticBaseSettingsSource,
        dotenv_settings: pydantic_settings.PydanticBaseSettingsSource,
        file_secret_settings: pydantic_settings.PydanticBaseSettingsSource,
    ) -> tuple[pydantic_settings.PydanticBaseSettingsSource, ...]:
        """Customizes the order and handling of settings sources for a pydantic_settings.BaseSettings subclass.

        This method determines the sources for application configuration settings, including
        environment variables, dotenv files and JSON configuration files.
        It ensures that a default configuration file exists and creates one if necessary.

        Args:
            settings_cls (Type[pydantic_settings.BaseSettings]): The Pydantic BaseSettings class for
                which sources are customized.
            init_settings (pydantic_settings.PydanticBaseSettingsSource): The initial settings source, typically passed at runtime.
            env_settings (pydantic_settings.PydanticBaseSettingsSource): Settings sourced from environment variables.
            dotenv_settings (pydantic_settings.PydanticBaseSettingsSource): Settings sourced from a dotenv file.
            file_secret_settings (pydantic_settings.PydanticBaseSettingsSource): Unused (needed for parent class interface).

        Returns:
            tuple[pydantic_settings.PydanticBaseSettingsSource, ...]: A tuple of settings sources in the order they     should be applied.

        Behavior:
            1. Checks for the existence of a JSON configuration file in the expected location.
            2. If the configuration file does not exist, creates the directory (if needed) and
               attempts to create a default configuration file in the location. If the creation
               fails, a temporary configuration directory is used.
            3. Creates a `pydantic_settings.JsonConfigSettingsSource` for the configuration
               file.
            4. Updates class attributes `GeneralSettings._config_folder_path` and
               `GeneralSettings._config_file_path` to reflect the determined paths.
            5. Returns a tuple containing all provided and newly created settings sources in
               the desired order.

        Notes:
            - This method logs an error if the default configuration file in the normal
              configuration directory cannot be created.
            - It ensures that a fallback to a default configuration file is always possible.
        """

        def lazy_config_file_settings() -> dict:
            """Config file settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            config_file_path, exists = cls._get_config_file_path()
            if not exists:
                # Create minimum config file
                config_minimum_content = '{ "general": { "version": "' + __version__ + '" } }'
                if config_file_path.is_relative_to(ConfigEOS.package_root_path):
                    # Never write into package directory
                    error_msg = (
                        f"Could not create minimum config file. "
                        f"Config file path '{config_file_path}' is within package root "
                        f"'{ConfigEOS.package_root_path}'"
                    )
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                try:
                    config_file_path.parent.mkdir(parents=True, exist_ok=True)
                    config_file_path.write_text(config_minimum_content, encoding="utf-8")
                except Exception as exc:
                    # Create minimum config in temporary config directory as last resort
                    error_msg = (
                        f"Could not create minimum config file in {config_file_path.parent}: {exc}"
                    )
                    logger.error(error_msg)
                    temp_dir = Path(tempfile.mkdtemp())
                    info_msg = f"Using temporary config directory {temp_dir}"
                    logger.info(info_msg)
                    config_file_path = temp_dir / config_file_path.name
                    config_file_path.write_text(config_minimum_content, encoding="utf-8")

            # Remember for other lazy settings and computed_field
            cls._config_file_path = config_file_path

            return {}

        def lazy_data_folder_path_settings() -> dict:
            """Data folder path settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            # Updates path to the data directory.
            data_folder_settings = {
                "general": {
                    "data_folder_path": default_data_folder_path(),
                },
            }

            return data_folder_settings

        def lazy_init_settings() -> dict:
            """Init settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            if not cls._init_config_eos.get("with_init_settings", True):
                logger.debug("Config initialisation with init settings is disabled.")
                return {}

            settings = init_settings()

            return settings

        def lazy_env_settings() -> dict:
            """Env settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            if not cls._init_config_eos.get("with_env_settings", True):
                logger.debug("Config initialisation with env settings is disabled.")
                return {}

            return env_settings()

        def lazy_dotenv_settings() -> dict:
            """Dotenv settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            if not cls._init_config_eos.get("with_dotenv_settings", True):
                logger.debug("Config initialisation with dotenv settings is disabled.")
                return {}

            return dotenv_settings()

        def lazy_file_settings() -> dict:
            """File settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.

            Ensures the config file exists and creates a backup if necessary.
            """
            if not cls._init_config_eos.get("with_file_settings", True):
                logger.debug("Config initialisation with file settings is disabled.")
                return {}

            config_file = cls._config_file_path  # provided by lazy_config_file_settings
            if config_file is None:
                # This should not happen
                raise RuntimeError("Config file path not set.")

            try:
                backup_file = config_file.with_suffix(f".{to_datetime(as_string='YYYYMMDDHHmmss')}")
                if migrate_config_file(config_file, backup_file):
                    # If the config file does have the correct version add it as settings source
                    settings = pydantic_settings.JsonConfigSettingsSource(
                        settings_cls, json_file=config_file
                    )()
            except Exception as ex:
                logger.error(
                    f"Error reading config file '{config_file}' (falling back to default config): {ex}"
                )
                settings = {}

            return settings

        def lazy_file_secret_settings() -> dict:
            """File secret settings.

            This function runs at **instance creation**, not class definition. Ensures if ConfigEOS
            is recreated this function is run.
            """
            if not cls._init_config_eos.get("with_file_secret_settings", True):
                logger.debug("Config initialisation with file secret settings is disabled.")
                return {}

            return file_secret_settings()

        # All the settings sources in priority sequence
        # The settings are all lazyly evaluated at instance creation time to allow for
        # runtime configuration.
        setting_sources = [
            lazy_config_file_settings,  # Prio high
            lazy_init_settings,
            lazy_env_settings,
            lazy_dotenv_settings,
            lazy_file_settings,
            lazy_data_folder_path_settings,
            lazy_file_secret_settings,  # Prio low
        ]

        return tuple(setting_sources)

    @classproperty
    def package_root_path(cls) -> Path:
        """Compute the package root path."""
        return Path(__file__).parent.parent.resolve()

    @classmethod
    def documentation_mode(cls) -> bool:
        """Are we running in documentation mode.

        Some checks may be relaxed to allow for proper documentation execution.
        """
        # Detect if Sphinx is importing this module
        is_sphinx = "sphinx" in sys.modules or getattr(sys, "_called_from_sphinx", False)
        return cls._force_documentation_mode or is_sphinx

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the singleton ConfigEOS instance.

        Configuration data is loaded from a configuration file or a default one is created if none
        exists.
        """
        # Check for singleton guard
        if hasattr(self, "_initialized"):
            logger.debug("Config init called again with parameters {} {}", args, kwargs)
            return
        logger.debug("Config init with parameters {} {}", args, kwargs)
        self._setup(self, *args, **kwargs)

    def _setup(self, *args: Any, **kwargs: Any) -> None:
        """Re-initialize global settings."""
        logger.debug("Config setup with parameters {} {}", args, kwargs)

        # Assure settings base knows the singleton EOS configuration
        SettingsBaseModel.config = self

        # (Re-)load settings - call base class init
        SettingsEOSDefaults.__init__(self, *args, **kwargs)

        self._initialized = True
        logger.debug(f"Config setup:\n{self}")

    def merge_settings(self, settings: SettingsEOS) -> None:
        """Merges the provided settings into the global settings for EOS, with optional overwrite.

        Args:
            settings (SettingsEOS): The settings to apply globally.

        Raises:
            ValueError: If the `settings` is not a `SettingsEOS` instance.
        """
        if not isinstance(settings, SettingsEOS):
            error_msg = f"Settings must be an instance of SettingsEOS: '{settings}'."
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.merge_settings_from_dict(settings.model_dump(exclude_none=True, exclude_unset=True))

    def merge_settings_from_dict(self, data: dict) -> None:
        """Merges the provided dictionary data into the current instance.

        Creates a new settings instance, then applies the dictionary data through validation,
        and finally merges the validated settings into the current instance. None values
        are not merged.

        Args:
            data (dict): Dictionary containing field values to merge into the
                current settings instance.

        Raises:
            ValidationError: If the data contains invalid values for the defined fields.

        Example:
            .. code-block:: python

                config = get_config()
                new_data = {"prediction": {"hours": 24}, "server": {"port": 8000}}
                config.merge_settings_from_dict(new_data)

        """
        self._setup(**merge_models(self, data))

    def reset_settings(self) -> None:
        """Reset all changed settings to environment/config file defaults.

        This functions basically deletes the settings provided before.
        """
        self._setup()

    def revert_settings(self, backup_id: str) -> None:
        """Revert application settings to a stored backup.

        This method restores configuration values from a backup file identified
        by `backup_id`. The backup is expected to exist alongside the main
        configuration file, using the main config file's path but with the given
        suffix. Any settings previously applied will be overwritten.

        Args:
            backup_id (str): The suffix used to locate the backup configuration
                file. Example: ``".bak"`` or ``".backup"``.

        Returns:
            None: The method does not return a value.

        Raises:
            ValueError: If the backup file cannot be found at the constructed path.
            json.JSONDecodeError: If the backup file exists but contains invalid JSON.
            TypeError: If the unpacked backup data fails to match the signature
                required by ``self._setup()``.
            OSError: If reading the backup file fails due to I/O issues.
        """
        backup_file_path = self.general.config_file_path.with_suffix(f".{backup_id}")
        if not backup_file_path.exists():
            error_msg = f"Configuration backup `{backup_id}` not found."
            logger.error(error_msg)
            raise ValueError(error_msg)

        with backup_file_path.open("r", encoding="utf-8") as f:
            backup_data: dict[str, Any] = json.load(f)
        backup_settings = migrate_config_data(backup_data)

        self._setup(**backup_settings.model_dump(exclude_none=True, exclude_unset=True))

    def list_backups(self) -> dict[str, dict[str, Any]]:
        """List available configuration backup files and extract metadata.

        Backup files are identified by sharing the same stem as the main config
        file but having a different suffix. Each backup file is assumed to contain
        a JSON object.

        The returned dictionary uses `backup_id` (suffix) as keys. The value for
        each key is a dictionary including:
        - ``storage_time``: The file modification timestamp in ISO-8601 format.
        - ``version``: Version information found in the backup file (defaults to ``"unknown"``).

        Returns:
            dict[str, dict[str, Any]]: Mapping of backup identifiers to metadata.

        Raises:
            OSError: If directory scanning or file reading fails.
            json.JSONDecodeError: If a backup file cannot be parsed as JSON.
        """
        result: dict[str, dict[str, Any]] = {}

        base_path: Path = self.general.config_file_path
        parent = base_path.parent
        stem = base_path.stem

        # Iterate files next to config file
        for file in parent.iterdir():
            if file.is_file() and file.stem == stem and file != base_path:
                backup_id = file.suffix[1:]

                # Read version from file
                with file.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)

                # Extract version safely
                version = data.get("general", {}).get("version", "unknown")

                # Read file modification time (OS-independent)
                ts = file.stat().st_mtime
                storage_time = to_datetime(ts, as_string=True)
                result[backup_id] = {
                    "date_time": storage_time,
                    "version": version,
                }

        return result

    @classmethod
    def _get_config_file_path(cls) -> tuple[Path, bool]:
        """Find a valid configuration file or return the desired path for a new config file.

        Searches:
            1. environment variable directory
            2. user configuration directory
            3. current working directory

        If running as Home Assistat add-on returns /data/config/EOS.config.json.

        Returns:
            tuple[Path, bool]: The path to the configuration file and if there is already a config file there
        """
        if is_home_assistant_addon():
            # Only /data is persistent for home assistant add-on
            cfile = Path("/data/config") / cls.CONFIG_FILE_NAME
            logger.debug(f"Config file forced to: '{cfile}'")
            return cfile, cfile.exists()

        config_dirs = []

        # 1. Directory specified by EOS_CONFIG_DIR
        config_dir: Optional[Union[Path, str]] = os.getenv(cls.EOS_CONFIG_DIR)
        if config_dir:
            logger.debug(f"Environment EOS_CONFIG_DIR: '{config_dir}'")
            config_dir = Path(config_dir).resolve()
            if config_dir.exists():
                config_dirs.append(config_dir)
            else:
                logger.info(f"Environment EOS_CONFIG_DIR: '{config_dir}' does not exist.")

        # 2. Directory specified by EOS_DIR / EOS_CONFIG_DIR
        eos_dir = os.getenv(cls.EOS_DIR)
        eos_config_dir = os.getenv(cls.EOS_CONFIG_DIR)
        if eos_dir and eos_config_dir:
            logger.debug(f"Environment EOS_DIR/EOS_CONFIG_DIR: '{eos_dir}/{eos_config_dir}'")
            config_dir = get_absolute_path(eos_dir, eos_config_dir)
            if config_dir:
                config_dir = Path(config_dir).resolve()
                if config_dir.exists():
                    config_dirs.append(config_dir)
                else:
                    logger.info(
                        f"Environment EOS_DIR/EOS_CONFIG_DIR: '{config_dir}' does not exist."
                    )
            else:
                logger.debug(
                    f"Environment EOS_DIR/EOS_CONFIG_DIR: '{eos_dir}/{eos_config_dir}' not a valid path"
                )

        # 3. Directory specified by EOS_DIR
        config_dir = os.getenv(cls.EOS_DIR)
        if config_dir:
            logger.debug(f"Environment EOS_DIR: '{config_dir}'")
            config_dir = Path(config_dir).resolve()
            if config_dir.exists():
                config_dirs.append(config_dir)
            else:
                logger.info(f"Environment EOS_DIR: '{config_dir}' does not exist.")

        # 4. User configuration directory
        config_dir = Path(user_config_dir(cls.APP_NAME, cls.APP_AUTHOR)).resolve()
        logger.debug(f"User config dir: '{config_dir}'")
        if config_dir.exists():
            config_dirs.append(config_dir)
        else:
            logger.info(f"User config dir: '{config_dir}' does not exist.")

        # 5. Current working directory
        config_dir = Path.cwd()
        logger.debug(f"Current working dir: '{config_dir}'")
        if config_dir.exists():
            config_dirs.append(config_dir)
        else:
            logger.info(f"Current working dir: '{config_dir}' does not exist.")

        # Search for file
        for cdir in config_dirs:
            cfile = cdir.joinpath(cls.CONFIG_FILE_NAME)
            if cfile.exists():
                logger.debug(f"Found config file: '{cfile}'")
                return cfile, True

        # Return highest priority directory with standard file name appended
        default_config_file = config_dirs[0].joinpath(cls.CONFIG_FILE_NAME)
        logger.debug(f"No config file found. Defaulting to: '{default_config_file}'")
        return default_config_file, False

    @classmethod
    def _setup_config_file(cls) -> Path:
        """Setup config file.

        Creates an initial config file if it does not exist.

        Returns:
            config_file_path (Path): Path to config file
        """
        config_file_path, exists = cls._get_config_file_path()
        if (
            GeneralSettings._config_file_path
            and GeneralSettings._config_file_path != config_file_path
        ):
            debug_msg = (
                f"Config file changed from '{GeneralSettings._config_file_path}' to "
                f"'{config_file_path}'"
            )
            logger.debug(debug_msg)
        if not exists:
            # Create minimum config file
            config_minimum_content = '{ "general": { "version": "' + __version__ + '" } }'
            try:
                config_file_path.parent.mkdir(parents=True, exist_ok=True)
                config_file_path.write_text(config_minimum_content, encoding="utf-8")
            except Exception as exc:
                # Create minimum config in temporary config directory as last resort
                error_msg = (
                    f"Could not create minimum config file in {config_file_path.parent}: {exc}"
                )
                logger.error(error_msg)
                temp_dir = Path(tempfile.mkdtemp())
                info_msg = f"Using temporary config directory {temp_dir}"
                logger.info(info_msg)
                config_file_path = temp_dir / config_file_path.name
                config_file_path.write_text(config_minimum_content, encoding="utf-8")

        # Remember config_dir and config file
        GeneralSettings._config_folder_path = config_file_path.parent
        GeneralSettings._config_file_path = config_file_path

        return config_file_path

    def to_config_file(self) -> None:
        """Saves the current configuration to the configuration file.

        Also updates the configuration file settings.

        Raises:
            ValueError: If the configuration file path is not specified or can not be written to.
        """
        if not self.general.config_file_path:
            raise ValueError("Configuration file path unknown.")
        with self.general.config_file_path.open("w", encoding="utf-8", newline="\n") as f_out:
            json_str = super().model_dump_json(indent=4)
            f_out.write(json_str)

    def update(self) -> None:
        """Updates all configuration fields.

        This method updates all configuration fields using the following order for value retrieval:
            1. Current settings.
            2. Environment variables.
            3. EOS configuration file.
            4. Field default constants.

        The first non None value in priority order is taken.
        """
        self._setup(**self.model_dump())
