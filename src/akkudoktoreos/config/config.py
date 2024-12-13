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
from typing import Any, ClassVar, Optional

import platformdirs
from pydantic import Field, TypeAdapter, ValidationError, computed_field

# settings
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.devices.devices import DevicesCommonSettings
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings
from akkudoktoreos.prediction.load import LoadCommonSettings
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.loadimport import LoadImportCommonSettings
from akkudoktoreos.prediction.prediction import PredictionCommonSettings
from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings
from akkudoktoreos.prediction.pvforecastimport import PVForecastImportCommonSettings
from akkudoktoreos.prediction.weather import WeatherCommonSettings
from akkudoktoreos.prediction.weatherimport import WeatherImportCommonSettings
from akkudoktoreos.server.server import ServerCommonSettings
from akkudoktoreos.utils.logutil import get_logger
from akkudoktoreos.utils.utils import UtilsCommonSettings

logger = get_logger(__name__)


class ConfigCommonSettings(SettingsBaseModel):
    """Settings for common configuration."""

    data_folder_path: Optional[Path] = Field(
        default=None, description="Path to EOS data directory."
    )

    data_output_subpath: Optional[Path] = Field(
        "output", description="Sub-path for the EOS output data directory."
    )

    data_cache_subpath: Optional[Path] = Field(
        "cache", description="Sub-path for the EOS cache data directory."
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_output_path(self) -> Optional[Path]:
        """Compute data_output_path based on data_folder_path."""
        if self.data_output_subpath is None:
            return self.data_folder_path
        if self.data_folder_path and self.data_output_subpath:
            return self.data_folder_path.joinpath(self.data_output_subpath)
        return None

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_cache_path(self) -> Optional[Path]:
        """Compute data_cache_path based on data_folder_path."""
        if self.data_cache_subpath is None:
            return self.data_folder_path
        if self.data_folder_path and self.data_cache_subpath:
            return self.data_folder_path.joinpath(self.data_cache_subpath)
        return None


class SettingsEOS(
    ConfigCommonSettings,
    DevicesCommonSettings,
    OptimizationCommonSettings,
    PredictionCommonSettings,
    ElecPriceCommonSettings,
    ElecPriceImportCommonSettings,
    LoadCommonSettings,
    LoadAkkudoktorCommonSettings,
    LoadImportCommonSettings,
    PVForecastCommonSettings,
    PVForecastImportCommonSettings,
    WeatherCommonSettings,
    WeatherImportCommonSettings,
    ServerCommonSettings,
    UtilsCommonSettings,
):
    """Settings for all EOS."""

    pass


class ConfigEOS(SingletonMixin, SettingsEOS):
    """Singleton configuration handler for the EOS application.

    ConfigEOS extends `SettingsEOS` with support for  default configuration paths and automatic
    initialization.

    `ConfigEOS` ensures that only one instance of the class is created throughout the application,
    allowing consistent access to EOS configuration settings. This singleton instance loads
    configuration data from a predefined set of directories or creates a default configuration if
    none is found.

    Initialization Process:
      - Upon instantiation, the singleton instance attempts to load a configuration file in this order:
        1. The directory specified by the `EOS_DIR` environment variable.
        2. A platform specific default directory for EOS.
        3. The current working directory.
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
        _settings (ClassVar[SettingsEOS]): Holds application-wide settings.
        _file_settings (ClassVar[SettingsEOS]): Stores configuration loaded from file.
        config_folder_path (Optional[Path]): Path to the configuration directory.
        config_file_path (Optional[Path]): Path to the configuration file.

    Raises:
        FileNotFoundError: If no configuration file is found, and creating a default configuration fails.

    Example:
        To initialize and access configuration attributes (only one instance is created):
        ```python
        config_eos = ConfigEOS()  # Always returns the same instance
        print(config_eos.prediction_hours)  # Access a setting from the loaded configuration
        ```

    """

    APP_NAME: ClassVar[str] = "net.akkudoktor.eos"  # reverse order
    APP_AUTHOR: ClassVar[str] = "akkudoktor"
    EOS_DIR: ClassVar[str] = "EOS_DIR"
    ENCODING: ClassVar[str] = "UTF-8"
    CONFIG_FILE_NAME: ClassVar[str] = "EOS.config.json"

    _settings: ClassVar[Optional[SettingsEOS]] = None
    _file_settings: ClassVar[Optional[SettingsEOS]] = None

    config_folder_path: Optional[Path] = Field(
        None, description="Path to EOS configuration directory."
    )

    config_file_path: Optional[Path] = Field(
        default=None, description="Path to EOS configuration file."
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_default_file_path(self) -> Path:
        """Compute the default config file path."""
        return Path(__file__).parent.parent.joinpath("data/default.config.json")

    def __init__(self) -> None:
        """Initializes the singleton ConfigEOS instance.

        Configuration data is loaded from a configuration file or a default one is created if none
        exists.
        """
        super().__init__()
        self.from_config_file()
        self.update()

    @property
    def settings(self) -> Optional[SettingsEOS]:
        """Returns global settings for EOS.

        Settings generally provide configuration for EOS and are typically set only once.

        Returns:
            SettingsEOS: The settings for EOS or None.
        """
        return self.__class__._settings

    @classmethod
    def _merge_and_update_settings(cls, settings: SettingsEOS) -> None:
        """Merge new and available settings.

        Args:
            settings (SettingsEOS): The new settings to apply.
        """
        for key in SettingsEOS.model_fields:
            if value := getattr(settings, key, None):
                setattr(cls._settings, key, value)

    def merge_settings(self, settings: SettingsEOS, force: Optional[bool] = None) -> None:
        """Merges the provided settings into the global settings for EOS, with optional overwrite.

        Args:
            settings (SettingsEOS): The settings to apply globally.
            force (Optional[bool]): If True, overwrites the existing settings completely.
                If False, the new settings are merged to the existing ones with priority for
                the new ones.

        Raises:
            ValueError: If settings are already set and `force` is not True or
                if the `settings` is not a `SettingsEOS` instance.
        """
        if not isinstance(settings, SettingsEOS):
            raise ValueError(f"Settings must be an instance of SettingsEOS: '{settings}'.")

        if self.__class__._settings is None or force:
            self.__class__._settings = settings
        else:
            self._merge_and_update_settings(settings)

        # Update configuration after merging
        self.update()

    def merge_settings_from_dict(self, data: dict) -> None:
        """Merges the provided dictionary data into the current instance.

        Creates a new settings instance with all optional fields reset to None,
        then applies the dictionary data through validation, and finally merges
        the validated settings into the current instance.

        Args:
            data (dict): Dictionary containing field values to merge into the
                current settings instance.

        Raises:
            ValidationError: If the data contains invalid values for the defined fields.

        Example:
            >>> config = get_config()
            >>> new_data = {"prediction_hours": 24, "server_fastapi_port": 8000}
            >>> config.merge_settings_from_dict(new_data)
        """
        # Create new settings instance with reset optional fields and merged data
        settings = SettingsEOS.from_dict_with_reset(data)
        self.merge_settings(settings)

    def reset_settings(self) -> None:
        """Reset all available settings.

        This functions basically deletes the settings provided before.
        """
        self.__class__._settings = None

    def _update_data_folder_path(self) -> None:
        """Updates path to the data directory."""
        # From Settings
        if self.settings and (data_dir := self.settings.data_folder_path):
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                self.data_folder_path = data_dir
                return
            except:
                pass
        # From EOS_DIR env
        env_dir = os.getenv(self.EOS_DIR)
        if env_dir is not None:
            try:
                data_dir = Path(env_dir).resolve()
                data_dir.mkdir(parents=True, exist_ok=True)
                self.data_folder_path = data_dir
                return
            except:
                pass
        # From configuration file
        if self._file_settings and (data_dir := self._file_settings.data_folder_path):
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                self.data_folder_path = data_dir
                return
            except:
                pass
        # From platform specific default path
        try:
            data_dir = platformdirs.user_data_dir(self.APP_NAME, self.APP_AUTHOR)
            if data_dir is not None:
                data_dir.mkdir(parents=True, exist_ok=True)
                self.data_folder_path = data_dir
                return
        except:
            pass
        # Current working directory
        data_dir = Path.cwd()
        self.data_folder_path = data_dir

    def _config_folder_path(self) -> Optional[Path]:
        """Finds the first directory containing a valid configuration file.

        Returns:
            Path: The path to the configuration directory, or None if not found.
        """
        config_dirs = []
        config_dir = None
        env_dir = os.getenv(self.EOS_DIR)
        logger.debug(f"Envionment '{self.EOS_DIR}': '{env_dir}'")
        if env_dir is not None:
            config_dirs.append(Path(env_dir).resolve())
        config_dirs.append(Path(platformdirs.user_config_dir(self.APP_NAME)))
        config_dirs.append(Path.cwd())
        for cdir in config_dirs:
            cfile = cdir.joinpath(self.CONFIG_FILE_NAME)
            if cfile.exists():
                logger.debug(f"Found config file: '{cfile}'")
                config_dir = cdir
                break
        return config_dir

    def _config_file_path(self) -> Path:
        """Finds the path to the configuration file.

        Returns:
            Path: The path to the configuration file. May not exist.
        """
        config_file = None
        config_dir = self._config_folder_path()
        if config_dir is None:
            # There is currently no configuration file - create it in default path
            env_dir = os.getenv(self.EOS_DIR)
            if env_dir is not None:
                config_dir = Path(env_dir).resolve()
            else:
                config_dir = Path(platformdirs.user_config_dir(self.APP_NAME))
            config_file = config_dir.joinpath(self.CONFIG_FILE_NAME)
        else:
            config_file = config_dir.joinpath(self.CONFIG_FILE_NAME)
        return config_file

    def from_config_file(self) -> None:
        """Loads the configuration file settings for EOS.

        Raises:
            ValueError: If the configuration file is invalid or incomplete.
        """
        config_file = self._config_file_path()
        config_dir = config_file.parent
        if not config_file.exists():
            config_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(self.config_default_file_path, config_file)
            except Exception as exc:
                logger.warning(f"Could not copy default config: {exc}. Using default copy...")
                config_file = self.config_default_file_path
                config_dir = config_file.parent

        with config_file.open("r", encoding=self.ENCODING) as f_in:
            try:
                json_str = f_in.read()
                self.__class__._file_settings = SettingsEOS.model_validate_json(json_str)
            except ValidationError as exc:
                raise ValueError(f"Configuration '{config_file}' is incomplete or not valid: {exc}")

        self.update()
        # Everthing worked, remember the values
        self.config_folder_path = config_dir
        self.config_file_path = config_file

    def to_config_file(self) -> None:
        """Saves the current configuration to the configuration file.

        Also updates the configuration file settings.

        Raises:
            ValueError: If the configuration file path is not specified or can not be written to.
        """
        if not self.config_file_path:
            raise ValueError("Configuration file path unknown.")
        with self.config_file_path.open("r", encoding=self.ENCODING) as f_out:
            try:
                json_str = super().to_json()
                # Write to file
                f_out.write(json_str)
                # Also remeber as actual settings
                self.__class__._file_settings = self.model_validate_json(json_str)
            except ValidationError as exc:
                raise ValueError(f"Could not update '{self.config_file_path}': {exc}")

    def _config_value(self, key: str, value_type: Any) -> Any:
        """Retrieves the configuration value for a specific key, following a priority order.

        Values are fetched in the following order:
            1. Settings.
            2. Environment variables.
            3. EOS configuration file.
            4. Current configuration.
            5. Field default constants.

        Args:
            key (str): The configuration key to retrieve.

        Returns:
            Any: The configuration value, or None if not found.
        """
        # Settings
        if self.__class__._settings:
            if (value := getattr(self.settings, key, None)) is not None:
                return value

        # Environment variables
        if (value := os.getenv(key)) is not None:
            try:
                adapter = TypeAdapter(value_type)
                return adapter.validate_json(value)
            except ValueError:
                return value

        # EOS configuration file.
        if self._file_settings:
            if (value := getattr(self._file_settings, key, None)) is not None:
                return value

        # Current configuration - key is valid as called by update().
        if (value := getattr(self, key, None)) is not None:
            return value

        # Field default constants
        if (value := self.model_fields[key].default) is not None:
            return value

        logger.debug(f"Value for configuration key '{key}' not found or is {value}")
        return None

    def update(self) -> None:
        """Updates all configuration fields.

        This method updates all configuration fields using the following order for value retrieval:
            1. Settings.
            2. Environment variables.
            3. EOS configuration file.
            4. Current configuration.
            5. Field default constants.

        The first non None value in priority order is taken.
        """
        self._update_data_folder_path()
        for key, field_info in self.model_fields.items():
            setattr(self, key, self._config_value(key, field_info.annotation))


def get_config() -> ConfigEOS:
    """Gets the EOS configuration data."""
    return ConfigEOS()
