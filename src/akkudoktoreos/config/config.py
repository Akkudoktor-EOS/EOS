"""This module provides functionality to manage and handle configuration for the EOS.

The module including loading, merging, and validating JSON configuration files.
It also provides utility functions for working directory setup and date handling.

Key features:
- Loading and merging configurations from default or custom JSON files
- Validating configurations using Pydantic models
- Managing directory setups for the application
"""

import os
import shutil
from pathlib import Path
from typing import Any, ClassVar, Optional, Type

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, computed_field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# settings
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cachesettings import CacheCommonSettings
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.decorators import classproperty
from akkudoktoreos.core.emsettings import EnergyManagementCommonSettings
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.logsettings import LoggingCommonSettings
from akkudoktoreos.core.pydantic import PydanticModelNestedValueMixin, merge_models
from akkudoktoreos.devices.settings import DevicesCommonSettings
from akkudoktoreos.measurement.measurement import MeasurementCommonSettings
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.load import LoadCommonSettings
from akkudoktoreos.prediction.prediction import PredictionCommonSettings
from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings
from akkudoktoreos.prediction.weather import WeatherCommonSettings
from akkudoktoreos.server.server import ServerCommonSettings
from akkudoktoreos.utils.datetimeutil import to_timezone
from akkudoktoreos.utils.utils import UtilsCommonSettings

logger = get_logger(__name__)


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


class GeneralSettings(SettingsBaseModel):
    """Settings for common configuration.

    General configuration to set directories of cache and output files and system location (latitude
    and longitude).
    Validators ensure each parameter is within a specified range. A computed property, `timezone`,
    determines the time zone based on latitude and longitude.

    Attributes:
        latitude (Optional[float]): Latitude in degrees, must be between -90 and 90.
        longitude (Optional[float]): Longitude in degrees, must be between -180 and 180.

    Properties:
        timezone (Optional[str]): Computed time zone string based on the specified latitude
            and longitude.

    Validators:
        validate_latitude (float): Ensures `latitude` is within the range -90 to 90.
        validate_longitude (float): Ensures `longitude` is within the range -180 to 180.
    """

    _config_folder_path: ClassVar[Optional[Path]] = None
    _config_file_path: ClassVar[Optional[Path]] = None

    data_folder_path: Optional[Path] = Field(
        default=None, description="Path to EOS data directory.", examples=[None, "/home/eos/data"]
    )

    data_output_subpath: Optional[Path] = Field(
        default="output", description="Sub-path for the EOS output data directory."
    )

    latitude: Optional[float] = Field(
        default=52.52,
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)",
    )
    longitude: Optional[float] = Field(
        default=13.405,
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees, within -180 to 180 (°)",
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def timezone(self) -> Optional[str]:
        """Compute timezone based on latitude and longitude."""
        if self.latitude and self.longitude:
            return to_timezone(location=(self.latitude, self.longitude), as_string=True)
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_output_path(self) -> Optional[Path]:
        """Compute data_output_path based on data_folder_path."""
        return get_absolute_path(self.data_folder_path, self.data_output_subpath)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_folder_path(self) -> Optional[Path]:
        """Path to EOS configuration directory."""
        return self._config_folder_path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_file_path(self) -> Optional[Path]:
        """Path to EOS configuration file."""
        return self._config_file_path


class SettingsEOS(BaseSettings, PydanticModelNestedValueMixin):
    """Settings for all EOS.

    Used by updating the configuration with specific settings only.
    """

    general: Optional[GeneralSettings] = Field(
        default=None,
        description="General Settings",
    )
    cache: Optional[CacheCommonSettings] = Field(
        default=None,
        description="Cache Settings",
    )
    ems: Optional[EnergyManagementCommonSettings] = Field(
        default=None,
        description="Energy Management Settings",
    )
    logging: Optional[LoggingCommonSettings] = Field(
        default=None,
        description="Logging Settings",
    )
    devices: Optional[DevicesCommonSettings] = Field(
        default=None,
        description="Devices Settings",
    )
    measurement: Optional[MeasurementCommonSettings] = Field(
        default=None,
        description="Measurement Settings",
    )
    optimization: Optional[OptimizationCommonSettings] = Field(
        default=None,
        description="Optimization Settings",
    )
    prediction: Optional[PredictionCommonSettings] = Field(
        default=None,
        description="Prediction Settings",
    )
    elecprice: Optional[ElecPriceCommonSettings] = Field(
        default=None,
        description="Electricity Price Settings",
    )
    load: Optional[LoadCommonSettings] = Field(
        default=None,
        description="Load Settings",
    )
    pvforecast: Optional[PVForecastCommonSettings] = Field(
        default=None,
        description="PV Forecast Settings",
    )
    weather: Optional[WeatherCommonSettings] = Field(
        default=None,
        description="Weather Settings",
    )
    server: Optional[ServerCommonSettings] = Field(
        default=None,
        description="Server Settings",
    )
    utils: Optional[UtilsCommonSettings] = Field(
        default=None,
        description="Utilities Settings",
    )

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        env_prefix="EOS_",
        ignored_types=(classproperty,),
    )


