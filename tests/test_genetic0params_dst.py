"""GENETIC0 parameter arrays must stay aligned with the start hour on DST days."""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start",
    [
        # Normal day: 23 elapsed hours since local midnight.
        pendulum.datetime(2026, 10, 24, 23, tz="Europe/Berlin"),
        # Fall back (CEST -> CET): 24 elapsed hours since local midnight.
        pendulum.datetime(2026, 10, 25, 23, tz="Europe/Berlin"),
        # Spring forward (CET -> CEST): 22 elapsed hours since local midnight.
        pendulum.datetime(2026, 3, 29, 23, tz="Europe/Berlin"),
    ],
    ids=["normal", "fall-back", "spring-forward"],
)
async def test_parameter_arrays_start_hour_aligned_on_dst_days(config_eos, start):
    config_eos.merge_settings_from_dict(
        {
            "general": {"timezone": "Europe/Berlin"},
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
    get_ems().set_start_datetime(start)
    ems_start = get_ems().start_datetime
    assert ems_start.hour == 23

    # The tariff is only published from the start slot on, as a live
    # provider would do late in the evening.
    prices = pd.Series(
        np.full(24 * 4, 0.0001),
        index=pd.date_range(ems_start, periods=24 * 4, freq="15min"),
    )
    array_starts = []

    async def read_array(**kwargs):
        array_starts.append(kwargs["start_datetime"])
        return np.full(48, 1.0)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(return_value=prices),
    )
    with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
        parameters = await Genetic0OptimizationParameters.prepare()

    assert parameters is not None
    # Index ``start.hour`` of every array is the start slot.
    assert array_starts
    for array_start in array_starts:
        assert array_start.add(hours=ems_start.hour) == ems_start
    tariffs = parameters.ems.feed_in_tariff_per_wh
    assert isinstance(tariffs, list)
    assert np.isfinite(tariffs[ems_start.hour : ems_start.hour + 24]).all()
