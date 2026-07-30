"""Test server endpoints directly calling the endpoint function (unit tests)."""

import numpy as np
import pytest

from akkudoktoreos.server.eos import fastapi_strompreis
from akkudoktoreos.utils.datetimeutil import to_duration


class _FakeConfig:
    def __init__(self):
        self.settings = None

    def merge_settings(self, settings):
        self.settings = settings


class _FakeEms:
    def __init__(self):
        self.run_kwargs = None

    async def run(self, **kwargs):
        self.run_kwargs = kwargs


class TestServerEndpointDirect:

    @pytest.mark.asyncio
    async def test_fastapi_strompreis(self, monkeypatch):
        class _FakePrediction:
            def __init__(self):
                self.key_to_array_kwargs = None

            async def key_to_array(self, **kwargs):
                self.key_to_array_kwargs = kwargs

                # two hours of hourly values
                return np.array([4.0, 16.0])

        config = _FakeConfig()
        ems = _FakeEms()
        prediction = _FakePrediction()

        monkeypatch.setattr(
            "akkudoktoreos.server.eos.get_config",
            lambda: config,
        )
        monkeypatch.setattr(
            "akkudoktoreos.server.eos.get_ems",
            lambda: ems,
        )
        monkeypatch.setattr(
            "akkudoktoreos.server.eos.get_prediction",
            lambda: prediction,
        )

        result = await fastapi_strompreis()

        assert ems.run_kwargs is not None
        assert prediction.key_to_array_kwargs is not None
        assert result == [4.0, 16.0]

        assert ems.run_kwargs == {
            "mode": ems.run_kwargs["mode"],  # see below
            "force_update": True,
        }

        kwargs = prediction.key_to_array_kwargs

        assert kwargs["key"] == "elecprice_marketprice_wh"
        assert kwargs["interval"] == to_duration("1 hour")
        assert kwargs["fill_method"] == "ffill"
        assert kwargs["resample_method"] == "interval_mean"
