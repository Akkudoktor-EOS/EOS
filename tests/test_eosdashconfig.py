"""Test suite for the EOS Dash configuration module.

This module contains tests for utility functions related to retrieving and processing
configuration data using Pydantic models.
"""

import json
from pathlib import Path
from typing import Union

import pytest
from pydantic.fields import FieldInfo

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecast import PVForecastPlaneSetting
from akkudoktoreos.server.dash.configuration import (
    configuration,
    get_default_value,
    get_nested_value,
    resolve_nested_types,
)

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_EOSSERVER_CONFIG_1 = DIR_TESTDATA.joinpath("eosserver_config_1.json")


class SampleModel(PydanticBaseModel):
    field1: str = "default_value"
    field2: int = 10


class TestEOSdashConfig:
    """Test case for EOS Dash configuration utility functions.

    This class tests functions for retrieving nested values, extracting default values,
    resolving nested types, and generating configuration details from Pydantic models.
    """

    def test_get_nested_value_from_dict(self):
        """Test retrieving a nested value from a dictionary using a sequence of keys."""
        data = {"a": {"b": {"c": 42}}}
        assert get_nested_value(data, ["a", "b", "c"]) == 42
        assert get_nested_value(data, ["a", "x"], default="not found") == "not found"
        with pytest.raises(TypeError):
            get_nested_value("not_a_dict", ["a"])  # type: ignore

    def test_get_nested_value_from_list(self):
        """Test retrieving a nested value from a list using a sequence of keys."""
        data = {"a": {"b": {"c": [42]}}}
        assert get_nested_value(data, ["a", "b", "c", 0]) == 42
        assert get_nested_value(data, ["a", "b", "c", "0"]) == 42

    def test_get_default_value(self):
        """Test retrieving the default value of a field based on FieldInfo metadata."""
        field_info = FieldInfo(default="test_value")
        assert get_default_value(field_info, True) == "test_value"
        field_info_no_default = FieldInfo()
        assert get_default_value(field_info_no_default, True) == ""
        assert get_default_value(field_info, False) == "N/A"

    def test_resolve_nested_types(self):
        """Test resolving nested types within a field, ensuring correct type extraction."""
        nested_types = resolve_nested_types(Union[int, str], [])
        assert (int, []) in nested_types
        assert (str, []) in nested_types

    def test_configuration(self):
        """Test extracting configuration details from a Pydantic model based on provided values."""
        values = {"field1": "custom_value", "field2": 20}
        config = configuration(SampleModel, values)
        assert any(
            item["name"] == "field1" and item["value"] == '"custom_value"' for item in config
        )
        assert any(item["name"] == "field2" and item["value"] == "20" for item in config)

    def test_configuration_eos(self, config_eos):
        """Test extracting EOS configuration details from EOS config based on provided values."""
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            values = json.load(fd)
        config = configuration(config_eos, values)
        assert any(
            item["name"] == "server.eosdash_port" and item["value"] == "8504" for item in config
        )
        assert any(
            item["name"] == "server.eosdash_host" and item["value"] == '"127.0.0.1"'
            for item in config
        )

    def test_configuration_pvforecast_plane_settings(self):
        """Test extracting EOS PV forecast plane configuration details from EOS config based on provided values."""
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            values = json.load(fd)
        config = configuration(
            PVForecastPlaneSetting(), values, values_prefix=["pvforecast", "planes", "0"]
        )
        assert any(
            item["name"] == "pvforecast.planes.0.surface_azimuth" and item["value"] == "170"
            for item in config
        )
        assert any(
            item["name"] == "pvforecast.planes.0.userhorizon"
            and item["value"] == "[20, 27, 22, 20]"
            for item in config
        )
