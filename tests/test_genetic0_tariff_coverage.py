"""GENETIC0 must not extend a published feed-in tariff beyond real coverage."""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic0.genetic0params import Genetic0OptimizationParameters
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.mark.asyncio
@pytest.mark.parametrize("published_hours", [20, 24])
async def test_dvhub_tariff_requires_real_control_horizon(
    config_eos, published_hours, caplog
):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"genetic0": {"horizon_hours": 24}},
            "feedintariff": {"provider": "FeedInTariffDvhubOnline"},
            "elecfee": {"provider": None},
            "devices": {
                "max_batteries": 0,
                "max_electric_vehicles": 0,
                "max_inverters": 0,
                "max_home_appliances": 0,
            },
        }
    )
    start = to_datetime("2026-08-01T00:00:00+00:00")
    get_ems().set_start_datetime(start)
    prices = pd.Series(
        np.arange(published_hours * 4, dtype=float) / 1_000_000,
        index=pd.date_range(start, periods=published_hours * 4, freq="15min"),
    )

    async def read_array(**kwargs):
        return np.full(48, 1.0)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(return_value=prices),
    )
    with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
        parameters = await Genetic0OptimizationParameters.prepare()

    prediction.update_data.assert_awaited_once()
    assert config_eos.feedintariff.provider == "FeedInTariffDvhubOnline"
    if published_hours < 24:
        assert parameters is None
        assert "Missing feed-in tariff within the GENETIC0 control horizon" in caplog.text
    else:
        assert parameters is not None
        assert parameters.ems.feed_in_tariff_per_wh[:24] == pytest.approx(
            [(4 * hour + 1.5) / 1_000_000 for hour in range(24)]
        )
        assert np.isnan(parameters.ems.feed_in_tariff_per_wh[24:]).all()
