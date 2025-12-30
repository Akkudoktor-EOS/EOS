"""Abstract and base classes for adapters."""

from abc import abstractmethod
from typing import Any, Optional

from loguru import logger
from pydantic import (
    Field,
    field_validator,
)

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    SingletonMixin,
    StartMixin,
)
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
)


class AdapterProvider(SingletonMixin, ConfigMixin, MeasurementMixin, StartMixin, PydanticBaseModel):
    """Abstract base class for adapter providers with singleton thread-safety and configurable data parameters.

    Note:
        Derived classes have to provide their own _update_data method.
    """

    update_datetime: Optional[DateTime] = Field(
        None, json_schema_extra={"description": "Latest update datetime for adapter data"}
    )

    @abstractmethod
    def provider_id(self) -> str:
        """Return the unique identifier for the adapter provider.

        To be implemented by derived classes.
        """
        return "AdapterProvider"

    def enabled(self) -> bool:
        """Return True if the provider is enabled according to configuration.

        Can be overwritten by derived classes.
        """
        if self.config.adapter is None:
            return False
        if isinstance(self.config.adapter.provider, str):
            return self.provider_id() == self.config.adapter.provider
        if isinstance(self.config.adapter.provider, list):
            return self.provider_id() in self.config.adapter.provider
        return False

    @abstractmethod
    def _update_data(self) -> None:
        """Abstract method for custom adapter data update logic, to be implemented by derived classes.

        Data update may be requested at different stages of energy management. The stage can be
        detected by self.ems.stage().
        """
        pass

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if hasattr(self, "_initialized"):
            return
        super().__init__(*args, **kwargs)

    def update_data(
        self,
        force_enable: Optional[bool] = False,
    ) -> None:
        """Calls the custom update function if enabled or forced.

        Args:
            force_enable (bool, optional): If True, forces the update even if the provider is disabled.
        """
        # Check after configuration is updated.
        if not force_enable and not self.enabled():
            return

        # Call the custom update logic
        logger.debug(f"Update adapter provider: {self.provider_id()}")
        self._update_data()


class AdapterContainer(SingletonMixin, ConfigMixin, PydanticBaseModel):
    """A container for managing multiple adapter provider instances.

    This class enables to control multiple adapter providers
    """

    providers: list[AdapterProvider] = Field(
        default_factory=list, json_schema_extra={"description": "List of adapter providers"}
    )

    @field_validator("providers")
    def check_providers(cls, value: list[AdapterProvider]) -> list[AdapterProvider]:
        # Check each item in the list
        for item in value:
            if not isinstance(item, AdapterProvider):
                raise TypeError(
                    f"Each item in the adapter providers list must be an AdapterProvider, got {type(item).__name__}"
                )
        return value

    @property
    def enabled_providers(self) -> list[Any]:
        """List of providers that are currently enabled."""
        enab = []
        for provider in self.providers:
            if provider.enabled():
                enab.append(provider)
        return enab

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if hasattr(self, "_initialized"):
            return
        super().__init__(*args, **kwargs)

    def provider_by_id(self, provider_id: str) -> AdapterProvider:
        """Retrieves an adapter provider by its unique identifier.

        This method searches through the list of all available providers and
        returns the first provider whose `provider_id` matches the given
        `provider_id`. If no matching provider is found, the method returns `None`.

        Args:
            provider_id (str): The unique identifier of the desired data provider.

        Returns:
            DataProvider: The data provider matching the given `provider_id`.

        Raises:
            ValueError if provider id is unknown.

        Example:
            provider = data.provider_by_id("WeatherImport")
        """
        providers = {provider.provider_id(): provider for provider in self.providers}
        if provider_id not in providers:
            error_msg = f"Unknown provider id: '{provider_id}' of '{providers.keys()}'."
            logger.error(error_msg)
            raise ValueError(error_msg)
        return providers[provider_id]

    def update_data(
        self,
        force_enable: Optional[bool] = False,
    ) -> None:
        """Calls the custom update function of all adapters if enabled or forced.

        Args:
            force_enable (bool, optional): If True, forces the update even if the provider is disabled.
        """
        # Call the custom update logic
        if len(self.providers) > 0:
            for provider in self.providers:
                provider.update_data(force_enable=force_enable)
