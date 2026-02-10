"""
Tests for Adapter and AdapterContainer integration.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeAlias

import pytest

from akkudoktoreos.adapter.adapter import (
    Adapter,
    AdapterCommonSettings,
)
from akkudoktoreos.adapter.adapterabc import AdapterContainer
from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter
from akkudoktoreos.adapter.nodered import NodeREDAdapter
from akkudoktoreos.core.coreabc import get_adapter

# ---------- Typed aliases for fixtures ----------
AdapterFixture: TypeAlias = Adapter
SettingsFixture: TypeAlias = AdapterCommonSettings


# ---------- Fixtures ----------
@pytest.fixture
def adapter() -> AdapterFixture:
    """Fixture returning a fully initialized Adapter instance."""
    return get_adapter()


@pytest.fixture
def settings() -> SettingsFixture:
    """Fixture providing default adapter common settings."""
    return AdapterCommonSettings()


# ---------- Test Class ----------
class TestAdapter:
    def test_is_adapter_container(self, adapter: AdapterFixture) -> None:
        """Adapter should be an AdapterContainer and an Adapter."""
        assert isinstance(adapter, AdapterContainer)
        assert isinstance(adapter, Adapter)

    def test_providers_present(self, adapter: AdapterFixture) -> None:
        """Adapter must contain HA and NodeRED providers."""
        assert len(adapter.providers) == 2
        assert any(isinstance(p, HomeAssistantAdapter) for p in adapter.providers)
        assert any(isinstance(p, NodeREDAdapter) for p in adapter.providers)

    def test_adapter_order(self, adapter: AdapterFixture) -> None:
        """Provider order should match HomeAssistantAdapter -> NodeREDAdapter."""
        assert isinstance(adapter.providers[0], HomeAssistantAdapter)
        assert isinstance(adapter.providers[1], NodeREDAdapter)

    # ----- AdapterCommonSettings -----

    def test_settings_default_provider(self, settings: SettingsFixture) -> None:
        """Default provider should be None."""
        assert settings.provider is None

    def test_settings_accepts_single_provider(self, settings: SettingsFixture) -> None:
        """Settings should accept a single provider literal."""
        settings.provider = ["HomeAssistant"]
        assert settings.provider == ["HomeAssistant"]

    def test_settings_accepts_multiple_providers(self, settings: SettingsFixture) -> None:
        """Settings should accept multiple provider literals."""
        settings.provider = ["HomeAssistant", "NodeRED"]
        assert isinstance(settings.provider, list)
        assert settings.provider == ["HomeAssistant", "NodeRED"]

    def test_provider_sub_settings(self, settings: SettingsFixture) -> None:
        """sub-settings (homeassistant & nodered) must be initialized."""
        assert hasattr(settings, "homeassistant")
        assert hasattr(settings, "nodered")
        assert settings.homeassistant is not None
        assert settings.nodered is not None