class SettingsEOSDefaults(SettingsEOS):
    """Settings for all of EOS with defaults.

    Used by ConfigEOS instance to make all fields available.
    """

    general: GeneralSettings = GeneralSettings()
    cache: CacheCommonSettings = CacheCommonSettings()
    ems: EnergyManagementCommonSettings = EnergyManagementCommonSettings()
    logging: LoggingCommonSettings = LoggingCommonSettings()
    devices: DevicesCommonSettings = DevicesCommonSettings()
    measurement: MeasurementCommonSettings = MeasurementCommonSettings()
    optimization: OptimizationCommonSettings = OptimizationCommonSettings()
    prediction: PredictionCommonSettings = PredictionCommonSettings()
    elecprice: ElecPriceCommonSettings = ElecPriceCommonSettings()
    load: LoadCommonSettings = LoadCommonSettings()
    pvforecast: PVForecastCommonSettings = PVForecastCommonSettings()
    weather: WeatherCommonSettings = WeatherCommonSettings()
    server: ServerCommonSettings = ServerCommonSettings()
    utils: UtilsCommonSettings = UtilsCommonSettings()


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

    Attributes:
        config_folder_path (Optional[Path]): Path to the configuration directory.
        config_file_path (Optional[Path]): Path to the configuration file.

    Raises:
        FileNotFoundError: If no configuration file is found, and creating a default configuration fails.

    Example:
        To initialize and access configuration attributes (only one instance is created):
        ```python
        config_eos = ConfigEOS()  # Always returns the same instance
        print(config_eos.prediction.hours)  # Access a setting from the loaded configuration
        ```

    """

    APP_NAME: ClassVar[str] = "net.akkudoktor.eos"  # reverse order
    APP_AUTHOR: ClassVar[str] = "akkudoktor"
    EOS_DIR: ClassVar[str] = "EOS_DIR"
    EOS_CONFIG_DIR: ClassVar[str] = "EOS_CONFIG_DIR"
    ENCODING: ClassVar[str] = "UTF-8"
    CONFIG_FILE_NAME: ClassVar[str] = "EOS.config.json"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customizes the order and handling of settings sources for a Pydantic BaseSettings subclass.

        This method determines the sources for application configuration settings, including
        environment variables, dotenv files and JSON configuration files.
        It ensures that a default configuration file exists and creates one if necessary.

        Args:
            settings_cls (Type[BaseSettings]): The Pydantic BaseSettings class for which sources are customized.
            init_settings (PydanticBaseSettingsSource): The initial settings source, typically passed at runtime.
            env_settings (PydanticBaseSettingsSource): Settings sourced from environment variables.
            dotenv_settings (PydanticBaseSettingsSource): Settings sourced from a dotenv file.
            file_secret_settings (PydanticBaseSettingsSource): Unused (needed for parent class interface).

        Returns:
            tuple[PydanticBaseSettingsSource, ...]: A tuple of settings sources in the order they should be applied.

        Behavior:
            1. Checks for the existence of a JSON configuration file in the expected location.
            2. If the configuration file does not exist, creates the directory (if needed) and attempts to copy a
               default configuration file to the location. If the copy fails, uses the default configuration file directly.
            3. Creates a `JsonConfigSettingsSource` for both the configuration file and the default configuration file.
            4. Updates class attributes `GeneralSettings._config_folder_path` and
               `GeneralSettings._config_file_path` to reflect the determined paths.
            5. Returns a tuple containing all provided and newly created settings sources in the desired order.

        Notes:
            - This method logs a warning if the default configuration file cannot be copied.
            - It ensures that a fallback to the default configuration file is always possible.
        """
        setting_sources = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]

        file_settings: Optional[JsonConfigSettingsSource] = None
        config_file, exists = cls._get_config_file_path()
        config_dir = config_file.parent
        if not exists:
            config_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(cls.config_default_file_path, config_file)
            except Exception as exc:
                logger.warning(f"Could not copy default config: {exc}. Using default config...")
                config_file = cls.config_default_file_path
                config_dir = config_file.parent
        try:
            file_settings = JsonConfigSettingsSource(settings_cls, json_file=config_file)
            setting_sources.append(file_settings)
        except Exception as e:
            logger.error(
                f"Error reading config file '{config_file}' (falling back to default config): {e}"
            )
        default_settings = JsonConfigSettingsSource(
            settings_cls, json_file=cls.config_default_file_path
        )
        GeneralSettings._config_folder_path = config_dir
        GeneralSettings._config_file_path = config_file

        setting_sources.append(default_settings)
        return tuple(setting_sources)

    @classproperty
    def config_default_file_path(cls) -> Path:
        """Compute the default config file path."""
        return cls.package_root_path.joinpath("data/default.config.json")

    @classproperty
    def package_root_path(cls) -> Path:
        """Compute the package root path."""
        return Path(__file__).parent.parent.resolve()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the singleton ConfigEOS instance.

        Configuration data is loaded from a configuration file or a default one is created if none
        exists.
        """
        if hasattr(self, "_initialized"):
            return
        self._setup(self, *args, **kwargs)

    def _setup(self, *args: Any, **kwargs: Any) -> None:
        """Re-initialize global settings."""
        # Assure settings base knows EOS configuration
        SettingsBaseModel.config = self
        # (Re-)load settings
        SettingsEOSDefaults.__init__(self, *args, **kwargs)
        # Init config file and data folder pathes
        self._create_initial_config_file()
        self._update_data_folder_path()

    def merge_settings(self, settings: SettingsEOS) -> None:
        """Merges the provided settings into the global settings for EOS, with optional overwrite.

        Args:
            settings (SettingsEOS): The settings to apply globally.

        Raises:
            ValueError: If the `settings` is not a `SettingsEOS` instance.
        """
        if not isinstance(settings, SettingsEOS):
            raise ValueError(f"Settings must be an instance of SettingsEOS: '{settings}'.")

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
            >>> config = get_config()
            >>> new_data = {"prediction": {"hours": 24}, "server": {"port": 8000}}
            >>> config.merge_settings_from_dict(new_data)
        """
        self._setup(**merge_models(self, data))

    def reset_settings(self) -> None:
        """Reset all changed settings to environment/config file defaults.

        This functions basically deletes the settings provided before.
        """
        self._setup()

    def _create_initial_config_file(self) -> None:
        if self.general.config_file_path and not self.general.config_file_path.exists():
            self.general.config_file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.general.config_file_path.open("w", encoding="utf-8", newline="\n") as f:
                    f.write(self.model_dump_json(indent=4))
            except Exception as e:
                logger.error(
                    f"Could not write configuration file '{self.general.config_file_path}': {e}"
                )

    def _update_data_folder_path(self) -> None:
        """Updates path to the data directory."""
        # From Settings
        if data_dir := self.general.data_folder_path:
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                self.general.data_folder_path = data_dir
                return
            except Exception as e:
                logger.warning(f"Could not setup data dir: {e}")
        # From EOS_DIR env
        if env_dir := os.getenv(self.EOS_DIR):
            try:
                data_dir = Path(env_dir).resolve()
                data_dir.mkdir(parents=True, exist_ok=True)
                self.general.data_folder_path = data_dir
                return
            except Exception as e:
                logger.warning(f"Could not setup data dir: {e}")
        # From platform specific default path
        try:
            data_dir = Path(user_data_dir(self.APP_NAME, self.APP_AUTHOR))
            if data_dir is not None:
                data_dir.mkdir(parents=True, exist_ok=True)
                self.general.data_folder_path = data_dir
                return
        except Exception as e:
            logger.warning(f"Could not setup data dir: {e}")
        # Current working directory
        data_dir = Path.cwd()
        self.general.data_folder_path = data_dir

    @classmethod
    def _get_config_file_path(cls) -> tuple[Path, bool]:
        """Finds the a valid configuration file or returns the desired path for a new config file.

        Returns:
            tuple[Path, bool]: The path to the configuration directory and if there is already a config file there
        """
        config_dirs = []
        env_base_dir = os.getenv(cls.EOS_DIR)
        env_config_dir = os.getenv(cls.EOS_CONFIG_DIR)
        env_dir = get_absolute_path(env_base_dir, env_config_dir)
        logger.debug(f"Environment config dir: '{env_dir}'")
        if env_dir is not None:
            config_dirs.append(env_dir.resolve())
        config_dirs.append(Path(user_config_dir(cls.APP_NAME, cls.APP_AUTHOR)))
        config_dirs.append(Path.cwd())
        for cdir in config_dirs:
            cfile = cdir.joinpath(cls.CONFIG_FILE_NAME)
            if cfile.exists():
                logger.debug(f"Found config file: '{cfile}'")
                return cfile, True
        return config_dirs[0].joinpath(cls.CONFIG_FILE_NAME), False

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


def get_config() -> ConfigEOS:
    """Gets the EOS configuration data."""
    return ConfigEOS()
