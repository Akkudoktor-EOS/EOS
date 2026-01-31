from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from akkudoktoreos.adapter.adapter import AdapterCommonSettings
from akkudoktoreos.adapter.nodered import NodeREDAdapter, NodeREDAdapterCommonSettings
from akkudoktoreos.core.emplan import DDBCInstruction, FRBCInstruction
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.utils.datetimeutil import DateTime, compare_datetimes, to_datetime


@pytest.fixture
def mock_ems() -> MagicMock:
    m = MagicMock()
    m.stage.return_value = EnergyManagementStage.DATA_ACQUISITION
    m.plan.return_value.get_active_instructions.return_value = []
    return m


@pytest.fixture
def adapter(config_eos, mock_ems: MagicMock) -> NodeREDAdapter:
    """Fully Pydantic-safe NodeREDAdapter fixture."""
    # Set nested value - also fills None values
    config_eos.set_nested_value("adapter/provider", ["NodeRED"])

    ad = NodeREDAdapter()

    # Mark update datetime invalid
    ad.update_datetime = None

    # Assign EMS
    object.__setattr__(ad, "ems", mock_ems)

    return ad


class TestNodeREDAdapter:

    def test_provider_id(self, adapter: NodeREDAdapter):
        assert adapter.provider_id() == "NodeRED"

    def test_enabled_detection_single(self, adapter: NodeREDAdapter):
        adapter.config.adapter.provider = ["NodeRED"]
        assert adapter.enabled() is True
        adapter.config.adapter.provider = ["HomeAssistant"]
        assert adapter.enabled() is False
        adapter.config.adapter.provider = ["HomeAssistant", "NodeRED"]
        assert adapter.enabled() is True

    @patch("requests.get")
    def test_update_datetime(self, mock_get, adapter: NodeREDAdapter):
        adapter.ems.stage.return_value = EnergyManagementStage.DATA_ACQUISITION
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"foo": "bar"}
        now = to_datetime()

        adapter.update_data(force_enable=True)

        mock_get.assert_called_once()
        assert compare_datetimes(adapter.update_datetime, now).approximately_equal

    @patch("requests.get")
    def test_update_data_data_acquisition_success(self, mock_get    , adapter: NodeREDAdapter):
        adapter.ems.stage.return_value = EnergyManagementStage.DATA_ACQUISITION
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"foo": "bar"}

        adapter.update_data(force_enable=True)

        mock_get.assert_called_once()
        url, = mock_get.call_args[0]
        assert "/eos/data_aquisition" in url

    @patch("requests.get", side_effect=Exception("boom"))
    def test_update_data_data_acquisition_failure(self, mock_get, adapter: NodeREDAdapter):
        adapter.ems.stage.return_value = EnergyManagementStage.DATA_ACQUISITION
        with pytest.raises(RuntimeError):
            adapter.update_data(force_enable=True)

    @patch("requests.post")
    def test_update_data_control_dispatch_instructions(self, mock_post, adapter: NodeREDAdapter):
        adapter.ems.stage.return_value = EnergyManagementStage.CONTROL_DISPATCH

        instr1 = DDBCInstruction(
            id="res1@extra", operation_mode_id="X", operation_mode_factor=0.5,
            actuator_id="dummy", execution_time=to_datetime()
        )
        instr2 = FRBCInstruction(
            id="resA", operation_mode_id="Y", operation_mode_factor=0.25,
            actuator_id="dummy", execution_time=to_datetime()
        )
        adapter.ems.plan.return_value.get_active_instructions.return_value = [instr1, instr2]

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {}

        adapter.update_data(force_enable=True)

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["res1_op_mode"] == "X"
        assert payload["res1_op_factor"] == 0.5
        assert payload["resA_op_mode"] == "Y"
        assert payload["resA_op_factor"] == 0.25
        url, = mock_post.call_args[0]
        assert "/eos/control_dispatch" in url

    @patch("requests.post")
    def test_update_data_disabled_provider(self, mock_post, adapter: NodeREDAdapter):
        adapter.config.adapter.provider = ["HomeAssistant"]  # NodeRED disabled
        adapter.update_data(force_enable=False)
        mock_post.assert_not_called()

    @patch("requests.post")
    def test_update_data_force_enable_overrides_disabled(self, mock_post, adapter: NodeREDAdapter):
        adapter.config.adapter.provider = ["HomeAssistant"]
        adapter.ems.stage.return_value = EnergyManagementStage.CONTROL_DISPATCH
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {}

        adapter.update_data(force_enable=True)

        mock_post.assert_called_once()
